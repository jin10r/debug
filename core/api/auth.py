"""Authentication API endpoints with JWT support"""
import logging
from aiohttp import web
from core.settings import settings
from core.middlewares.auth import generate_jwt_tokens, verify_jwt_token
from core.utils.telegram_validation import validate_telegram_webapp_data

logger = logging.getLogger(__name__)


async def get_validation_config_handler(request: web.Request) -> web.Response:
    """
    GET /api/validation-config

    Returns Telegram validation configuration.
    Frontend calls this BEFORE loading map to know access rules.

    Response: {
        "telegram_validation_enabled": true/false,
        "redirect_url": "..."
    }

    Security:
    - telegram_validation_enabled=true: Only Telegram WebView allowed
    - telegram_validation_enabled=false: Any webview allowed (dev mode)
    """
    validation_enabled = getattr(settings.app, 'telegram_validation_enabled', True)
    redirect_url = getattr(settings.bot, 'redirect_url', None)
    
    # Ensure redirect_url is set when validation is enabled
    if validation_enabled and not redirect_url:
        # Default fallback - should be configured in production
        redirect_url = 'https://t.me/your_bot'
        logger.warning(
            "[Config] REDIRECT_URL not set but validation is enabled. "
            "Set REDIRECT_URL in .env for production."
        )
    
    response_data = {
        'telegram_validation_enabled': validation_enabled,
        'redirect_url': redirect_url
    }
    
    logger.debug(f"[Config] Returning validation config: {response_data}")
    
    return web.json_response(response_data)


async def validate_init_handler(request: web.Request) -> web.Response:
    """
    Validate Telegram WebApp initData and issue JWT tokens.

    POST /api/validate-init
    Body: {"init_data": "..."}

    Response: {
        "valid": true/false,
        "user": {...},
        "access_token": "...",
        "refresh_token": "...",
        "expires_in": 900
    }
    """
    try:
        data = await request.json()
        init_data = data.get('init_data')
        
        # Simplified validation - always accept in current implementation
        if not settings.app.telegram_validation_enabled:
            # Development mode - accept any request
            user_data = {
                'id': '123456789',
                'first_name': 'Dev',
                'username': 'dev_user'
            }
        else:
            # Production mode - validate Telegram initData
            is_valid, user_data = validate_telegram_webapp_data(
                init_data,
                settings.bot.token
            )
            
            if not is_valid:
                return web.json_response(
                    {'valid': False, 'error': 'Invalid init data'},
                    status=401
                )

        # Generate tokens
        access_token, refresh_token = generate_jwt_tokens(user_data)
        
        return web.json_response({
            'valid': True,
            'user': user_data,
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_in': settings.jwt.access_token_ttl
        })
        
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return web.json_response(
            {'valid': False, 'error': 'Server error'},
            status=500
        )


async def refresh_token_handler(request: web.Request) -> web.Response:
    """
    Refresh access token using refresh token.

    POST /api/auth/refresh
    Body: {"refresh_token": "..."}

    Response: {
        "access_token": "...",
        "expires_in": 900
    }
    """
    try:
        data = await request.json()
        refresh_token = data.get('refresh_token')
        
        if not refresh_token:
            return web.json_response(
                {'error': 'Missing refresh_token'},
                status=400
            )

        # Verify refresh token
        payload = verify_jwt_token(refresh_token, 'refresh')
        if not payload:
            return web.json_response(
                {'error': 'Invalid refresh token'},
                status=401
            )

        # Generate new access token
        import time
        now = int(time.time())
        access_payload = {
            'sub': payload['sub'],
            'iat': now,
            'exp': now + settings.jwt.access_token_ttl,
            'type': 'access'
        }
        
        import jwt
        new_access_token = jwt.encode(
            access_payload,
            settings.jwt.secret,
            algorithm=settings.jwt.algorithm
        )

        return web.json_response({
            'access_token': new_access_token,
            'expires_in': settings.jwt.access_token_ttl
        })
        
    except Exception as e:
        logger.error(f"Token refresh error: {e}")
        return web.json_response(
            {'error': 'Server error'},
            status=500
        )


async def init_cache(app: web.Application):
    """Initialize in-memory cache on app startup"""
    cache = app.get('cache')
    if cache:
        await cache.connect()


async def close_cache(app: web.Application):
    """Close in-memory cache on app shutdown"""
    cache = app.get('cache')
    if cache:
        await cache.close()
