"""Processor — NLP pipeline: потребляет из pending_events, обрабатывает, пишет в events."""

import asyncio
import json as json_lib
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Optional
from enum import Enum

import asyncpg

from core.settings import settings

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

from core.db.db_adapter import DBAdapter
from .morphology import Morphology
from .phonetic_index import PhoneticIndex
from .geo_matcher import GeoMatcher
from .semantic_resolver import SemanticResolver
from .semantic_matcher import SemanticMatcher
from .health import HealthServer
from .layer_classifier import LayerClassifier
from .word_tokenizer import tokenize
from core.text_preprocessor import preprocess_light, strip_tail, is_promotional

logger = logging.getLogger(__name__)

_TRANSIENT_ERRORS = (
    asyncpg.PostgresConnectionError,
    asyncpg.exceptions.TooManyConnectionsError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.InterfaceError,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
)

_MAX_WORKER_CONCURRENCY = 8


class CircuitState(Enum):
    """Circuit breaker состояния."""
    CLOSED = "closed"  # Нормальная работа
    OPEN = "open"      # Ошибки превысили порог, блокировка запросов
    HALF_OPEN = "half_open"  # Тестирование восстановления


class CircuitBreaker:
    """Circuit breaker для защиты от перегрузки БД."""
    
    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = CircuitState.CLOSED
        self._lock = asyncio.Lock()
    
    async def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self.last_failure_time and \
                   (asyncio.get_event_loop().time() - self.last_failure_time) > self.timeout:
                    logger.info("Circuit breaker: transitioning to HALF_OPEN")
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise Exception("Circuit breaker is OPEN")
        
        try:
            result = await func(*args, **kwargs)
            async with self._lock:
                if self.state == CircuitState.HALF_OPEN:
                    logger.info("Circuit breaker: transitioning to CLOSED")
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
            return result
        except Exception as e:
            async with self._lock:
                self.failure_count += 1
                self.last_failure_time = asyncio.get_event_loop().time()
                
                if self.failure_count >= self.failure_threshold:
                    if self.state != CircuitState.OPEN:
                        logger.error(f"Circuit breaker: transitioning to OPEN after {self.failure_count} failures")
                        self.state = CircuitState.OPEN
            raise


_INSERT_EVENT_SIMPLE = """
    WITH inserted AS (
        INSERT INTO events
            (message_id, event_time, description, photo_url,
             layer, strategy, geom, matches)
        VALUES ($1, $2, $3, $4, $5, $6, ST_GeomFromText($7, 4326), '[]'::jsonb)
        ON CONFLICT (message_id, event_time) DO NOTHING
        RETURNING id, event_time, geom, layer, strategy, description,
                  photo_url, matches
    ),
    meta_upd AS (
        UPDATE events_meta
        SET version = version + 1,
            updated_at = now(),
            max_event_id = (SELECT id FROM inserted)
        WHERE id = 1 AND EXISTS (SELECT 1 FROM inserted)
        RETURNING 1
    ),
    notify_call AS (
        SELECT pg_notify(
            'events_new',
            jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(i.geom)::jsonb,
                'properties', jsonb_build_object(
                    'id', i.id,
                    'layer', i.layer,
                    'strategy', i.strategy,
                    'description', left(i.description, 200),
                    'photo_url', i.photo_url,
                    'matches', i.matches,
                    'time', to_char(i.event_time AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
                )
            )::text
        )
        FROM inserted i
    )
    SELECT i.id, i.layer, i.strategy
    FROM inserted i,
         (SELECT count(*) FROM notify_call) _force_notify
"""

_INSERT_EVENT_FROM_CANDIDATES = """
    WITH pc AS (
        SELECT result_geom, result_strategy, result_matches
        FROM process_candidates(
            $6::int[], $7::double precision[], $8::text[], $9::varchar,
            $10::float, $11::float, $12::float
        )
    ),
    inserted AS (
        INSERT INTO events
            (message_id, event_time, description, photo_url,
             layer, strategy, geom, matches)
        SELECT $1, $2, $3, $4, $5,
               pc.result_strategy, pc.result_geom, pc.result_matches
        FROM pc
        WHERE pc.result_geom IS NOT NULL
        ON CONFLICT (message_id, event_time) DO NOTHING
        RETURNING id, event_time, geom, layer, strategy, description,
                  photo_url, matches
    ),
    meta_upd AS (
        UPDATE events_meta
        SET version = version + 1,
            updated_at = now(),
            max_event_id = (SELECT id FROM inserted)
        WHERE id = 1 AND EXISTS (SELECT 1 FROM inserted)
        RETURNING 1
    ),
    notify_call AS (
        SELECT pg_notify(
            'events_new',
            jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(i.geom)::jsonb,
                'properties', jsonb_build_object(
                    'id', i.id,
                    'layer', i.layer,
                    'strategy', i.strategy,
                    'description', left(i.description, 200),
                    'photo_url', i.photo_url,
                    'matches', i.matches,
                    'time', to_char(i.event_time AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
                )
            )::text
        )
        FROM inserted i
    )
    SELECT i.id, i.layer, i.strategy
    FROM inserted i,
         (SELECT count(*) FROM notify_call) _force_notify
"""


class ProcessorBot:
    """NLP processor: потребляет pending_events, обрабатывает, пишет в events."""

    def __init__(self):
        """Инициализация процессора: подсистемы NLP, БД, health-сервер."""
        self.db: Optional[DBAdapter] = None
        self._running = False
        self._messages_processed = 0
        self._errors = 0
        self._worker_tasks: list[asyncio.Task] = []
        self._worker_seq = 0
        self._shutdown_started = False
        self._listen_conn: Optional[asyncpg.Connection] = None

        self.morph = Morphology()
        self.index = PhoneticIndex(self.morph)
        self.matcher = GeoMatcher(self.morph, self.index)
        self.resolver = SemanticResolver(self.morph, self.index)
        self.semantic_matcher = SemanticMatcher()
        self.health_server = HealthServer()
        self.layer_classifier = LayerClassifier(self.morph)

        self._worker_concurrency = max(
            1, min(_MAX_WORKER_CONCURRENCY, settings.processor.worker_concurrency)
        )
        self._poll_interval = settings.processor.poll_interval
        
        # Circuit breaker для защиты БД
        self._circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=60.0)

    async def initialize(self) -> bool:
        """Инициализация всех компонентов: БД, NLP, подписки PG notify."""
        try:
            if not await self._init_database():
                return False
            if not await self._init_nlp():
                return False
            logger.info("✅ ProcessorBot initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize ProcessorBot: {e}")
            return False

    async def _init_database(self) -> bool:
        """Подключение к PostgreSQL через DBAdapter."""
        logger.info("Connecting to PostgreSQL...")
        self.db = DBAdapter()
        if not await self.db.connect():
            logger.error("Failed to connect to PostgreSQL, exiting")
            return False
        logger.info("✅ PostgreSQL connected")
        return True

    async def _init_nlp(self) -> bool:
        """Загрузка и инициализация всех NLP-компонентов (матчеры, резолверы)."""
        logger.info("Initializing NLP pipeline...")
        try:
            sim = settings.similarity if settings and settings.similarity else None
            if sim:
                logger.info(
                    f"Geo matcher settings: "
                    f"phonetic_threshold={sim.phonetic_match_threshold}, "
                    f"lemma_threshold={sim.entity_similarity_threshold}"
                )

            logger.info("Initializing GeoMatcher + PhoneticIndex...")
            if not await self.matcher.initialize(self.db.pool):
                logger.error("GeoMatcher initialization failed")
                return False

            logger.info("Initializing SemanticMatcher (rubert-tiny2 ONNX)...")
            try:
                async with self.db.pool.acquire() as conn:
                    geo_rows = await conn.fetch(
                        "SELECT id, names, type FROM geo WHERE geom IS NOT NULL"
                    )
                geo_data = [dict(r) for r in geo_rows]
                await asyncio.to_thread(self.semantic_matcher.initialize, geo_data)
                self.matcher.set_semantic_matcher(self.semantic_matcher)
                logger.info("✅ SemanticMatcher initialized")
            except Exception as e:
                logger.error(f"❌ SemanticMatcher initialization failed: {e}")
                logger.warning("⚠️ Continuing without SemanticMatcher")

            logger.info("Initializing SemanticResolver...")
            await self.resolver.initialize(self.db.pool)

            logger.info("Setting up PostgreSQL notifications...")
            await self._setup_pg_notify()

            self.health_server.set_initialized(True)

            logger.info("✅ NLP pipeline initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize NLP pipeline: {e}")
            return False

    async def _setup_pg_notify(self):
        """Подписка на канал geo_updated для автообновления индекса."""
        try:
            self._listen_conn = await self.db.pool.acquire()
            await self._listen_conn.add_listener("geo_updated", self._on_geo_updated)
            logger.info("Subscribed to geo_updated channel")
        except Exception as e:
            logger.error(f"Failed to setup pg_notify: {e}")

    async def _on_geo_updated(self, conn, pid, channel, payload):
        """Обработка уведомления об изменении geo-данных."""
        logger.info("🔄 geo_updated received, reindexing...")

        async def _reindex(func, *args):
            """Перестроить индекс после обновления geo-данных."""
            try:
                await func(*args)
                logger.info("✅ Reindexing completed")
            except Exception as e:
                logger.error(f"❌ Reindexing failed: {e}")

        def _on_reindex_done(task):
            """Обработка результата фоновой перестройки индекса."""
            try:
                exc = task.exception()
                if exc is not None:
                    logger.error(f"❌ Background reindex task crashed: {exc}")
            except asyncio.CancelledError:
                pass

        try:
            geo_id = json_lib.loads(payload).get('geo_id')
        except Exception:
            geo_id = None

        if geo_id:
            task = asyncio.create_task(_reindex(self.matcher.reindex_geo, self.db.pool, geo_id))
        else:
            task = asyncio.create_task(_reindex(self.matcher.reindex_all, self.db.pool))
        task.add_done_callback(_on_reindex_done)

    async def run(self):
        """Главный цикл: запуск health-сервера и воркеров."""
        self._running = True

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_stop)

        logger.info("🚀 Starting processor...")

        try:
            await self.health_server.start(port=8765)
        except Exception as e:
            logger.error(f"❌ Failed to start health server: {e}")

        for _ in range(self._worker_concurrency):
            self._spawn_worker()
        logger.info(f"Started {self._worker_concurrency} worker(s)")

        while self._running:
            self.health_server.touch()
            await asyncio.sleep(1)

    def _spawn_worker(self) -> asyncio.Task:
        """Запуск нового воркера с автонадзором."""
        worker_id = self._worker_seq
        self._worker_seq += 1
        task = asyncio.create_task(self._worker(worker_id))
        task.add_done_callback(self._supervise_worker)
        self._worker_tasks.append(task)
        return task

    def _supervise_worker(self, task):
        """Перезапуск упавшего воркера."""
        if not self._running:
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        logger.critical(f"Worker died unexpectedly ({exc!r}) — respawning")
        self._spawn_worker()

    async def _worker(self, worker_id: int):
        """Цикл обработки: выбирает pending-событие, обрабатывает, помечает done/error."""
        logger.info(f"Worker {worker_id} started")
        while self._running:
            # Circuit breaker check
            if self._circuit_breaker.state == CircuitState.OPEN:
                logger.warning(f"Worker {worker_id}: circuit breaker is OPEN, backing off")
                await asyncio.sleep(self._poll_interval * 10)
                continue
            
            try:
                row = await self._circuit_breaker.call(self._fetch_pending)
            except Exception as e:
                logger.error(f"Worker {worker_id}: circuit breaker prevented fetch: {e}")
                await asyncio.sleep(self._poll_interval * 5)
                continue
            
            if not row:
                await asyncio.sleep(self._poll_interval)
                continue

            msg_id = row['message_id']
            attempt = 0
            while True:
                attempt += 1
                try:
                    result = await self._process_row(row)
                    if result:
                        await self._mark_done(row['id'])
                        self._messages_processed += 1
                        logger.info(
                            f"✅ Message {msg_id} processed: "
                            f"event_id={result['event_id']}, layer={result['layer']}, "
                            f"strategy={result.get('strategy', '?')}"
                        )
                    else:
                        await self._mark_done(row['id'])
                        logger.debug(f"Message {msg_id}: duplicate or no geometry")
                    break
                except Exception as e:
                    transient = isinstance(e, _TRANSIENT_ERRORS)
                    if attempt < (8 if transient else 3):
                        delay = min(2 ** attempt, 30)
                        logger.warning(
                            f"Message {msg_id}: attempt {attempt} failed "
                            f"({type(e).__name__}: {e}); retry in {delay}s"
                        )
                        await asyncio.sleep(delay)
                    else:
                        self._errors += 1
                        await self._mark_error(row['id'], str(e))
                        logger.error(
                            f"Message {msg_id}: failed after {attempt} attempts: {e}"
                        )
                        break

    async def _fetch_pending(self):
        """Выборка одного pending-события из очереди с блокировкой."""
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                return await conn.fetchrow(
                    "SELECT id, message_id, text, event_time, photo_file_id "
                    "FROM pending_events "
                    "WHERE status = 'pending' "
                    "ORDER BY created_at "
                    "LIMIT 1 "
                    "FOR UPDATE SKIP LOCKED"
                )

    async def _mark_done(self, row_id: int):
        """Пометить pending-событие как выполненное."""
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_events SET status = 'done', processed_at = now() "
                "WHERE id = $1",
                row_id,
            )

    async def _mark_error(self, row_id: int, error_msg: str):
        """Пометить pending-событие как ошибочное."""
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_events SET status = 'error', error_message = $1 "
                "WHERE id = $2",
                error_msg, row_id,
            )

    @staticmethod
    def _sanitize_text(text: Optional[str]) -> Optional[str]:
        """Очистка текста от некорректных UTF-8 последовательностей."""
        if not text:
            return text
        return text.encode('utf-8', errors='replace').decode('utf-8')

    async def _process_row(self, row) -> Optional[dict]:
        """Полный цикл обработки одного сообщения: токенизация, поиск geo, вставка."""
        message_id = row['message_id']
        event_time = row['event_time']
        raw_text = row['text'] or ''

        tokens = tokenize(raw_text)
        lemmas = self.morph.lemmatize_tokens(tokens)
        layer = self.layer_classifier.classify(lemmas)

        max_text_length = (
            settings.similarity.max_text_length
            if settings and settings.similarity else 380
        )
        promotional = is_promotional(raw_text)
        if promotional or not raw_text or len(raw_text) > max_text_length:
            if not raw_text:
                description = 'без описания'
            elif promotional:
                description = raw_text
            else:
                description = 'слишком длинное сообщение не является релевантной локацией'
            return self._enrich(
                await self._insert_event(
                    message_id=message_id, event_time=event_time,
                    description=description, photo_path=None,
                    layer=layer, strategy='random',
                    geom_wkt=self._random_point(),
                ),
                tokens=tokens, geo_ids=[],
            )

        entities = await self.matcher.find_geo(
            tokens=tokens, lemmas=lemmas, text=raw_text,
        )

        geo_ids = []
        geo_scores = []
        geo_texts = []
        for ent in entities:
            if ent['geo_id'] not in geo_ids:
                geo_ids.append(ent['geo_id'])
                geo_scores.append(ent['score'])
                geo_texts.append(ent['text'])

        if not geo_ids:
            return self._enrich(
                await self._insert_event(
                    message_id=message_id, event_time=event_time,
                    description=raw_text, photo_path=None,
                    layer=layer, strategy='random',
                    geom_wkt=self._random_point(),
                ),
                tokens=tokens, geo_ids=geo_ids,
            )

        strategy = None
        if len(geo_ids) > 1:
            resolved = await self.resolver.resolve(
                text=raw_text, tokens=tokens, lemmas=lemmas, candidates=entities,
            )
            if resolved is not None:
                strategy = resolved.get('strategy')
                resolved_ids = resolved.get('geo_ids')
                if resolved_ids is not None:
                    id_set = set(resolved_ids)
                    geo_ids = [gid for gid in geo_ids if gid in id_set]
                    geo_scores = [s for gid, s in zip(geo_ids, geo_scores) if gid in id_set]
                    geo_texts = [t for gid, t in zip(geo_ids, geo_texts) if gid in id_set]

        return self._enrich(
            await self._insert_event_from_candidates(
                message_id=message_id, event_time=event_time,
                description=raw_text, photo_path=None,
                layer=layer, geo_ids=geo_ids, geo_scores=geo_scores,
                geo_texts=geo_texts, strategy=strategy,
            ),
            tokens=tokens, geo_ids=geo_ids,
        )

    @staticmethod
    def _enrich(result, *, tokens, geo_ids):
        """Добавление служебных полей в результат обработки."""
        if result is not None:
            result['tokens'] = len(tokens)
            result['geo_matched'] = len(geo_ids)
            result['geo_ids'] = geo_ids
        return result

    async def _insert_event(self, *, message_id, event_time, description,
                             photo_path, layer, strategy, geom_wkt):
        """Вставка простого события (без кандидатов) в таблицу events."""
        return await self._run_insert(
            _INSERT_EVENT_SIMPLE,
            (message_id, event_time, description, photo_path, layer, strategy, geom_wkt),
            message_id=message_id,
        )

    async def _insert_event_from_candidates(self, *, message_id, event_time,
                                              description, photo_path, layer,
                                              geo_ids, geo_scores, geo_texts,
                                              strategy=None):
        """Вставка события с вызовом process_candidates для разрешения кандидатов."""
        scores_array = [float(s) for s in geo_scores]
        if settings and hasattr(settings, 'question_overlay'):
            qo = settings.question_overlay
            center_lon, center_lat, radius = qo.center_lon, qo.center_lat, qo.radius
        else:
            center_lon, center_lat, radius = 30.83135, 46.49804, 0.045
        return await self._run_insert(
            _INSERT_EVENT_FROM_CANDIDATES,
            (message_id, event_time, description, photo_path, layer,
             geo_ids, scores_array, geo_texts, strategy,
             center_lon, center_lat, radius),
            message_id=message_id,
        )

    async def _run_insert(self, sql, params, *, message_id):
        """Выполнение SQL-запроса вставки события и возврат результата."""
        async with self.db.pool.acquire() as c:
            row = await c.fetchrow(sql, *params)
        if row is None:
            return None
        return {
            'event_id': row['id'],
            'layer': row['layer'],
            'strategy': row['strategy'],
        }

    def _random_point(self):
        """Генерация случайной точки в радиусе от центра для fallback-событий."""
        import math
        import random
        if settings and hasattr(settings, 'question_overlay'):
            qo = settings.question_overlay
            center_lat, center_lng, radius = qo.center_lat, qo.center_lon, qo.radius
        else:
            center_lat, center_lng, radius = 46.49804, 30.83135, 0.045
        r = radius * math.sqrt(random.random())
        theta = random.random() * 2 * math.pi
        return f"POINT({center_lng + r * math.cos(theta)} {center_lat + r * math.sin(theta)})"

    def _request_stop(self):
        """Установка флага остановки процессора."""
        logger.info("Stop signal received — requesting graceful shutdown")
        self._running = False

    async def shutdown(self):
        """Корректное завершение: остановка воркеров, закрытие соединений."""
        if self._shutdown_started:
            return
        self._shutdown_started = True

        logger.info("Shutting down processor...")
        self._running = False

        tasks = [t for t in self._worker_tasks if t and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if self._listen_conn:
            try:
                await self._listen_conn.remove_listener("geo_updated", self._on_geo_updated)
                await self._listen_conn.close()
            except Exception:
                pass

        if self.matcher:
            await self.matcher.close()
        if self.resolver:
            await self.resolver.close()
        if self.semantic_matcher:
            self.semantic_matcher.close()
        if self.db:
            await self.db.close()

        logger.info(
            f"Processor stopped. Processed: {self._messages_processed}, "
            f"Errors: {self._errors}"
        )


async def main():
    """Точка входа: создание, инициализация и запуск ProcessorBot."""
    processor = ProcessorBot()
    try:
        success = await processor.initialize()
        if not success:
            logger.error("Failed to initialize, exiting")
            sys.exit(1)
        await processor.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        await processor.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
