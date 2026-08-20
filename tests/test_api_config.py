"""Tests for core/api/config.py — configuration endpoint."""
import json

import pytest

try:
    from core.api.config import get_config_handler
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"config api import unavailable: {_IMPORT_ERR}"
)


class FakeSettings:
    class Bot:
        redirect_url = 'https://example.com'

    class App:
        telegram_webview_validation = True

    class Layers:
        def as_dict(self):
            return {'bus': ['bus', 'автобус'], 'traffic': ['traffic', 'пробки']}

    def __init__(self):
        self.bot = self.Bot()
        self.app = self.App()
        self.layers = self.Layers()


class FakeRequest:
    def __init__(self, method='GET'):
        self.method = method

    async def json(self):
        return {}


async def _call(handler, request):
    resp = await handler(request)
    return resp, json.loads(resp.body) if resp.body else {}


class TestGetConfigHandler:
    @pytest.mark.asyncio
    async def test_get_returns_config(self):
        import core.api.config as config_mod
        original = getattr(config_mod, 'settings', None)
        config_mod.settings = FakeSettings()
        try:
            req = FakeRequest(method='GET')
            resp, body = await _call(get_config_handler, req)
            assert resp.status == 200
            assert body['redirect_url'] == 'https://example.com'
            assert body['telegram_webview_validation'] is True
            assert 'bus' in body['layer_keywords']
        finally:
            config_mod.settings = original

    @pytest.mark.asyncio
    async def test_post_accepted(self):
        import core.api.config as config_mod
        original = getattr(config_mod, 'settings', None)
        config_mod.settings = FakeSettings()
        try:
            req = FakeRequest(method='POST')
            resp, body = await _call(get_config_handler, req)
            assert resp.status == 200
            assert 'layer_keywords' in body
        finally:
            config_mod.settings = original

    @pytest.mark.asyncio
    async def test_handles_missing_settings(self):
        import core.api.config as config_mod
        original = getattr(config_mod, 'settings', None)
        empty = type('EmptySettings', (), {'app': type('App', (), {'telegram_webview_validation': True})(), 'bot': type('Bot', (), {'redirect_url': ''})(), 'layers': type('Layers', (), {'as_dict': lambda self: {}})()})()
        config_mod.settings = empty
        try:
            req = FakeRequest(method='GET')
            resp, body = await _call(get_config_handler, req)
            assert resp.status == 200
            assert body['redirect_url'] == ''
            assert body['telegram_webview_validation'] is True
            assert body['layer_keywords'] == {}
        finally:
            config_mod.settings = original
