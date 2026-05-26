"""Monitoring - запуск парсера Telegram каналов.

Мониторит канал, обрабатывает новые сообщения и сохраняет события в БД.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from pyrogram import Client, filters
from pyrogram.types import Message

# Настраиваем логирование на уровне модуля — до любых импортов,
# чтобы ошибки при загрузке зависимостей были видны в docker logs.
# LOG_FORMAT=json (по умолчанию, симметрично main.py app-сервиса) → JSON-логи
# через core.utils.logging_config.JSONFormatter; LOG_FORMAT=text — человеко-
# читаемые логи для локальной разработки.
_LOG_LEVEL = getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
_LOG_FORMAT = os.getenv('LOG_FORMAT', 'json').lower()

if _LOG_FORMAT == 'json':
    from core.utils.logging_config import JSONFormatter
    _formatter = JSONFormatter()
else:
    _formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_formatter)
logging.root.handlers = [_handler]
logging.root.setLevel(_LOG_LEVEL)

from core.settings import settings
from .db_adapter import DBAdapter
from .message_processor import MessageProcessor

logger = logging.getLogger(__name__)

# Время событий от Telegram приходит в UTC; конвертируем в киевский пояс
# сразу при получении (см. ParserBot._to_kiev).
KIEV_TZ = ZoneInfo('Europe/Kiev')


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
        self._cleanup_listener_task: Optional[asyncio.Task] = None  # NOTIFY-слушатель удаления фото
        # Очередь обработки: pyrogram-хендлер только кладёт сообщение в очередь,
        # единственный воркер разбирает её последовательно — без гонок и потерь.
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._worker_task: Optional[asyncio.Task] = None

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

            # 1a. Гарантируем схему (миграция для существующего тома БД)
            logger.info("Ensuring database schema...")
            if not await self.db.ensure_schema():
                logger.error("Failed to ensure database schema, exiting")
                return False

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

            # session.session создаётся администратором вручную вне приложения
            # и монтируется в контейнер; в рантайме сессия не создаётся.
            session_path = os.path.join("/app/parser", "session.session")
            if not os.path.exists(session_path):
                logger.error(
                    f"❌ Session file not found: {session_path}. "
                    "Файл session.session должен быть создан администратором "
                    "вручную и смонтирован в контейнер (volume)."
                )
                return False

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
        """Уложить последние сообщения канала в очередь обработки (бэкфилл)."""
        try:
            logger.info(f"Loading history from channel {self.channel_id}...")

            await self._warmup_peer()

            count = 0
            async for message in self.app.get_chat_history(
                chat_id=self.channel_id,
                limit=5
            ):
                await self._message_queue.put(message)
                count += 1

            logger.info(f"✅ Chat history queued: {count} messages")

        except Exception as e:
            logger.error(f"Failed to load chat history: {e}")

    async def _warmup_peer(self) -> bool:
        """Populate session peer cache before history fetch.

        Pyrogram needs access_hash in session.session for numeric peer IDs.
        Iterating get_dialogs() until we find the target channel writes it
        without fetching the full dialog list.
        Returns True if peer was found, False otherwise.
        """
        target_id = int(self.channel_id)
        try:
            async for dialog in self.app.get_dialogs():
                if dialog.chat.id == target_id:
                    logger.info(f"Peer cache warmed for channel {self.channel_id}")
                    return True
            logger.warning(
                f"Channel {self.channel_id} not found in dialogs — "
                "peer cache not warmed, history load may fail"
            )
            return False
        except Exception as e:
            logger.warning(f"Peer warmup failed ({e}) — will attempt history load anyway")
            return False

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

        # Хендлер целевого канала: только кладёт сообщение в очередь —
        # быстро, не блокирует апдейт-цикл pyrogram, ничего не теряет.
        target_filter = filters.chat(int(self.channel_id)) & (filters.text | filters.caption)

        @self.app.on_message(target_filter)
        async def handle_message(client: Client, message: Message):
            """Live-сообщение целевого канала → в очередь обработки."""
            await self._message_queue.put(message)

        # Клиент уже запущен в initialize(), проверяем состояние
        try:
            if not self.app.is_connected:
                logger.error("Telegram client is not connected - check initialization")
                raise Exception("Telegram client not properly initialized")

            logger.info("✅ Telegram client already running")

            # Воркер очереди стартует до бэкфилла — сразу разбирает сообщения.
            self._worker_task = asyncio.create_task(self._message_worker())

            # Бэкфилл истории наполняет ту же очередь; пересечение с live
            # снимается дедупликацией по message_id (ON CONFLICT в БД).
            await self._load_chat_history()

            # Слушаем events_cleaned для удаления фото по команде pg_cron
            self._cleanup_listener_task = asyncio.create_task(
                self._run_photo_cleanup_listener()
            )

            # Держим соединение; heartbeat обновляется каждую секунду —
            # healthcheck контейнера проверяет его свежесть (живость event loop).
            while self._running:
                self._write_heartbeat()
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Telegram client error: {e}")
            raise

    @staticmethod
    def _to_kiev(dt: Optional[datetime]) -> datetime:
        """Привести время сообщения к киевскому поясу сразу при получении.

        Telegram отдаёт время в UTC. Naive-datetime трактуется как UTC.
        """
        if dt is None:
            return datetime.now(KIEV_TZ)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KIEV_TZ)

    @staticmethod
    def _write_heartbeat():
        """Обновить heartbeat-файл — healthcheck контейнера проверяет свежесть."""
        try:
            with open('/tmp/parser_heartbeat', 'w') as f:
                f.write(str(int(datetime.now(timezone.utc).timestamp())))
        except OSError:
            pass

    async def _message_worker(self):
        """Единственный воркер очереди — последовательная обработка сообщений.

        Последовательность исключает гонки на пуле БД и сохраняет порядок;
        очередь буферизует всплески, поэтому сообщения не теряются.
        Завершается отменой задачи (CancelledError) при shutdown.
        """
        logger.info("Message queue worker started")
        while True:
            message = await self._message_queue.get()
            try:
                await self._process_with_retry(message)
            finally:
                self._message_queue.task_done()

    async def _process_with_retry(self, message: Message, attempts: int = 3):
        """Обработать сообщение с ретраями (повторы идемпотентны — ON CONFLICT)."""
        for attempt in range(1, attempts + 1):
            try:
                await self._process_message(message)
                return
            except Exception as e:
                if attempt < attempts:
                    delay = 2 ** attempt
                    logger.warning(
                        f"Message {message.id}: attempt {attempt}/{attempts} "
                        f"failed ({e}); retry in {delay}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    self._errors += 1
                    logger.error(
                        f"Message {message.id}: giving up after {attempts} attempts: {e}"
                    )

    @staticmethod
    def _extract_text(message) -> str:
        """Извлечь текст сообщения, исключив символы, ломающие UTF-16 entity-парсер pyrogram.

        Pyrogram применяет Telegram-entities (bold, mention и т.д.) используя
        UTF-16-смещения. Если символ вне BMP (emoji, code point > U+FFFF) попадает
        на границу entity, возникает UnicodeDecodeError 'utf-16-le … unexpected end of data'.
        При падении обращаемся к неформатированному тексту и вырезаем не-BMP символы —
        источник проблемы — оставляя остальной контент нетронутым.
        """
        try:
            return message.text or message.caption or ''
        except UnicodeDecodeError:
            pass
        # entity-форматирование упало — читаем сырой текст в обход entity-парсера.
        # pyrogram 2.x хранит неформатированный текст в _text / _caption.
        raw = (getattr(message, '_text', None)
               or getattr(message, '_caption', None)
               or '')
        if not raw:
            raw_obj = getattr(message, '_raw', None)
            if raw_obj:
                raw = (getattr(raw_obj, 'message', None)
                       or getattr(raw_obj, 'caption', None)
                       or '')
        # Убираем символы вне BMP — именно они создают суррогатные пары в UTF-16
        # и вызывают смещение entity-границ.
        stripped = ''.join(c for c in raw if ord(c) <= 0xFFFF)
        logger.warning(
            "Message %s: entity formatting failed (non-BMP char at entity boundary); "
            "stripped %d char(s), processing continues without formatting",
            getattr(message, 'id', '?'), len(raw) - len(stripped),
        )
        return stripped

    async def _process_message(self, message: Message):
        """Обработать одно сообщение канала: предобработка и сохранение события.

        Исключения не подавляются — пробрасываются в _process_with_retry.
        Сырой текст передаётся без изменений: вся предобработка централизована
        в message_processor (text_preprocessor).
        """
        if str(message.chat.id) != str(self.channel_id):
            logger.debug(f"Skipping message from wrong channel: {message.chat.id}")
            return

        start_time = datetime.now(timezone.utc)
        message_id = message.id

        msg_data = {
            'message_id': message_id,
            'text': self._extract_text(message),
            'event_time': self._to_kiev(message.date),
            'photo': message.photo,
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
            # None = дубликат (ON CONFLICT) либо пустая геометрия — не ошибка.
            logger.info(f"Message {message_id}: skipped (duplicate or no geometry)")

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

    async def _run_photo_cleanup_listener(self):
        """Слушать NOTIFY events_cleaned и удалять физические файлы фото.

        pg_cron запускает clean_old_events() каждые 5 минут. Если среди
        удалённых событий есть записи с photo_url, функция передаёт их
        список в поле photo_urls уведомления events_cleaned. Этот метод
        получает уведомление и удаляет файлы — сервис parser монтирует
        /media/events с правами :rw, в отличие от app (:ro).
        """
        conn = None
        try:
            conn = await self.db.pool.acquire()

            def _on_notify(connection, pid, channel, payload):
                try:
                    data = json.loads(payload)
                    for url in data.get('photo_urls') or []:
                        if url and os.path.isfile(url):
                            try:
                                os.unlink(url)
                                logger.info(f"Удалено устаревшее фото: {url}")
                            except OSError as e:
                                logger.warning(f"Не удалось удалить фото {url}: {e}")
                except Exception as e:
                    logger.warning(f"Ошибка обработчика events_cleaned: {e}")

            await conn.add_listener('events_cleaned', _on_notify)
            logger.info("Слушаем events_cleaned для удаления устаревших фото")

            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Photo cleanup listener завершился с ошибкой: {e}", exc_info=True)
        finally:
            if conn is not None:
                try:
                    await conn.remove_listener('events_cleaned', _on_notify)
                except Exception:
                    pass
                try:
                    await self.db.pool.release(conn)
                except Exception:
                    pass

    async def shutdown(self):
        """Корректное завершение работы."""
        if not self._running:
            return

        logger.info("Shutting down parser...")
        self._running = False

        # Останавливаем воркер очереди и слушатель events_cleaned.
        for task in (self._worker_task, self._cleanup_listener_task):
            if task and not task.done():
                task.cancel()

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
