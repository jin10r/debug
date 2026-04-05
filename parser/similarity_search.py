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
        1. Проверяем пары слов (2 слова) и одиночные слова
        2. Фильтруем по единому порогу threshold
        3. Ранжируем по score (descending)
        4. Дедупликация по street_id
        5. Возвращаем топ-K результатов
        """
        if not self._initialized:
            logger.warning("SlidingWindowMatcher not initialized")
            return []

        words = text.lower().split()
        if not words:
            return []

        # ЭТАП 1: Сбор всех кандидатов выше порога
        all_candidates = []
        seen_street_ids: Set[int] = set()

        # Проверяем пары слов (2 слова)
        if len(words) >= 2:
            bigrams = self._generate_ngrams(words, 2)

            for bigram in bigrams:
                # Пропускаем bigram с стоп-словами
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
                    for name, score, _ in matches:
                        street_id = self._name_to_id.get(name)
                        if street_id and street_id not in seen_street_ids:
                            seen_street_ids.add(street_id)
                            all_candidates.append({
                                'text': bigram,
                                'street_id': street_id,
                                'matched_name': name,
                                'score': score / 100.0,
                                'source': 'bigram'
                            })

        # Проверяем одиночные слова (если не набрали top_k из bigrams)
        remaining = top_k * 2  # запас для слов
        for word in words:
            if len(word) < 3:
                continue
            if word in self._stopwords:
                continue

            matches = process.extract(
                word,
                self._all_names,
                scorer=fuzz.ratio,
                limit=MAX_CANDIDATES,
                score_cutoff=threshold * 100
            )

            if matches:
                for name, score, _ in matches:
                    street_id = self._name_to_id.get(name)
                    if street_id and street_id not in seen_street_ids:
                        seen_street_ids.add(street_id)
                        all_candidates.append({
                            'text': word,
                            'street_id': street_id,
                            'matched_name': name,
                            'score': score / 100.0,
                            'source': 'word'
                        })

        # ЭТАП 2: Ранжирование по score (descending)
        all_candidates.sort(key=lambda x: x['score'], reverse=True)

        # ЭТАП 3: Дедупликация по street_id (уже выполнена через seen_street_ids)

        # ЭТАП 4: Ограничение топ-K
        entities = all_candidates[:top_k]

        # ЭТАП 5: Сортировка - bigrams имеют приоритет
        entities.sort(key=lambda x: (0 if x['source'] == 'bigram' else 1, -x['score']))

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