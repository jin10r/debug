"""StreetMatcher — sliding-window span linker.

Генерирует кандидаты скользящим окном по ВСЕМ токенам (1..max_sliding_window)
и линкует каждый кандидат к street_id через три тира:

  Tier 1 [Surface fuzzy] — rapidfuzz по сырому тексту vs. алиасы в индексе (0.85).
  Tier 2 [Lemma exact]   — точное совпадение кортежа лемм → O(1) lookup.
  Tier 3 [Lemma fuzzy]   — rapidfuzz над лемматизированными фразами газеттира (0.82).

Кандидатам с локационным предлогом ("на/по/в") добавляется небольшой score-бонус.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Set, Tuple

from rapidfuzz import fuzz
from rapidfuzz import process as rf_process

from .morphology import Lemma, Morphology
from .phonetic_index import PhoneticEntry, PhoneticIndex
from .word_tokenizer import Token

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Candidate: (surface_text, lemma_tuple, start_i, end_i, size, is_gap, is_anchored)
Candidate = Tuple[str, Tuple[str, ...], int, int, int, bool, bool]

# Локационные предлоги: наличие одного из них перед кандидатом → is_anchored=True
_LOC_PREPS: frozenset = frozenset({
    'на', 'по', 'в', 'у', 'до',       # русский / украинский core
    'від', 'біля',                     # украинский (от, рядом)
    'около', 'возле', 'вдоль',         # русский (рядом, вдоль)
})


class StreetMatcher:
    """Sliding-window линкер: кандидаты → street_id через surface/lemma индекс."""

    def __init__(self, morph: Morphology, index: PhoneticIndex) -> None:
        self._morph = morph
        self._index = index
        self._initialized = False
        self._stopwords: Set[str] = set()

    # ---------------------------------------------------------------- initialize

    async def initialize(self, pg_pool) -> bool:
        try:
            async with pg_pool.acquire() as conn:
                street_rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )
                sw_rows = await conn.fetch("SELECT word FROM stopwords")

            await asyncio.to_thread(self._index.build, street_rows)
            self._stopwords = {row['word'].strip().lower() for row in sw_rows if row['word']}
            logger.info(f"[Street] Loaded {len(self._stopwords)} stopwords")
            self._initialized = True
            return True
        except Exception as exc:
            logger.error(f"[Street] Init failed: {exc}")
            return False

    async def reindex_all(self, pg_pool) -> int:
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
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, names FROM streets WHERE id = $1 AND geom IS NOT NULL",
                    street_id,
                )
            await asyncio.to_thread(
                self._index.replace_street, street_id, dict(row) if row else None
            )
        except Exception as exc:
            logger.error(f"[Street] reindex_street({street_id}) failed: {exc}")

    async def close(self) -> None:
        """No-op — нет внешних ресурсов."""

    # --------------------------------------------------------------- token helpers

    def _punctuation_set(self) -> Set[str]:
        if settings and settings.similarity:
            return set(getattr(settings.similarity, 'punctuation_tokens', ()))
        return {'#', '/', ',', '.', '(', ')', '!', '?', '-', '«', '»', '"', ':', ';'}

    def _strip_noise(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
    ) -> Tuple[List[Token], List[Lemma]]:
        """Префильтр пунктуации из tokens+lemmas (параллельно)."""
        if len(tokens) != len(lemmas):
            logger.warning(
                f"[Street] tokens/lemmas length mismatch: {len(tokens)}/{len(lemmas)}"
            )
            n = min(len(tokens), len(lemmas))
            tokens, lemmas = tokens[:n], lemmas[:n]
        punct = self._punctuation_set()
        keep_t, keep_l = [], []
        for t, l in zip(tokens, lemmas):
            surface = (t.text or '').strip()
            if not surface or all(ch in punct for ch in surface):
                continue
            keep_t.append(t)
            keep_l.append(l)
        return keep_t, keep_l

    def _candidates_sliding_window(
        self,
        clean_tokens: List[Token],
        clean_lemmas: List[Lemma],
        max_window: Optional[int] = None,
    ) -> List[Candidate]:
        """Скользящее окно по всем токенам: кандидаты размером 1..max_window.

        Заменяет NER-gate: улицы в косвенных падежах находятся без NER.
        Кандидату выставляется is_anchored=True, если перед ним стоит
        локационный предлог — это повышает score при дедупликации.
        """
        if max_window is None:
            max_window = (
                settings.similarity.max_sliding_window
                if settings and settings.similarity else 3
            )
        out: List[Candidate] = []
        seen: Set[Tuple[int, int]] = set()
        n = len(clean_tokens)

        for start_i in range(n):
            prev_text = clean_tokens[start_i - 1].text.lower() if start_i > 0 else ''
            is_anchored = prev_text in _LOC_PREPS
            for end_i in range(start_i, min(start_i + max_window, n)):
                if (start_i, end_i) in seen:
                    continue
                seen.add((start_i, end_i))
                slice_t = clean_tokens[start_i:end_i + 1]
                slice_l = clean_lemmas[start_i:end_i + 1]
                surface_text = ' '.join(t.text.lower() for t in slice_t)
                lemma_tuple = tuple(l.normal_form for l in slice_l if l.normal_form)
                out.append((surface_text, lemma_tuple, start_i, end_i,
                             end_i - start_i + 1, False, is_anchored))
        return out

    # ------------------------------------------------------------- span → street

    def _link_span(
        self,
        surface: str,
        lemmas: Tuple[str, ...],
        span: Tuple[int, int],
    ) -> Optional[Dict]:
        """Линковать одного кандидата к street_id. Три тира.

        Tier 1: surface fuzzy (rapidfuzz, порог 0.85) — ловит опечатки.
        Tier 2: exact lemma tuple (O(1) lookup) — ловит падежи.
        Tier 3: lemma fuzzy (rapidfuzz, порог 0.82) — ловит POS-расхождения.
        """
        if not surface:
            return None

        surface_thresh = (
            settings.similarity.phonetic_match_threshold * 100
            if settings and settings.similarity else 85.0
        )
        fuzzy_thresh = (
            settings.similarity.entity_similarity_threshold * 100
            if settings and settings.similarity else 75.0
        )

        # Tier 1: surface fuzzy — rapidfuzz по сырому тексту против алиасов в индексе.
        # Ловит опечатки в 1-2 буквы без фонетики (чепаевская→чапаевская = 90%).
        s_phrases, s_meta = self._index.surface_phrases()
        if s_phrases:
            s_match = rf_process.extractOne(
                surface,
                s_phrases,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=surface_thresh,
            )
            if s_match:
                _, score, idx = s_match
                entry = s_meta[idx]
                return {
                    'street_id': entry.street_id,
                    'score': score / 100.0,
                    'matched_name': entry.canonical_name,
                    'text': surface,
                    'source': 'surface_fuzzy',
                    '_span': span,
                }

        # Tier 2: exact lemma tuple
        if lemmas:
            tier_a = self._index.query_lemma_tuple(lemmas)
            if tier_a:
                return {
                    'street_id': tier_a[0].street_id,
                    'score': 0.90,
                    'matched_name': tier_a[0].canonical_name,
                    'text': surface,
                    'source': 'lemma_exact',
                    '_span': span,
                }

        # Tier 3: fuzzy lemma
        lemma_on = (
            settings.similarity.lemma_fallback_enabled
            if settings and settings.similarity else True
        )
        if lemmas and lemma_on:
            phrases, phrase_meta = self._index.lemma_phrases()
            if phrases:
                lemma_text = ' '.join(lemmas)
                match = rf_process.extractOne(
                    lemma_text,
                    phrases,
                    scorer=fuzz.token_sort_ratio,
                    score_cutoff=fuzzy_thresh,
                )
                if match:
                    _, score, idx = match
                    entry: PhoneticEntry = phrase_meta[idx]
                    return {
                        'street_id': entry.street_id,
                        'score': score / 100.0,
                        'matched_name': entry.canonical_name,
                        'text': surface,
                        'source': 'lemma_fuzzy',
                        '_span': span,
                    }

        return None

    # ------------------------------------------------------------------ finalize

    def _finalize(self, best_by_street: Dict[int, Dict]) -> List[Dict]:
        """Dedup по street_id уже выполнен; sort + top-K + очистка служебных полей."""
        top_k = (
            settings.similarity.max_entities
            if settings and settings.similarity else 3
        )
        results = sorted(
            best_by_street.values(),
            key=lambda x: x['score'],
            reverse=True,
        )[:top_k]
        for r in results:
            r.pop('_span', None)
        source_stats = {}
        for r in results:
            source_stats[r['source']] = source_stats.get(r['source'], 0) + 1
        logger.info(
            f"[Street] Found {len(results)} (sources={source_stats}): "
            f"{[(r['matched_name'], round(r['score'], 2), r['source']) for r in results]}"
        )
        return results

    # ----------------------------------------------------------------- public API

    def find_streets(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
    ) -> List[Dict]:
        """Sliding-window линкер: все токены → List[Dict] street_id/score/matched_name."""
        if not self._initialized:
            logger.warning("[Street] Not initialized")
            return []
        if self._index.is_empty:
            logger.warning("[Street] Index is empty")
            return []
        if not tokens or not lemmas:
            return []

        clean_tokens, clean_lemmas = self._strip_noise(tokens, lemmas)
        if not clean_tokens:
            return []

        candidates = self._candidates_sliding_window(clean_tokens, clean_lemmas)
        if not candidates:
            return []

        boost = (
            settings.similarity.prepositional_boost
            if settings and settings.similarity else 0.05
        )

        best_by_street: Dict[int, Dict] = {}
        for surface, lemma_tuple, start_i, end_i, _size, _gap, is_anchored in candidates:
            if surface in self._stopwords:
                continue
            result = self._link_span(surface, lemma_tuple, (start_i, end_i))
            if result is None:
                continue
            if is_anchored:
                result['score'] = min(1.0, result['score'] + boost)
            sid = result['street_id']
            existing = best_by_street.get(sid)
            if existing is None or result['score'] > existing['score']:
                best_by_street[sid] = result

        return self._finalize(best_by_street)
