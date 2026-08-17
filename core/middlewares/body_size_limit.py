"""
Request body size limit middleware.

Limits total request body size to MAX_BODY_BYTES (1 MB) for all endpoints
except media upload/serving paths. Returns HTTP 413 (Payload Too Large)
when the Content-Length header exceeds the limit, or when the body is
being streamed and reaches the limit.

Media endpoints (/api/media/*) are exempt since media is served via
FileResponse (responses, not requests) — but the exemption guard ensures
future POST endpoints for media uploads can opt in.
"""

import logging
from aiohttp import web

from core.utils.validators import MAX_BODY_BYTES

logger = logging.getLogger(__name__)

_EXEMPT_PATHS = frozenset([
    '/api/media',  # Media is served (not uploaded) — no request body needed
])

# Endpoints that may carry larger JSON payloads (GeoJSON)
_LARGE_BODY_PATHS = frozenset([
    '/api/events',
    '/api/events/updates',
    '/api/events/status',
])

_LARGE_BODY_BYTES = 5 * 1024 * 1024  # 5 MB for GeoJSON payloads


def _get_max_bytes(path: str) -> int:
    for prefix in _LARGE_BODY_PATHS:
        if path == prefix or path.startswith(prefix + '/') or path.startswith(prefix + '?'):
            return _LARGE_BODY_BYTES
    return MAX_BODY_BYTES


def _is_exempt(path: str) -> bool:
    for prefix in _EXEMPT_PATHS:
        if path == prefix or path.startswith(prefix + '/') or path.startswith(prefix + '?'):
            return True
    return False


@web.middleware
async def body_size_limit_middleware(request: web.Request, handler):
    """Reject requests with oversized bodies before they consume memory."""
    path = request.path

    if _is_exempt(path):
        return await handler(request)

    max_bytes = _get_max_bytes(path)

    # Fast path: check Content-Length header
    content_length = request.content_length
    if content_length is not None and content_length > max_bytes:
        logger.warning(
            "Request body too large: %s %s content-length=%d max=%d",
            request.method, path, content_length, max_bytes,
        )
        return web.json_response(
            {'error': 'Request body too large', 'code': 'BODY_TOO_LARGE'},
            status=413,
        )

    # Stream-guard: if no Content-Length (chunked transfer encoding), read
    # the body with a hard limit and reject if exceeded.
    if content_length is None:
        remaining = max_bytes
        try:
            reader = request.content
            while True:
                chunk = await reader.read(min(65536, remaining + 1))
                if not chunk:
                    break
                remaining -= len(chunk)
                if remaining < 0:
                    logger.warning(
                        "Chunked request body too large: %s %s max=%d",
                        request.method, path, max_bytes,
                    )
                    return web.json_response(
                        {'error': 'Request body too large', 'code': 'BODY_TOO_LARGE'},
                        status=413,
                    )
        except Exception:
            pass

    return await handler(request)
