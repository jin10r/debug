"""Message Processor - обработка текста и сохранение событий.

Очищает текст от спецсимволов, определяет слой события,
находит сущности через sliding window + rapidfuzz, сохраняет в БД.
"""

import asyncio
import html
import json as json_lib
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List, Tuple, Set

import asyncpg
import pytz
from rapidfuzz import fuzz, process

# Импорт настроек из локального модуля
try:
    from .settings import settings, QuestionOverlayConfig
    DEFAULT_SIMILARITY_THRESHOLD = 0.67
except Exception as e:
    settings = None
    DEFAULT_SIMILARITY_THRESHOLD = 0.67

logger = logging.getLogger(__name__)

# Часовой пояс Kiev
KIEV_TZ = pytz.timezone('Europe/Kiev')

# Константы
MAX_ENTITIES = 5        # Максимум сущностей в тексте
MAX_CANDIDATES = 3     # Максимум кандидатов на сущность
WINDOW_SIZE = 2        # Скользящее окно - 2 слова

# Прилагательные, которые не являются названиями улиц
ADJECTIVES = {
    'красный', 'красная', 'красное', 'красные', 'красным', 'красной',
    'синий', 'синяя', 'синее', 'синие', 'синим', 'синей',
    'белый', 'белая', 'белое', 'белые', 'белым', 'белой',
    'старый', 'старая', 'старое', 'старые', 'старым', 'старой',
    'новый', 'новая', 'новое', 'новые', 'новым', 'новой',
    'черный', 'черная', 'черное', 'черные', 'черным', 'черной',
    'зеленый', 'зеленая', 'зеленое', 'зеленые', 'зеленым', 'зеленой',
    'серый', 'серая', 'серое', 'серые', 'серым', 'серой',
    'темный', 'темная', 'темное', 'темные', 'темным', 'темной',
    'светлый', 'светлая', 'светлое', 'светлые', 'светлым', 'светлой',
}


def _get_layer_keywords(layer: str) -> tuple:
    """Получить ключевые слова для слоя из settings."""
    if settings and hasattr(settings, 'similarity'):
        return settings.similarity.get_layer_keywords(layer)
    # Fallback
    if layer == 'cops':
        return ('коп', 'полиц', 'мусор', 'люстр', 'бп', 'блокпост', 'мигалк', 'патрул', 'б/п', 'пост')
    elif layer == 'bus':
        return ('бус', 'автобус', 'спринтер', 'рено', 'h1', 'h2', 'h3', 'h4', 'h5', 'фольц', 'хендай', 'вито')
    elif layer == 'traffic':
        return ('дтп', 'авар', 'пробк', 'затор', 'закрыт', 'перекрыт',
                'ремонт', 'реконструкц', 'стоянк', 'парковк', 'эвакуатор',
                'сбил', 'наезд', 'столкн', 'встречк', 'обочин')
    return ()


class SlidingWindowMatcher:
    """Поиск сущностей через sliding window + rapidfuzz."""

    def __init__(self):
        self._streets: Dict[int, List[str]] = {}  # street_id -> list of names
        self._all_names: List[str] = []           # все имена для fuzzy matching
        self._name_to_id: Dict[str, int] = {}    # name -> street_id
        self._stopwords: Set[str] = set()         # стоп-слова для игнорирования
        self._initialized = False

    async def initialize(self, pg_pool) -> bool:
        """Загрузить улицы из PostgreSQL."""
        try:
            logger.info("Loading streets from PostgreSQL...")

            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, names, geom FROM streets WHERE geom IS NOT NULL"
                )

                # Загружаем стоп-слова
                stopwords_rows = await conn.fetch("SELECT word FROM stopwords")
                self._stopwords = {row['word'].lower() for row in stopwords_rows}
                logger.info(f"Loaded {len(self._stopwords)} stopwords")

            for row in rows:
                street_id = row['id']
                names = row['names'] or []
                self._streets[street_id] = names

                # Добавляем все имена в общий список
                for name in names:
                    name_lower = name.lower().strip()
                    if name_lower and name_lower not in self._name_to_id:
                        self._all_names.append(name_lower)
                        self._name_to_id[name_lower] = street_id

            self._initialized = True
            logger.info(f"✅ SlidingWindowMatcher initialized: {len(self._streets)} streets, {len(self._all_names)} names, {len(self._stopwords)} stopwords")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize SlidingWindowMatcher: {e}")
            return False

    def _generate_ngrams(self, words: List[str], size: int) -> List[str]:
        """Генерирует n-grams заданного размера."""
        ngrams = []
        for i in range(len(words) - size + 1):
            ngram = ' '.join(words[i:i + size])
            ngrams.append(ngram)
        return ngrams

    def find_entities(self, text: str, top_k: int = MAX_ENTITIES,
                      threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> List[Dict]:
        """
        Находит сущности в тексте:
        1. Сначала проверяем пары слов (2 слова) - больший приоритет
        2. Затем проверяем одиночные слова
        3. Всего не более 5 сущностей, для каждой до 3 кандидатов
        """
        if not self._initialized:
            logger.warning("SlidingWindowMatcher not initialized")
            return []

        # Разбиваем на слова
        words = text.lower().split()

        if not words:
            return []

        entities = []
        seen_street_ids: Set[int] = set()

        # ЭТАП 1: Проверяем пары слов (2 слова) - больший приоритет
        if len(words) >= 2:
            bigrams = self._generate_ngrams(words, 2)
            logger.debug(f"Checking {len(bigrams)} bigrams...")

            for bigram in bigrams:
                if len(entities) >= top_k:
                    break

                # Пропускаем bigram если содержит стоп-слова
                bigram_words = bigram.split()
                if any(w in self._stopwords for w in bigram_words):
                    continue

                matches = process.extract(
                    bigram,
                    self._all_names,
                    scorer=fuzz.ratio,
                    limit=MAX_CANDIDATES,
                    score_cutoff=threshold * 100
                )

                if matches:
                    name, score, _ = matches[0]
                    street_id = self._name_to_id.get(name)

                    if street_id and street_id not in seen_street_ids:
                        # ПРОВЕРКА OVERLAP: слова из name должны присутствовать в тексте
                        name_words = set(name.lower().split())
                        text_words = set(words)
                        
                        overlap = name_words & text_words
                        overlap_ratio = len(overlap) / len(name_words) if name_words else 0
                        
                        # Отбрасываем совпадения без overlap (минимум 50% слов)
                        if overlap_ratio < 0.5:
                            logger.debug(f"Skipping bigram '{name}': no overlap with text (ratio={overlap_ratio:.2f})")
                        else:
                            seen_street_ids.add(street_id)
                            entities.append({
                                'text': bigram,
                                'street_id': street_id,
                                'matched_name': name,
                                'score': score / 100.0,
                                'source': 'bigram',
                                'overlap_ratio': overlap_ratio,
                                'candidates': [
                                    {'name': m[0], 'score': m[1] / 100.0}
                                    for m in matches
                                ]
                            })

        # ЭТАП 2: Проверяем одиночные слова (если ещё есть место)
        if len(entities) < top_k:
            for word in words:
                if len(entities) >= top_k:
                    break

                # Пропускаем короткие слова (< 4 символов)
                if len(word) < 4:
                    continue

                # Пропускаем стоп-слова
                if word in self._stopwords:
                    continue
                
                # Пропускаем прилагательные (не являются названиями улиц)
                if word in ADJECTIVES:
                    logger.debug(f"Skipping adjective: {word}")
                    continue

                matches = process.extract(
                    word,
                    self._all_names,
                    scorer=fuzz.ratio,
                    limit=MAX_CANDIDATES,
                    score_cutoff=threshold * 100
                )

                if matches:
                    name, score, _ = matches[0]
                    street_id = self._name_to_id.get(name)

                    if street_id and street_id not in seen_street_ids:
                        # ПРОВЕРКА OVERLAP: слово должно присутствовать в тексте
                        name_words = set(name.lower().split())
                        text_words = set(words)
                        
                        overlap = name_words & text_words
                        overlap_ratio = len(overlap) / len(name_words) if name_words else 0
                        
                        # Отбрасываем совпадения без overlap (минимум 50% слов)
                        if overlap_ratio < 0.5:
                            logger.debug(f"Skipping unigram '{name}': no overlap with text (ratio={overlap_ratio:.2f})")
                        else:
                            seen_street_ids.add(street_id)
                            entities.append({
                                'text': word,
                                'street_id': street_id,
                                'matched_name': name,
                                'score': score / 100.0,
                                'source': 'word',
                                'overlap_ratio': overlap_ratio,
                                'candidates': [
                                    {'name': m[0], 'score': m[1] / 100.0}
                                    for m in matches
                                ]
                            })

        # Сортируем: bigramы first
        entities.sort(key=lambda x: (0 if x['source'] == 'bigram' else 1))

        logger.debug(f"Found {len(entities)} entities: {[(e['text'], e['source']) for e in entities]}")
        return entities

    async def close(self):
        self._streets.clear()
        self._all_names.clear()
        self._name_to_id.clear()
        self._initialized = False
        logger.info("SlidingWindowMatcher closed")


class MessageProcessor:
    """Процессор сообщений Telegram.

    Отвечает за:
    - Очистку текста
    - Поиск сущностей через sliding window + rapidfuzz
    - Определение слоя
    - Сохранение событий в PostgreSQL
    """

    MAX_TEXT_LENGTH = 370

    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool
        # Sliding window + rapidfuzz matcher
        self.matcher = SlidingWindowMatcher()
        self._pg_notify_task: Optional[asyncio.Task] = None
        self._listen_conn: Optional[asyncpg.Connection] = None

    async def initialize(self) -> bool:
        """Инициализация при старте."""
        try:
            logger.info(f"✅ Using similarity threshold: {DEFAULT_SIMILARITY_THRESHOLD}")
            logger.info(f"✅ Using sliding window size: {WINDOW_SIZE}")

            # 1. Инициализация SlidingWindowMatcher
            logger.info("Initializing SlidingWindowMatcher...")
            success = await self.matcher.initialize(self.db_pool)
            if not success:
                logger.error("SlidingWindowMatcher initialization failed")
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

    def _on_streets_updated(self, conn: asyncpg.Connection, pid: int, channel: str, payload: str):
        """Callback при уведомлении об изменении улиц."""
        logger.info(f"🔄 Received streets_updated notification, reinitializing matcher...")
        asyncio.create_task(self.matcher.initialize(self.db_pool))

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
            # Sliding window + rapidfuzz поиск
            logger.info(f"Message {message_id}: Using sliding window + rapidfuzz...")

            entities = self.matcher.find_entities(
                cleaned,
                top_k=MAX_ENTITIES,
                threshold=DEFAULT_SIMILARITY_THRESHOLD
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
                # Найдены улицы - используем лучший match
                async with self.db_pool.acquire() as conn:
                    scores_array = [float(s) for s in street_scores]

                    result = await conn.fetch(
                        "SELECT * FROM process_location_smart($1::timestamptz, $2::text, $3::text, $4::text, $5::int[], $6::double precision[])",
                        kiev_time, text, layer, photo_path, street_ids, scores_array
                    )
                    row = result[0] if result else None

                    if row:
                        return {
                            'event_id': row['event_id'],
                            'layer': row['result_layer'],
                            'strategy': row['result_strategy'],
                            'geom': row['result_geom'],
                            'matches': row['result_matches'],
                            'entity_matches': [
                                {'id': sid, 'score': ss}
                                for sid, ss in zip(street_ids, street_scores)
                            ]
                        }

                    logger.warning(f"Message {message_id}: process_location_smart returned None")
                    return None

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

                await conn.execute(
                    "SELECT pg_notify('events_new', $1::text)",
                    json_lib.dumps({
                        'type': 'Feature',
                        'geometry': {'type': 'Point', 'coordinates': [float(geom.split('(')[1].split(')')[0].split()[0]), float(geom.split('(')[1].split(')')[0].split()[1])]},
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
                    if word.startswith(keyword):
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