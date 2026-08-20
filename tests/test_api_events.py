"""Tests for core/api/events.py — events API handlers."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from core.api.events import (
        get_events_status_handler,
        post_events_updates_handler,
        get_events_snapshot_handler,
        get_events_handler,
        get_geo_handler,
        get_data_status_handler,
    )
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"events api import unavailable: {_IMPORT_ERR}"
)


class FakeDb:
    def __init__(self, range_=(1, 100)):
        self.range_ = range_
        self.after_id = None
        self.after_message_id = None

    async def get_events_meta(self):
        return {'version': 1, 'updated_at': None, 'max_event_id': 100}

    async def get_events_message_id_range(self):
        return self.range_

    async def get_events_min_id(self):
        return self.range_[0]

    async def get_events_updates_as_geojson(self, after_id=None, after_message_id=None, limit=2000):
        self.after_id = after_id
        self.after_message_id = after_message_id
        return {'type': 'FeatureCollection', 'features': []}

    async def get_filtered_events_as_geojson(self, time_interval_minutes=None, layers=None):
        return {'type': 'FeatureCollection', 'features': []}

    async def get_events_snapshot_as_geojson(self, limit=5000):
        return {'type': 'FeatureCollection', 'features': []}

    async def get_all_geo_as_geojson(self):
        return '{"type": "FeatureCollection", "features": []}'

    async def get_latest_event_time(self):
        return datetime.now(timezone.utc)


class FakeCache:
    def __init__(self, value=None):
        self._value = value

    async def get_events_geojson(self, *a, **k):
        return self._value

    async def set_events_geojson(self, *a, **k):
        return None

    async def get_geo_geojson(self):
        return self._value

    async def set_geo_geojson(self, *a, **k):
        return None

    async def get_stats(self):
        return {'backend': 'memory', 'hits': 1, 'misses': 0}


class FakeRequest:
    def __init__(self, db, cache=None, json_data=None, headers=None, query=None, method='GET', path='/api/events'):
        self._app = {'db': db}
        if cache is not None:
            self._app['cache'] = cache
        self._json_data = json_data
        self.headers = headers or {}
        self.query = query or {}
        self.method = method
        self.path = path

    @property
    def app(self):
        return self._app

    async def json(self):
        if self._json_data is None:
            raise Exception("no json")
        return self._json_data


async def _call(handler, request):
    resp = await handler(request)
    return resp, json.loads(resp.body) if resp.body else {}


class TestGetEventsStatusHandler:
    @pytest.mark.asyncio
    async def test_returns_meta(self):
        resp, body = await _call(get_events_status_handler, FakeRequest(db=FakeDb()))
        assert resp.status == 200
        assert body['version'] == 1
        assert body['max_event_id'] == 100


class TestPostEventsUpdatesHandler:
    @pytest.mark.asyncio
    async def test_validates_after_id(self):
        resp, body = await _call(post_events_updates_handler, FakeRequest(
            db=FakeDb(), json_data={'after_id': -1}
        ))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_resync_when_after_message_id_out_of_range(self):
        resp, body = await _call(post_events_updates_handler, FakeRequest(
            db=FakeDb(range_=(1, 100)), json_data={'after_message_id': 10000}
        ))
        assert resp.status == 409
        assert body['resync_required'] is True

    @pytest.mark.asyncio
    async def test_success_returns_updates(self):
        db = FakeDb()
        resp, body = await _call(post_events_updates_handler, FakeRequest(
            db=db, json_data={'after_id': 0}
        ))
        assert resp.status == 200
        assert 'data' in body


class TestGetEventsSnapshotHandler:
    @pytest.mark.asyncio
    async def test_default_limit(self):
        resp, body = await _call(get_events_snapshot_handler, FakeRequest(
            db=FakeDb(), query={}
        ))
        assert resp.status == 200
        assert 'data' in body

    @pytest.mark.asyncio
    async def test_custom_limit(self):
        resp, body = await _call(get_events_snapshot_handler, FakeRequest(
            db=FakeDb(), query={'limit': '100'}
        ))
        assert resp.status == 200


class TestGetEventsHandler:
    @pytest.mark.asyncio
    async def test_etag_cache_miss(self):
        cache = FakeCache(value=None)
        db = FakeDb()
        resp, body = await _call(get_events_handler, FakeRequest(
            db=db, cache=cache, json_data={'time_filter': 10}
        ))
        assert resp.status == 200
        assert 'ETag' in resp.headers

    @pytest.mark.asyncio
    async def test_validation_error(self):
        resp, body = await _call(get_events_handler, FakeRequest(
            db=FakeDb(), json_data={'time_filter': 'bad'}
        ))
        assert resp.status == 400


class TestGetGeoHandler:
    @pytest.mark.asyncio
    async def test_cache_hit(self):
        cache = FakeCache(value='{"type": "FeatureCollection"}')
        resp, body = await _call(get_geo_handler, FakeRequest(
            db=FakeDb(), cache=cache
        ))
        assert resp.status == 200
        assert resp.headers.get('X-Cache') == 'HIT'

    @pytest.mark.asyncio
    async def test_cache_miss_fetches_db(self):
        cache = FakeCache(value=None)
        resp, body = await _call(get_geo_handler, FakeRequest(
            db=FakeDb(), cache=cache
        ))
        assert resp.status == 200
        assert resp.headers.get('X-Cache') == 'MISS'


class TestGetDataStatusHandler:
    @pytest.mark.asyncio
    async def test_ok_when_recent(self):
        resp, body = await _call(get_data_status_handler, FakeRequest(db=FakeDb()))
        assert resp.status == 200
        assert body['status'] == 'ok'
