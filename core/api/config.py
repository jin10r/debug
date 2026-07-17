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

    layer_keywords = {
        layer: list(keywords)
        for layer, keywords in (settings.layers.as_dict().items() if settings else ())
    }

    qo = settings.question_overlay if settings else None

    config = {
        'redirect_url': (settings.bot.redirect_url if settings and settings.bot else '') or '',
        'telegram_validation_enabled': settings.app.telegram_validation_enabled if settings and settings.app else False,
        'layer_keywords': layer_keywords,
        'map_center_lat': qo.center_lat if qo else 46.48,
        'map_center_lng': qo.center_lon if qo else 30.73,
        'map_default_zoom': 12,
        'enable_random_points': True,
    }

    return web.json_response(config)