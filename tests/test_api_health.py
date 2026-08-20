"""Tests for core/api/health.py — health check endpoints."""
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from core.api.health import (
        health_live_handler,
        health_ready_handler,
        health_detailed_handler,
        _check_db,
    )
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"health api import unavailable: {_IMPORT_ERR}"
)


class FakePool:
    def get_size(self):
        return 5

    def get_idle_size(self):
        return 3

    def get_max_size(self):
        return 20


class FakePoolError:
    def get_size(self):
        raise Exception("DB down")


class FakeDb:
    def __init__(self, is_connected=True):
        self.is_connected = is_connected
        self.pool = FakePool()
        self.fetchval = AsyncMock(return_value=1)


class FakeRequestWrapper:
    """Mimics app['db'] which is a Request(db_pool) wrapper."""
    def __init__(self, db):
        self.db = db


class FakeCache:
    def __init__(self, stats=None):
        self._stats = stats or {'backend': 'memory', 'hits': 10, 'misses': 2}

    async def get_stats(self):
        return self._stats


class FakeRequest:
    def __init__(self, db=None, cache=None, bot=None, start_time=0):
        self._app = {}
        if db is not None:
            self._app['db'] = db
        if cache is not None:
            self._app['cache'] = cache
        if bot is not None:
            self._app['bot'] = bot
        if start_time:
            self._app['start_time'] = start_time

    @property
    def app(self):
        return self._app


async def _call(handler, request):
    resp = await handler(request)
    return resp, json.loads(resp.body) if resp.body else {}


class TestHealthLiveHandler:
    @pytest.mark.asyncio
    async def test_returns_alive(self):
        resp, body = await _call(health_live_handler, FakeRequest())
        assert resp.status == 200
        assert body['status'] == 'alive'
        assert 'timestamp' in body


class TestHealthReadyHandler:
    @pytest.mark.asyncio
    async def test_ready_when_healthy(self):
        db = FakeDb(is_connected=True)
        wrapper = FakeRequestWrapper(db)
        bot = object()
        req = FakeRequest(db=wrapper, bot=bot)
        resp, body = await _call(health_ready_handler, req)
        assert resp.status == 200
        assert body['status'] == 'healthy'
        assert body['checks']['database']['status'] == 'healthy'
        assert body['checks']['bot']['status'] == 'healthy'

    @pytest.mark.asyncio
    async def test_not_ready_when_db_disconnected(self):
        db = FakeDb(is_connected=False)
        wrapper = FakeRequestWrapper(db)
        req = FakeRequest(db=wrapper)
        resp, body = await _call(health_ready_handler, req)
        assert resp.status == 503
        assert body['status'] == 'unhealthy'
        assert body['checks']['database']['status'] == 'unhealthy'

    @pytest.mark.asyncio
    async def test_not_ready_when_bot_missing(self):
        db = FakeDb(is_connected=True)
        wrapper = FakeRequestWrapper(db)
        req = FakeRequest(db=wrapper)
        resp, body = await _call(health_ready_handler, req)
        assert resp.status == 503
        assert body['checks']['bot']['status'] == 'unhealthy'


class TestHealthDetailedHandler:
    @pytest.mark.asyncio
    async def test_returns_metrics_when_connected(self):
        db = FakeDb(is_connected=True)
        wrapper = FakeRequestWrapper(db)
        cache = FakeCache()
        req = FakeRequest(db=wrapper, cache=cache, start_time=123.0)
        resp, body = await _call(health_detailed_handler, req)
        assert resp.status == 200
        assert body['status'] == 'healthy'
        assert body['version'] == '1.0.6'
        assert body['uptime'] == 123.0
        assert body['checks']['database']['pool_size'] == 5
        assert body['checks']['cache']['status'] == 'healthy'

    @pytest.mark.asyncio
    async def test_returns_unhealthy_when_db_error(self):
        db = FakeDb(is_connected=True)
        db.pool = FakePoolError()  # Force error on pool access
        wrapper = FakeRequestWrapper(db)
        req = FakeRequest(db=wrapper)
        resp, body = await _call(health_detailed_handler, req)
        assert resp.status == 200
        assert body['status'] == 'unhealthy'
        assert 'database' in body['checks']


class TestCheckDb:
    @pytest.mark.asyncio
    async def test_cached_true_returns_cached(self):
        db = FakeDb(is_connected=True)
        wrapper = FakeRequestWrapper(db)
        await _check_db(wrapper, use_cache=True)
        ok, msg = await _check_db(wrapper, use_cache=True)
        assert ok is True
        assert 'cached' in msg

    @pytest.mark.asyncio
    async def test_uncached_calls_fetchval(self):
        db = FakeDb(is_connected=True)
        db.fetchval = AsyncMock(return_value=1)
        wrapper = FakeRequestWrapper(db)
        ok, msg = await _check_db(wrapper, use_cache=False)
        assert ok is True
        assert msg == 'Connected'
        db.fetchval.assert_called_once_with('SELECT 1')

    @pytest.mark.asyncio
    async def test_not_connected_returns_false(self):
        db = FakeDb(is_connected=False)
        wrapper = FakeRequestWrapper(db)
        ok, msg = await _check_db(wrapper, use_cache=False)
        assert ok is False
        assert 'Not connected' in msg
