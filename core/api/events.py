"""Events API handlers"""
import json
import logging
import hashlib
from datetime import datetime
from aiohttp import web
from pydantic import ValidationError

from core.models import EventsFilterRequest
from core.utils.cache import CacheManager
from core.settings import settings

logger = logging.getLogger(__name__)


async def get_events_status_handler(request: web.Request):
    db_request = request.app['db']

    # Поддержка как GET, так и POST запросов
    if request.method == 'POST':
        try:
            await request.json()  # Просто проверяем, что тело - JSON
        except Exception:
            pass  # Игнорируем ошибки при чтении тела POST-запроса

    meta = await db_request.get_events_meta()

    updated_at = meta.get('updated_at')
    updated_at_str = updated_at.isoformat() if updated_at else None

    return web.json_response({
        'version': meta.get('version', 0),
        'max_event_id': meta.get('max_event_id', 0),
        'updated_at': updated_at_str
    })


async def post_events_updates_handler(request: web.Request):
    db_request = request.app['db']

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    except Exception:
        data = {}

    after_id_raw = data.get('after_id', 0)
    limit_raw = data.get('limit', 2000)

    try:
        after_id = int(after_id_raw)
        if after_id < 0:
            raise ValueError()
    except Exception:
        return web.json_response({'error': 'Invalid after_id'}, status=400)

    if after_id > 0:
        min_id = await db_request.get_events_min_id()
        if min_id and after_id < (min_id - 1):
            return web.json_response({'resync_required': True}, status=409)

    try:
        limit = int(limit_raw)
        if limit < 1:
            raise ValueError()
        limit = min(limit, 10000)
    except Exception:
        limit = 2000

    meta = await db_request.get_events_meta()
    geojson_data = await db_request.get_events_updates_as_geojson(after_id=after_id, limit=limit)

    updated_at = meta.get('updated_at')
    updated_at_str = updated_at.isoformat() if updated_at else None

    return web.json_response({
        'version': meta.get('version', 0),
        'max_event_id': meta.get('max_event_id', 0),
        'updated_at': updated_at_str,
        'data': geojson_data
    })


async def get_events_snapshot_handler(request: web.Request):
    db_request = request.app['db']
    logger.info(f"[Events Snapshot] Request received, path: {request.path}, method: {request.method}")

    limit_raw = request.query.get('limit', '5000')
    try:
        limit = int(limit_raw)
        if limit < 1:
            raise ValueError()
        limit = min(limit, 20000)
    except Exception:
        limit = 5000

    meta = await db_request.get_events_meta()
    geojson_data = await db_request.get_events_snapshot_as_geojson(limit=limit)
    
    features_count = len(geojson_data.get('features', [])) if geojson_data else 0
    logger.info(f"[Events Snapshot] Returning {features_count} features")

    updated_at = meta.get('updated_at')
    updated_at_str = updated_at.isoformat() if updated_at else None

    return web.json_response({
        'version': meta.get('version', 0),
        'max_event_id': meta.get('max_event_id', 0),
        'updated_at': updated_at_str,
        'data': geojson_data
    })


async def get_events_handler(request: web.Request):
    """
    Optimized events handler with ETag support for conditional requests.
    Reduces bandwidth by returning 304 Not Modified when data hasn't changed.
    """
    db_request = request.app['db']
    cache: CacheManager = request.app.get('cache')
    
    try:
        data = await request.json()
        # Validate input with Pydantic
        filters = EventsFilterRequest(**data)
    except ValidationError as e:
        return web.json_response(
            {'error': 'Validation error', 'details': e.errors()},
            status=400
        )
    except json.JSONDecodeError:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error parsing request: {e}")
        return web.json_response({'error': 'Invalid request'}, status=400)
    
    try:
        # Incremental updates bypass cache and ETag
        if filters.since:
            since_dt = datetime.fromisoformat(filters.since.replace('Z', '+00:00'))
            geojson_data = await db_request.get_incremental_events(
                since=since_dt,
                time_interval_minutes=filters.time_filter,
                layers=filters.layers or None
            )
            return web.json_response({'data': geojson_data}, headers={'X-Cache': 'BYPASS'})
        
        # Check cache first
        cached_response = None
        if cache:
            cached_response = await cache.get_events_geojson(filters.time_filter, filters.layers)
        
        # If no cache, fetch from DB
        if not cached_response:
            geojson_data = await db_request.get_filtered_events_as_geojson(
                time_interval_minutes=filters.time_filter,
                layers=filters.layers or None
            )
            cached_response = json.dumps(geojson_data)
            
            # Cache result with optimized TTL
            if cache:
                # Увеличено TTL до 30 секунд для снижения нагрузки на БД
                # Старые события (>5 минут) кешируются дольше
                ttl = 60 if filters.time_filter > 5 else 30
                await cache.set_events_geojson(
                    filters.time_filter,
                    filters.layers,
                    cached_response,
                    ttl=ttl
                )
        
        # Generate ETag from cached response
        etag = hashlib.md5(cached_response.encode()).hexdigest()
        client_etag = request.headers.get('If-None-Match')
        
        # Return 304 Not Modified if ETag matches
        if client_etag == etag:
            logger.debug(f"ETag match - returning 304 Not Modified (ETag: {etag})")
            return web.Response(status=304, headers={'ETag': etag})
        
        # Return full response with ETag
        logger.debug(f"ETag mismatch or new request - returning full response (ETag: {etag})")
        response_data = {'data': json.loads(cached_response)}
        return web.json_response(
            response_data,
            headers={
                'ETag': etag,
                'X-Cache': 'HIT' if cache else 'MISS',
                'Cache-Control': 'no-cache'  # Client must revalidate with server
            }
        )
    except Exception as e:
        logger.error(f"Error fetching filtered events: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def get_streets_handler(request: web.Request):
    """Handles requests with Redis caching."""
    cache: CacheManager = request.app.get('cache')
    
    # Check cache first
    if cache:
        cached = await cache.get_streets_geojson()
        if cached:
            return web.Response(
                text=cached,
                content_type='application/json',
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'X-Cache': 'HIT'
                }
            )
    
    # Fetch from DB
    db_request = request.app['db']
    try:
        geojson_data = await db_request.get_all_streets_as_geojson()
        
        # Cache for 1 hour
        if cache:
            await cache.set_streets_geojson(geojson_data, ttl=3600)
        
        return web.Response(
            text=geojson_data,
            content_type='application/json',
            headers={
                'Cache-Control': 'public, max-age=3600',
                'X-Cache': 'MISS'
            }
        )
    except Exception as e:
        logger.error(f"Error fetching streets for API: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def get_data_status_handler(request: web.Request):
    """Проверяет статус данных - когда было последнее обновление."""
    db_request = request.app['db']
    try:
        # Получаем последнее событие из БД
        latest_event = await db_request.get_latest_event_time()
        
        if not latest_event:
            return web.json_response({
                'status': 'no_data',
                'message': 'Нет данных в базе',
                'last_update': None
            })
        
        # Проверяем, насколько старое последнее событие
        from datetime import timedelta

        now = datetime.utcnow()
        time_diff = now - latest_event
        
        # Считаем данные устаревшими если последнее событие старше 5 минут
        is_stale = time_diff > timedelta(minutes=5)
        
        return web.json_response({
            'status': 'stale' if is_stale else 'ok',
            'message': 'Данные не обновляются' if is_stale else 'Данные актуальны',
            'last_update': latest_event.isoformat(),
            'minutes_ago': int(time_diff.total_seconds() / 60)
        })
    except Exception as e:
        logger.error(f"Error checking data status: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)
