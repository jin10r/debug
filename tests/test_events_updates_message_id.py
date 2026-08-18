"""Tests for REST /api/events (POST) catch-up watermark by after_message_id.

post_events_updates_handler в core/api/events.py:
  - after_message_id вне диапазона БД → 409 resync_required (аналог WS);
  - after_message_id в диапазоне → обычный ответ, watermark передаётся в БД;
  - after_id остаётся fallback'ом для старых клиентов;
  - невалидный after_message_id → 400.

Handler напрямую: он использует только request.app['db'] и request.json(),
поэтому фейковый request — простой duck-type объект без реального HTTP.
"""
import json

import pytest

try:
    from core.api.events import post_events_updates_handler
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:  # runtime dep missing
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"events api import chain unavailable: {_IMPORT_ERR}"
)


class FakeDb:
    def __init__(self, range_=(1, 100)):
        self.range_ = range_
        self.after_id = None
        self.after_message_id = None

    async def get_events_message_id_range(self):
        return self.range_

    async def get_events_min_id(self):
        return self.range_[0]

    async def get_events_meta(self):
        return {'version': 1, 'updated_at': None, 'max_event_id': 100}

    async def get_events_updates_as_geojson(self, after_id=None, after_message_id=None, limit=2000):
        self.after_id = after_id
        self.after_message_id = after_message_id
        return {'type': 'FeatureCollection', 'features': []}


class _FakeRequest:
    def __init__(self, db, payload: dict):
        self._app = {'db': db}
        self._data = payload

    @property
    def app(self):
        return self._app

    async def json(self):
        return self._data


async def _call(db: FakeDb, payload: dict):
    return await post_events_updates_handler(_FakeRequest(db, payload))


async def test_after_message_id_out_of_db_range_returns_409():
    resp = await _call(FakeDb(range_=(1, 100)), {'after_message_id': 10_000})

    assert resp.status == 409
    body = json.loads(resp.body)
    assert body['resync_required'] is True


async def test_after_message_id_below_range_returns_409():
    resp = await _call(FakeDb(range_=(50, 100)), {'after_message_id': 10})

    assert resp.status == 409


async def test_after_message_id_at_range_edge_is_ok():
    # min-1 — клиент ничего не имеет, полный набор доступен.
    resp = await _call(FakeDb(range_=(50, 100)), {'after_message_id': 49})

    assert resp.status == 200


async def test_after_message_id_in_range_returns_events():
    db = FakeDb(range_=(1, 100))
    resp = await _call(db, {'after_message_id': 42})

    assert resp.status == 200
    body = json.loads(resp.body)
    assert body['data']['features'] == []
    assert db.after_message_id == 42
    assert db.after_id is None


async def test_after_id_fallback_still_works():
    db = FakeDb(range_=(1, 100))
    resp = await _call(db, {'after_id': 5})

    assert resp.status == 200
    assert db.after_id == 5
    assert db.after_message_id is None


async def test_invalid_after_message_id_returns_400():
    db = FakeDb(range_=(1, 100))
    resp = await _call(db, {'after_message_id': 'abc'})

    assert resp.status == 400