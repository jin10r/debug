"""Authentication API endpoints with JWT support"""
import logging
from aiohttp import web
from core.settings import settings
from core.middlewares.auth import (
    validate_init_data_endpoint,
    refresh_token_endpoint,
    RedisManager
)

logger = logging.getLogger(__name__)


async def get_validation_config_handler(request: web.Request) -> web.Response:
    """
    GET/POST /api/validation-config

    Returns Telegram validation configuration.
    Frontend calls this BEFORE loading validator.js to know if validation is enabled.

    Response: {
        "telegram_validation_enabled": true/false,
        "redirect_url": "..."
    }
    """
    # Get redirect_url - only use fallback if it's truly None or empty string
    # Empty string means no redirect configured, not "use default"
    redirect_url = getattr(settings.bot, 'redirect_url', None)
    # Only use fallback if redirect_url is None or empty string
    # But we want to return None/empty to frontend, not a fallback
    # Frontend will use its own fallback if needed
    if redirect_url is None or redirect_url == '':
        redirect_url = None

    return web.json_response({
        'telegram_validation_enabled': getattr(settings.app, 'telegram_validation_enabled', True),
        'redirect_url': redirect_url
    })


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
    return await validate_init_data_endpoint(request)


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
    return await refresh_token_endpoint(request)


async def init_redis(app: web.Application):
    """Initialize Redis connection on app startup"""
    await RedisManager().connect()


async def close_redis(app: web.Application):
    """Close Redis connection on app shutdown"""
    await RedisManager().disconnect()
