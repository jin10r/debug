"""Message Processor — обработка текста и сохранение событий.

Конвейер на сообщение:
  strip_tail → clean (text_preprocessor) → определение слоя (layer_classifier)
  → при пустом/длинном тексте strategy='random', иначе лексический поиск улиц
  (lexical_matcher) → стратегия геометрии (process_candidates) → INSERT.

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
from .lexical_matcher import LexicalMatcher, SIMILARITY_THRESHOLD, MAX_ENTITIES
from .text_preprocessor import MAX_TEXT_LENGTH, clean, strip_tail

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Процессор сообщений Telegram: предобработка, классификация, сохранение."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool
        self.matcher = LexicalMatcher()
        # LayerClassifier переиспользует MorphAnalyzer из LexicalMatcher —
        # один экземпляр анализатора на процесс (экономия ~15-20 МБ RAM).
        self.layer_classifier = LayerClassifier(self.matcher.morph)
        self._listen_conn: Optional[asyncpg.Connection] = None

    async def initialize(self) -> bool:
        """Инициализация при старте."""
        try:
            logger.info(f"✅ Using lexical similarity threshold: {SIMILARITY_THRESHOLD}")

            # 1. Инициализация LexicalMatcher
            logger.info("Initializing LexicalMatcher...")
            success = await self.matcher.initialize(self.db_pool)
            if not success:
                logger.error("LexicalMatcher initialization failed")
                return False

            # 2. Индексация улиц (лемматизация aliases)
            logger.info("Indexing street aliases...")
            indexed = await self.matcher.reindex_all(self.db_pool)
            logger.info(f"✅ Indexed {indexed} aliases")

            # 3. Подписка на уведомления от PostgreSQL
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

        # Предобработка: отбрасывание хвоста → очистка → удаление одиночных суррогатов.
        cleaned = self._sanitize_text(clean(strip_tail(raw_text)))

        # Определение слоя — над очищенным текстом, до проверки длины.
        layer = self.layer_classifier.classify(cleaned)

        # Пустой или слишком длинный текст — не релевантная локация:
        # поиск улиц пропускается, событию назначается случайная точка.
        if not cleaned or len(cleaned) > MAX_TEXT_LENGTH:
            if not cleaned:
                description = 'без описания'
                logger.info(f"Message {message_id}: empty text → random point")
            else:
                description = 'слишком длинное сообщение не является релевантной локацией'
                logger.warning(
                    f"Message {message_id}: text too long ({len(cleaned)}) → random point"
                )
            return await self._insert_event(
                message_id=message_id, event_time=event_time, description=description,
                photo_path=photo_path, layer=layer, strategy='random',
                geom_wkt=self._generate_random_point_in_question_overlay(),
            )

        # Лексический поиск улиц.
        logger.info(f"Message {message_id}: lexical search (mawo_pymorphy3 + rapidfuzz)...")
        entities = await self.matcher.async_find_entities(
            cleaned, top_k=MAX_ENTITIES, threshold=SIMILARITY_THRESHOLD, pg_pool=self.db_pool,
        )

        street_ids: list = []
        street_scores: list = []
        for ent in entities:
            if ent['street_id'] not in street_ids:
                street_ids.append(ent['street_id'])
                street_scores.append(ent['score'])

        # Улиц не нашлось — случайная точка.
        if not street_ids:
            logger.info(f"Message {message_id}: no street matches → random point")
            return await self._insert_event(
                message_id=message_id, event_time=event_time, description=cleaned,
                photo_path=photo_path, layer=layer, strategy='random',
                geom_wkt=self._generate_random_point_in_question_overlay(),
            )

        # Улицы найдены — геометрия и стратегия через process_candidates (псевдо-радиус 150 м).
        logger.info(f"Message {message_id}: {len(street_ids)} streets matched: {street_ids}")
        async with self.db_pool.acquire() as conn:
            scores_array = [float(s) for s in street_scores]
            pc_rows = await conn.fetch(
                """
                SELECT result_strategy,
                       result_matches,
                       ST_AsText(result_geom) AS geom_wkt
                FROM process_candidates($1::int[], $2::double precision[], $3::float)
                """,
                street_ids, scores_array, 150.0,
            )
            if not pc_rows or pc_rows[0]['geom_wkt'] is None:
                logger.warning(f"Message {message_id}: process_candidates returned no geometry")
                return None

            pc = pc_rows[0]
            return await self._insert_event(
                message_id=message_id, event_time=event_time, description=cleaned,
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
                    'geometry': {'type': 'Point', 'coordinates': [row['lng'], row['lat']]},
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
