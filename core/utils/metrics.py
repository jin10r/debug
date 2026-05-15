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
# Cache Metrics
# ============================================

cache_hits_total = Counter(
    'cache_hits_total',
    'Total cache hits',
    ['cache_type']  # e.g., 'streets', 'events', 'nlp'
)

cache_misses_total = Counter(
    'cache_misses_total',
    'Total cache misses',
    ['cache_type']
)

cache_operations_duration_seconds = Histogram(
    'cache_operations_duration_seconds',
    'Cache operation duration',
    ['operation', 'cache_type'],  # operation: get, set, delete
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5)
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

db_query_duration_seconds = Histogram(
    'db_query_duration_seconds',
    'Database query duration',
    ['query_type'],  # SELECT, INSERT, UPDATE, DELETE
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
)

db_slow_queries_total = Counter(
    'db_slow_queries_total',
    'Total number of slow queries (>100ms)',
    ['query_type']
)

db_pool_usage_percent = Gauge(
    'db_pool_usage_percent',
    'Database pool usage percentage (0-100)'
)

cache_miss_rate = Gauge(
    'cache_miss_rate',
    'Cache miss rate percentage (0-100)',
    ['cache_type']
)

parser_errors_total = Counter(
    'parser_errors_total',
    'Total parser errors',
    ['error_type']  # 'processing', 'database', 'timeout', etc.
)

parser_messages_processed_total = Counter(
    'parser_messages_processed_total',
    'Total messages processed by parser'
)

# ============================================
# NLP Metrics
# ============================================

nlp_processing_duration_seconds = Histogram(
    'nlp_processing_duration_seconds',
    'NLP processing duration',
    ['operation'],  # normalize, tokenize, ner, query_variants
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0)
)

nlp_worker_pool_tasks = Gauge(
    'nlp_worker_pool_tasks',
    'Number of tasks in NLP worker pool queue'
)

# ============================================
# Background Tasks Metrics
# ============================================

background_task_duration_seconds = Histogram(
    'background_task_duration_seconds',
    'Background task execution duration',
    ['task_name'],
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0)
)

background_task_errors_total = Counter(
    'background_task_errors_total',
    'Total background task errors',
    ['task_name']
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
    application_info.info({
        'version': version,
        'name': 'temperature_optimization'
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
        '/api/streets',
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

def record_cache_hit(cache_type: str):
    """Record a cache hit"""
    cache_hits_total.labels(cache_type=cache_type).inc()


def record_cache_miss(cache_type: str):
    """Record a cache miss"""
    cache_misses_total.labels(cache_type=cache_type).inc()


def update_db_pool_metrics(stats: dict):
    """Update database pool metrics"""
    db_pool_size.set(stats.get('size', 0))
    db_pool_idle.set(stats.get('idle', 0))
    db_pool_in_use.set(stats.get('in_use', 0))


def record_slow_query(query_type: str):
    """Record a slow query"""
    db_slow_queries_total.labels(query_type=query_type).inc()


# ============================================
# Metrics Endpoint
# ============================================

async def metrics_handler(request: web.Request):
    """
    Prometheus metrics endpoint
    
    Returns metrics in Prometheus text format
    """
    # Update DB pool metrics before exposing
    if 'db_pool' in request.app:
        try:
            stats = request.app['db_pool'].get_pool_stats()
            update_db_pool_metrics(stats)
        except Exception as e:
            logger.error(f"Failed to update DB pool metrics: {e}")
    
    # Generate metrics
    metrics_output = generate_latest(REGISTRY)
    
    return web.Response(
        body=metrics_output,
        content_type=CONTENT_TYPE_LATEST
    )


def setup_metrics_routes(app: web.Application):
    """Setup metrics routes"""
    app.router.add_get('/metrics', metrics_handler)
    logger.info("Metrics endpoint registered at /metrics")



