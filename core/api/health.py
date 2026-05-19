"""Health check endpoints for monitoring and orchestration"""
import asyncio
import logging
from aiohttp import web
from datetime import datetime

logger = logging.getLogger(__name__)


async def health_live_handler(request: web.Request):
    """
    Liveness probe - checks if app is running
    Returns 200 if the application process is alive
    
    Use for: Kubernetes liveness probe, load balancer health checks
    """
    utc_time = datetime.utcnow()
    return web.json_response({
        'status': 'alive',
        'timestamp': utc_time.isoformat()
    })


async def health_ready_handler(request: web.Request):
    """
    Readiness probe - checks if app is ready to serve traffic
    Validates all critical dependencies:
    - PostgreSQL connection
    - Redis connection (optional, warns if down)
    - Bot initialization
    
    Returns:
        200 - Ready to serve traffic
        503 - Not ready (dependencies unavailable)
    
    Use for: Kubernetes readiness probe, deployment validation
    """
    db_request = request.app.get('db')
    cache = request.app.get('cache')
    
    checks = {
        'database': {'status': 'unknown', 'message': ''},
        'redis': {'status': 'unknown', 'message': ''},
        'bot': {'status': 'unknown', 'message': ''},
        'overall': 'healthy'
    }
    
    # Check PostgreSQL
    try:
        if db_request and db_request.db.is_connected:
            # Simple query to verify connection
            await db_request.db.fetchval('SELECT 1')
            checks['database'] = {'status': 'healthy', 'message': 'Connected'}
        else:
            checks['database'] = {'status': 'unhealthy', 'message': 'Not connected'}
            checks['overall'] = 'unhealthy'
    except Exception as e:
        checks['database'] = {'status': 'unhealthy', 'message': f'Error: {str(e)}'}
        checks['overall'] = 'unhealthy'
        logger.error(f"Database health check failed: {e}")
    
    # Check Redis via RedisManager (auth/session store). Optional — degraded,
    # не unhealthy, если недоступен. In-memory кэш событий (app['cache']) — отдельно.
    try:
        from core.middlewares.auth import RedisManager
        redis = await RedisManager().get_redis()
        if redis is not None:
            await redis.ping()
            checks['redis'] = {'status': 'healthy', 'message': 'Connected'}
        else:
            checks['redis'] = {'status': 'degraded', 'message': 'Not connected'}
    except Exception as e:
        checks['redis'] = {'status': 'degraded', 'message': f'Error: {str(e)}'}
        logger.warning(f"Redis health check failed: {e}")
    
    # Check Telegram Bot (basic check)
    try:
        bot = request.app.get('bot')
        if bot:
            # Check if bot token is present
            checks['bot'] = {'status': 'healthy', 'message': 'Initialized'}
        else:
            checks['bot'] = {'status': 'unhealthy', 'message': 'Not initialized'}
            checks['overall'] = 'unhealthy'
    except Exception as e:
        checks['bot'] = {'status': 'unhealthy', 'message': f'Error: {str(e)}'}
        checks['overall'] = 'unhealthy'
        logger.error(f"Bot health check failed: {e}")
    
    # Return response
    utc_time = datetime.utcnow()
    status_code = 200 if checks['overall'] == 'healthy' else 503

    return web.json_response({
        'status': checks['overall'],
        'timestamp': utc_time.isoformat(),
        'checks': checks
    }, status=status_code)


async def health_detailed_handler(request: web.Request):
    """
    Detailed health check with metrics
    Returns comprehensive system information
    
    Use for: Monitoring dashboards, debugging
    """
    db_request = request.app.get('db')
    cache = request.app.get('cache')

    utc_time = datetime.utcnow()
    health_data = {
        'status': 'healthy',
        'timestamp': utc_time.isoformat(),
        'uptime': request.app.get('start_time', 0),  # Track app start time
        'version': '1.0.6',
        'checks': {}
    }
    
    # Database metrics
    try:
        if db_request and db_request.db.pool:
            pool = db_request.db.pool
            health_data['checks']['database'] = {
                'status': 'healthy',
                'pool_size': pool.get_size(),
                'pool_free': pool.get_size() - pool.get_idle_size(),
                'pool_max': pool.get_max_size()
            }
    except Exception as e:
        health_data['checks']['database'] = {
            'status': 'unhealthy',
            'error': str(e)
        }
        health_data['status'] = 'unhealthy'
    
    # Cache metrics
    try:
        if cache:
            cache_stats = await cache.get_stats()
            health_data['checks']['cache'] = {
                'status': 'healthy' if cache._connected else 'degraded',
                'backend': cache_stats.get('backend', 'unknown'),
                'stats': cache_stats
            }
    except Exception as e:
        health_data['checks']['cache'] = {
            'status': 'error',
            'error': str(e)
        }
    
    return web.json_response(health_data)
