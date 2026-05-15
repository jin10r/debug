"""Application factory for creating and configuring the aiohttp application"""
import json
import os
import time
import logging
import asyncio
import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
import aiohttp_cors

from core.settings import settings
from core.db.dbconnect import Database, Request
from core.utils.cache import CacheManager
from core.handlers import basic_router
from core.middlewares.dbmiddleware import DbMiddleware
from core.middlewares.ratelimit import RateLimiter
from core.utils.metrics import setup_metrics_routes, set_application_info, metrics_middleware
from core.utils.logging_config import setup_logging, logging_middleware
from core.api.routes import setup_routes
from core.api.auth import init_redis, close_redis
from core.api.websocket import WebSocketManager
from core.tasks.background import cleanup_photos_task
from core.middlewares.jwt_auth import jwt_auth_middleware
from core.middlewares.auth import check_redis_required_connection
from core.middlewares.csrf import csrf_middleware

logger = logging.getLogger(__name__)


async def on_startup(app: web.Application):
    """Actions to perform on application startup."""
    print("--- ON_STARTUP CALLED (PRINT) ---")
    logger.info("--- ON_STARTUP CALLED (LOGGER) ---")
    # Retrieve components from the app context
    db_request: Request = app['db']
    bot: Bot = app['bot']
    dp: Dispatcher = app['dp']

    # --- Sequential Initialization ---
    logger.info("--- Starting Sequential Initialization ---")

    # 0. Проверка Redis (если обязателен)
    logger.info("Step 0/4: Checking Redis availability...")
    try:
        await check_redis_required_connection()
    except RuntimeError as e:
        logger.critical(f"Redis check failed: {e}")
        raise

    # 1. Initialize Redis connection
    logger.info("Step 1/4: Initializing Redis connection...")
    await init_redis(app)

    # 2. Database Schema Initialization (handled by init.sql)
    logger.info("Step 2/4: Database schema initialization is handled by init.sql.")

    # 3. Start Background Tasks
    logger.info("Step 3/4: Starting background tasks...")
    
    # Create shutdown event for polling task
    shutdown_event = asyncio.Event()
    app['shutdown_event'] = shutdown_event
    
    # Start bot polling in background (with proper error handling)
    async def run_bot_polling():
        """Wrapper for bot polling with restart on errors"""
        should_restart = True
        while should_restart:
            try:
                logger.info("🤖 Starting bot polling...")
                await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
                # If polling exits normally (shouldn't happen), stop restarting
                should_restart = False
            except asyncio.CancelledError:
                logger.info("✅ Bot polling cancelled (shutdown requested)")
                should_restart = False
                raise
            except Exception as e:
                # Only restart on unexpected errors, not during shutdown
                if not shutdown_event.is_set():
                    logger.error(f"❌ Bot polling error: {e}, restarting in 5 seconds...")
                    try:
                        await asyncio.sleep(5)
                    except asyncio.CancelledError:
                        logger.info("✅ Bot polling restart cancelled (shutdown requested)")
                        should_restart = False
                        raise
                else:
                    logger.info("✅ Bot polling stopped (shutdown in progress)")
                    should_restart = False
    
    app['bot_polling_task'] = asyncio.create_task(run_bot_polling())
    
    # Channel monitor ALWAYS runs in separate parser_service.py (microservice)
    logger.info("ℹ️ Channel monitor runs in parser_service.py (separate microservice)")
    app['channel_monitor_task'] = None
    
    # cleanup_photos_task принимает shutdown_event
    app['cleanup_photos_task'] = asyncio.create_task(cleanup_photos_task(shutdown_event))
    
    # PG NOTIFY listener для событий от parser
    logger.info("✅ PostgreSQL LISTEN for WebSocket events enabled")

    # Start PG NOTIFY -> WebSocket bridge for events created by other processes (e.g. parser)
    async def _pg_notify_listener():
        conn = None
        try:
            loop = asyncio.get_running_loop()
            db_pool = app.get('db_pool')
            ws_manager = app.get('websocket_manager')
            if not db_pool or not getattr(db_pool, 'pool', None) or not ws_manager:
                logger.warning("PG NOTIFY listener not started: missing db_pool or websocket_manager")
                return

            conn = await db_pool.pool.acquire()
            app['pg_notify_conn'] = conn

            def _on_notify(connection, pid, channel, payload):
                try:
                    if channel == 'events_new':
                        logger.info("📣 PG NOTIFY received on events_new")
                        event_data = json.loads(payload)
                        loop.create_task(ws_manager.broadcast_event(event_data))
                    elif channel == 'events_cleaned':
                        logger.info("📣 PG NOTIFY received on events_cleaned")
                        cleaned_data = json.loads(payload)
                        loop.create_task(ws_manager.broadcast_events_cleaned(cleaned_data))
                except Exception as e:
                    logger.warning(f"Failed to process NOTIFY {channel}: {e}")

            await conn.add_listener('events_new', _on_notify)
            await conn.add_listener('events_cleaned', _on_notify)
            logger.info("✅ Listening for PostgreSQL NOTIFY on channels: events_new, events_cleaned")

            shutdown_event = app.get('shutdown_event')
            if shutdown_event:
                await shutdown_event.wait()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"PG NOTIFY listener crashed: {e}", exc_info=True)
        finally:
            try:
                if conn is not None:
                    await conn.remove_listener('events_new', _on_notify)
            except Exception:
                pass
            try:
                if conn is not None:
                    db_pool = app.get('db_pool')
                    if db_pool and getattr(db_pool, 'pool', None):
                        await db_pool.pool.release(conn)
            except Exception:
                pass

    app['pg_notify_task'] = asyncio.create_task(_pg_notify_listener())

    logger.info("Step 4/4: Background tasks started.")
    logger.info("--- Initialization Complete ---")



async def on_shutdown(app: web.Application):
    """Actions to perform on application shutdown."""
    logger.info("🛑 Shutting down application...")
    
    # Set shutdown event to prevent task restarts
    shutdown_event = app.get('shutdown_event')
    if shutdown_event:
        shutdown_event.set()
    
    # Use asyncio.gather with timeout for parallel shutdown operations
    shutdown_tasks = []
    
    # 1. Shutdown notification to admin removed - admin functionality disabled
    bot: Bot = app.get('bot')
    dp: Dispatcher = app.get('dp')
    
    # 2. Stop bot polling gracefully
    async def stop_bot_polling():
        bot_polling_task = app.get('bot_polling_task')
        if bot_polling_task and not bot_polling_task.done():
            logger.info("⏸️ Stopping bot polling...")
            
            # Stop dispatcher first (non-blocking)
            if dp:
                try:
                    await asyncio.wait_for(dp.stop_polling(), timeout=3.0)  # Reduced timeout
                    logger.info("✅ Dispatcher polling stopped")
                except (asyncio.TimeoutError, Exception) as e:
                    logger.debug(f"Dispatcher stop: {e}")
            
            # Cancel polling task
            bot_polling_task.cancel()
            try:
                await asyncio.wait_for(bot_polling_task, timeout=5.0)  # Reduced timeout
                logger.info("✅ Bot polling stopped")
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
                logger.debug(f"Bot polling cancellation: {e}")
    
    shutdown_tasks.append(stop_bot_polling())

    # 3. Cancel background tasks
    async def cancel_background_tasks():
        cleanup_photos_task = app.get('cleanup_photos_task')

        tasks_to_cancel = []
        if cleanup_photos_task and not cleanup_photos_task.done():
            tasks_to_cancel.append(cleanup_photos_task)

        if tasks_to_cancel:
            logger.info(f"⏸️ Cancelling {len(tasks_to_cancel)} background tasks...")
            for task in tasks_to_cancel:
                task.cancel()
            # Задача cleanup_photos_task теперь проверяет shutdown_event и завершается быстро
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                    timeout=5.0
                )
                logger.info("✅ Background tasks cancelled")
            except asyncio.TimeoutError:
                logger.warning("Background tasks cancellation timeout")

    shutdown_tasks.append(cancel_background_tasks())

    # 3.5 Stop PG NOTIFY listener
    async def stop_pg_notify_listener():
        pg_task = app.get('pg_notify_task')
        if pg_task and not pg_task.done():
            pg_task.cancel()
            try:
                await asyncio.wait_for(pg_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass

    shutdown_tasks.append(stop_pg_notify_listener())
    
    # Execute shutdown tasks in parallel with overall timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(*shutdown_tasks, return_exceptions=True),
            timeout=10.0  # Общий таймаут для всех параллельных операций
        )
    except asyncio.TimeoutError:
        logger.warning("⏱️ Some shutdown operations timed out, continuing...")
    
    # 4. Close connections sequentially (critical resources)
    # Close bot session
    if bot:
        try:
            await asyncio.wait_for(bot.session.close(), timeout=3.0)  # Reduced timeout
            logger.info("✅ Bot session closed")
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"Bot session close: {e}")
    
    # 5. Close cache connection
    cache: CacheManager = app.get('cache')
    if cache:
        try:
            await asyncio.wait_for(cache.close(), timeout=3.0)  # Reduced timeout
            logger.info("✅ Cache connection closed")
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"Cache close: {e}")
    
    # 6. Close database connection (most critical, allow more time)
    db_pool = app.get('db_pool')
    if db_pool:
        try:
            await asyncio.wait_for(db_pool.close(), timeout=5.0)  # Reduced but still reasonable
            logger.info("✅ Database connection closed")
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"Database close: {e}")
    
    # 7. Close Redis connection
    try:
        await asyncio.wait_for(close_redis(app), timeout=3.0)
        logger.info("✅ Redis connection closed")
    except (asyncio.TimeoutError, Exception) as e:
        logger.debug(f"Redis close: {e}")


async def create_app():
    """Creates and configures the aiohttp application."""
    # Database setup
    db_pool = Database()
    try:
        await db_pool.connect(
            host=settings.db.host, port=settings.db.port, database=settings.db.database,
            user=settings.db.user, password=settings.db.password
        )
    except (asyncpg.PostgresError, OSError, ConnectionError, asyncio.TimeoutError) as e:
        logger.critical(f"Database connection failed after all retries: {e}")
        raise RuntimeError(f"Failed to connect to database: {e}") from e

    db_request = Request(db_pool)
    
    # Initialize in-memory cache
    cache_manager = CacheManager()
    await cache_manager.connect()
    logger.info("✅ In-memory cache initialized")
    
    # Bot and dispatcher setup
    bot = Bot(token=settings.bot.token, default=DefaultBotProperties())
    dp = Dispatcher()
    dp.update.middleware(DbMiddleware(db_request))
    dp.include_router(basic_router)
    
    # Channel monitor NOT created (runs in separate parser_service.py)
    # This prevents Pyrogram session conflicts and event duplication

    # Initialize in-memory rate limiter (single instance deployment)
    rate_limiter = RateLimiter(
        default_limit=60,
        window_seconds=60,
        cleanup_interval=300
    )
    logger.info("Using in-memory rate limiter")

    # Create app with middlewares (order matters: logging -> metrics -> csrf -> auth -> rate limiting)
    app = web.Application(middlewares=[
        logging_middleware,      # Request ID + structured logging
        metrics_middleware,      # Prometheus metrics collection
        csrf_middleware,         # CSRF protection (protects state-changing requests)
        jwt_auth_middleware,     # JWT authentication (protects API endpoints)
        rate_limiter.middleware  # Rate limiting
    ])
    
    # Store application context
    app['start_time'] = time.time()
    app['db_pool'] = db_pool
    app['db'] = db_request
    app['bot'] = bot
    app['dp'] = dp
    app['cache'] = cache_manager
    app['websocket_manager'] = WebSocketManager(db_request, cache_manager)

    # Связываем WebSocketManager с EventOperations для отправки новых событий
    # через WebSocket сразу после их добавления в базу данных
    app['db'].events.websocket_manager = app['websocket_manager']

    # Register lifecycle handlers
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Setup monitoring routes (health routes already in setup_routes)
    set_application_info(version='2.0.0')  # Application metadata for metrics
    setup_metrics_routes(app)

    # Setup API and static routes
    setup_routes(app)
    
    # Configure CORS (Cross-Origin Resource Sharing)
    allowed_origins = os.getenv('ALLOWED_ORIGINS', '*').split(',')
    cors_defaults = {}
    
    for origin in allowed_origins:
        origin = origin.strip()
        cors_defaults[origin] = aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "OPTIONS"]
        )
    
    cors = aiohttp_cors.setup(app, defaults=cors_defaults)
    
    # Apply CORS to all routes
    for route in list(app.router.routes()):
        cors.add(route)
    
    logger.info(f"CORS configured for origins: {allowed_origins}")

    return app
