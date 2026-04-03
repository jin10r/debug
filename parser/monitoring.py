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

import pyrogram
from pyrogram import Client, filters
from pyrogram.types import Message

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

        # Конфигурация из env
        self.channel_id = os.getenv('CHANNEL_ID', '-1002050105527')
        self.events_media_dir = os.getenv('EVENTS_MEDIA_DIR', '/media/events')

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

            self.app = Client(
                name="session",  # Имя файла сессии: session.session
                workdir="/app/parser"
                # ❌ bot_token НЕ используется — это пользовательская сессия
            )

            logger.info("✅ ParserBot initialized")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize ParserBot: {e}")
            return False

    async def _load_chat_history(self):
        """Загрузить последние 5 сообщений из истории при первом запуске."""
        try:
            logger.info("Loading last 5 messages from chat history...")

            async for message in self.app.get_chat_history(
                chat_id=self.channel_id,
                limit=15
            ):
                # Обрезаем текст по ключевому слову "сообщить" (case-insensitive)
                if message.text or message.caption:
                    text = message.text or message.caption
                    text_lower = text.lower()
                    marker = 'сообщить'
                    if marker in text_lower:
                        idx = text_lower.index(marker)
                        text = text[:idx].strip()

                    # Обновляем message перед обработкой
                    message.text = text

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

        # Регистрируем обработчик сообщений
        @self.app.on_message(
            filters.channel
            & filters.text
        )
        async def handle_message(client: Client, message: Message):
            """Обработка нового сообщения."""
            await self._process_message(message)

        # Запускаем клиента
        try:
            await self.app.start()
            logger.info("✅ Telegram client started")

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

    async def _process_message(self, message: Message):
        """
        Обработка одного сообщения.

        Args:
            message: Сообщение Telegram
        """
        start_time = datetime.now(timezone.utc)
        message_id = message.id

        try:
            logger.info(f"Processing message {message_id}...")

            # 1. Формируем словарь с сырыми данными (БЕЗ обработки текста)
            msg_data = {
                'message_id': message.id,
                'text': message.text or message.caption,
                'event_time': message.date or datetime.now(timezone.utc),
                'photo': message.photo
            }

            # 2. Скачиваем фото если есть
            photo_path = None
            if message.photo:
                photo_path = await self._download_photo(message)
                msg_data['photo_path'] = photo_path

            # 3. Передаём в процессор БЕЗ предварительной обработки
            result = await self.processor.process_message(msg_data)

            if result:
                self._messages_processed += 1
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

                logger.info(
                    f"✅ Message {message_id} processed in {elapsed:.2f}s: "
                    f"event_id={result['event_id']}, "
                    f"layer={result['layer']}, "
                    f"strategy={result['strategy']}"
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
