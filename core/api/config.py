"""Configuration API handlers"""
import json
from aiohttp import web

from core.settings import settings


async def get_config_handler(request: web.Request):
    """Return client configuration from centralized settings."""
    # Поддержка как GET, так и POST запросов
    if request.method == 'POST':
        try:
            await request.json()  # Просто проверяем, что тело - JSON
        except Exception:
            pass  # Игнорируем ошибки при чтении тела POST-запроса

    config = {
        'redirect_url': (settings.bot.redirect_url if settings and settings.bot else '') or '',
        'telegram_validation_enabled': settings.app.telegram_validation_enabled if settings and settings.app else False,
        'stopwords': list(settings.similarity.stop_words) if settings else [],
        'layer_keywords': {
            'pig': list(settings.layers.pig) if settings else [],
            'cops': list(settings.layers.cops) if settings else [],
            'bus': list(settings.layers.bus) if settings else [],
            'traffic': list(settings.layers.traffic) if settings else [],
        }
    }

    return web.json_response(config)