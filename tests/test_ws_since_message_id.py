"""Tests for WS catch-up watermark by message_id (core/api/websocket.py).

send_events_since:
  - since_message_id в диапазоне БД → обычный catch-up (after_message_id передан);
  - since_message_id вне диапазона (> MAX или < MIN-1) → resync_required
    ПЕРВЫМ сообщением + полный snapshot без водяного знака;
  - since_id остаётся fallback'ом, когда since_message_id не передан.
"""
import json

import pytest

try:
    from core.api.websocket import WebSocketManager
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:  # asyncpg / runtime dep missing
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"websocket import chain unavailable: {_IMPORT_ERR}"
)


def _feature(mid):
    return {
        'type': 'Feature',
        'geometry': None,
        'properties': {'id': mid, 'message_id': mid, 'description': f'event {mid}'},
    }


class FakeDb:
    """Минимальный db_request-фасад: range + get_filtered_events_as_geojson."""

    def __init__(self, range_=(1, 100)):
        self.range_ = range_
        self.calls = []

    async def get_events_message_id_range(self):
        return self.range_

    async def get_filtered_events_as_geojson(self, **kwargs):
        self.calls.append(kwargs)
        return {'type': 'FeatureCollection', 'features': [_feature(1), _feature(2)]}


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send_str(self, payload: str):
        self.sent.append(json.loads(payload))


def _manager(db=None):
    return WebSocketManager(db_request=db or FakeDb())


async def _sent_types(features: list) -> list:
    return [f['type'] for f in features]


async def test_catch_up_in_range_uses_after_message_id():
    db = FakeDb(range_=(1, 100))
    ws = FakeWs()
    await _manager(db).send_events_since(ws, since_message_id=42)

    assert await _sent_types(ws.sent) == ['feature', 'feature', 'events_snapshot_end']
    call = db.calls[0]
    assert call['after_message_id'] == 42
    assert call['after_id'] is None
    assert call['since_timestamp'] is None


async def test_watermark_above_db_max_triggers_resync_first():
    db = FakeDb(range_=(1, 100))
    ws = FakeWs()
    await _manager(db).send_events_since(ws, since_message_id=10_000)

    types = await _sent_types(ws.sent)
    assert types[0] == 'resync_required'
    assert types[-1] == 'events_snapshot_end'
    # полный snapshot: водяной знак сброшен в None
    call = db.calls[0]
    assert call['after_message_id'] is None
    assert call['after_id'] is None
    assert call['since_timestamp'] is None


async def test_watermark_below_db_min_minus_one_triggers_resync():
    db = FakeDb(range_=(50, 100))
    ws = FakeWs()
    await _manager(db).send_events_since(ws, since_message_id=10)

    types = await _sent_types(ws.sent)
    assert types[0] == 'resync_required'
    assert db.calls[0]['after_message_id'] is None


async def test_watermark_at_min_minus_one_is_fine():
    """since_message_id = min-1 — клиент ничего не имеет, обычный catch-up."""
    db = FakeDb(range_=(50, 100))
    ws = FakeWs()
    await _manager(db).send_events_since(ws, since_message_id=49)

    assert ws.sent[0]['type'] == 'feature'
    assert db.calls[0]['after_message_id'] == 49


async def test_since_id_fallback_when_no_message_id():
    db = FakeDb(range_=(1, 100))
    ws = FakeWs()
    await _manager(db).send_events_since(ws, since_id=77)

    assert ws.sent[0]['type'] == 'feature'
    call = db.calls[0]
    assert call['after_id'] == 77
    assert call['after_message_id'] is None


async def test_initial_load_without_watermark_is_full_snapshot():
    db = FakeDb(range_=(1, 100))
    ws = FakeWs()
    await _manager(db).send_events_since(ws, since_timestamp=None)

    call = db.calls[0]
    assert call['after_message_id'] is None
    assert call['after_id'] is None
    assert call['since_timestamp'] is None