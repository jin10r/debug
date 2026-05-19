"""DB Adapter - подключение к PostgreSQL."""

import logging
import os
import asyncio
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


class DBAdapter:
    """Адаптер для работы с PostgreSQL."""

    def __init__(self):
        """Инициализация адаптера."""
        self._host = os.getenv('DB_HOST', 'postgres')
        self._port = int(os.getenv('DB_PORT', '5432'))
        self._database = os.getenv('DB_NAME', 'postgres')
        self._user = os.getenv('DB_USER', 'postgres')
        self._password = os.getenv('DB_PASSWORD', 'postgres')
        self.__pool: Optional[asyncpg.Pool] = None

    async def connect(self, max_retries: int = 10, retry_delay: float = 2.0) -> bool:
        """
        Установить пул соединений с повторными попытками.

        Args:
            max_retries: Максимальное количество попыток
            retry_delay: Задержка между попытками (секунды)

        Returns:
            True если успешно
        """
        dsn = (
            f"postgresql://{self._user}:{self._password}@"
            f"{self._host}:{self._port}/{self._database}"
        )

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Connecting to PostgreSQL (attempt {attempt}/{max_retries})...")
                
                self.__pool = await asyncpg.create_pool(
                    dsn,
                    min_size=2,
                    max_size=10,
                    command_timeout=30,
                    statement_cache_size=100,
                    # Киевский пояс на стороне сессии БД — консистентно с
                    # core/db/db_base.py; время событий хранится привязанным к Киеву.
                    server_settings={'timezone': 'Europe/Kiev'},
                )

                # Проверяем подключение
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

    async def ensure_schema(self) -> bool:
        """Идемпотентно привести схему events к требуемой parser.

        Init-скрипты PostgreSQL (`/docker-entrypoint-initdb.d`) выполняются
        только при создании тома данных; на уже существующем томе колонку
        message_id (дедупликация по Telegram message id) нужно добавить здесь —
        при каждом старте, безопасно для повторов.
        """
        try:
            async with self.__pool.acquire() as conn:
                await conn.execute(
                    "ALTER TABLE events ADD COLUMN IF NOT EXISTS message_id BIGINT"
                )
                await conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_message_id "
                    "ON events(message_id)"
                )
            logger.info("✅ Schema ensured: events.message_id + unique index")
            return True
        except Exception as e:
            logger.error(f"❌ ensure_schema failed: {e}")
            return False

    async def close(self):
        """Закрыть пул соединений."""
        if self.__pool:
            await self.__pool.close()
            self.__pool = None
            logger.info("PostgreSQL connection closed")

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        """Получить пул соединений."""
        return self.__pool
