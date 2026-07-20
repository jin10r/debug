"""Processor — NLP pipeline: потребляет из pending_events, обрабатывает, пишет в events."""

import asyncio
import json as json_lib
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Optional

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

from .db_adapter import DBAdapter
from .morphology import Morphology
from .phonetic_index import PhoneticIndex
from .geo_matcher import GeoMatcher
from .semantic_resolver import SemanticResolver
from .layer_classifier import LayerClassifier
from .word_tokenizer import tokenize
from core.utils.text_preprocessor import preprocess_light, strip_tail, is_promotional

try:
    from .nominatim_client import NominatimClient
except ImportError:
    NominatimClient = None

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
                    'description', i.description,
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
            $6::int[], $7::double precision[], $8::text[], $9::varchar
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
                    'description', i.description,
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
        self.layer_classifier = LayerClassifier(self.morph)

        self._worker_concurrency = max(
            1, min(_MAX_WORKER_CONCURRENCY, settings.processor.worker_concurrency)
        )
        self._poll_interval = settings.processor.poll_interval

        self.nominatim: Optional[NominatimClient] = None
        if NominatimClient is not None and settings.nominatim.enabled:
            self.nominatim = NominatimClient(settings.nominatim)

    async def initialize(self) -> bool:
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
        logger.info("Connecting to PostgreSQL...")
        self.db = DBAdapter()
        if not await self.db.connect():
            logger.error("Failed to connect to PostgreSQL, exiting")
            return False
        logger.info("✅ PostgreSQL connected")
        return True

    async def _init_nlp(self) -> bool:
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

            logger.info("Initializing SemanticResolver...")
            await self.resolver.initialize(self.db.pool)

            if self.nominatim:
                logger.info("Connecting to Nominatim DB...")
                if await self.nominatim.connect():
                    logger.info("✅ Nominatim DB connected")
                else:
                    logger.warning("⚠️  Nominatim DB unavailable, fallback disabled")

            logger.info("Setting up PostgreSQL notifications...")
            await self._setup_pg_notify()

            logger.info("✅ NLP pipeline initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize NLP pipeline: {e}")
            return False

    async def _setup_pg_notify(self):
        try:
            self._listen_conn = await self.db.pool.acquire()
            await self._listen_conn.add_listener("geo_updated", self._on_geo_updated)
            logger.info("Subscribed to geo_updated channel")
        except Exception as e:
            logger.error(f"Failed to setup pg_notify: {e}")

    async def _on_geo_updated(self, conn, pid, channel, payload):
        logger.info("🔄 geo_updated received, reindexing...")

        async def _reindex(func, *args):
            try:
                await func(*args)
                logger.info("✅ Reindexing completed")
            except Exception as e:
                logger.error(f"❌ Reindexing failed: {e}")

        def _on_reindex_done(task):
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
        self._running = True

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_stop)

        logger.info("🚀 Starting processor...")

        for _ in range(self._worker_concurrency):
            self._spawn_worker()
        logger.info(f"Started {self._worker_concurrency} worker(s)")

        while self._running:
            self._write_heartbeat()
            await asyncio.sleep(1)

    @staticmethod
    def _write_heartbeat():
        try:
            with open('/tmp/processor_heartbeat', 'w') as f:
                f.write(str(int(datetime.now(timezone.utc).timestamp())))
        except OSError:
            pass

    def _spawn_worker(self) -> asyncio.Task:
        worker_id = self._worker_seq
        self._worker_seq += 1
        task = asyncio.create_task(self._worker(worker_id))
        task.add_done_callback(self._supervise_worker)
        self._worker_tasks.append(task)
        return task

    def _supervise_worker(self, task):
        if not self._running:
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        logger.critical(f"Worker died unexpectedly ({exc!r}) — respawning")
        self._spawn_worker()

    async def _worker(self, worker_id: int):
        logger.info(f"Worker {worker_id} started")
        while self._running:
            row = await self._fetch_pending()
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
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_events SET status = 'done', processed_at = now() "
                "WHERE id = $1",
                row_id,
            )

    async def _mark_error(self, row_id: int, error_msg: str):
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_events SET status = 'error', error_message = $1 "
                "WHERE id = $2",
                error_msg, row_id,
            )

    @staticmethod
    def _sanitize_text(text: Optional[str]) -> Optional[str]:
        if not text:
            return text
        return text.encode('utf-8', errors='replace').decode('utf-8')

    async def _process_row(self, row) -> Optional[dict]:
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

        entities = await self.matcher.find_geo(tokens=tokens, lemmas=lemmas)

        geo_ids = []
        geo_scores = []
        geo_texts = []
        for ent in entities:
            if ent['geo_id'] not in geo_ids:
                geo_ids.append(ent['geo_id'])
                geo_scores.append(ent['score'])
                geo_texts.append(ent['text'])

        if not geo_ids:
            if self.nominatim:
                nom_entities = await self.matcher.find_geo_nominatim(
                    tokens, self.nominatim,
                )
                if nom_entities:
                    best = nom_entities[0]
                    geom = best.get('_nominatim_geom')
                    if geom and geom.get('type') and geom.get('coordinates'):
                        geom_type = geom['type']
                        coords = geom['coordinates']
                        if geom_type == 'Point':
                            geom_wkt = f"POINT({coords[0]} {coords[1]})"
                        elif geom_type == 'LineString':
                            pts = ', '.join(f"{c[0]} {c[1]}" for c in coords)
                            geom_wkt = f"LINESTRING({pts})"
                        elif geom_type == 'Polygon':
                            rings = []
                            for ring in coords:
                                pts = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                                rings.append(f"({pts})")
                            geom_wkt = f"POLYGON({', '.join(rings)})"
                        elif geom_type == 'MultiPolygon':
                            polys = []
                            for poly in coords:
                                rings = []
                                for ring in poly:
                                    pts = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                                    rings.append(f"({pts})")
                                polys.append(f"({', '.join(rings)})")
                            geom_wkt = f"MULTIPOLYGON({', '.join(polys)})"
                        else:
                            geom_wkt = None
                        if geom_wkt:
                            return self._enrich(
                                await self._insert_event(
                                    message_id=message_id, event_time=event_time,
                                    description=raw_text, photo_path=None,
                                    layer=layer, strategy='single_match',
                                    geom_wkt=geom_wkt,
                                ),
                                tokens=tokens,
                                geo_ids=[best['geo_id']],
                            )

            return self._enrich(
                await self._insert_event(
                    message_id=message_id, event_time=event_time,
                    description=raw_text, photo_path=None,
                    layer=layer, strategy='random',
                    geom_wkt=self._random_point(),
                ),
                tokens=tokens, geo_ids=[],
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
        if result is not None:
            result['tokens'] = len(tokens)
            result['geo_matched'] = len(geo_ids)
            result['geo_ids'] = geo_ids
        return result

    async def _insert_event(self, *, message_id, event_time, description,
                             photo_path, layer, strategy, geom_wkt):
        return await self._run_insert(
            _INSERT_EVENT_SIMPLE,
            (message_id, event_time, description, photo_path, layer, strategy, geom_wkt),
            message_id=message_id,
        )

    async def _insert_event_from_candidates(self, *, message_id, event_time,
                                             description, photo_path, layer,
                                             geo_ids, geo_scores, geo_texts,
                                             strategy=None):
        scores_array = [float(s) for s in geo_scores]
        return await self._run_insert(
            _INSERT_EVENT_FROM_CANDIDATES,
            (message_id, event_time, description, photo_path, layer,
             geo_ids, scores_array, geo_texts, strategy),
            message_id=message_id,
        )

    async def _run_insert(self, sql, params, *, message_id):
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
        logger.info("Stop signal received — requesting graceful shutdown")
        self._running = False

    async def shutdown(self):
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
        if self.nominatim:
            await self.nominatim.close()
        if self.db:
            await self.db.close()

        logger.info(
            f"Processor stopped. Processed: {self._messages_processed}, "
            f"Errors: {self._errors}"
        )


async def main():
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
