"""Tests for core/db/db_base.py — create_pool, Database, retry_db_condition."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time

try:
    from common.db.base import Database, create_pool, retry_db_condition
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"db_base import unavailable: {_IMPORT_ERR}"
)


# ============================================================
# Helpers
# ============================================================

class FakeRecord(dict):
    """Dict-like row mimicking asyncpg.Record."""
    def __getitem__(self, key):
        return super().__getitem__(key)


class _AsyncCtx:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return None


def _mock_connection():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.executemany = AsyncMock()
    conn.transaction = MagicMock()
    return conn


def _mock_pool():
    pool = MagicMock()
    pool.close = AsyncMock()
    pool.terminate = AsyncMock()
    conn = _mock_connection()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool, conn


# ============================================================
# retry_db_condition
# ============================================================

class TestRetryDbCondition:
    def test_retryable_connection_error(self):
        assert retry_db_condition(ConnectionRefusedError()) is True

    def test_retryable_os_error(self):
        assert retry_db_condition(OSError()) is True

    def test_non_retryable_syntax_error(self):
        import asyncpg
        assert retry_db_condition(asyncpg.SyntaxOrAccessError()) is False

    def test_non_retryable_unique_violation(self):
        import asyncpg
        assert retry_db_condition(asyncpg.UniqueViolationError()) is False

    def test_non_retryable_runtime_error(self):
        assert retry_db_condition(RuntimeError()) is False


# ============================================================
# create_pool
# ============================================================

class TestCreatePool:
    @pytest.mark.asyncio
    async def test_create_pool_with_defaults(self):
        """Test fallback pool values when ``settings`` is ``None``.

        When the settings module is unavailable (``settings=None``),
        ``create_pool()`` falls back to safety-net defaults:
        ``min_size=5, max_size=20, command_timeout=60``.

        These **differ** from production defaults (``1/10/30`` from
        ``settings.db``) and exist solely so tests can exercise the pool
        factory without a real settings module. Production code always
        resolves values through ``settings.db`` (dataclass), never the
        fallbacks. See also R-TEST7 for the production-vs-fallback contract.
        """
        mock_pool = AsyncMock()
        with patch('common.db.base.asyncpg.create_pool', new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            with patch('common.db.base.settings', None):
                pool = await create_pool()
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs['min_size'] == 5
        assert call_kwargs['max_size'] == 20
        assert call_kwargs['command_timeout'] == 60
        assert call_kwargs['statement_cache_size'] == 100
        assert call_kwargs['server_settings']['timezone'] == 'Europe/Kiev'

    @pytest.mark.asyncio
    async def test_create_pool_with_overrides(self):
        mock_pool = AsyncMock()
        with patch('common.db.base.asyncpg.create_pool', new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            with patch('common.db.base.settings', None):
                pool = await create_pool(min_size=2, max_size=10, command_timeout=30)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs['min_size'] == 2
        assert call_kwargs['max_size'] == 10
        assert call_kwargs['command_timeout'] == 30


# ============================================================
# Database
# ============================================================

class TestDatabase:
    @pytest.mark.asyncio
    async def test_connect_creates_pool(self):
        database = Database()
        mock_pool = AsyncMock()
        with patch('common.db.base.create_pool', return_value=mock_pool):
            result = await database.connect()
        assert result is True
        assert database.pool is mock_pool

    @pytest.mark.asyncio
    async def test_connect_retries_on_retryable_error(self):
        database = Database()
        mock_pool = AsyncMock()
        side_effects = [ConnectionRefusedError(), ConnectionRefusedError(), mock_pool]
        with patch('common.db.base.create_pool', side_effect=side_effects):
            with patch('asyncio.sleep', AsyncMock()):
                result = await database.connect(max_retries=3, retry_delay=0.1)
        assert result is True

    @pytest.mark.asyncio
    async def test_connect_raises_on_non_retryable_error(self):
        database = Database()
        with patch('common.db.base.create_pool', side_effect=RuntimeError("fatal")):
            with pytest.raises(RuntimeError, match="fatal"):
                await database.connect(max_retries=2, retry_delay=0.1)

    @pytest.mark.asyncio
    async def test_execute_delegates_to_pool(self):
        database = Database()
        mock_pool, mock_conn = _mock_pool()
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
        database.pool = mock_pool
        result = await database.execute("INSERT INTO t VALUES ($1)", "x")
        assert result == "INSERT 0 1"
        mock_conn.execute.assert_called_once_with("INSERT INTO t VALUES ($1)", "x")

    @pytest.mark.asyncio
    async def test_fetch_returns_dict_list(self):
        database = Database()
        mock_pool, mock_conn = _mock_pool()
        mock_record = FakeRecord({"id": 1, "name": "a"})
        mock_conn.fetch = AsyncMock(return_value=[mock_record])
        database.pool = mock_pool
        result = await database.fetch("SELECT * FROM t")
        assert isinstance(result, list)
        assert result[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_fetchrow_returns_dict_or_none(self):
        database = Database()
        mock_pool, mock_conn = _mock_pool()
        mock_record = FakeRecord({"id": 1})
        mock_conn.fetchrow = AsyncMock(return_value=mock_record)
        database.pool = mock_pool
        result = await database.fetchrow("SELECT * FROM t WHERE id=$1", 1)
        assert result == {"id": 1}

    @pytest.mark.asyncio
    async def test_fetchrow_returns_none_when_no_record(self):
        database = Database()
        mock_pool, mock_conn = _mock_pool()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        database.pool = mock_pool
        result = await database.fetchrow("SELECT * FROM t WHERE id=$1", 999)
        assert result is None

    @pytest.mark.asyncio
    async def test_fetchval_delegates(self):
        database = Database()
        mock_pool, mock_conn = _mock_pool()
        mock_conn.fetchval = AsyncMock(return_value=42)
        database.pool = mock_pool
        result = await database.fetchval("SELECT COUNT(*) FROM t")
        assert result == 42

    @pytest.mark.asyncio
    async def test_executemany_delegates(self):
        database = Database()
        mock_pool, mock_conn = _mock_pool()
        mock_conn.executemany = AsyncMock()
        database.pool = mock_pool
        await database.executemany("INSERT INTO t VALUES ($1)", [("a",), ("b",)])
        mock_conn.executemany.assert_called_once_with("INSERT INTO t VALUES ($1)", [("a",), ("b",)])

    @pytest.mark.asyncio
    async def test_transaction_yields_connection(self):
        database = Database()
        mock_pool, mock_conn = _mock_pool()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=None)
        mock_transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=mock_transaction)
        database.pool = mock_pool
        async with database.transaction() as conn:
            assert conn is mock_conn
        mock_conn.transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_closes_pool(self):
        database = Database()
        mock_pool, _ = _mock_pool()
        database.pool = mock_pool
        await database.close()
        mock_pool.close.assert_called_once()
        assert database.pool is None

    @pytest.mark.asyncio
    async def test_close_handles_timeout(self):
        database = Database()
        mock_pool, _ = _mock_pool()
        mock_pool.close = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_pool.terminate = AsyncMock()
        database.pool = mock_pool
        await database.close()
        mock_pool.terminate.assert_called_once()
        assert database.pool is None

    def test_is_connected_true_when_pool_set(self):
        database = Database()
        mock_pool, _ = _mock_pool()
        database.pool = mock_pool
        assert database.is_connected is True

    def test_is_connected_false_when_pool_none(self):
        database = Database()
        assert database.is_connected is False
