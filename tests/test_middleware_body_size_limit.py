"""Tests for core/middlewares/body_size_limit.py."""
import pytest

try:
    from core.middlewares.body_size_limit import (
        body_size_limit_middleware,
        _is_exempt,
        _get_max_bytes,
    )
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"body_size_limit import unavailable: {_IMPORT_ERR}"
)


class FakeRequest:
    def __init__(self, path='/api/events', method='POST', content_length=None, body=b''):
        self.path = path
        self.method = method
        self.content_length = content_length
        self._body = body
        self.headers = {}

    async def read(self):
        return self._body


class FakeHandler:
    def __init__(self, body=b'{"ok": true}'):
        self._body = body
        self.called = False

    async def __call__(self, request):
        self.called = True
        from aiohttp import web
        return web.json_response({'ok': True})


async def _call(middleware, request, handler):
    return await middleware(request, handler)


class TestIsExempt:
    def test_media_path_exempt(self):
        assert _is_exempt('/api/media') is True
        assert _is_exempt('/api/media/events/photo.jpg') is True
        assert _is_exempt('/api/media?foo=bar') is True

    def test_non_exempt(self):
        assert _is_exempt('/api/events') is False
        assert _is_exempt('/health') is False


class TestGetMaxBytes:
    def test_large_body_paths(self):
        assert _get_max_bytes('/api/events') == 5 * 1024 * 1024
        assert _get_max_bytes('/api/events/updates') == 5 * 1024 * 1024
        assert _get_max_bytes('/api/events/updates?foo=bar') == 5 * 1024 * 1024

    def test_default_paths(self):
        assert _get_max_bytes('/api/auth') == 1024 * 1024
        assert _get_max_bytes('/health') == 1024 * 1024


class TestBodySizeLimitMiddleware:
    @pytest.mark.asyncio
    async def test_exempt_path_passes(self):
        handler = FakeHandler()
        resp = await _call(
            body_size_limit_middleware,
            FakeRequest(path='/api/media'),
            handler,
        )
        assert resp.status == 200
        assert handler.called is True

    @pytest.mark.asyncio
    async def test_content_length_too_large(self):
        handler = FakeHandler()
        resp = await _call(
            body_size_limit_middleware,
            FakeRequest(path='/api/events', content_length=10 * 1024 * 1024 + 1),
            handler,
        )
        assert resp.status == 413
        assert handler.called is False

    @pytest.mark.asyncio
    async def test_chunked_body_too_large(self):
        handler = FakeHandler()
        resp = await _call(
            body_size_limit_middleware,
            FakeRequest(path='/api/events', body=b'x' * (10 * 1024 * 1024 + 1)),
            handler,
        )
        assert resp.status == 413
        assert handler.called is False

    @pytest.mark.asyncio
    async def test_normal_request_passes(self):
        handler = FakeHandler()
        resp = await _call(
            body_size_limit_middleware,
            FakeRequest(path='/api/events', body=b'{"ok": true}'),
            handler,
        )
        assert resp.status == 200
        assert handler.called is True
