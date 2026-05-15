"""Similarity Search - поиск сущностей через sliding window + rapidfuzz.

Изолированный модуль для fuzzy matching улиц по тексту сообщения.
"""

import logging
from typing import Dict, List, Set, Optional

from rapidfuzz import fuzz, process

# Импорт настроек - обязательный (без fallback)
from .settings import settings
SIMILARITY_THRESHOLD = settings.similarity.entity_similarity_threshold

logger = logging.getLogger(__name__)

# Константы
MAX_ENTITIES = 5
MAX_CANDIDATES = 3


class SlidingWindowMatcher:
    """Поиск сущностей через sliding window + rapidfuzz."""

    def __init__(self):
        self._streets: Dict[int, List[str]] = {}
        self._all_names: List[str] = []
        self._name_to_id: Dict[str, int] = {}
        self._stopwords: Set[str] = set()
        self._initialized = False

    async def initialize(self, pg_pool) -> bool:
        """Загрузить улицы и стоп-слова из PostgreSQL."""
        try:
            logger.info("Loading streets from PostgreSQL...")

            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, names, geom FROM streets WHERE geom IS NOT NULL"
                )
                stopwords_rows = await conn.fetch("SELECT word FROM stopwords")
                self._stopwords = {row['word'].lower() for row in stopwords_rows}
                logger.info(f"Loaded {len(self._stopwords)} stopwords")

            for row in rows:
                street_id = row['id']
                names = row['names'] or []
                self._streets[street_id] = names
                for name in names:
                    name_lower = name.lower().strip()
                    if name_lower and name_lower not in self._name_to_id:
                        self._all_names.append(name_lower)
                        self._name_to_id[name_lower] = street_id

            self._initialized = True
            logger.info(f"✅ SlidingWindowMatcher: {len(self._streets)} streets, {len(self._all_names)} names, {len(self._stopwords)} stopwords")
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

    def find_entities(
        self,
        text: str,
        top_k: int = MAX_ENTITIES,
        threshold: float = SIMILARITY_THRESHOLD
    ) -> List[Dict]:
        """
        Находит сущности в тексте:
        1. Генерируем униграммы и биграммы из текста
        2. Для каждой n-gramмы ищем совпадения в базе улиц
        3. Все кандидаты собираются в общий пул без приоритета по типу
        4. Фильтруем по порогу threshold
        5. Ранжируем по score (descending)
        6. Дедупликация по street_id (оставляем лучший score)
        7. Возвращаем топ-K результатов
        """
        if not self._initialized:
            logger.warning("SlidingWindowMatcher not initialized")
            return []

        words = text.lower().split()
        if not words:
            return []

        # ЭТАП 1: Генерация всех n-gramm (униграммы + биграммы)
        ngrams = []

        # Униграммы
        for word in words:
            if len(word) >= 3 and word not in self._stopwords:
                ngrams.append((word, 'word'))

        # Биграммы
        if len(words) >= 2:
            bigrams = self._generate_ngrams(words, 2)
            for bigram in bigrams:
                bigram_words = bigram.split()
                if not any(w in self._stopwords for w in bigram_words):
                    ngrams.append((bigram, 'bigram'))

        # ЭТАП 2: Сбор всех кандидатов выше порога (общий пул)
        all_candidates = []

        for ngram_text, ngram_type in ngrams:
            matches = process.extract(
                ngram_text,
                self._all_names,
                scorer=fuzz.ratio,
                limit=MAX_CANDIDATES,
                score_cutoff=threshold * 100
            )

            for name, score, _ in matches:
                street_id = self._name_to_id.get(name)
                if street_id:
                    all_candidates.append({
                        'text': ngram_text,
                        'street_id': street_id,
                        'matched_name': name,
                        'score': score / 100.0,
                        'source': ngram_type
                    })

        # ЭТАП 3: Дедупликация по street_id — оставляем лучший score
        deduplicated = {}
        for candidate in all_candidates:
            sid = candidate['street_id']
            if sid not in deduplicated or candidate['score'] > deduplicated[sid]['score']:
                deduplicated[sid] = candidate

        # ЭТАП 4: Ранжирование по score (descending) и ограничение топ-K
        entities = sorted(deduplicated.values(), key=lambda x: x['score'], reverse=True)[:top_k]

        logger.debug(f"Found {len(entities)} entities: {[(e['text'], e['source'], e['score']) for e in entities]}")
        return entities

    async def close(self):
        self._streets.clear()
        self._all_names.clear()
        self._name_to_id.clear()
        self._stopwords.clear()
        self._initialized = False
        logger.info("SlidingWindowMatcher closed")


# Функция для быстрого вызова
async def create_matcher(pg_pool) -> Optional[SlidingWindowMatcher]:
    """Создать и инициализировать matcher."""
    matcher = SlidingWindowMatcher()
    if await matcher.initialize(pg_pool):
        return matcher
    return None