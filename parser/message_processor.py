"""Message Processor — обработка текста и сохранение событий.

Конвейер на сообщение (sliding-window архитектура):
  strip_tail → preprocess_light (regex-чистка с сохранением регистра/пунктуации)
  → word_tokenizer.tokenize (токены)
  → Morphology.lemmatize_tokens (леммы с POS)
  → LayerClassifier.classify (lemmas) (определение слоя)
  → если текст пустой/длинный — strategy='random'
  → StreetMatcher.find_streets (tokens + lemmas → sliding-window T1/T2/T3)
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
from .phonetic_index import PhoneticIndex
from .street_matcher import StreetMatcher
from .word_tokenizer import tokenize
from .text_preprocessor import preprocess_light, strip_tail

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Процессор сообщений Telegram: предобработка, классификация, сохранение."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool
        # Один MorphAnalyzer на процесс — переиспользуется индексом, матчером и классификатором
        self.morph = Morphology()
        self.index = PhoneticIndex(self.morph)
        self.matcher = StreetMatcher(self.morph, self.index)
        self.layer_classifier = LayerClassifier(self.morph)
        self._listen_conn: Optional[asyncpg.Connection] = None

    async def initialize(self) -> bool:
        """Инициализация при старте."""
        try:
            sim = settings.similarity if settings and settings.similarity else None
            if sim:
                logger.info(
                    f"Using street matcher settings: "
                    f"phonetic_threshold={sim.phonetic_match_threshold}, "
                    f"lemma_threshold={sim.entity_similarity_threshold}, "
                    f"pseudo_radius={sim.pseudo_intersection_radius_meters}m, "
                    f"max_entities={sim.max_entities}"
                )

            # 1. StreetMatcher + PhoneticIndex — критично, без них парсер не работает.
            # matcher.initialize() сам грузит streets из БД и строит surface+lemma индекс
            # (off-loaded в thread, т.к. лемматизация всех алиасов — это секунды CPU).
            logger.info("Initializing StreetMatcher + PhoneticIndex...")
            success = await self.matcher.initialize(self.db_pool)
            if not success:
                logger.error("StreetMatcher initialization failed")
                return False

            # 2. Подписка на уведомления от PostgreSQL
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

        def _on_reindex_done(task: asyncio.Task) -> None:
            """Callback на завершение фоновой reindex-задачи.

            asyncio.create_task без await может проглотить exception. Этот
            callback логирует unobserved exceptions (если _reindex сам не
            обработал) и предотвращает silent failure.
            """
            try:
                exc = task.exception()
                if exc is not None:
                    logger.error(f"❌ Background reindex task crashed: {exc}")
            except asyncio.CancelledError:
                pass

        try:
            street_id = json_lib.loads(payload).get('street_id')
        except Exception as e:
            logger.error(f"Failed to parse streets_updated payload: {e}")
            street_id = None

        if street_id:
            task = asyncio.create_task(
                _reindex(self.matcher.reindex_street, self.db_pool, street_id)
            )
        else:
            # Без street_id — переиндексируем все улицы.
            task = asyncio.create_task(
                _reindex(self.matcher.reindex_all, self.db_pool)
            )
        task.add_done_callback(_on_reindex_done)

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

        # Токенизация + лемматизация для матчера/классификатора (один проход).
        tokens = tokenize(preserved)
        lemmas = self.morph.lemmatize_tokens(tokens)

        # Определение слоя — на готовых леммах.
        # tokens передаётся для хэштег-override (##блокпост бьёт soft-keyword cops).
        layer = self.layer_classifier.classify(lemmas, tokens=tokens)

        # Пустой или слишком длинный текст — поиск улиц пропускается.
        max_text_length = (
            settings.similarity.max_text_length
            if settings and settings.similarity else 380
        )
        if not preserved or len(preserved) > max_text_length:
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

        logger.info(f"Message {message_id}: street search (tokens={len(tokens)})")
        entities = self.matcher.find_streets(tokens=tokens, lemmas=lemmas)

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
            # Один roundtrip вместо 3: INSERT events + UPDATE events_meta +
            # pg_notify в одном CTE-statement. PostgreSQL гарантирует
            # transactional atomicity. Все три побочных эффекта (meta-update,
            # notify) пропускаются если ON CONFLICT (дубль) — guard через
            # EXISTS (SELECT 1 FROM inserted).
            row = await c.fetchrow(
                """
                WITH inserted AS (
                    INSERT INTO events
                        (message_id, event_time, description, photo_url,
                         layer, strategy, geom, matches)
                    VALUES ($1, $2, $3, $4, $5, $6, ST_GeomFromText($7, 4326), $8::jsonb)
                    ON CONFLICT (message_id) DO NOTHING
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
                SELECT i.id,
                       ST_AsGeoJSON(i.geom)::text AS geom_json,
                       ST_X(ST_Centroid(i.geom)) AS lng,
                       ST_Y(ST_Centroid(i.geom)) AS lat
                FROM inserted i,
                     (SELECT count(*) FROM notify_call) _force_notify
                """,
                message_id, event_time, description, photo_path,
                layer, strategy, geom_wkt, matches_json,
            )
            if row is None:
                logger.info(f"Message {message_id}: duplicate, skipped")
                return None

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
