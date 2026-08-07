"""Regression tests for the /ws WebSocketResponse construction.

The handler (core/api/websocket.py) builds `web.WebSocketResponse(...)` with
`max_msg_size`. aiohttp 3.10 removed the old `max_ws_bytes` kwarg (renamed to
`max_msg_size` in 3.9); on aiohttp 3.14 the old kwarg raises TypeError and
breaks every /ws connection (см. ошибку в логах core 2026-08-07). Эти тесты
фиксируют контракт, чтобы удалённый kwarg не вернулся в код.
"""
from pathlib import Path

import pytest

try:
    from aiohttp import web
    _AIOHTTP_OK = True
    _AIOHTTP_ERR = None
except Exception as e:  # aiohttp absent in a bare dev box
    _AIOHTTP_OK = False
    _AIOHTTP_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _AIOHTTP_OK, reason=f"aiohttp unavailable: {_AIOHTTP_ERR}"
)

# Must mirror the kwargs used in core/api/websocket.py websocket_handler.
_HEARTBEAT = 120
_WS_MAX_MSG_BYTES = 65536

_SRC = Path(__file__).resolve().parent.parent / "core/api/websocket.py"


def test_handler_does_not_use_removed_kwarg():
    """The handler must use max_msg_size=, never the kwarg form max_ws_bytes=
    (removed in aiohttp 3.10). Comments may mention the old name."""
    src = _SRC.read_text(encoding="utf-8")
    assert "max_ws_bytes=" not in src
    assert "max_msg_size=" in src


def test_websocket_response_accepts_handler_kwargs():
    """The exact kwargs used by websocket_handler must construct without error."""
    ws = web.WebSocketResponse(heartbeat=_HEARTBEAT, max_msg_size=_WS_MAX_MSG_BYTES)
    assert ws is not None


def test_websocket_response_size_limit_is_applied():
    """Verify the size limit is actually wired through, not silently ignored."""
    ws = web.WebSocketResponse(heartbeat=_HEARTBEAT, max_msg_size=_WS_MAX_MSG_BYTES)
    assert ws._max_msg_size == _WS_MAX_MSG_BYTES
