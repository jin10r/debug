"""
JWT Authentication Middleware

Protects ALL API endpoints by requiring valid JWT tokens.
Only health checks are public.
"""
import logging
from aiohttp import web
from typing import Set

from core.settings import settings
from core.middlewares.auth import verify_jwt_token

logger = logging.getLogger(__name__)


# Endpoints that don't require authentication (health checks + auth endpoints)
PUBLIC_ENDPOINTS: Set[str] = {
    '/health',
    '/health/live',
    '/health/ready',
    '/health/detailed',
    '/api/validation-config',  # Needed to determine if validation is enabled
    '/api/validate-init',      # Used to get JWT tokens (has its own validation)
    '/api/auth/refresh',       # Used to refresh JWT tokens
}


async def jwt_auth_middleware(app: web.Application, handler):
    """
    Middleware that checks JWT authentication for ALL endpoints.
    
    If TELEGRAM_VALIDATION_ENABLED=false, all requests are allowed (dev mode).
    If TELEGRAM_VALIDATION_ENABLED=true, ALL endpoints require valid JWT.
    """
    async def middleware_handler(request: web.Request) -> web.Response:
        # Skip authentication if validation is disabled (development mode)
        validation_enabled = getattr(settings.app, 'telegram_validation_enabled', True)
        logger.debug(f"[JWT] {request.method} {request.path} validation={validation_enabled}")
        
        if not validation_enabled:
            # No authentication, no user data
            return await handler(request)
        
        # Check if endpoint is public (health checks only).
        # request.path в aiohttp уже без query-string, но нормализуем trailing
        # slash чтобы и /health, и /health/ покрывались PUBLIC_ENDPOINTS.
        path = request.path.rstrip('/') or '/'
        if path in PUBLIC_ENDPOINTS:
            return await handler(request)

        # For WebSocket, skip JWT check here (handled in websocket handler).
        # Поддерживаем варианты /ws и /ws/ — оба после rstrip дают /ws.
        if path == '/ws':
            return await handler(request)
        
        # Try to get token from Authorization header
        auth_header = request.headers.get('Authorization', '')
        token = None
        
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
        
        # Try to get from session cookie
        if not token:
            token = request.cookies.get('session_token')
        
        # No token provided
        if not token:
            logger.warning(f"Unauthorized access attempt to {path}")
            return web.json_response(
                {'error': 'Authentication required', 'code': 'UNAUTHORIZED'},
                status=401
            )
        
        # Verify token
        payload = verify_jwt_token(token, 'access')
        if not payload:
            logger.warning(f"Invalid/expired token for {path}")
            return web.json_response(
                {'error': 'Invalid or expired token', 'code': 'TOKEN_INVALID'},
                status=401
            )
        
        # Attach user data to request
        request['telegram_user'] = {
            'id': int(payload['sub']),
            'first_name': payload.get('first_name', ''),
            'username': payload.get('username', ''),
            'is_premium': payload.get('is_premium', False)
        }
        
        return await handler(request)
    
    return middleware_handler
