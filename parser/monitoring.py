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

# Импорт settings первым делом — log-параметры берутся из централизованного
# AppConfig (log_level/log_format), не из env. settings.py использует
# logging.getLogger без basicConfig — это безопасно до настройки ниже.
from core.settings import settings

# Настраиваем логирование сразу после импорта settings, до загрузки тяжёлых
# зависимостей (pymorphy3, rapidfuzz), чтобы их init-логи попадали в нужный
# формат. log_format=json (default) → JSON через JSONFormatter; =text —
# человеко-читаемые логи для локальной разработки.
_LOG_LEVEL = getattr(logging, settings.app.log_level.upper(), logging.INFO)
_LOG_FORMAT = settings.app.log_format.lower()

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

        # Конфигурация из env через систему настроек — валидация до доступа
        # к settings.parser, чтобы все последующие обращения были безопасны.
        if not settings or not settings.bot or not settings.bot.channel_id:
            raise RuntimeError(
                "CHANNEL_ID not configured in settings. "
                "Check that CHANNEL_ID is set in .env and passed to the parser container."
            )
        self.channel_id = settings.bot.channel_id
        self.events_media_dir = settings.parser.events_media_dir

        # Очередь обработки: pyrogram-хендлер только кладёт сообщение в очередь,
        # единственный воркер разбирает её последовательно — без гонок и потерь.
        self._message_queue: asyncio.Queue = asyncio.Queue(
            maxsize=settings.parser.message_queue_maxsize
        )
        self._worker_task: Optional[asyncio.Task] = None

    async def initialize(self) -> bool:
        """Инициализация компонентов: БД → процессор → Telegram-клиент."""
        try:
            if not await self._init_database():
                return False
            if not await self._init_processor():
                return False
            if not await self._init_telegram_client():
                return False
            logger.info("✅ ParserBot initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize ParserBot: {e}")
            return False

    async def _init_database(self) -> bool:
        """Подключение к PostgreSQL и гарантия схемы (миграция тома)."""
        logger.info("Connecting to PostgreSQL...")
        self.db = DBAdapter()
        if not await self.db.connect():
            logger.error("Failed to connect to PostgreSQL, exiting")
            return False
        logger.info("✅ PostgreSQL connected")

        logger.info("Ensuring database schema...")
        if not await self.db.ensure_schema():
            logger.error("Failed to ensure database schema, exiting")
            return False
        return True

    async def _init_processor(self) -> bool:
        """Инициализация процессора сообщений (общий пул БД)."""
        logger.info("Initializing message processor...")
        self.processor = MessageProcessor(db_pool=self.db.pool)
        if not await self.processor.initialize():
            logger.error("Failed to initialize message processor")
            return False
        logger.info("✅ Message processor initialized")
        return True

    async def _init_telegram_client(self) -> bool:
        """Старт pyrogram-клиента под существующей пользовательской сессией.

        session.session создаётся администратором вручную вне приложения и
        монтируется volume'ом; рантайм сессию не создаёт. SOCKS5/HTTP-прокси
        задаётся через ParserConfig.{socks5_host,proxy_*} (см. core/settings).
        """
        session_path = os.path.join("/app/parser", "session.session")
        if not os.path.exists(session_path):
            logger.error(
                f"❌ Session file not found: {session_path}. "
                "Файл session.session должен быть создан администратором "
                "вручную и смонтирован в контейнер (volume)."
            )
            return False

        proxy_host = settings.parser.socks5_host or settings.parser.proxy_host
        proxy_config = None
        if proxy_host:
            proxy_config = {
                "scheme": settings.parser.proxy_scheme,
                "hostname": proxy_host,
                "port": settings.parser.proxy_port,
            }
            logger.info(
                f"Using proxy: {proxy_config['scheme']}://"
                f"{proxy_config['hostname']}:{proxy_config['port']}"
            )

        try:
            self.app = Client(
                name="session",
                workdir="/app/parser",
                **({"proxy": proxy_config} if proxy_config else {})
            )
            logger.info("Starting Telegram client...")
            await self.app.start()
            logger.info("✅ Telegram client started successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize Telegram client: {e}")
            logger.error("Check if session.session file exists and is valid")
            return False

    async def _load_chat_history(self):
        """Уложить последние сообщения канала в очередь обработки (бэкфилл)."""
        try:
            logger.info(f"Loading history from channel {self.channel_id}...")

            await self._warmup_peer()

            count = 0
            async for message in self.app.get_chat_history(
                chat_id=self.channel_id,
                limit=settings.parser.history_limit,
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
        """Извлечь текст сообщения как обычный str.

        pyrogram отдаёт текст объектом Str (наследник str) с переопределённым
        __getitem__, который гоняет срез через UTF-16 surrogate round-trip. При
        срезе, попавшем на середину суррогатной пары не-BMP символа (emoji), это
        падает с UnicodeDecodeError 'utf-16-le … unexpected end of data'. Приводим
        к обычному str — срезы downstream (strip_tail, токенизатор) работают по
        code point и не ломаются.
        """
        return str(message.text or message.caption or '')

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
        """Скачать фото сообщения и вернуть **публичный URL** для фронтенда.

        Файл пишется на диск в ``events_media_dir`` (контейнерный путь, напр.
        ``/app/media/events``), но в БД сохраняется публичный URL
        ``/media/events/<filename>`` — именно его браузер кладёт в ``<img src>``.
        nginx обслуживает ``/media/events/*.jpg`` напрямую с диска (alias),
        с fallback на ``/api/media/events/<file>`` если файла нет (см. nginx.conf).

        Returns:
            Публичный URL фото или None при ошибке / path-traversal / symlink-attack.
        """
        try:
            if not self.events_media_dir:
                return None

            from pathlib import Path

            # Канонизируем target-директорию: resolve следует за symlink'ами
            # и нормализует .. компоненты.
            target_dir = Path(self.events_media_dir).resolve()
            target_dir.mkdir(parents=True, exist_ok=True)

            # Имя файла строится из timestamp + msg.id. Используем Path().name
            # для извлечения базового имени — никаких path-components.
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            safe_msg_id = abs(int(message.id))  # защита от отрицательных/inf
            filename = Path(f"event_{timestamp}_{safe_msg_id}.jpg").name
            final_path = (target_dir / filename).resolve()

            # Path-traversal guard: финальный путь должен быть строго внутри
            # target_dir (а не выше или сбоку через symlink в target_dir).
            try:
                final_path.relative_to(target_dir)
            except ValueError:
                logger.error(
                    f"Path traversal blocked: {final_path} not under {target_dir}"
                )
                return None

            # Symlink-attack guard: если файл уже существует как symlink —
            # удалить, чтобы download_media не записал по адресу target'а.
            if final_path.is_symlink():
                logger.warning(f"Removing pre-existing symlink: {final_path}")
                final_path.unlink()

            # Скачиваем через client.download_media на диск.
            await self.app.download_media(message.photo, file_name=str(final_path))

            public_url = f"/media/events/{filename}"
            logger.debug(f"Downloaded photo to {final_path} → URL {public_url}")
            return public_url

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

        Auto-reconnect: при потере PostgreSQL connection (network blip,
        server restart) listener молча отваливался. Теперь — exponential
        backoff (1s/5s/30s) с повторным acquire+add_listener.
        """
        media_dir = self.events_media_dir.rstrip('/')

        def _resolve_photo_path(url: str) -> Optional[str]:
            """URL из photo_url-колонки → реальный путь на диске.

            Новый формат: ``/media/events/<file>`` → ``<media_dir>/<file>``.
            Legacy: ``/app/media/events/<file>`` (фс-путь) → как есть.
            """
            if not url:
                return None
            if url.startswith('/media/events/'):
                return f"{media_dir}/{url[len('/media/events/'):]}"
            return url  # legacy absolute path

        def _on_notify(connection, pid, channel, payload):
            try:
                data = json.loads(payload)
                deleted = 0
                for url in data.get('photo_urls') or []:
                    path = _resolve_photo_path(url)
                    if path and os.path.isfile(path):
                        try:
                            os.unlink(path)
                            deleted += 1
                            logger.debug(f"Удалено устаревшее фото: {path}")
                        except OSError as e:
                            logger.warning(f"Не удалось удалить фото {path}: {e}")
                if deleted:
                    logger.info(f"Удалено устаревших фото: {deleted}")
            except Exception as e:
                logger.warning(f"Ошибка обработчика events_cleaned: {e}")

        backoff_schedule = [1, 5, 30]  # секунды между retry
        backoff_idx = 0

        while self._running:
            conn = None
            try:
                conn = await self.db.pool.acquire()
                await conn.add_listener('events_cleaned', _on_notify)
                logger.info("Слушаем events_cleaned для удаления устаревших фото")
                backoff_idx = 0  # успех — сбросить backoff

                # Heartbeat-loop: спим, периодически проверяя что соединение
                # ещё живо. asyncpg бросит при closed connection — поймаем
                # в outer except.
                while self._running:
                    await asyncio.sleep(5)
                    # ping connection — если упал, поймает заголовок цикла
                    if conn.is_closed():
                        raise ConnectionError("Listener connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                delay = backoff_schedule[min(backoff_idx, len(backoff_schedule) - 1)]
                logger.warning(
                    f"Photo cleanup listener lost connection ({e}), "
                    f"retry in {delay}s"
                )
                backoff_idx += 1
            finally:
                if conn is not None:
                    try:
                        await conn.remove_listener('events_cleaned', _on_notify)
                    except Exception as e:
                        logger.warning(f"remove_listener failed: {e}")
                    try:
                        await self.db.pool.release(conn, timeout=5)
                    except Exception as e:
                        logger.warning(f"pool.release failed: {e}")

            if not self._running:
                break
            # Backoff sleep ВНЕ try-блока, чтобы не путать с listener error
            try:
                await asyncio.sleep(
                    backoff_schedule[min(backoff_idx - 1, len(backoff_schedule) - 1)]
                )
            except asyncio.CancelledError:
                raise

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
    """Точка входа.

    Логирование уже настроено на module-level из settings.app.log_format/log_level
    сразу после импорта settings (до загрузки тяжёлых зависимостей).
    """
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
