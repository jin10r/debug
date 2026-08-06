"""
Prometheus metrics for monitoring application performance
"""
from prometheus_client import (
    Counter, Histogram, Gauge, Info,
    generate_latest, CONTENT_TYPE_LATEST, REGISTRY
)
from aiohttp import web
import time
import logging

logger = logging.getLogger(__name__)

# ============================================
# HTTP Metrics
# ============================================

http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint'],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

http_requests_in_progress = Gauge(
    'http_requests_in_progress',
    'Number of HTTP requests currently being processed'
)

# ============================================
# Database Metrics
# ============================================

db_pool_size = Gauge(
    'db_pool_size',
    'Current database connection pool size'
)

db_pool_idle = Gauge(
    'db_pool_idle',
    'Number of idle database connections'
)

db_pool_in_use = Gauge(
    'db_pool_in_use',
    'Number of in-use database connections'
)

# ============================================
# WebSocket Metrics
# ============================================

ws_connections_active = Gauge(
    'ws_connections_active',
    'Current number of active WebSocket connections'
)

ws_connections_rejected_total = Counter(
    'ws_connections_rejected_total',
    'Total WebSocket connections rejected (max limit reached)'
)

ws_ping_rate_limited_total = Counter(
    'ws_ping_rate_limited_total',
    'Total ping messages rejected by rate limiting'
)

ws_broadcasts_total = Counter(
    'ws_broadcasts_total',
    'Total WebSocket broadcasts',
    ['message_type']  # 'feature', 'events_cleaned'
)

ws_broadcast_latency_seconds = Histogram(
    'ws_broadcast_latency_seconds',
    'WebSocket broadcast latency (send to all clients)',
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
)

ws_broadcast_errors_total = Counter(
    'ws_broadcast_errors_total',
    'Total individual send failures during WebSocket broadcasts'
)


# ============================================
# Application Info
# ============================================

application_info = Info(
    'application',
    'Application metadata'
)

# Set application info (call this once at startup)
def set_application_info(version: str = '1.0.0'):
    """Устанавливает мета-информацию о приложении для Prometheus."""
    application_info.info({
        'version': version,
        'name': 'survival_map'
    })


# ============================================
# Middleware for HTTP Metrics
# ============================================

@web.middleware
async def metrics_middleware(request: web.Request, handler):
    """Middleware to collect HTTP request metrics"""
    
    # Skip metrics endpoint itself
    if request.path == '/metrics':
        return await handler(request)
    
    # Track requests in progress
    http_requests_in_progress.inc()
    
    start_time = time.time()
    status = 500  # Default to 500 in case of unhandled exception
    
    try:
        response = await handler(request)
        status = response.status
        return response
    
    except web.HTTPException as e:
        status = e.status
        raise
    
    except Exception:
        status = 500
        raise
    
    finally:
        duration = time.time() - start_time
        
        # Record metrics
        http_requests_total.labels(
            method=request.method,
            endpoint=_normalize_endpoint(request.path),
            status=status
        ).inc()
        
        http_request_duration_seconds.labels(
            method=request.method,
            endpoint=_normalize_endpoint(request.path)
        ).observe(duration)
        
        http_requests_in_progress.dec()


def _normalize_endpoint(path: str) -> str:
    """
    Normalize endpoint path for metrics
    
    Replaces dynamic segments to avoid high cardinality
    E.g., /api/user/123 -> /api/user/{id}
    """
    # Keep known endpoints as-is
    known_endpoints = {
        '/api/events',
        '/api/geo',
        '/api/data_status',
        '/health',
        '/health/ready',
        '/health/live',
        '/metrics'
    }
    
    if path in known_endpoints:
        return path
    
    # Group other paths
    if path.startswith('/api/'):
        return '/api/other'
    
    return '/other'


# ============================================
# Helper Functions
# ============================================

def update_db_pool_metrics(stats: dict):
    """Обновляет метрики пула соединений БД."""
    db_pool_size.set(stats.get('size', 0))
    db_pool_idle.set(stats.get('idle', 0))
    db_pool_in_use.set(stats.get('in_use', 0))


# ============================================
# Metrics Endpoint
# ============================================

async def metrics_handler(request: web.Request):
    """
    Prometheus metrics endpoint
    
    Returns metrics in Prometheus text format
    """
    # Update DB pool metrics before exposing.
    # У Database (core/db/db_base.py) нет метода get_pool_stats — читаем
    # актуальные счётчики напрямую из asyncpg-пула.
    db_pool = request.app.get('db_pool')
    if db_pool and getattr(db_pool, 'pool', None):
        try:
            pool = db_pool.pool
            update_db_pool_metrics({
                'size': pool.get_size(),
                'idle': pool.get_idle_size(),
                'in_use': pool.get_size() - pool.get_idle_size(),
            })
        except Exception as e:
            logger.error(f"Failed to update DB pool metrics: {e}")
    
    # Generate metrics
    metrics_output = generate_latest(REGISTRY)
    
    # CONTENT_TYPE_LATEST = 'text/plain; version=0.0.4; charset=utf-8' уже
    # содержит charset — aiohttp web.Response добавляет свой к параметру
    # content_type= и падает (ValueError). Задаём готовый Content-Type
    # заголовком, без content_type=/charset= параметров.
    return web.Response(
        body=metrics_output,
        headers={'Content-Type': CONTENT_TYPE_LATEST}
    )


def setup_metrics_routes(app: web.Application):
    """Регистрирует маршрут /metrics в aiohttp-приложении."""
    app.router.add_get('/metrics', metrics_handler)
    logger.info("Metrics endpoint registered at /metrics")

