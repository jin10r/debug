"""Application factory for creating and configuring the aiohttp application"""
import json
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
from core.api.auth import init_redis
from core.api.websocket import WebSocketManager
from core.middlewares.jwt_auth import jwt_auth_middleware
from core.middlewares.auth import check_redis_required_connection
from core.middlewares.csrf import csrf_middleware

logger = logging.getLogger(__name__)


async def _run_bot_polling(app: web.Application):
    """Bot polling loop with exponential backoff on network errors."""
    bot: Bot = app['bot']
    dp: Dispatcher = app['dp']
    shutdown_event = app['shutdown_event']

    if not settings or not settings.bot or not settings.bot.token:
        logger.warning("BOT_TOKEN not configured, skipping bot polling")
        return

    delay = 30
    max_delay = 60  # 60s вместо 300s — graceful shutdown не должен ждать 5 минут

    try:
        while not shutdown_event.is_set():
            try:
                logger.info("Starting bot polling...")
                delay = 30
                # handle_signals=False: иначе aiogram ставит СВОИ SIGTERM/SIGINT-хендлеры
                # поверх main.py — он останавливает только polling, а aiohttp-сервер
                # (site/runner) не получает shutdown и процесс висит до SIGKILL (137).
                # Сигналы обрабатывает единый хендлер в main.py → shutdown_event.
                await dp.start_polling(
                    bot,
                    handle_signals=False,
                    allowed_updates=dp.resolve_used_update_types(),
                )
                break
            except asyncio.CancelledError:
                logger.info("Bot polling cancelled (shutdown requested)")
                break
            except Exception as e:
                if shutdown_event.is_set():
                    break
                logger.warning(f"Bot polling failed: {e}. Retry in {delay}s...")
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break
                delay = min(delay * 2, max_delay)
    finally:
        # Сигналы обрабатывает main.py (polling запущен с handle_signals=False).
        # Этот finally — страховка: если polling завершился по иной причине
        # (исчерпан ретрай, краш), main() всё равно выйдет из shutdown_event.wait()
        # и запустит runner.cleanup().
        shutdown_event.set()


async def _run_pg_notify_listener(app: web.Application):
    """PostgreSQL LISTEN/NOTIFY → WebSocket bridge for parser-originated events."""
    conn = None
    notify_tasks: set = set()  # отслеживаем broadcast-задачи, чтобы не терять их
    try:
        loop = asyncio.get_running_loop()
        db_pool = app.get('db_pool')
        ws_manager = app.get('websocket_manager')
        if not db_pool or not getattr(db_pool, 'pool', None) or not ws_manager:
            logger.warning("PG NOTIFY listener not started: missing db_pool or websocket_manager")
            return

        conn = await db_pool.pool.acquire()
        app['pg_notify_conn'] = conn

        def _spawn(coro):
            # Отслеживаемая задача: ссылка хранится до завершения (иначе GC может
            # отменить задачу), при shutdown — отменяется в finally.
            task = loop.create_task(coro)
            notify_tasks.add(task)
            task.add_done_callback(notify_tasks.discard)

        def _on_notify(connection, pid, channel, payload):
            try:
                if channel == 'events_new':
                    _spawn(ws_manager.broadcast_event(json.loads(payload)))
                elif channel == 'events_cleaned':
                    _spawn(ws_manager.broadcast_events_cleaned(json.loads(payload)))
            except Exception as e:
                logger.warning(f"Failed to process NOTIFY {channel}: {e}")

        await conn.add_listener('events_new', _on_notify)
        await conn.add_listener('events_cleaned', _on_notify)
        logger.info("Listening for PostgreSQL NOTIFY on: events_new, events_cleaned")

        shutdown_event = app.get('shutdown_event')
        if shutdown_event:
            await shutdown_event.wait()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"PG NOTIFY listener crashed: {e}", exc_info=True)
    finally:
        for task in list(notify_tasks):
            if not task.done():
                task.cancel()
        if conn is not None:
            # Каждый шаг ограничен дедлайном: если UNLISTEN/release зависнут,
            # они не должны затормозить shutdown. Главное — вернуть соединение
            # в пул, иначе graceful pool.close() будет ждать его бесконечно.
            try:
                await asyncio.wait_for(conn.remove_listener('events_new', _on_notify), timeout=1.0)
                await asyncio.wait_for(conn.remove_listener('events_cleaned', _on_notify), timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                pass
            try:
                db_pool = app.get('db_pool')
                if db_pool and getattr(db_pool, 'pool', None):
                    await asyncio.wait_for(db_pool.pool.release(conn), timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                pass
            app['pg_notify_conn'] = None


async def on_startup(app: web.Application):
    """Actions to perform on application startup."""
    logger.info("--- ON_STARTUP CALLED ---")
    db_request: Request = app['db']
    bot: Bot = app['bot']
    dp: Dispatcher = app['dp']

    logger.info("--- Starting Sequential Initialization ---")

    logger.info("Step 0/4: Checking Redis availability...")
    try:
        await check_redis_required_connection()
    except RuntimeError as e:
        logger.critical(f"Redis check failed: {e}")
        raise

    logger.info("Step 1/4: Initializing Redis connection...")
    await init_redis(app)

    logger.info("Step 2/4: Database schema initialization is handled by init.sql.")

    logger.info("Step 3/4: Starting background tasks...")

    shutdown_event = asyncio.Event()
    app['shutdown_event'] = shutdown_event

    app['bot_polling_task'] = asyncio.create_task(_run_bot_polling(app))
    logger.info("Мониторинг канала и удаление фото выполняются в сервисе parser (отдельный микросервис)")
    app['channel_monitor_task'] = None

    logger.info("PostgreSQL LISTEN для WebSocket-событий включён")
    app['pg_notify_task'] = asyncio.create_task(_run_pg_notify_listener(app))

    logger.info("Step 4/4: Background tasks started.")
    logger.info("--- Initialization Complete ---")


async def on_shutdown(app: web.Application):
    """Actions to perform on application shutdown."""
    logger.info("Shutting down application...")

    shutdown_event = app.get('shutdown_event')
    if shutdown_event:
        shutdown_event.set()

    ws_manager = app.get('websocket_manager')
    if ws_manager:
        try:
            await asyncio.wait_for(ws_manager.close_all(), timeout=5.0)
            logger.info("WebSocket connections closed")
        except asyncio.TimeoutError:
            logger.warning("WebSocket close timed out")

    shutdown_tasks = []

    async def stop_bot_polling():
        bot_polling_task = app.get('bot_polling_task')
        if bot_polling_task and not bot_polling_task.done():
            logger.info("Stopping bot polling...")
            dp: Dispatcher = app.get('dp')
            if dp:
                try:
                    await asyncio.wait_for(dp.stop_polling(), timeout=3.0)
                except (asyncio.TimeoutError, Exception) as e:
                    logger.debug(f"Dispatcher stop: {e}")
            bot_polling_task.cancel()
            try:
                await asyncio.wait_for(bot_polling_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
                logger.debug(f"Bot polling cancellation: {e}")

    shutdown_tasks.append(stop_bot_polling())

    async def stop_pg_notify_listener():
        pg_task = app.get('pg_notify_task')
        if not pg_task or pg_task.done():
            return
        # shutdown_event уже выставлен (см. начало on_shutdown) — listener сам
        # выходит из await и выполняет finally: снимает подписки и возвращает
        # соединение в пул. НЕ отменяем его здесь преждевременно — cancel прервал
        # бы release, соединение утекло бы, и graceful pool.close() завис бы до
        # terminate(). Отменяем только как аварийный fallback по таймауту.
        try:
            await asyncio.wait_for(pg_task, timeout=3.0)
        except asyncio.TimeoutError:
            pg_task.cancel()
            await asyncio.gather(pg_task, return_exceptions=True)
        except Exception:
            pass

    shutdown_tasks.append(stop_pg_notify_listener())

    try:
        await asyncio.wait_for(
            asyncio.gather(*shutdown_tasks, return_exceptions=True),
            timeout=10.0
        )
    except asyncio.TimeoutError:
        logger.warning("Some shutdown operations timed out, continuing...")

    bot: Bot = app.get('bot')
    if bot:
        try:
            await asyncio.wait_for(bot.session.close(), timeout=3.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"Bot session close: {e}")

    cache: CacheManager = app.get('cache')
    if cache:
        try:
            await asyncio.wait_for(cache.close(), timeout=3.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"Cache close: {e}")

    db_pool = app.get('db_pool')
    if db_pool:
        try:
            await asyncio.wait_for(db_pool.close(), timeout=5.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"Database close: {e}")


async def create_app():
    """Creates and configures the aiohttp application."""
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

    cache_manager = CacheManager()
    await cache_manager.connect()
    logger.info("In-memory cache initialized")

    bot = Bot(token=settings.bot.token, default=DefaultBotProperties())
    dp = Dispatcher()
    dp.update.middleware(DbMiddleware(db_request))
    dp.include_router(basic_router)

    rate_limiter = RateLimiter(
        default_limit=60,
        window_seconds=60,
        cleanup_interval=300
    )

    app = web.Application(middlewares=[
        logging_middleware,
        metrics_middleware,
        csrf_middleware,
        jwt_auth_middleware,
        rate_limiter.middleware
    ])

    app['start_time'] = time.time()
    app['db_pool'] = db_pool
    app['db'] = db_request
    app['bot'] = bot
    app['dp'] = dp
    app['cache'] = cache_manager
    app['websocket_manager'] = WebSocketManager(db_request, cache_manager)
    app['db'].events.websocket_manager = app['websocket_manager']

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    set_application_info(version='2.0.0')
    setup_metrics_routes(app)
    setup_routes(app)

    # CORS: фронтенд приходит через тот же nginx (same-origin) — CORS вообще
    # не нужен в нормальном режиме. settings.app.allowed_origins пустой =
    # CORS выключен. При явно перечисленных origins-ах включаем CORS с
    # credentials на каждый перечисленный домен.
    allowed_origins = [
        o for o in settings.app.allowed_origins
        if o and o != '*'
    ]

    if allowed_origins:
        cors_defaults = {
            origin: aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods=["GET", "POST", "OPTIONS"]
            )
            for origin in allowed_origins
        }
        cors = aiohttp_cors.setup(app, defaults=cors_defaults)
        for route in list(app.router.routes()):
            cors.add(route)
        logger.info(f"CORS configured for explicit origins: {allowed_origins}")
    else:
        logger.info("CORS disabled (same-origin only) — ALLOWED_ORIGINS not set")

    return app
