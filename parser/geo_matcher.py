"""GeoMatcher — поиск по единому справочнику geo (улицы + нас.пункты + POI).

Два тира:
  Tier 1 [Stem exact] — точное совпадение кортежа стемов из PhoneticIndex.
  Tier 2 [Surface typo] — rapidfuzz по сырым алиасам как корректор опечаток.

Порядок приоритета типов: settlement (village/town) > street > остальные.
"""

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional, Set, Tuple

from rapidfuzz import fuzz
from rapidfuzz import process as rf_process

from .morphology import Lemma, Morphology
from .phonetic_index import PhoneticIndex
from .word_tokenizer import Token

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Candidate: (surface_text, stem_tuple, start_i, end_i, size, is_gap, is_anchored)
Candidate = Tuple[str, Tuple[str, ...], int, int, int, bool, bool]


def _fuzzy_match(query: str, phrases: list, threshold: float):
    """Module-level function for fuzzy matching to enable pickling for ProcessPoolExecutor."""
    try:
        return rf_process.extractOne(
            query,
            phrases,
            scorer=fuzz.ratio,
            score_cutoff=threshold,
        )
    except Exception:
        return None

_STEM_MATCH_SCORE = 0.97

_LOC_PREPS: frozenset = frozenset({
    'на', 'по', 'в', 'у', 'до',
    'від', 'біля',
    'около', 'возле', 'вдоль',
})

# Типы в порядке приоритета: settlement выше street
class GeoMatcher:
    """Поиск по geo таблице: кандидаты → geo_id через surface/lemma индекс."""

    def __init__(self, morph: Morphology, index: PhoneticIndex) -> None:
        self._morph = morph
        self._index = index
        self._initialized = False
        self._stopwords: Set[str] = set()
        self._executor: Optional[ProcessPoolExecutor] = None

    async def initialize(self, pg_pool) -> bool:
        try:
            async with pg_pool.acquire() as conn:
                geo_rows = await conn.fetch(
                    "SELECT id, names, type FROM geo WHERE geom IS NOT NULL"
                )
                sw_rows = await conn.fetch("SELECT word FROM stopwords")

            await asyncio.to_thread(self._index.build, geo_rows)
            self._stopwords = {row['word'].strip().lower() for row in sw_rows if row['word']}
            self._executor = ProcessPoolExecutor(max_workers=None)
            logger.info(f"[Geo] Loaded {len(self._stopwords)} stopwords, {len(geo_rows)} objects, ProcessPoolExecutor initialized")
            self._initialized = True
            return True
        except Exception as exc:
            logger.error(f"[Geo] Init failed: {exc}")
            return False

    async def reindex_all(self, pg_pool) -> int:
        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, names, type FROM geo WHERE geom IS NOT NULL"
                )
            count = await asyncio.to_thread(self._index.build, rows)
            logger.info(f"[Geo] Reindexed {count} variants")
            return count
        except Exception as exc:
            logger.error(f"[Geo] reindex_all failed: {exc}")
            return 0

    async def reindex_geo(self, pg_pool, geo_id: int) -> None:
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, names FROM geo WHERE id = $1 AND geom IS NOT NULL",
                    geo_id,
                )
            await asyncio.to_thread(
                self._index.replace_street, geo_id, dict(row) if row else None
            )
        except Exception as exc:
            logger.error(f"[Geo] reindex_geo({geo_id}) failed: {exc}")

    async def close(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=True)
            logger.info("[Geo] ProcessPoolExecutor shutdown")
        self._executor = None

    def _punctuation_set(self) -> Set[str]:
        if settings and settings.similarity:
            return set(getattr(settings.similarity, 'punctuation_tokens', ()))
        return {'#', '/', ',', '.', '(', ')', '!', '?', '-', '«', '»', '"', ':', ';'}

    def _strip_noise(self, tokens: List[Token], lemmas: List[Lemma]) -> Tuple[List[Token], List[Lemma]]:
        if len(tokens) != len(lemmas):
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
        self, clean_tokens: List[Token], clean_stems: List[str], max_window: Optional[int] = None,
    ) -> List[Candidate]:
        if max_window is None:
            max_window = (
                settings.similarity.max_sliding_window
                if settings and settings.similarity else 3
            )
        out = []
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
                slice_s = clean_stems[start_i:end_i + 1]
                surface_text = ' '.join(t.text.lower() for t in slice_t)
                stem_tuple = tuple(s for s in slice_s if s)
                out.append((surface_text, stem_tuple, start_i, end_i, end_i - start_i + 1, False, is_anchored))
        return out

    async def _link_span(self, surface: str, stems: Tuple[str, ...], span: Tuple[int, int]) -> Optional[Dict]:
        if not surface:
            return None

        # Tier 1: точный кортеж стемов — ядро распознавания.
        if stems:
            hit = self._index.query_stem_tuple(stems)
            source = 'stem_exact'
            if not hit and len(stems) >= 2:
                hit = self._index.query_stem_tuple_sorted(stems)
                source = 'stem_reorder'
            if hit:
                if len({e.street_id for e in hit}) > 1:
                    best = max(hit, key=lambda e: fuzz.ratio(surface, e.variant_text))
                else:
                    best = hit[0]
                return {
                    'geo_id': best.street_id,
                    'score': _STEM_MATCH_SCORE,
                    'matched_name': best.canonical_name,
                    'text': surface,
                    'source': source,
                    '_span': span,
                }

        # Tier 2: орфо-корректор по surface.
        typo_thresh = (
            settings.similarity.surface_typo_threshold * 100
            if settings and settings.similarity
            and getattr(settings.similarity, 'surface_typo_threshold', None) is not None
            else 90.0
        )
        s_phrases, s_meta = self._index.surface_phrases()
        if s_phrases and len(surface) >= 5:
            if self._executor:
                try:
                    loop = asyncio.get_event_loop()
                    s_match = await loop.run_in_executor(
                        self._executor,
                        _fuzzy_match,
                        surface,
                        s_phrases,
                        typo_thresh
                    )
                except Exception as e:
                    logger.warning(f"[Geo] Parallel fuzzy match failed: {e}, falling back to sync")
                    s_match = rf_process.extractOne(
                        surface,
                        s_phrases,
                        scorer=fuzz.ratio,
                        score_cutoff=typo_thresh,
                    )
            else:
                s_match = rf_process.extractOne(
                    surface,
                    s_phrases,
                    scorer=fuzz.ratio,
                    score_cutoff=typo_thresh,
                )

            if s_match:
                cand, score, idx = s_match
                if (surface[0] == cand[0]
                        and abs(len(cand) - len(surface)) <= max(2, int(0.2 * len(surface)))):
                    entry = s_meta[idx]
                    return {
                        'geo_id': entry.street_id,
                        'score': score / 100.0,
                        'matched_name': entry.canonical_name,
                        'text': surface,
                        'source': 'surface_typo',
                        '_span': span,
                    }
        return None

    def _finalize(self, best_by_geo: Dict[int, Dict]) -> List[Dict]:
        top_k = (
            settings.similarity.max_entities if settings and settings.similarity else 5
        )
        kept: List[Dict] = []
        for r in sorted(best_by_geo.values(),
                        key=lambda x: (x['_span'][1] - x['_span'][0], x['score']),
                        reverse=True):
            s, e = r['_span']
            if any(ks <= s and e <= ke and (ke - ks) > (e - s)
                   for k in kept for (ks, ke) in (k['_span'],)):
                continue
            kept.append(r)

        results = sorted(kept, key=lambda x: x['score'], reverse=True)[:top_k]
        for r in results:
            r.pop('_span', None)
        source_stats = {}
        for r in results:
            source_stats[r['source']] = source_stats.get(r['source'], 0) + 1
        logger.debug(
            f"[Geo] Found {len(results)} (sources={source_stats}): "
            f"{[(r['matched_name'], round(r['score'], 2), r['source']) for r in results]}"
        )
        return results

    async def find_geo(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
    ) -> List[Dict]:
        """Поиск по geo таблице. Возвращает List[Dict] с geo_id/score/matched_name.

        Результаты сортируются: сначала settlement (village/town), потом street.
        """
        if not self._initialized:
            logger.warning("[Geo] Not initialized")
            return []
        if self._index.is_empty:
            logger.warning("[Geo] Index is empty")
            return []
        if not tokens or not lemmas:
            return []

        clean_tokens, _clean_lemmas = self._strip_noise(tokens, lemmas)
        if not clean_tokens:
            return []

        clean_stems = self._morph.stem_tokens(clean_tokens)
        candidates = self._candidates_sliding_window(clean_tokens, clean_stems)
        if not candidates:
            return []

        boost = (
            settings.similarity.prepositional_boost
            if settings and settings.similarity else 0.05
        )

        best_by_geo: Dict[int, Dict] = {}
        for surface, stem_tuple, start_i, end_i, _size, _gap, is_anchored in candidates:
            if surface in self._stopwords:
                continue
            result = await self._link_span(surface, stem_tuple, (start_i, end_i))
            if result is None:
                continue
            if is_anchored:
                result['score'] = min(1.0, result['score'] + boost)
            gid = result['geo_id']
            existing = best_by_geo.get(gid)
            if existing is None or result['score'] > existing['score']:
                best_by_geo[gid] = result

        return self._finalize(best_by_geo)
