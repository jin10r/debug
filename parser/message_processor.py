"""Message Processor — обработка текста и сохранение событий.

Конвейер на сообщение (NER-first архитектура):
  strip_tail → preprocess_light (regex-чистка с сохранением регистра/пунктуации)
  → NERExtractor.extract (LOC-спаны) || RazdelTokenizer.tokenize (токены)
  → Morphology.lemmatize_tokens (леммы с POS)
  → LayerClassifier.classify (lemmas) (определение слоя)
  → если текст пустой/длинный — strategy='random'
  → StreetMatcher.find_streets (loc_spans + lemmas → улицы T1+T3)
  → process_candidates SQL → INSERT.

Вставка идемпотентна по message_id (ON CONFLICT DO NOTHING) — повторная
обработка одного сообщения (бэкфилл истории, ретраи воркера) не создаёт дублей.
"""

import asyncio
import json as json_lib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import asyncpg

from .layer_classifier import LayerClassifier
from .morphology import Morphology
from .ner_extractor import NERExtractor
from .razdel_tokenizer import RazdelTokenizer
from .street_matcher import StreetMatcher, SIMILARITY_THRESHOLD, MAX_ENTITIES
from .text_preprocessor import MAX_TEXT_LENGTH, preprocess_light, strip_tail

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Процессор сообщений Telegram: предобработка, классификация, сохранение."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool
        # Один MorphAnalyzer на процесс — переиспользуется матчером и классификатором
        self.morph = Morphology()
        self.tokenizer = RazdelTokenizer()
        self.ner = NERExtractor()
        self.matcher = StreetMatcher(self.morph)
        self.layer_classifier = LayerClassifier(self.morph)
        self._listen_conn: Optional[asyncpg.Connection] = None

    async def initialize(self) -> bool:
        """Инициализация при старте."""
        try:
            logger.info(f"✅ Using street similarity threshold: {SIMILARITY_THRESHOLD}")

            # 1. NER (natasha + Navec) — graceful, не блокирует при ошибке
            logger.info("Initializing NERExtractor (natasha + Navec)...")
            ner_ok = self.ner.initialize()
            if not ner_ok:
                logger.warning("NER unavailable → fallback to T3 (lexical-only)")

            # 2. StreetMatcher — критично, без него парсер не работает
            logger.info("Initializing StreetMatcher...")
            success = await self.matcher.initialize(self.db_pool)
            if not success:
                logger.error("StreetMatcher initialization failed")
                return False

            # 3. Индексация улиц (лемматизация aliases)
            logger.info("Indexing street aliases...")
            indexed = await self.matcher.reindex_all(self.db_pool)
            logger.info(f"✅ Indexed {indexed} aliases")

            # 4. Подписка на уведомления от PostgreSQL
            logger.info("Setting up PostgreSQL notifications...")
            await self._setup_pg_notify()

            logger.info("✅ MessageProcessor initialized")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize MessageProcessor: {e}")
            return False

    async def _setup_pg_notify(self):
        """Настроить уведомление от PostgreSQL при изменении улиц."""
        try:
            self._listen_conn = await self.db_pool.acquire()
            await self._listen_conn.add_listener(
                "streets_updated",
                self._on_streets_updated
            )
            logger.info("Subscribed to streets_updated channel")
        except Exception as e:
            logger.error(f"Failed to setup pg_notify: {e}")

    async def _on_streets_updated(self, conn: asyncpg.Connection, pid: int,
                                  channel: str, payload: str):
        """Callback pg_notify streets_updated → переиндексация alias-индекса."""
        logger.info("🔄 streets_updated received, reindexing...")

        async def _reindex(func, *args):
            try:
                await func(*args)
                logger.info("✅ Reindexing completed")
            except Exception as e:
                logger.error(f"❌ Reindexing failed: {e}")

        try:
            street_id = json_lib.loads(payload).get('street_id')
        except Exception as e:
            logger.error(f"Failed to parse streets_updated payload: {e}")
            street_id = None

        if street_id:
            asyncio.create_task(_reindex(self.matcher.reindex_street, self.db_pool, street_id))
        else:
            # Без street_id — переиндексируем все улицы.
            asyncio.create_task(_reindex(self.matcher.reindex_all, self.db_pool))

    @staticmethod
    def _sanitize_text(text: Optional[str]) -> Optional[str]:
        """Strip lone surrogates that asyncpg cannot encode for PostgreSQL text."""
        if not text:
            return text
        return text.encode('utf-8', errors='replace').decode('utf-8')

    async def process_message(self, msg_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Обработать одно сообщение Telegram и сохранить событие.

        Возвращает dict с event_id/layer/strategy либо None, если сообщение —
        дубль (ON CONFLICT) или геометрия не определена.
        """
        message_id = msg_data.get('message_id', 0)
        photo_path = msg_data.get('photo_path')
        # Время уже в киевском поясе — конвертация выполнена в monitoring при получении.
        event_time = msg_data.get('event_time') or datetime.now(timezone.utc)

        raw_text = msg_data.get('text', '') or ''

        # Предобработка: strip_tail → preprocess_light сохраняет регистр/пунктуацию.
        stripped = strip_tail(raw_text)
        preserved = self._sanitize_text(preprocess_light(stripped)) or ''

        # NER-извлечение LOC-спанов из текста с регистром/пунктуацией.
        loc_spans = self.ner.extract(preserved)

        # Токенизация + лемматизация для матчера/классификатора (один проход).
        tokens = self.tokenizer.tokenize(preserved)
        lemmas = self.morph.lemmatize_tokens(tokens)

        # Определение слоя — на готовых леммах.
        layer = self.layer_classifier.classify(lemmas)

        # Пустой или слишком длинный текст — поиск улиц пропускается.
        if not preserved or len(preserved) > MAX_TEXT_LENGTH:
            if not preserved:
                description = 'без описания'
                logger.info(f"Message {message_id}: empty text → random point")
            else:
                description = 'слишком длинное сообщение не является релевантной локацией'
                logger.warning(
                    f"Message {message_id}: text too long ({len(preserved)}) → random point"
                )
            return await self._insert_event(
                message_id=message_id, event_time=event_time, description=description,
                photo_path=photo_path, layer=layer, strategy='random',
                geom_wkt=self._generate_random_point_in_question_overlay(),
            )

        # NER-first поиск улиц.
        logger.info(
            f"Message {message_id}: street search "
            f"(NER spans={len(loc_spans)}, tokens={len(tokens)})"
        )
        entities = self.matcher.find_streets(
            loc_spans=loc_spans,
            lemmas=lemmas,
            threshold=SIMILARITY_THRESHOLD,
            top_k=MAX_ENTITIES,
        )

        street_ids: list = []
        street_scores: list = []
        street_texts: list = []
        for ent in entities:
            if ent['street_id'] not in street_ids:
                street_ids.append(ent['street_id'])
                street_scores.append(ent['score'])
                street_texts.append(ent['text'])

        # Улиц не нашлось — случайная точка.
        if not street_ids:
            logger.info(f"Message {message_id}: no street matches → random point")
            return await self._insert_event(
                message_id=message_id, event_time=event_time, description=preserved,
                photo_path=photo_path, layer=layer, strategy='random',
                geom_wkt=self._generate_random_point_in_question_overlay(),
            )

        # Улицы найдены — геометрия и стратегия через process_candidates.
        # Радиус псевдо-пересечения настраиваемый через PSEUDO_INTERSECTION_RADIUS_METERS.
        pseudo_radius = (
            settings.similarity.pseudo_intersection_radius_meters
            if settings and settings.similarity else 150.0
        )
        logger.info(
            f"Message {message_id}: {len(street_ids)} streets matched: {street_ids} "
            f"(pseudo_radius={pseudo_radius}m)"
        )
        async with self.db_pool.acquire() as conn:
            scores_array = [float(s) for s in street_scores]
            pc_rows = await conn.fetch(
                """
                SELECT result_strategy,
                       result_matches,
                       ST_AsText(result_geom) AS geom_wkt
                FROM process_candidates($1::int[], $2::double precision[], $3::float, $4::text[])
                """,
                street_ids, scores_array, pseudo_radius, street_texts,
            )
            if not pc_rows or pc_rows[0]['geom_wkt'] is None:
                logger.warning(f"Message {message_id}: process_candidates returned no geometry")
                return None

            pc = pc_rows[0]
            return await self._insert_event(
                message_id=message_id, event_time=event_time, description=preserved,
                photo_path=photo_path, layer=layer, strategy=pc['result_strategy'],
                geom_wkt=pc['geom_wkt'], matches=pc['result_matches'], conn=conn,
            )

    async def _insert_event(
        self, *, message_id: int, event_time: datetime, description: str,
        photo_path: Optional[str], layer: str, strategy: str, geom_wkt: str,
        matches: Optional[Any] = None, conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[Dict[str, Any]]:
        """Идемпотентно вставить событие, обновить мету, оповестить WebSocket.

        Объединяет три ранее дублировавшихся блока INSERT. Вставка с
        ON CONFLICT (message_id) DO NOTHING: при повторе RETURNING пуст — это
        дубль, возвращается None (не ошибка). Центроид геометрии вычисляется в
        RETURNING и используется как точка для pg_notify('events_new').
        """
        matches_json = matches if matches is not None else '[]'

        async def _run(c: asyncpg.Connection) -> Optional[Dict[str, Any]]:
            row = await c.fetchrow(
                """
                INSERT INTO events
                    (message_id, event_time, description, photo_url,
                     layer, strategy, geom, matches)
                VALUES ($1, $2, $3, $4, $5, $6, ST_GeomFromText($7, 4326), $8::jsonb)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING id,
                          ST_AsGeoJSON(geom)::text AS geom_json,
                          ST_X(ST_Centroid(geom)) AS lng,
                          ST_Y(ST_Centroid(geom)) AS lat
                """,
                message_id, event_time, description, photo_path,
                layer, strategy, geom_wkt, matches_json,
            )
            if row is None:
                logger.info(f"Message {message_id}: duplicate, skipped")
                return None

            await c.execute(
                "UPDATE events_meta SET version = version + 1, "
                "updated_at = now(), max_event_id = $1 WHERE id = 1",
                row['id'],
            )
            await c.execute(
                "SELECT pg_notify('events_new', $1::text)",
                json_lib.dumps({
                    'type': 'Feature',
                    'geometry': json_lib.loads(row['geom_json']),
                    'properties': {
                        'id': row['id'],
                        'layer': layer,
                        'strategy': strategy,
                        'description': description,
                        'time': event_time.isoformat(),
                    },
                }),
            )
            logger.info(
                f"Message {message_id}: event {row['id']} saved "
                f"(layer={layer}, strategy={strategy})"
            )
            return {'event_id': row['id'], 'layer': layer, 'strategy': strategy}

        if conn is not None:
            return await _run(conn)
        async with self.db_pool.acquire() as c:
            return await _run(c)

    def _generate_random_point_in_question_overlay(self) -> str:
        """Сгенерировать случайную точку в круге question_overlay (WKT POINT)."""
        import math
        import random

        if settings and hasattr(settings, 'question_overlay'):
            qo = settings.question_overlay
            center_lat = qo.center_lat
            center_lng = qo.center_lon
            radius = qo.radius
        else:
            center_lat = 46.49804
            center_lng = 30.83135
            radius = 0.045

        r = radius * math.sqrt(random.random())
        theta = random.random() * 2 * math.pi

        lng = center_lng + r * math.cos(theta)
        lat = center_lat + r * math.sin(theta)

        return f"POINT({lng} {lat})"

    async def close(self):
        """Закрытие соединений."""
        logger.info("Closing MessageProcessor...")

        if self._listen_conn:
            try:
                await self._listen_conn.remove_listener(
                    "streets_updated",
                    self._on_streets_updated
                )
                await self._listen_conn.close()
            except Exception as e:
                logger.debug(f"Error closing listen connection: {e}")

        if self.matcher:
            await self.matcher.close()

        logger.info("MessageProcessor closed")
