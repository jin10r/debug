"""Message Processor — обработка текста и сохранение событий.

Конвейер на сообщение (sliding-window архитектура):
  strip_tail → preprocess_light (regex-чистка с сохранением регистра/пунктуации)
  → word_tokenizer.tokenize (токены)
  → Morphology.lemmatize_tokens (леммы с POS)
  → LayerClassifier.classify (lemmas) (определение слоя)
  → если текст пустой/длинный — strategy='random'
  → GeoMatcher.find_geo (tokens + lemmas → sliding-window T1/T2)
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
from .geo_matcher import GeoMatcher
from .semantic_resolver import SemanticResolver
from .spacy_relation_extractor import SpaCyRelationExtractor
from .word_tokenizer import tokenize
from .text_preprocessor import preprocess_light, strip_tail, is_promotional

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Общий хвост INSERT-CTE: bump версии events_meta, pg_notify('events_new') и
# возврат id/layer/strategy вставленной строки. Используется обеими ветками
# вставки (готовый WKT и process_candidates) — payload уведомления описан в
# одном месте. Все три побочных эффекта пропускаются при ON CONFLICT (дубль)
# через EXISTS/FROM inserted. `_force_notify` форсирует выполнение notify_call.
_EVENT_INSERT_TAIL = """
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

# Ветка с готовым WKT (random / пустой / длинный / нет матчей улиц):
# strategy и geom известны в Python, matches всегда '[]'.
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
""" + _EVENT_INSERT_TAIL

# Матч-ветка: geom/strategy/matches приходят из process_candidates ВНУТРИ того
# же statement — один roundtrip вместо двух (был отдельный SELECT + INSERT).
# p_strategy=$9: 'single_match', 'intersection', 'midpoint' или NULL (fallback).
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
""" + _EVENT_INSERT_TAIL


class MessageProcessor:
    """Процессор сообщений Telegram: предобработка, классификация, сохранение."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool
        # Один MorphAnalyzer на процесс — переиспользуется индексом, матчером и классификатором
        self.morph = Morphology()

        self.index = PhoneticIndex(self.morph)
        self.matcher = GeoMatcher(self.morph, self.index)
        self.resolver = SemanticResolver(self.morph, self.index)
        self.layer_classifier = LayerClassifier(self.morph)
        self.spacy_extractor = SpaCyRelationExtractor()
        self._listen_conn: Optional[asyncpg.Connection] = None

    async def initialize(self) -> bool:
        """Инициализация при старте."""
        try:
            sim = settings.similarity if settings and settings.similarity else None
            if sim:
                logger.info(
                    f"Using geo matcher settings: "
                    f"phonetic_threshold={sim.phonetic_match_threshold}, "
                    f"lemma_threshold={sim.entity_similarity_threshold}, "
                    f"pseudo_radius={sim.pseudo_intersection_radius_meters}m, "
                    f"max_entities={sim.max_entities}"
                )

            # GeoMatcher + PhoneticIndex — критично, без них парсер не работает.
            # matcher.initialize() сам грузит geo из БД и строит surface+lemma индекс
            # (off-loaded в thread, т.к. лемматизация всех алиасов — это секунды CPU).
            logger.info("Initializing GeoMatcher + PhoneticIndex...")
            success = await self.matcher.initialize(self.db_pool)
            if not success:
                logger.error("GeoMatcher initialization failed")
                return False

            # SemanticResolver — загружает стоп-слова, инициализирует модель (если enabled).
            logger.info("Initializing SemanticResolver...")
            await self.resolver.initialize(self.db_pool)

            # SpaCyRelationExtractor — загружает модель spaCy (если enabled).
            logger.info("Initializing SpaCyRelationExtractor...")
            await self.spacy_extractor.initialize()

            # Подписка на уведомления от PostgreSQL
            logger.info("Setting up PostgreSQL notifications...")
            await self._setup_pg_notify()

            logger.info("✅ MessageProcessor initialized")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize MessageProcessor: {e}")
            return False

    async def _setup_pg_notify(self):
        """Настроить уведомление от PostgreSQL при изменении geo объектов."""
        try:
            self._listen_conn = await self.db_pool.acquire()
            await self._listen_conn.add_listener(
                "geo_updated",
                self._on_geo_updated
            )
            logger.info("Subscribed to geo_updated channel")
        except Exception as e:
            logger.error(f"Failed to setup pg_notify: {e}")

    async def _on_geo_updated(self, conn: asyncpg.Connection, pid: int,
                               channel: str, payload: str):
        """Callback pg_notify geo_updated → переиндексация alias-индекса."""
        logger.info("🔄 geo_updated received, reindexing...")

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
            geo_id = json_lib.loads(payload).get('geo_id')
        except Exception as e:
            logger.error(f"Failed to parse geo_updated payload: {e}")
            geo_id = None

        if geo_id:
            task = asyncio.create_task(
                _reindex(self.matcher.reindex_geo, self.db_pool, geo_id)
            )
        else:
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

        # Определение слоя — на готовых леммах (теги '#' уже убраны в preprocess).
        layer = self.layer_classifier.classify(lemmas)

        # Реклама/спам, пустой или слишком длинный текст → улицы НЕ ищем, но
        # событие ВСЁ РАВНО создаётся со strategy=random (случайная точка на
        # карте). Ни одно сообщение не игнорируется — реклама/нерелевантные
        # локации/фото без описания всё равно доходят до фронтенда.
        max_text_length = (
            settings.similarity.max_text_length
            if settings and settings.similarity else 380
        )
        promotional = is_promotional(preserved)
        if promotional or not preserved or len(preserved) > max_text_length:
            if not preserved:
                description = 'без описания'
                logger.debug(f"Message {message_id}: empty text → random point")
            elif promotional:
                description = preserved  # рекламу показываем как есть, точка random
                logger.info(f"Message {message_id}: promotional → random point")
            else:
                description = 'слишком длинное сообщение не является релевантной локацией'
                logger.warning(
                    f"Message {message_id}: text too long ({len(preserved)}) → random point"
                )
            return self._enrich(
                await self._insert_event(
                    message_id=message_id, event_time=event_time, description=description,
                    photo_path=photo_path, layer=layer, strategy='random',
                    geom_wkt=self._generate_random_point_in_question_overlay(),
                ),
                tokens=tokens, geo_ids=[],
            )

        logger.debug(f"Message {message_id}: geo search (tokens={len(tokens)})")
        entities = await self.matcher.find_geo(tokens=tokens, lemmas=lemmas)

        geo_ids: list = []
        geo_scores: list = []
        geo_texts: list = []
        for ent in entities:
            if ent['geo_id'] not in geo_ids:
                geo_ids.append(ent['geo_id'])
                geo_scores.append(ent['score'])
                geo_texts.append(ent['text'])

        # SpaCyRelationExtractor: уточнение типов и пространственные отношения
        spatial_plan = None
        if geo_ids and self.spacy_extractor._enabled:
            try:
                # Prepare candidates for spaCy (add span if available, otherwise None)
                spacy_candidates = []
                for ent in entities:
                    spacy_candidates.append({
                        'id': ent['geo_id'],
                        'name': ent.get('matched_name', ent['text']),
                        'type': 'unknown',  # GeoMatcher doesn't return type, would need DB lookup
                        'geom_type': 'unknown',
                        'span': ent.get('_span')  # May not be available
                    })

                spatial_plan = self.spacy_extractor.extract_plan(preserved, spacy_candidates)
                logger.debug(f"Message {message_id}: spaCy plan: {spatial_plan}")
            except Exception as e:
                logger.warning(f"Message {message_id}: spaCy extraction failed: {e}")

        # Объектов не нашлось — случайная точка.
        if not geo_ids:
            proper = [t.text for t in tokens
                      if t.text[:1].isupper() and len(t.text) >= 5 and not t.text.isdigit()]
            if proper:
                logger.info(
                    f"Message {message_id}: no geo match; "
                    f"proper tokens for gazetteer review: {proper}"
                )
            logger.debug(f"Message {message_id}: no geo matches → random point")
            return self._enrich(
                await self._insert_event(
                    message_id=message_id, event_time=event_time, description=preserved,
                    photo_path=photo_path, layer=layer, strategy='random',
                    geom_wkt=self._generate_random_point_in_question_overlay(),
                ),
                tokens=tokens, geo_ids=geo_ids,
            )

        # SemanticResolver: определяет стратегию для 2+ кандидатов.
        strategy: Optional[str] = None
        if len(geo_ids) > 1:
            resolved = await self.resolver.resolve(
                text=preserved,
                tokens=tokens,
                lemmas=lemmas,
                candidates=entities,
                spatial_plan=spatial_plan,
            )
            if resolved is not None:
                strategy = resolved.get('strategy')
                resolved_ids = resolved.get('geo_ids')
                if resolved_ids is not None:
                    # Пересортировать geo_ids/scores/texts по решению модели
                    id_set = set(resolved_ids)
                    geo_ids = [gid for gid in geo_ids if gid in id_set]
                    geo_scores = [s for gid, s in zip(geo_ids, geo_scores) if gid in id_set]
                    geo_texts = [t for gid, t in zip(geo_ids, geo_texts) if gid in id_set]
                logger.info(
                    f"Message {message_id}: resolver → {strategy}, "
                    f"geo_ids={geo_ids}"
                )

        logger.debug(
            f"Message {message_id}: {len(geo_ids)} geo matched: {geo_ids}"
        )
        return self._enrich(
            await self._insert_event_from_candidates(
                message_id=message_id, event_time=event_time, description=preserved,
                photo_path=photo_path, layer=layer,
                geo_ids=geo_ids, geo_scores=geo_scores,
                geo_texts=geo_texts, strategy=strategy,
            ),
            tokens=tokens, geo_ids=geo_ids,
        )

    @staticmethod
    def _enrich(
        result: Optional[Dict[str, Any]], *, tokens: list, geo_ids: list
    ) -> Optional[Dict[str, Any]]:
        """Дополнить ненулевой результат метой обработки для итогового лога monitoring."""
        if result is not None:
            result['tokens'] = len(tokens)
            result['geo_matched'] = len(geo_ids)
            result['geo_ids'] = geo_ids
        return result

    async def _insert_event(
        self, *, message_id: int, event_time: datetime, description: str,
        photo_path: Optional[str], layer: str, strategy: str, geom_wkt: str,
    ) -> Optional[Dict[str, Any]]:
        """Вставить событие с готовой WKT-геометрией (random/пустой/нет матчей).

        strategy и geom известны в Python; matches всегда пуст. Делегирует
        общий INSERT-CTE (_INSERT_EVENT_SIMPLE) в _run_insert.
        """
        return await self._run_insert(
            _INSERT_EVENT_SIMPLE,
            (message_id, event_time, description, photo_path, layer, strategy, geom_wkt),
            message_id=message_id,
        )

    async def _insert_event_from_candidates(
        self, *, message_id: int, event_time: datetime, description: str,
        photo_path: Optional[str], layer: str, geo_ids: list,
        geo_scores: list, geo_texts: list, strategy: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Вставить событие, посчитав геометрию через process_candidates в том же
        запросе — один roundtrip к БД (раньше было SELECT + отдельный INSERT).
        Если strategy указан (от SemanticResolver), передаётся в SQL как p_strategy.
        """
        scores_array = [float(s) for s in geo_scores]
        return await self._run_insert(
            _INSERT_EVENT_FROM_CANDIDATES,
            (message_id, event_time, description, photo_path, layer,
             geo_ids, scores_array, geo_texts, strategy),
            message_id=message_id,
        )

    async def _run_insert(
        self, sql: str, params: tuple, *, message_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Выполнить INSERT-CTE (одним roundtrip'ом: INSERT + meta + pg_notify).

        Вставка идемпотентна (ON CONFLICT (message_id, event_time) DO NOTHING): при повторе
        RETURNING пуст → fetchrow вернёт None → это дубль или пустая геометрия,
        возвращаем None (не ошибка). layer/strategy читаются из вставленной
        строки — корректны для обеих веток (в т.ч. когда strategy пришла из
        process_candidates).
        """
        async with self.db_pool.acquire() as c:
            row = await c.fetchrow(sql, *params)
        if row is None:
            logger.debug(f"Message {message_id}: duplicate or no geometry, skipped")
            return None
        logger.debug(
            f"Message {message_id}: event {row['id']} saved "
            f"(layer={row['layer']}, strategy={row['strategy']})"
        )
        return {
            'event_id': row['id'],
            'layer': row['layer'],
            'strategy': row['strategy'],
        }

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
                    "geo_updated",
                    self._on_geo_updated
                )
                await self._listen_conn.close()
            except Exception as e:
                logger.debug(f"Error closing listen connection: {e}")

        if self.matcher:
            await self.matcher.close()

        if self.resolver:
            await self.resolver.close()

        if self.spacy_extractor:
            await self.spacy_extractor.close()

        logger.info("MessageProcessor closed")
