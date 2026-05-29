"""StreetMatcher — phonetic-first matcher + lemma fallback.

Двухуровневая стратегия (NER и SymSpell удалены):

  T2 [Phonetic] — для каждой n-граммы сообщения (длиной 1..MAX_PHONETIC_NGRAM_LENGTH)
                  склеиваем исходные surface формы токенов, считаем русский
                  Metaphone (через PhoneticIndex), O(1) lookup → rapidfuzz
                  верификация по token_sort_ratio с порогом PHONETIC_MATCH_THRESHOLD.
  T3 [Lemma]    — для n-грамм без T2-хита: tier-A через exact lemma-tuple,
                  tier-B через rapidfuzz по лемматизированным фразам с порогом
                  ENTITY_SIMILARITY_THRESHOLD.

Слияние T2 ∪ T3 — max score по street_id, при равенстве побеждает T2
(phonetic > lemma_exact > lemma_fuzzy). Финальный top-K = MAX_ENTITIES.

Состояние индекса вынесено в PhoneticIndex; матчер хранит только stop-words.
"""

import asyncio
import logging
from typing import Dict, List, Sequence, Set, Tuple

from rapidfuzz import fuzz, process as rf_process

from .morphology import Lemma, Morphology
from .phonetic_index import PhoneticEntry, PhoneticIndex
from .razdel_tokenizer import Token

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

_SOURCE_PRIORITY = {'phonetic': 3, 'lemma_exact': 2, 'lemma_fuzzy': 1}


def _phonetic_cutoff() -> float:
    """Порог rapidfuzz для T2 (Metaphone-кандидаты). 0-100."""
    if settings and settings.similarity:
        raw = getattr(settings.similarity, 'phonetic_match_threshold', 0.85)
        return raw * 100 if raw <= 1.0 else float(raw)
    return 85.0


def _lemma_cutoff() -> float:
    """Порог rapidfuzz для T3 tier-B (леммо-fuzzy). 0-100."""
    if settings and settings.similarity:
        raw = settings.similarity.entity_similarity_threshold
        return raw * 100 if raw <= 1.0 else float(raw)
    return 75.0


def _length_bias(size: int) -> float:
    """Length-bias для n-граммы из `size` слов. 0-1.

    1-g/2-g берутся из settings (исторические калибровки), 3+ — 0.95 (фразы
    длиннее обычно надёжнее, малая скидка чтобы не задавить более короткие).
    """
    if settings and settings.similarity:
        sim = settings.similarity
        if size == 1:
            return sim.length_bias_1gram
        if size == 2:
            return sim.length_bias_2gram
    if size == 1:
        return 0.85
    if size == 2:
        return 0.90
    return 0.95


class StreetMatcher:
    """Phonetic-first matcher: T2 (Metaphone) + T3 (lemma fallback)."""

    def __init__(self, morph: Morphology, index: PhoneticIndex) -> None:
        self._morph = morph
        self._index = index
        self._stopwords: Set[str] = set()
        self._initialized = False

    # ---------------------------------------------------------------- initialize

    async def initialize(self, pg_pool) -> bool:
        """Загрузить стоп-слова и построить phonetic-индекс из streets."""
        try:
            async with pg_pool.acquire() as conn:
                sw_rows = await conn.fetch("SELECT word FROM stopwords")
                self._stopwords = {row['word'].lower() for row in sw_rows}
                logger.info(f"[Street] Loaded {len(self._stopwords)} stopwords")

                street_rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )

            # Тяжёлая работа (pymorphy3 lexeme × Metaphone × cartesian product)
            # на ~1000 улицах — это секунды CPU. off-load в thread чтобы не
            # блокировать event-loop.
            await asyncio.to_thread(self._index.build, street_rows)
            self._initialized = True
            return True
        except Exception as exc:
            logger.error(f"[Street] Init failed: {exc}")
            return False

    async def reindex_all(self, pg_pool) -> int:
        """Полная перезагрузка индекса (pg_notify streets_updated без street_id)."""
        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )
            count = await asyncio.to_thread(self._index.build, rows)
            logger.info(f"[Street] Reindexed {count} variants")
            return count
        except Exception as exc:
            logger.error(f"[Street] reindex_all failed: {exc}")
            return 0

    async def reindex_street(self, pg_pool, street_id: int) -> None:
        """Точечная переиндексация одной улицы (pg_notify streets_updated с street_id)."""
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, names FROM streets "
                    "WHERE id = $1 AND geom IS NOT NULL",
                    street_id,
                )
            await asyncio.to_thread(self._index.replace_street, street_id, dict(row) if row else None)
        except Exception as exc:
            logger.error(f"[Street] reindex_street({street_id}) failed: {exc}")

    async def close(self) -> None:
        """No-op — нет внешних ресурсов."""

    # ----------------------------------------------------------- n-gram setup

    def _is_qualified_token(self, surface: str) -> bool:
        """Слово годится для n-граммы: не стоп-слово, длина ≥ min, либо цифра."""
        if not surface:
            return False
        min_len = (
            settings.similarity.entity_min_word_length
            if settings and settings.similarity else 2
        )
        low = surface.lower()
        if low.isdigit():
            return True
        return low not in self._stopwords and len(low) >= min_len

    def _generate_ngrams(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
    ) -> List[Tuple[str, Tuple[str, ...], int, int]]:
        """Все n-граммы (size 1..max_phonetic_ngram_length) над токенами.

        Возвращает список кортежей (surface_text, lemma_tuple, start_i, size).
        Фильтр: хотя бы одно слово в окне должно быть «значимым»
        (см. `_is_qualified_token`), иначе окно отбрасывается.
        """
        if not tokens:
            return []
        max_n = (
            settings.similarity.max_phonetic_ngram_length
            if settings and settings.similarity else 4
        )
        max_n = max(1, min(max_n, len(tokens)))

        # Lemma-выравнивание: lemmatize_tokens возвращает Lemma per token в том
        # же порядке (см. Morphology.lemmatize_tokens), поэтому индексы совпадают.
        n_tokens = len(tokens)
        # При несовпадении длин (на случай если caller передал что-то странное)
        # ограничиваем по минимальной длине и логируем.
        if len(lemmas) != n_tokens:
            logger.warning(
                f"[Street] tokens/lemmas length mismatch: {n_tokens}/{len(lemmas)}"
            )
            n_tokens = min(n_tokens, len(lemmas))

        out: List[Tuple[str, Tuple[str, ...], int, int]] = []
        for size in range(1, max_n + 1):
            for i in range(n_tokens - size + 1):
                token_slice = tokens[i:i + size]
                lemma_slice = lemmas[i:i + size]
                surfaces = [t.text.lower() for t in token_slice]
                # хотя бы одно «значимое» слово в окне (иначе шум вроде «на и»)
                if not any(self._is_qualified_token(s) for s in surfaces):
                    continue
                surface_text = ' '.join(surfaces)
                lemma_tuple = tuple(
                    l.normal_form for l in lemma_slice if l.normal_form
                )
                out.append((surface_text, lemma_tuple, i, size))
        return out

    # ---------------------------------------------------------------- T2 / T3

    def _merge_candidate(
        self,
        best: Dict[int, Dict],
        street_id: int,
        adjusted: float,
        source: str,
        text: str,
        matched_name: str,
    ) -> None:
        """Слить нового кандидата с лучшим текущим по street_id.

        Победитель — больший adjusted; при равенстве — выше source priority.
        """
        existing = best.get(street_id)
        new_priority = _SOURCE_PRIORITY[source]
        if existing is None:
            best[street_id] = {
                'street_id': street_id,
                'matched_name': matched_name,
                'text': text,
                'score': adjusted / 100.0,
                '_adjusted': adjusted,
                '_priority': new_priority,
                'source': source,
            }
            return
        if adjusted > existing['_adjusted'] or (
            adjusted == existing['_adjusted'] and new_priority > existing['_priority']
        ):
            existing.update({
                'matched_name': matched_name,
                'text': text,
                'score': adjusted / 100.0,
                '_adjusted': adjusted,
                '_priority': new_priority,
                'source': source,
            })

    def _phonetic_pass(
        self,
        ngrams: Sequence[Tuple[str, Tuple[str, ...], int, int]],
        best: Dict[int, Dict],
    ) -> Set[Tuple[int, int]]:
        """T2: для каждой n-граммы поиск фонетических кандидатов + rapidfuzz.

        Возвращает множество (start_i, size) n-грамм, в которых был хит — это
        исключает соответствующие позиции из T3-фоллбэка.
        """
        if not (settings and settings.similarity and settings.similarity.phonetic_enabled):
            return set()
        cutoff = _phonetic_cutoff()
        covered: Set[Tuple[int, int]] = set()
        for surface_text, _lemma_tuple, start_i, size in ngrams:
            entries = self._index.query_phonetic(surface_text)
            if not entries:
                continue
            for entry in entries:
                score = fuzz.token_sort_ratio(surface_text, entry.variant_text)
                if score < cutoff:
                    continue
                adjusted = score * _length_bias(size)
                if adjusted < cutoff:
                    continue
                self._merge_candidate(
                    best, entry.street_id, adjusted,
                    'phonetic', surface_text, entry.canonical_name,
                )
                covered.add((start_i, size))
        return covered

    def _lemma_pass(
        self,
        ngrams: Sequence[Tuple[str, Tuple[str, ...], int, int]],
        covered: Set[Tuple[int, int]],
        best: Dict[int, Dict],
    ) -> None:
        """T3: для n-грамм без T2-хита — exact-tuple + rapidfuzz fallback."""
        if not (settings and settings.similarity and settings.similarity.lemma_fallback_enabled):
            return
        lemma_cutoff = _lemma_cutoff()
        phrases, phrase_meta = self._index.lemma_phrases()
        extract_limit = (
            settings.similarity.max_candidates_per_ngram
            if settings and settings.similarity else 2
        )

        for surface_text, lemma_tuple, start_i, size in ngrams:
            if (start_i, size) in covered:
                continue
            if not lemma_tuple:
                continue

            # Tier-A: точный match по кортежу лемм.
            tier_a = self._index.query_lemma_tuple(lemma_tuple)
            if tier_a:
                adjusted = 100.0 * _length_bias(size)
                for entry in tier_a:
                    self._merge_candidate(
                        best, entry.street_id, adjusted,
                        'lemma_exact', surface_text, entry.canonical_name,
                    )
                continue

            # Tier-B: rapidfuzz по списку лемматизированных фраз.
            if not phrases:
                continue
            lemma_text = ' '.join(lemma_tuple)
            scorer = fuzz.ratio if size == 1 else fuzz.token_set_ratio
            matches = rf_process.extract(
                lemma_text,
                phrases,
                scorer=scorer,
                score_cutoff=lemma_cutoff,
                limit=extract_limit,
            )
            for _matched_text, score, idx in matches:
                adjusted = score * _length_bias(size)
                if adjusted < lemma_cutoff:
                    continue
                entry: PhoneticEntry = phrase_meta[idx]
                self._merge_candidate(
                    best, entry.street_id, adjusted,
                    'lemma_fuzzy', surface_text, entry.canonical_name,
                )

    # ----------------------------------------------------------- public API

    def find_streets(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
    ) -> List[Dict]:
        """Найти улицы: T2 (phonetic) + T3 (lemma fallback) на n-граммах.

        Args:
            tokens: токены из RazdelTokenizer (нужны исходные surface формы для
                    фонетики).
            lemmas: соответствующие лемматизированные слова от Morphology
                    (нужны для T3 fallback по кортежу лемм).

        Returns:
            list of {street_id, matched_name, text, score, source} — score 0-1,
            source ∈ {phonetic, lemma_exact, lemma_fuzzy}, отсортированы по
            score ↓, ограничены top-K = MAX_ENTITIES.
        """
        if not self._initialized:
            logger.warning("[Street] Not initialized")
            return []
        if self._index.is_empty:
            logger.warning("[Street] Index is empty")
            return []
        if not tokens or not lemmas:
            return []

        ngrams = self._generate_ngrams(tokens, lemmas)
        if not ngrams:
            return []

        best_by_street: Dict[int, Dict] = {}
        covered = self._phonetic_pass(ngrams, best_by_street)
        self._lemma_pass(ngrams, covered, best_by_street)

        top_k = (
            settings.similarity.max_entities
            if settings and settings.similarity else 3
        )
        for v in best_by_street.values():
            v.pop('_adjusted', None)
            v.pop('_priority', None)

        entities = sorted(
            best_by_street.values(),
            key=lambda x: x['score'],
            reverse=True,
        )[:top_k]

        logger.info(
            f"[Street] Found {len(entities)} (ngrams={len(ngrams)}, T2-covered={len(covered)}): "
            f"{[(e['matched_name'], round(e['score'], 2), e['source']) for e in entities]}"
        )
        return entities
