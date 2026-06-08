"""
Base database connection handler with connection pooling.
Separates low-level database operations from business logic.
"""

import asyncio
import asyncpg
import logging
from typing import List, Dict, Any, Optional

try:
    from core.settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)


# Исключения при которых стоит делать retry
RETRYABLE_EXCEPTIONS = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
    asyncpg.ConnectionRejectionError,
    OSError,  # Network issues
    ConnectionError,
)

# Исключения при которых НЕ стоит делать retry
NON_RETRYABLE_EXCEPTIONS = (
    asyncpg.SyntaxOrAccessError,
    asyncpg.InvalidColumnReferenceError,
    asyncpg.ForeignKeyViolationError,
    asyncpg.UniqueViolationError,
    RuntimeError,  # Application errors
)


def retry_db_condition(exception):
    """Определяет стоит ли делать retry для исключения."""
    if isinstance(exception, NON_RETRYABLE_EXCEPTIONS):
        return False
    return isinstance(exception, RETRYABLE_EXCEPTIONS)


class Database:
    """Low-level database connection handler with connection pooling."""

    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self, max_retries: int = 10, retry_delay: float = 2.0, **kwargs) -> bool:
        """Create connection pool with manual retry logic."""
        # Serve all event timestamps in Kiev time so client-side time filtering
        # is anchored to Kiev regardless of the device's timezone.
        kwargs.setdefault('server_settings', {})
        kwargs['server_settings'].setdefault('timezone', 'Europe/Kiev')

        # Pool tuning из settings (env-overrides). Fallback на defaults
        # совпадает с hardcoded версией.
        pool_min = settings.db.pool_min_size if settings and settings.db else 5
        pool_max = settings.db.pool_max_size if settings and settings.db else 20
        cmd_timeout = settings.db.command_timeout if settings and settings.db else 60

        for attempt in range(1, max_retries + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    min_size=pool_min,
                    max_size=pool_max,
                    command_timeout=cmd_timeout,
                    **kwargs
                )
                logger.info(f"Database connection pool created on attempt {attempt}/{max_retries}")
                return True
            except RETRYABLE_EXCEPTIONS as e:
                logger.warning(f"Connection attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Failed to connect to PostgreSQL after {max_retries} attempts")
                    raise
            except Exception as e:
                logger.error(f"Non-retryable error on attempt {attempt}: {e}")
                raise
        return False

    async def close(self) -> None:
        """Close connection pool.

        Сначала graceful close с коротким дедлайном. Если он не успевает
        (например, удерживается долгоживущее LISTEN/NOTIFY-соединение —
        тогда asyncpg ждёт его возврата в пул бесконечно), рвём все
        соединения немедленно через terminate(), чтобы не висеть при
        остановке контейнера.
        """
        if self.pool:
            try:
                await asyncio.wait_for(self.pool.close(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                self.pool.terminate()
            self.pool = None
            logger.info("Database connection pool closed")

    async def execute(self, query: str, *args) -> str:
        """Execute SQL query and return status."""
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> List[Dict]:
        """Fetch multiple rows as dictionaries."""
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        
        async with self.pool.acquire() as conn:
            records = await conn.fetch(query, *args)
            return [dict(record) for record in records]

    async def fetchrow(self, query: str, *args) -> Optional[Dict]:
        """Fetch single row as dictionary."""
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(query, *args)
            return dict(record) if record else None

    async def fetchval(self, query: str, *args) -> Any:
        """Fetch single value."""
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def executemany(self, query: str, args_list: List[tuple]) -> None:
        """Execute query with multiple parameter sets."""
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        
        async with self.pool.acquire() as conn:
            await conn.executemany(query, args_list)

    async def transaction(self):
        """Get a transaction context manager."""
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        
        return self.pool.acquire()

    @property
    def is_connected(self) -> bool:
        """Check if database is connected."""
        return self.pool is not None

