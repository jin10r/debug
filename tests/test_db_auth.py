"""Tests for core/db/db_auth.py — AuthOperations."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from core.db.db_auth import AuthOperations
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"db_auth import unavailable: {_IMPORT_ERR}"
)


# ============================================================
# Helpers
# ============================================================

def _mock_connection():
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    return conn


class FakeDb:
    def __init__(self):
        self.pool = MagicMock()
        self.pool.acquire = MagicMock(return_value=_mock_connection())
        self.pool.acquire.return_value.execute = AsyncMock()
        self.pool.acquire.return_value.fetchval = AsyncMock(return_value=None)
        self.execute = AsyncMock()
        self.fetchval = AsyncMock(return_value=None)


# ============================================================
# AuthOperations
# ============================================================

class TestAuthOperations:
    @pytest.fixture
    def ops(self):
        return AuthOperations(db=FakeDb())

    @pytest.mark.asyncio
    async def test_store_refresh_token_inserts_with_on_conflict(self, ops):
        ops.db.execute = AsyncMock(return_value="INSERT 0 1")
        expires_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await ops.store_refresh_token(jti="token123", user_id="42", expires_at=expires_at)
        args = ops.db.execute.call_args[0]
        assert "INSERT INTO refresh_tokens" in args[0]
        assert "ON CONFLICT (jti) DO NOTHING" in args[0]
        assert args[1] == "token123"
        assert args[2] == "42"
        assert args[3] == expires_at

    @pytest.mark.asyncio
    async def test_store_refresh_token_idempotent_on_conflict(self, ops):
        ops.db.execute = AsyncMock(return_value="INSERT 0 0")
        expires_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await ops.store_refresh_token(jti="token123", user_id="42", expires_at=expires_at)
        args = ops.db.execute.call_args[0]
        assert "ON CONFLICT (jti) DO NOTHING" in args[0]

    @pytest.mark.asyncio
    async def test_consume_refresh_token_returns_true_when_fresh(self, ops):
        ops.db.fetchval = AsyncMock(return_value="token123")
        result = await ops.consume_refresh_token("token123")
        assert result is True
        args = ops.db.fetchval.call_args[0]
        assert "UPDATE refresh_tokens" in args[0]
        assert "SET used_at = now()" in args[0]

    @pytest.mark.asyncio
    async def test_consume_refresh_token_returns_false_when_used(self, ops):
        ops.db.fetchval = AsyncMock(return_value=None)
        result = await ops.consume_refresh_token("token123")
        assert result is False

    @pytest.mark.asyncio
    async def test_consume_refresh_token_returns_false_when_revoked(self, ops):
        ops.db.fetchval = AsyncMock(return_value=None)
        result = await ops.consume_refresh_token("revoked_token")
        assert result is False

    @pytest.mark.asyncio
    async def test_consume_refresh_token_returns_false_when_expired(self, ops):
        ops.db.fetchval = AsyncMock(return_value=None)
        result = await ops.consume_refresh_token("expired_token")
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_all_user_tokens_returns_count(self, ops):
        ops.db.execute = AsyncMock(return_value="UPDATE 3")
        result = await ops.revoke_all_user_tokens("42")
        assert result == 3
        args = ops.db.execute.call_args[0]
        assert "UPDATE refresh_tokens" in args[0]
        assert "SET revoked = TRUE" in args[0]

    @pytest.mark.asyncio
    async def test_revoke_all_user_tokens_returns_zero_on_no_match(self, ops):
        ops.db.execute = AsyncMock(return_value="UPDATE 0")
        result = await ops.revoke_all_user_tokens("42")
        assert result == 0

    @pytest.mark.asyncio
    async def test_revoke_all_user_tokens_handles_invalid_result(self, ops):
        ops.db.execute = AsyncMock(return_value="UPDATE abc")
        result = await ops.revoke_all_user_tokens("42")
        assert result == 0

    @pytest.mark.asyncio
    async def test_cleanup_expired_tokens_calls_procedure(self, ops):
        ops.db.execute = AsyncMock(return_value="SELECT 1")
        result = await ops.cleanup_expired_tokens()
        assert result == 0
        args = ops.db.execute.call_args[0]
        assert "cleanup_expired_refresh_tokens()" in args[0]
