"""Monitoring - запуск парсера Telegram каналов.

Мониторит канал, обрабатывает новые сообщения и сохраняет события в БД.
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from pyrogram import Client, filters
from pyrogram.types import Message

# Настраиваем логирование на уровне модуля — до любых импортов,
# чтобы ошибки при загрузке зависимостей были видны в docker logs.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True,
)

from core.settings import settings
from .db_adapter import DBAdapter
from .message_processor import MessageProcessor

logger = logging.getLogger(__name__)


class ParserBot:
    """Бот для парсинга Telegram каналов."""

    def __init__(self):
        """Инициализация бота."""
        self.db: Optional[DBAdapter] = None
        self.processor: Optional[MessageProcessor] = None
        self.app: Optional[Client] = None
        self._running = False
        self._messages_processed = 0
        self._errors = 0

        # Конфигурация из env через систему настроек
        if not settings or not settings.bot or not settings.bot.channel_id:
            raise RuntimeError(
                "CHANNEL_ID not configured in settings. "
                "Check that CHANNEL_ID is set in .env and passed to the parser container."
            )
        self.channel_id = settings.bot.channel_id
        self.events_media_dir = os.getenv('EVENTS_MEDIA_DIR', '/app/media/events')

    async def initialize(self) -> bool:
        """
        Инициализация компонентов.

        Returns:
            True если успешно, False иначе
        """
        try:
            # 1. Подключение к PostgreSQL
            logger.info("Connecting to PostgreSQL...")
            self.db = DBAdapter()
            success = await self.db.connect()
            if not success:
                logger.error("Failed to connect to PostgreSQL, exiting")
                return False
            logger.info("✅ PostgreSQL connected")

            # 2. Инициализация процессора сообщений
            logger.info("Initializing message processor...")
            self.processor = MessageProcessor(
                db_pool=self.db.pool  # ← Используем property pool
            )

            success = await self.processor.initialize()
            if not success:
                logger.error("Failed to initialize message processor")
                return False

            logger.info("✅ Message processor initialized")

            # 3. Инициализация Telegram клиента
            # Используется пользовательская сессия (не бот)
            # api_id/api_hash не используются, авторизация делается отдельно
            # сессия уже содержит авторизацию пользователя
            logger.info("Initializing Telegram client...")

            try:
                proxy_host = os.getenv('SOCKS5_HOST') or os.getenv('PROXY_HOST')
                proxy_config = None
                if proxy_host:
                    proxy_config = {
                        "scheme": os.getenv('PROXY_SCHEME', 'socks5'),
                        "hostname": proxy_host,
                        "port": int(os.getenv('PROXY_PORT', '1080')),
                    }
                    logger.info(f"Using proxy: {proxy_config['scheme']}://{proxy_config['hostname']}:{proxy_config['port']}")

                self.app = Client(
                    name="session",
                    workdir="/app/parser",
                    **({"proxy": proxy_config} if proxy_config else {})
                )
                logger.info("✅ Telegram client created")
                
                # Диагностика состояния клиента перед запуском
                logger.info("Diagnosing client state before start...")
                logger.info(f"Client is_connected: {self.app.is_connected}")
                logger.info(f"Client is_initialized: {self.app.is_initialized}")
                
                # Важно: всегда пытаемся остановить клиент перед перезапуском
                # Это предотвращает "already connected" ошибки
                try:
                    if self.app.is_connected:
                        logger.info("Stopping existing client connection...")
                        await self.app.stop()
                        logger.info("✅ Existing client stopped")
                        # Даем небольшое время для завершения
                        await asyncio.sleep(1)
                except Exception as stop_error:
                    logger.warning(f"Error stopping client (may be expected): {stop_error}")
                
                # Только после этого запускаем клиент
                if not self.app.is_connected:
                    logger.info("Starting Telegram client...")
                    await self.app.start()
                    logger.info("✅ Telegram client started successfully")
                else:
                    logger.warning("⚠️  Client still connected after attempted stop - this may cause issues")
                
            except Exception as e:
                logger.error(f"❌ Failed to initialize Telegram client: {e}")
                logger.error("Check if session.session file exists and is valid")
                return False

            logger.info("✅ ParserBot initialized")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize ParserBot: {e}")
            return False

    async def _load_chat_history(self):
        """Загрузить последние сообщения из целевого канала."""
        try:
            logger.info(f"Loading history from channel {self.channel_id}...")
            
            # Клиент уже подключен из initialize(), только диагностика
            logger.info(f"Client state - is_connected: {self.app.is_connected}, is_initialized: {self.app.is_initialized}")

            async for message in self.app.get_chat_history(
                chat_id=self.channel_id,
                limit=25
            ):
                await self._process_message(message)

            logger.info("✅ Chat history loaded")
            
        except Exception as e:
            logger.error(f"Failed to load chat history: {e}")

    async def start(self):
        """Запуск бота."""
        if not self.processor:
            logger.error("Cannot start: not initialized")
            return

        self._running = True

        # Обработчики сигналов
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self.shutdown())
            )

        logger.info("🚀 Starting parser bot...")

        # Регистрируем обработчик ТОЛЬКО для целевого канала
        target_filter = filters.chat(int(self.channel_id)) & (filters.text | filters.caption)
        
        @self.app.on_message(target_filter)
        async def handle_message(client: Client, message: Message):
            """Обработка сообщения из целевого канала."""
            await self._process_message(message)

        # Клиент уже запущен в initialize(), проверяем состояние
        try:
            if not self.app.is_connected:
                logger.error("Telegram client is not connected - check initialization")
                raise Exception("Telegram client not properly initialized")
            
            logger.info("✅ Telegram client already running")

            # Загружаем последние 5 сообщений из истории
            await self._load_chat_history()

            # Отправляем сообщение о запуске
            await self._send_status_message("Parser started")

            # Держим соединение
            while self._running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Telegram client error: {e}")
            raise

    @staticmethod
    def _truncate_text(text: str) -> str:
        """Отбросить текст начиная с маркеров 'сообщить', 'подписаться' или '|'."""
        if not text:
            return text
        
        text_lower = text.lower()
        markers = ['сообщить', 'подписаться', '|']
        
        earliest_pos = len(text)
        for marker in markers:
            pos = text_lower.find(marker)
            if pos != -1 and pos < earliest_pos:
                earliest_pos = pos
        
        if earliest_pos < len(text):
            return text[:earliest_pos].strip()
        
        return text.strip()

    async def _process_message(self, message: Message):
        """Обработка сообщения из целевого канала."""
        if str(message.chat.id) != str(self.channel_id):
            logger.debug(f"Skipping message from wrong channel: {message.chat.id}")
            return
        
        start_time = datetime.now(timezone.utc)
        message_id = message.id

        try:
            raw_text = message.text or message.caption or ''
            truncated = self._truncate_text(raw_text)
            
            msg_data = {
                'message_id': message.id,
                'text': truncated.lower(),
                'event_time': message.date or datetime.now(timezone.utc),
                'photo': message.photo
            }

            if message.photo:
                msg_data['photo_path'] = await self._download_photo(message)

            result = await self.processor.process_message(msg_data)

            if result:
                self._messages_processed += 1
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                logger.info(
                    f"✅ Message {message_id} processed in {elapsed:.2f}s: "
                    f"event_id={result['event_id']}, layer={result['layer']}"
                )
            else:
                logger.warning(f"Message {message_id}: processing returned None")

        except Exception as e:
            self._errors += 1
            logger.error(f"Error processing message {message_id}: {e}")

    async def _download_photo(self, message: Message) -> Optional[str]:
        """
        Скачать фото сообщения.

        Args:
            message: Сообщение с фото

        Returns:
            Путь к файлу или None
        """
        try:
            if not self.events_media_dir:
                return None

            # Создаём директорию если нет
            os.makedirs(self.events_media_dir, exist_ok=True)

            # Генерируем имя файла
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            filename = f"event_{timestamp}_{message.id}.jpg"
            filepath = os.path.join(self.events_media_dir, filename)

            # Скачиваем через client.download_media
            await self.app.download_media(message.photo, file_name=filepath)

            logger.debug(f"Downloaded photo to {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Failed to download photo: {e}")
            return None

    async def _send_status_message(self, text: str):
        """Отправить сообщение о статусе (если настроено)."""
        try:
            # Можно реализовать отправку в лог-канал
            pass
        except Exception as e:
            logger.debug(f"Failed to send status: {e}")

    async def shutdown(self):
        """Корректное завершение работы."""
        if not self._running:
            return

        logger.info("Shutting down parser...")
        self._running = False

        # Останавливаем Telegram клиента
        if self.app:
            try:
                await self.app.stop()
                logger.info("Telegram client stopped")
            except Exception as e:
                logger.error(f"Error stopping Telegram client: {e}")

        # Закрываем процессор
        if self.processor:
            await self.processor.close()

        # Закрываем БД
        if self.db:
            await self.db.close()

        # Статистика
        logger.info(
            f"Parser stopped. Processed: {self._messages_processed}, "
            f"Errors: {self._errors}"
        )

    def get_statistics(self) -> Dict[str, Any]:
        """Получить статистику работы."""
        return {
            'running': self._running,
            'messages_processed': self._messages_processed,
            'errors': self._errors
        }


async def main():
    """Точка входа."""
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    parser = ParserBot()

    try:
        # Инициализация
        success = await parser.initialize()
        if not success:
            logger.error("Failed to initialize, exiting")
            sys.exit(1)

        # Запуск
        await parser.start()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        await parser.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
