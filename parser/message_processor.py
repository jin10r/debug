"""Message Processor - обработка текста и сохранение событий.

Очищает текст от спецсимволов, определяет слой события,
находит сущности через лексический поиск (mawo_pymorphy3 + rapidfuzz),
сохраняет в БД.
"""

import asyncio
import html
import json as json_lib
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List

import asyncpg
import pytz

# Импорт модуля поиска сущностей — семантический поиск
from .lexical_matcher import LexicalMatcher, SIMILARITY_THRESHOLD, MAX_ENTITIES

# Импорт настроек из локального модуля
try:
    from .settings import settings, QuestionOverlayConfig
except Exception as e:
    settings = None

logger = logging.getLogger(__name__)

# Часовой пояс Kiev
KIEV_TZ = pytz.timezone('Europe/Kiev')


def _get_layer_keywords(layer: str) -> tuple:
    """Получить ключевые слова для слоя из settings."""
    if settings and hasattr(settings, 'similarity'):
        return settings.similarity.get_layer_keywords(layer)
    # Fallback
    if layer == 'cops':
        return ('коп', 'полиц', 'мусор', 'люстр', 'бп', 'блокпост', 'мигалк', 'патрул', 'б/п', 'пост')
    elif layer == 'bus':
        return ('бус', 'хайс', 'автобус', 'спринтер', 'рено', 'h1', 'h2', 'h3', 'h4', 'h5', 'фольц', 'хендай', 'вито', 'сталкер', 'транспортёр')
    elif layer == 'traffic':
        return ('дтп', 'авар', 'пробк', 'затор', 'светофор')
    return ()


class MessageProcessor:
    """Процессор сообщений Telegram.

    Отвечает за:
    - Очистку текста
    - Поиск сущностей через лексический поиск (mawo_pymorphy3 + rapidfuzz)
    - Определение слоя
    - Сохранение событий в PostgreSQL
    """

    MAX_TEXT_LENGTH = 370

    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool
        self.matcher = LexicalMatcher()
        self._pg_notify_task: Optional[asyncio.Task] = None
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

    async def _on_streets_updated(self, conn: asyncpg.Connection, pid: int, channel: str, payload: str):
        """Callback при уведомлении об изменении улиц."""
        logger.info(f"🔄 Received streets_updated notification, reindexing street...")
        # payload содержит street_id в формате JSON
        import json
        
        async def _reindex_async_wrapper(func, *args):
            """Обертка для безопасного выполнения асинхронных задач."""
            try:
                await func(*args)
                logger.info("✅ Reindexing completed successfully")
            except Exception as e:
                logger.error(f"❌ Reindexing failed: {e}")
        
        try:
            # Использовать асинхронный executor для CPU-bound парсинга JSON
            from concurrent.futures import ThreadPoolExecutor
            loop = asyncio.get_running_loop()
            
            with ThreadPoolExecutor() as executor:
                data = await loop.run_in_executor(executor, json.loads, payload)
                street_id = data.get('street_id')
                
                if street_id:
                    asyncio.create_task(_reindex_async_wrapper(
                        self.matcher.reindex_street, self.db_pool, street_id
                    ))
                else:
                    # Если street_id не указан, переиндексируем все
                    asyncio.create_task(_reindex_async_wrapper(
                        self.matcher.reindex_all, self.db_pool
                    ))
        except Exception as e:
            logger.error(f"Failed to parse streets_updated payload: {e}")
            # При ошибке парсинга переиндексируем все улицы
            asyncio.create_task(_reindex_async_wrapper(
                self.matcher.reindex_all, self.db_pool
            ))

    async def process_message(self, msg_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Обработка сообщения из Telegram."""
        message_id = msg_data.get('message_id', 0)
        photo_path = msg_data.get('photo_path')

        # 1. Конвертация времени UTC → Kiev
        utc_time = msg_data.get('event_time', datetime.now(timezone.utc))
        kiev_time = utc_time.astimezone(KIEV_TZ)

        # 2. Валидация текста
        text = msg_data.get('text', '')
        layer = 'pig'
        street_ids: List[int] = []
        street_scores: List[float] = []
        geom = None
        strategy = None

        # Очищаем текст
        plain = self.extract_plain_text(text)
        truncated = self.truncate_at_pipe(plain)
        cleaned = self.clean_text(truncated)

        if not text or len(text.strip()) == 0:
            text = "без описания"
            logger.info(f"Message {message_id}: empty text → using placeholder")
            geom = self._generate_random_point_in_question_overlay()
            strategy = 'random'

        elif len(text) > self.MAX_TEXT_LENGTH:
            text = "слишком длинное сообщение не является релевантной локацией"
            logger.warning(f"Message {message_id}: text too long ({len(text)} chars) → using placeholder")
            geom = self._generate_random_point_in_question_overlay()
            strategy = 'random'

        else:
            # Лексический поиск сущностей
            logger.info(f"Message {message_id}: Using lexical search (mawo_pymorphy3 + rapidfuzz)...")

            entities = await self.matcher.async_find_entities(
                cleaned,
                top_k=MAX_ENTITIES,
                threshold=SIMILARITY_THRESHOLD,
                pg_pool=self.db_pool,
            )

            if entities:
                logger.info(f"   - Found {len(entities)} entities: {[e['text'] for e in entities]}")

                for ent in entities:
                    if ent['street_id'] not in street_ids:
                        street_ids.append(ent['street_id'])
                        street_scores.append(ent['score'])

            # Определение слоя
            layer_from_keywords = self.detect_layer(text)
            layer = layer_from_keywords or 'pig'

            logger.debug(f"Message {message_id}: layer={layer}, street_ids={len(street_ids)}")

            if len(street_ids) == 0:
                # Нет сходств с улицами
                logger.info(f"Message {message_id}: no entity matches → using random point")
                geom = self._generate_random_point_in_question_overlay()
                strategy = 'random'

                # Вставляем событие с random точкой
                async with self.db_pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO events (event_time, description, photo_url, layer, strategy, geom, matches)
                        VALUES ($1, $2, $3, $4, $5, ST_GeomFromText($6, 4326), '[]'::jsonb)
                        RETURNING id
                        """,
                        kiev_time, cleaned, photo_path, layer, strategy, geom
                    )
                    return {
                        'event_id': row['id'],
                        'layer': layer,
                        'strategy': strategy,
                        'geom': geom,
                        'matches': [],
                        'entity_matches': []
                    }
            else:
                # Найдены улицы → geometry via process_candidates (150m pseudo-radius)
                async with self.db_pool.acquire() as conn:
                    scores_array = [float(s) for s in street_scores]

                    pc_rows = await conn.fetch(
                        """
                        SELECT
                            result_strategy,
                            result_matches,
                            ST_AsText(result_geom)           AS geom_wkt,
                            ST_X(ST_Centroid(result_geom))   AS centroid_lng,
                            ST_Y(ST_Centroid(result_geom))   AS centroid_lat
                        FROM process_candidates($1::int[], $2::double precision[], $3::float)
                        """,
                        street_ids, scores_array, 150.0,
                    )

                    if not pc_rows or pc_rows[0]['geom_wkt'] is None:
                        logger.warning(f"Message {message_id}: process_candidates returned no geometry")
                        return None

                    pc = pc_rows[0]
                    v_strategy  = pc['result_strategy']
                    v_geom_wkt  = pc['geom_wkt']
                    v_matches   = pc['result_matches']
                    centroid_lng = pc['centroid_lng']
                    centroid_lat = pc['centroid_lat']

                    logger.info(
                        f"Message {message_id}: strategy={v_strategy}, "
                        f"streets={street_ids}, centroid=({centroid_lng:.4f},{centroid_lat:.4f})"
                    )

                    # INSERT события
                    event_row = await conn.fetchrow(
                        """
                        INSERT INTO events
                            (event_time, description, photo_url, layer, strategy, geom, matches)
                        VALUES ($1, $2, $3, $4, $5, ST_GeomFromText($6, 4326), $7)
                        RETURNING id
                        """,
                        kiev_time, cleaned, photo_path, layer,
                        v_strategy, v_geom_wkt, v_matches,
                    )

                    # Обновляем events_meta для WebSocket-синхронизации
                    await conn.execute(
                        "UPDATE events_meta SET version = version + 1, updated_at = now(), max_event_id = $1 WHERE id = 1",
                        event_row['id'],
                    )

                    # pg_notify: отправляем центроид как Point (WebSocket клиенты)
                    await conn.execute(
                        "SELECT pg_notify('events_new', $1::text)",
                        json_lib.dumps({
                            'type': 'Feature',
                            'geometry': {
                                'type': 'Point',
                                'coordinates': [centroid_lng, centroid_lat],
                            },
                            'properties': {
                                'id': event_row['id'],
                                'layer': layer,
                                'strategy': v_strategy,
                                'description': cleaned,
                                'time': kiev_time.isoformat(),
                            },
                        }),
                    )

                    return {
                        'event_id': event_row['id'],
                        'layer': layer,
                        'strategy': v_strategy,
                        'geom': v_geom_wkt,
                        'matches': v_matches,
                        'entity_matches': [
                            {'id': sid, 'score': ss}
                            for sid, ss in zip(street_ids, street_scores)
                        ],
                    }

        # Для random - упрощённая вставка
        if strategy == 'random':
            if self.db_pool is None:
                logger.error(f"Message {message_id}: db_pool not initialized")
                return None

            logger.info(f"Message {message_id}: inserting random event with strategy='{strategy}', geom='{geom}'")

            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO events (event_time, description, photo_url, layer, strategy, geom, matches)
                    VALUES ($1, $2, $3, $4, $5, ST_GeomFromText($6, 4326), '[]'::jsonb)
                    RETURNING id
                    """,
                    kiev_time, cleaned, photo_path, layer, strategy, geom
                )

                await conn.execute(
                    "UPDATE events_meta SET version = version + 1, updated_at = now(), max_event_id = $1 WHERE id = 1",
                    row['id']
                )

                # Безопасно извлекаем координаты из WKT геометрии
                coords = self._safe_parse_wkt_point(geom)
                if coords is None:
                    logger.warning(f"Failed to parse geometry: {geom}")
                    coords = [0, 0]  # Fallback coordinates
                    
                await conn.execute(
                    "SELECT pg_notify('events_new', $1::text)",
                    json_lib.dumps({
                        'type': 'Feature',
                        'geometry': {'type': 'Point', 'coordinates': list(coords)},
                        'properties': {
                            'id': row['id'],
                            'layer': layer,
                            'strategy': strategy,
                            'description': text,
                            'time': kiev_time.isoformat()
                        }
                    })
                )

                return {
                    'event_id': row['id'],
                    'layer': layer,
                    'strategy': strategy,
                    'geom': geom,
                    'matches': [],
                    'entity_matches': []
                }

        return None

    @staticmethod
    def extract_plain_text(text_value) -> str:
        """Извлечь чистый текст, удалив HTML теги и декодировав entities."""
        if text_value is None:
            return ""

        plain_text = html.unescape(str(text_value))
        plain_text = re.sub(r'<[^>]+>', '', plain_text)

        return plain_text.strip()

    @staticmethod
    def truncate_at_pipe(text: str) -> str:
        """Обрезать текст по маркеру 'Сообщить' (case-insensitive)."""
        if not text:
            return ""
        
        # Case-insensitive поиск маркера
        text_lower = text.lower()
        marker = 'сообщить'
        
        if marker in text_lower:
            idx = text_lower.index(marker)
            return text[:idx].strip()
        
        return text.strip()

    @staticmethod
    def clean_text(text: str) -> str:
        """Очистить текст: заменить все спецсимволы и пунктуацию на пробелы."""
        if not text:
            return ""

        # Заменяем все не-буквенные и не-цифровые символы на пробелы
        cleaned = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9]', ' ', text)

        # Украинские → русские буквы
        cleaned = cleaned.replace('і', 'и').replace('ї', 'и').replace('є', 'е')

        # Убираем множественные пробелы
        cleaned = re.sub(r'\s+', ' ', cleaned)

        return cleaned.strip().lower()

    @staticmethod
    def detect_layer(text: str) -> Optional[str]:
        """Определить слой события по ключевым словам."""
        if not text:
            return None

        text_lower = text.lower()
        words = text_lower.split()

        layers = ['bus', 'cops', 'traffic']
        for layer in layers:
            keywords = _get_layer_keywords(layer)
            for word in words:
                for keyword in keywords:
                    # Регистронезависимое сравнение
                    if word.startswith(keyword.lower()):
                        logger.debug(f"Detected layer '{layer}' by keyword '{keyword}'")
                        return layer

        return None

    def is_too_long(self, text: str) -> bool:
        """Проверить, не превышает ли текст допустимую длину."""
        return len(text) > self.MAX_TEXT_LENGTH

    def _generate_random_point_in_question_overlay(self) -> str:
        """Сгенерировать случайную точку в круге question_overlay."""
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

    def _safe_parse_wkt_point(self, wkt: str) -> Optional[tuple]:
        """Безопасное извлечение координат из WKT POINT."""
        import re
        try:
            match = re.search(r'POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)', wkt)
            if match:
                lon = float(match.group(1))
                lat = float(match.group(2))
                if -180 <= lon <= 180 and -90 <= lat <= 90:
                    return lon, lat
            return None
        except Exception as e:
            logger.warning(f"Invalid WKT format: {wkt}, error: {e}")
            return None

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