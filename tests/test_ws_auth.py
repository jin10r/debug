"""Tests for core/api/websocket._ws_authenticate (the /ws auth gate).

The import chain pulls core.db.dbconnect → asyncpg, which may be absent in a bare
dev box; in that case the whole module is skipped. It runs fully in CI / the
container where runtime deps are installed.
"""
import pytest

try:
    from core.api.websocket import _ws_authenticate
    from core.middlewares.auth import generate_jwt_tokens
    from core.settings import settings
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:  # asyncpg / runtime dep missing
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"websocket import chain unavailable: {_IMPORT_ERR}"
)


def _validation_on():
    return bool(getattr(settings.app, "telegram_webview_validation", True))


def test_rejects_without_credentials():
    if not _validation_on():
        pytest.skip("validation disabled (dev-bypass)")
    assert _ws_authenticate({}) is False
    assert _ws_authenticate({"type": "auth"}) is False


def test_accepts_valid_jwt():
    if not _validation_on():
        pytest.skip("validation disabled (dev-bypass)")
    access, _ = generate_jwt_tokens({"id": 7})
    assert _ws_authenticate({"token": access}) is True


def test_rejects_invalid_jwt():
    if not _validation_on():
        pytest.skip("validation disabled (dev-bypass)")
    assert _ws_authenticate({"token": "not.a.jwt"}) is False
