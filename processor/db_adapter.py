"""DB Adapter — подключение к PostgreSQL для processor."""

import logging
import asyncio
from typing import Optional

import asyncpg

from core.settings import settings

logger = logging.getLogger(__name__)


class DBAdapter:
    """Адаптер для работы с PostgreSQL."""

    def __init__(self):
        self._host = settings.db.host
        self._port = settings.db.port
        self._database = settings.db.database
        self._user = settings.db.user
        self._password = settings.db.password
        self.__pool: Optional[asyncpg.Pool] = None

    async def connect(self, max_retries: int = 10, retry_delay: float = 2.0) -> bool:
        dsn = (
            f"postgresql://{self._user}:{self._password}@"
            f"{self._host}:{self._port}/{self._database}"
        )

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Connecting to PostgreSQL (attempt {attempt}/{max_retries})...")

                self.__pool = await asyncpg.create_pool(
                    dsn,
                    min_size=settings.db.pool_min_size,
                    max_size=settings.db.pool_max_size,
                    command_timeout=settings.db.command_timeout,
                    statement_cache_size=100,
                    server_settings={'timezone': 'Europe/Kiev'},
                )

                async with self.__pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")

                logger.info(
                    f"✅ PostgreSQL connected at {self._host}:{self._port}/{self._database}"
                )
                return True

            except Exception as e:
                logger.warning(f"Connection attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"❌ Failed to connect to PostgreSQL after {max_retries} attempts")
                    return False

        return False

    async def close(self):
        if self.__pool:
            await self.__pool.close()
            self.__pool = None
            logger.info("PostgreSQL connection closed")

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        return self.__pool
