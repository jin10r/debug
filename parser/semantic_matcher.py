"""Semantic Matcher — поиск сущностей через векторные эмбеддинги + pgvector.

Использует rubert-tiny2 ONNX + PostgreSQL pgvector для семантического поиска улиц.
"""

import re
import logging
from typing import Dict, List, Optional, Set

import asyncpg
import numpy as np

from .embedder import RuBertEmbedder, EMBEDDING_DIM
from .settings import settings

_DEFAULT_THRESHOLD = 0.67
SIMILARITY_THRESHOLD = (
    settings.similarity.entity_similarity_threshold
    if settings and settings.similarity
    else _DEFAULT_THRESHOLD
)
MAX_ENTITIES = 5
MAX_CANDIDATES_PER_NGRAM = 5  # Увеличено для украинских словоформ

logger = logging.getLogger(__name__)


class SemanticMatcher:
    """Поиск сущностей через семантические эмбеддинги + pgvector.
    
    Логика:
    1. Генерация n-грамм (униграммы + биграммы) из текста
    2. Кодирование каждой n-граммы в вектор через rubert-tiny2 ONNX
    3. Поиск ближайших соседей в PostgreSQL через pgvector
    4. Дедупликация по street_id, возврат топ-K результатов
    """

    def __init__(self):
        self._embedder = RuBertEmbedder()
        self._stopwords: Set[str] = set()
        self._street_names: List[str] = []  # Все названия улиц для локального поиска
        self._street_id_map: Dict[str, int] = {}  # name -> street_id
        self._street_id_to_names: Dict[int, List[str]] = {}  # street_id -> [names]
        self._initialized = False

    async def initialize(self, pg_pool) -> bool:
        """Инициализация: загрузка эмбеддера, стоп-слов и названий улиц.
        
        Args:
            pg_pool: Пул соединений PostgreSQL
            
        Returns:
            True если инициализация успешна
        """
        try:
            # 0. Проверка и создание street_embeddings таблицы если не существует
            logger.info("Ensuring street_embeddings table exists...")
            async with pg_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS street_embeddings (
                        street_id INTEGER NOT NULL REFERENCES streets(id) ON DELETE CASCADE,
                        name TEXT NOT NULL,
                        embedding vector(312),
                        PRIMARY KEY (street_id, name)
                    );
                    
                    CREATE INDEX IF NOT EXISTS idx_street_embeddings_embedding
                        ON street_embeddings USING hnsw (embedding vector_cosine_ops)
                        WITH (M = 16, ef_construction = 64);
                """)
                logger.info("✅ street_embeddings table verified")
                
                # Check if process_location_smart function exists
                func_exists = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_proc 
                        WHERE proname = 'process_location_smart'
                    )
                """)
                if not func_exists:
                    logger.warning("⚠️ process_location_smart function missing - events will use random strategy")
                else:
                    logger.info("✅ process_location_smart function verified")

            # 1. Загрузка ONNX эмбеддера
            logger.info("Loading RuBertEmbedder...")
            embedder_success = await self._embedder.initialize()
            if not embedder_success:
                logger.error("Failed to initialize RuBertEmbedder")
                return False

            # 2. Загрузка стоп-слов и названий улиц из БД
            logger.info("Loading stopwords and street names from PostgreSQL...")
            async with pg_pool.acquire() as conn:
                # Стоп-слова
                stopwords_rows = await conn.fetch("SELECT word FROM stopwords")
                self._stopwords = {row['word'].lower() for row in stopwords_rows}
                logger.info(f"Loaded {len(self._stopwords)} stopwords")

                # Улицы с названиями
                streets_rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )
                
                for row in streets_rows:
                    street_id = row['id']
                    names = row['names'] or []
                    self._street_id_to_names[street_id] = names
                    
                    for name in names:
                        name_lower = name.lower().strip()
                        if name_lower and name_lower not in self._street_id_map:
                            self._street_names.append(name_lower)
                            self._street_id_map[name_lower] = street_id

            logger.info(
                f"✅ SemanticMatcher initialized: "
                f"{len(self._street_id_to_names)} streets, "
                f"{len(self._street_names)} names"
            )
            
            # Очистка локальных структур для экономии RAM (данные в pgvector)
            self._street_names.clear()
            self._street_id_map.clear()
            logger.debug("Cleared local street name caches (using pgvector)")
            
            self._initialized = True
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize SemanticMatcher: {e}")
            return False

    def _generate_ngrams(self, words: List[str]) -> List[tuple]:
        """Генерирует униграммы и биграммы из текста.
        
        Исключает:
        - Стоп-слова
        - Слова < 3 символов
        - Однозначные числа (1-9) — чтобы не было ложных совпадений
          с номерами в названиях ("2" → "Гимназия 2")
        """
        ngrams = []
        
        # Униграммы
        for word in words:
            if len(word) >= 3 and word not in self._stopwords:
                # Пропускаем однозначные числа
                if word.isdigit() and len(word) == 1:
                    continue
                ngrams.append((word, 'word'))

        # Биграммы
        if len(words) >= 2:
            for i in range(len(words) - 1):
                bigram = ' '.join(words[i:i + 2])
                bigram_words = bigram.split()
                if not any(w in self._stopwords for w in bigram_words):
                    # Пропускаем биграммы, где одно из слов — однозначное число
                    if any(w.isdigit() and len(w) == 1 for w in bigram_words):
                        continue
                    ngrams.append((bigram, 'bigram'))

        return ngrams

    async def _search_nearest(self, pg_pool, embedding: np.ndarray, limit: int = MAX_CANDIDATES_PER_NGRAM) -> List[Dict]:
        """Поиск ближайших соседей через pgvector.

        Ищет по ВСЕМ embeddings названий улиц (каждое название — отдельный вектор).
        Дедупликация по street_id происходит на уровне вызывающего кода.

        Args:
            pg_pool: Пул соединений PostgreSQL
            embedding: Вектор-кандидат формы (312,)
            limit: Количество кандидатов

        Returns:
            Список кандидатов с street_id, matched_name и score
        """
        # Преобразуем numpy array в строку для PostgreSQL vector
        # Формат: '[val1,val2,val3,...]'
        embedding_str = '[' + ','.join(f'{v:.6f}' for v in embedding.tolist()) + ']'

        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT se.street_id, se.name, s.names,
                       1 - (se.embedding <=> $1::vector) AS similarity
                FROM street_embeddings se
                JOIN streets s ON s.id = se.street_id
                WHERE se.embedding IS NOT NULL
                ORDER BY se.embedding <=> $1::vector
                LIMIT $2
                """,
                embedding_str,
                limit,
            )

        candidates = []
        for row in rows:
            street_id = row['street_id']
            matched_name = row['name']
            similarity = float(row['similarity'])

            candidates.append({
                'street_id': street_id,
                'matched_name': matched_name,
                'score': similarity,
            })

        return candidates

    def _calculate_lexical_score(
        self,
        input_text: str,
        candidate_name: str
    ) -> float:
        """Расчет лексического сходства между текстом запроса и названием улицы.
        
        Args:
            input_text: Исходный текст поиска
            candidate_name: Название кандидата
            
        Returns:
            Лексический score от 0 до 1
        """
        input_words = set(input_text.lower().split())
        candidate_words = set(candidate_name.lower().split())
        
        # Точное совпадение слов
        if input_words.issubset(candidate_words) or candidate_words.issubset(input_words):
            return 0.9  # Очень высокий бонус за вхождение одних слов в другие
        
        # Частичное совпадение
        intersection = input_words & candidate_words
        if intersection:
            return len(intersection) / max(len(input_words), len(candidate_words)) * 0.6
        
        # Сходство подстрок (для сокращений и алиасов)
        input_lower = input_text.lower()
        candidate_lower = candidate_name.lower()
        
        if input_lower in candidate_lower or candidate_lower in input_lower:
            return 0.7  # Один текст содержится в другом
        
        # Сходство с учетом общих префиксов/суффиксов
        common_prefix_len = 0
        for i in range(min(len(input_lower), len(candidate_lower))):
            if input_lower[i] == candidate_lower[i]:
                common_prefix_len += 1
            else:
                break
        
        if common_prefix_len >= 3:  # Общий префикс из 3+ символов
            return common_prefix_len / max(len(input_lower), len(candidate_lower)) * 0.5
        
        return 0.0
    
    async def _get_street_geometries(self, pg_pool, street_ids: List[int]) -> Dict[int, str]:
        """Получение геометрий улиц по их IDs для расчета расстояний."""
        if not street_ids:
            return {}
            
        placeholders = ','.join([f'${i+1}' for i in range(len(street_ids))])
        query = f"""
        SELECT id as street_id, ST_AsText(geom) as geometry_wkt
        FROM streets 
        WHERE id IN ({placeholders}) AND geom IS NOT NULL
        """
        
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(query, *street_ids)
            
        geometries = {}
        for row in rows:
            geometries[row['street_id']] = row['geometry_wkt']
            
        return geometries
    
    async def _calculate_distance(self, pg_pool, event_location: tuple, street_geometry_wkt: str) -> float:
        """
        Расчет расстояния между событием и геометрией улицы в метрах.
        
        Args:
            event_location: tuple (lng, lat) координаты события
            street_geometry_wkt: WKT представление геометрии улицы
        
        Returns:
            Расстояние в метрах или infinity если не удалось рассчитать
        """
        if not event_location or len(event_location) != 2:
            return float('inf')
            
        lng, lat = event_location
        
        query = """
        SELECT ST_Distance(
            ST_Transform(ST_SetSRID(ST_MakePoint($1, $2), 4326), 3857),
            ST_Transform(ST_GeomFromText($3, 4326), 3857)
        ) as distance_meters
        """
        
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(query, lng, lat, street_geometry_wkt)
                return row['distance_meters'] if row else float('inf')
        except Exception as e:
            logger.warning(f"Distance calculation failed: {e}")
            return float('inf')
    
    def _calculate_lexical_match_score(self, input_text: str, candidate_name: str) -> float:
        """Бонус за точные совпадения и ключевые слова.
        
        Special cases for station names, numbers, and exact matches.
        """
        score = 0.0
        
        input_lower = self._clean_text(input_text).lower()
        candidate_lower = candidate_name.lower()
        
        input_words = set(input_lower.split())
        candidate_words = set(candidate_lower.split())
        
        # 1. Бонус за точные совпадения чисел и станций
        input_numbers = {w for w in input_words if w.isdigit()}
        candidate_numbers = {w for w in candidate_words if w.isdigit()}
        
        if input_numbers and candidate_numbers:
            # Если есть общие номера (11, 12 и т.д.)
            common_numbers = input_numbers & candidate_numbers
            if common_numbers:
                score += 0.15  # Бонус за общие номера
                
        # 2. Бонус за станционную терминологию
        station_words = {'станц', 'ст.', 'st.', 'остановк', 'ост.'}
        has_station_reference = any(w in input_lower for w in station_words)
        has_station_candidate = any(w in candidate_lower for w in station_words)
        
        if has_station_reference and has_station_candidate:
            score += 0.2  # Сильный бонус за совпадение станций
            
        # 3. Бонус за общие ключевые слова "фонтана"/"фонтан"
        fontan_variants = {'фонтана', 'фонтане', 'фонтану', 'фонтан', 'фонта', 'фонтана'}
        if any(w in candidate_words for w in fontan_variants):
            score += 0.1  # Бонус за тематику Фонтанской дороги
            
        # 4. Бонус за точное совпадение ключевого слова
        exact_keywords = ['фонтана', 'фонтан']
        for keyword in exact_keywords:
            if keyword in input_lower and keyword in candidate_lower:
                score += 0.05
                
        return min(score, 0.5)  # Максимальный бонус 0.5
    
    def _clean_text(self, text: str) -> str:
        """Базовая очистка текста для лексического анализа."""
        cleaned = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9\s]', ' ', text)
        # Украинские → русские буквы
        cleaned = cleaned.replace('і', 'и').replace('ї', 'и').replace('є', 'е')
        return cleaned.strip()

    def _filter_candidates_by_word_overlap(
        self, 
        ngram_text: str, 
        candidates: List[Dict]
    ) -> List[Dict]:
        """Фильтрует кандидатов по наличию общих слов с n-граммой.
        
        Усиленная проверка:
        1. Точное совпадение слов (token overlap)
        2. Подстрока ТОЛЬКО если >= 4 символов и является полным словом
        3. Общий корень (первые 6+ символов совпадают) — для склонений
        """
        ngram_words = set(ngram_text.lower().split())
        if not ngram_words:
            return candidates
        
        filtered = []
        for candidate in candidates:
            matched_name = candidate['matched_name'].lower()
            name_words = set(matched_name.split())
            
            # 1. Точное совпадение слов
            has_overlap = bool(ngram_words & name_words)
            
            # 2. Подстрока только для полных слов (>= 4 символа)
            is_valid_substring = False
            if len(ngram_text) >= 4:
                pattern = r'\b' + re.escape(ngram_text.lower()) + r'\b'
                if re.search(pattern, matched_name):
                    is_valid_substring = True
                pattern2 = r'\b' + re.escape(matched_name) + r'\b'
                if re.search(pattern2, ngram_text.lower()):
                    is_valid_substring = True
            
            # 3. Общий корень (первые min(6, len) символов совпадают)
            has_common_root = False
            for nw in ngram_words:
                if len(nw) < 4:
                    continue
                root_len = min(6, len(nw))
                root = nw[:root_len]
                for name_w in name_words:
                    if len(name_w) >= 4 and name_w[:root_len] == root:
                        has_common_root = True
                        break
                if has_common_root:
                    break
            
            if has_overlap or is_valid_substring or has_common_root:
                filtered.append(candidate)
                logger.debug(f"  ✅ PASS filter: ngram='{ngram_text}' → '{candidate['matched_name']}' (overlap={has_overlap}, substring={is_valid_substring}, root={has_common_root})")
            else:
                logger.debug(f"  ❌ FAIL filter: ngram='{ngram_text}' → '{candidate['matched_name']}' (score={candidate['score']:.3f})")
        
        return filtered

    def find_entities(
        self,
        text: str,
        top_k: int = MAX_ENTITIES,
        threshold: float = SIMILARITY_THRESHOLD,
        pg_pool=None,  # Требуется для поиска через pgvector
    ) -> List[Dict]:
        """
        Находит сущности в тексте через семантический поиск.
        
        NOTE: Это синхронная обёртка. Для асинхронного поиска
        используйте async_find_entities().
        
        Если pg_pool не передан, используется fallback на локальный поиск
        (менее точный, но работает без БД).
        """
        import asyncio
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            self.async_find_entities(text, top_k, threshold, pg_pool)
        )

    async def async_find_entities(
        self,
        text: str,
        top_k: int = MAX_ENTITIES,
        threshold: float = SIMILARITY_THRESHOLD,
        pg_pool=None,
        event_location: Optional[tuple] = None  # (lng, lat) для геопространственного скоринга
    ) -> List[Dict]:
        """
        Асинхронный поиск сущностей через семантические эмбеддинги.

        Этапы:
        1. Генерация n-грамм (униграммы + биграммы)
        2. Кодирование каждой n-граммы в вектор
        3. Поиск ближайших соседей через pgvector
        4. Дедупликация и геопространственный расчет
        5. Комбинированный скоринг и возврат топ-K результатов
        """
        if not self._initialized:
            logger.warning("SemanticMatcher not initialized")
            return []

        words = text.lower().split()
        if not words:
            return []

        # ЭТАП 1: Генерация n-грамм
        ngrams = self._generate_ngrams(words)
        if not ngrams:
            logger.debug(f"No ngrams generated for text: '{text}'")
            return []

        logger.debug(f"Ngrams: {[(n[0], n[1]) for n in ngrams]}")

        # ЭТАП 2: Кодирование всех n-грамм в векторы
        ngram_texts = [ngram[0] for ngram in ngrams]
        ngram_types = [ngram[1] for ngram in ngrams]

        embeddings = self._embedder.encode(ngram_texts)

        # Проверка качества эмбеддингов
        if embeddings.size > 0:
            norms = np.linalg.norm(embeddings, axis=1)
            logger.debug(f"Embedding norms (should be ~1.0): min={norms.min():.4f}, max={norms.max():.4f}, mean={norms.mean():.4f}")
            if norms.mean() < 0.5:
                logger.warning("⚠️ Embedding norms are very low — embeddings may be degenerate (zeros or near-zeros)")

        # ЭТАП 3: Поиск ближайших соседей
        all_candidates = []

        for i, (ngram_text, ngram_type) in enumerate(ngrams):
            embedding = embeddings[i]

            if pg_pool:
                # Поиск через pgvector
                candidates = await self._search_nearest(
                    pg_pool, embedding, limit=MAX_CANDIDATES_PER_NGRAM
                )

                logger.debug(f"Ngram '{ngram_text}' → {len(candidates)} candidates before filter: "
                             f"{[(c['matched_name'], round(c['score'], 3)) for c in candidates]}")

                # Фильтрация по word overlap (убираем семантически близкие, но нерелевантные)
                candidates = self._filter_candidates_by_word_overlap(ngram_text, candidates)

                logger.debug(f"Ngram '{ngram_text}' → {len(candidates)} candidates after filter: "
                             f"{[(c['matched_name'], round(c['score'], 3)) for c in candidates]}")
            else:
                # Fallback: локальный поиск по names (без pgvector)
                candidates = self._search_local_fallback(ngram_text)
            
            for candidate in candidates:
                if candidate['score'] >= threshold:
                    all_candidates.append({
                        'text': ngram_text,
                        'street_id': candidate['street_id'],
                        'matched_name': candidate['matched_name'],
                        'score': candidate['score'],
                        'source': ngram_type,
                    })

        # ЭТАП 4: Дедупликация и оптимизированный скоринг
        deduplicated = {}
        
        for candidate in all_candidates:
            sid = candidate['street_id']
            
            # Оригинальный семантический скоринг
            semantic_score = candidate['score']
            
            # Лексический скоринг (сравнение строк)
            lexical_score = self._calculate_lexical_score(text, candidate['matched_name'])
            
            # Комбинированный скоринг: семантика 70%, лексика 30%
            combined_score = semantic_score * 0.7 + lexical_score * 0.3
            
            # Обновляем кандидата с комбинированным score
            candidate_with_score = candidate.copy()
            candidate_with_score['combined_score'] = combined_score
            candidate_with_score['lexical_score'] = lexical_score
            
            # Сохраняем лучший комбинированный score для street_id
            if sid not in deduplicated or combined_score > deduplicated[sid]['combined_score']:
                deduplicated[sid] = candidate_with_score

        # ЭТАП 5: Ранжирование по комбинированному score
        entities = sorted(deduplicated.values(), key=lambda x: x['combined_score'], reverse=True)[:top_k]

        logger.debug(
            f"Found {len(entities)} entities with combined scoring: "
            f"{[e['text'] for e in entities]}"
        )
        return entities

    def _search_local_fallback(self, ngram_text: str) -> List[Dict]:
        """Локальный поиск без pgvector (fallback).
        
        Использует косинусное сходство между эмбеддингом n-граммы
        и предвычисленными эмбеддингами названий улиц.
        
        NOTE: Этот метод менее эффективен, т.к. требует кодирования
        всех названий улиц при каждом поиске. Рекомендуется использовать
        pgvector через async_find_entities().
        """
        # Для fallback просто ищем точное/частичное совпадение
        # Это временное решение — основной режим через pgvector
        candidates = []
        ngram_lower = ngram_text.lower()
        
        for name, street_id in self._street_id_map.items():
            # Простое fuzzy-совпадение (без RapidFuzz)
            if ngram_lower in name or name in ngram_lower:
                candidates.append({
                    'street_id': street_id,
                    'matched_name': name,
                    'score': 0.7,  # Условный порог для fallback
                })
        
        return candidates[:MAX_CANDIDATES_PER_NGRAM]

    async def reindex_street(self, pg_pool, street_id: int) -> bool:
        """Переиндексировать одну улицу (обновить embeddings для ВСЕХ названий).

        Вызывается при получении уведомления streets_updated.
        Генерирует отдельный embedding для каждого названия улицы.
        """
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, names FROM streets WHERE id = $1",
                    street_id,
                )

                if not row:
                    logger.warning(f"Street {street_id} not found for reindexing")
                    return False

                names = row['names'] or []
                if not names:
                    return False

                # Генерируем embeddings для ВСЕХ названий (в нижнем регистре)
                names_lower = [n.lower().strip() for n in names if n.strip()]
                if not names_lower:
                    return False

                embeddings = self._embedder.encode(names_lower)

                # Upsert в street_embeddings
                for name, embedding in zip(names_lower, embeddings):
                    embedding_str = '[' + ','.join(f'{v:.6f}' for v in embedding.tolist()) + ']'
                    await conn.execute(
                        """
                        INSERT INTO street_embeddings (street_id, name, embedding)
                        VALUES ($1, $2, $3::vector)
                        ON CONFLICT (street_id, name)
                        DO UPDATE SET embedding = $3::vector
                        """,
                        street_id,
                        name,
                        embedding_str,
                    )

                logger.debug(f"Reindexed street {street_id}: {len(names_lower)} names")
                return True

        except Exception as e:
            logger.error(f"Failed to reindex street {street_id}: {e}")
            return False

    async def reindex_all(self, pg_pool) -> int:
        """Переиндексировать все улицы — генерация embeddings для ВСЕХ названий.

        Для каждой улицы создаётся отдельный embedding для каждого названия.
        Названия кодируются в НИЖНЕМ регистре для консистентности с поиском.

        Returns:
            Количество переиндексированных улиц
        """
        # Проверяем, что embedder инициализирован
        if not self._embedder.is_initialized:
            logger.error("❌ Embedder not initialized, cannot index streets")
            return 0

        try:
            async with pg_pool.acquire() as conn:
                # Находим улицы, у которых нет embeddings в street_embeddings
                rows = await conn.fetch(
                    """
                    SELECT s.id, s.names
                    FROM streets s
                    WHERE s.geom IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM street_embeddings se WHERE se.street_id = s.id
                      )
                    """
                )

                if not rows:
                    logger.info("All streets already indexed")
                    return 0

                logger.info(f"Indexing {len(rows)} streets...")

                # Батчинг для оптимизации
                batch_size = 50
                indexed_count = 0

                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]

                    # Собируем ВСЕ названия для кодирования
                    all_names = []
                    street_name_map = []  # (street_id, name_index_start, name_index_end)

                    for row in batch:
                        street_id = row['id']
                        names = row['names'] or []
                        names_lower = [n.lower().strip() for n in names if n.strip()]

                        start_idx = len(all_names)
                        all_names.extend(names_lower)
                        street_name_map.append((street_id, start_idx, len(all_names)))

                    # Кодируем все названия одним батчем
                    if all_names:
                        embeddings = self._embedder.encode(all_names)

                        # Вставляем в street_embeddings
                        for street_id, start, end in street_name_map:
                            for name_idx in range(start, end):
                                name = all_names[name_idx]
                                embedding = embeddings[name_idx]
                                embedding_str = '[' + ','.join(f'{v:.6f}' for v in embedding.tolist()) + ']'

                                await conn.execute(
                                    """
                                    INSERT INTO street_embeddings (street_id, name, embedding)
                                    VALUES ($1, $2, $3::vector)
                                    ON CONFLICT (street_id, name)
                                    DO UPDATE SET embedding = $3::vector
                                    """,
                                    street_id,
                                    name,
                                    embedding_str,
                                )
                            indexed_count += 1

                    logger.info(f"Indexed batch {i // batch_size + 1}/{(len(rows) + batch_size - 1) // batch_size}: {indexed_count}/{len(rows)} streets")

                logger.info(f"✅ Indexed {indexed_count} streets")
                return indexed_count

        except Exception as e:
            logger.error(f"Failed to reindex all streets: {e}")
            return 0

    async def close(self):
        """Закрытие ресурсов."""
        await self._embedder.close()
        self._stopwords.clear()
        self._street_names.clear()
        self._street_id_map.clear()
        self._street_id_to_names.clear()
        self._initialized = False
        logger.info("SemanticMatcher closed")


# Функция для быстрого вызова
async def create_matcher(pg_pool) -> Optional[SemanticMatcher]:
    """Создать и инициализировать matcher."""
    matcher = SemanticMatcher()
    if await matcher.initialize(pg_pool):
        # Индексируем улицы без embedding
        indexed = await matcher.reindex_all(pg_pool)
        if indexed > 0:
            logger.info(f"Indexed {indexed} new streets during initialization")
        return matcher
    return None
