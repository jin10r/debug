"""StreetMatcher — sliding-window span linker (морфологический распознаватель).

Генерирует кандидаты скользящим окном по ВСЕМ токенам (1..max_sliding_window)
и линкует каждый к street_id через два тира:

  Tier 1 [Stem exact] — точное совпадение кортежа Snowball-стемов → O(1) lookup.
      Это ядро: стем инвариантен к падежу и OOV-устойчив (Гаванной≡Гаванная),
      а обычные слова (среди/металлик) дают другой стем → не матчатся.
  Tier 2 [Surface typo] — rapidfuzz по сырым алиасам ТОЛЬКО как корректор
      опечаток (высокий cutoff + length-guard), а не семантический матч.

Падежи и согласование теперь свойство стем-индекса, а не настройка порога.
Кандидатам с локационным предлогом ("на/по/в") добавляется небольшой score-бонус.
"""

import asyncio
import logging
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

# Score точного стем-распознавания (Tier 1). Выше любого typo-матча.
_STEM_MATCH_SCORE = 0.97

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
        clean_stems: List[str],
        max_window: Optional[int] = None,
    ) -> List[Candidate]:
        """Скользящее окно по всем токенам: кандидаты размером 1..max_window.

        Заменяет NER-gate: улицы в косвенных падежах находятся без NER —
        распознавание живёт в стем-индексе самого матча. Кандидату выставляется
        is_anchored=True, если перед ним стоит локационный предлог.
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
                slice_s = clean_stems[start_i:end_i + 1]
                surface_text = ' '.join(t.text.lower() for t in slice_t)
                stem_tuple = tuple(s for s in slice_s if s)
                out.append((surface_text, stem_tuple, start_i, end_i,
                             end_i - start_i + 1, False, is_anchored))
        return out

    # ------------------------------------------------------------- span → street

    def _link_span(
        self,
        surface: str,
        stems: Tuple[str, ...],
        span: Tuple[int, int],
    ) -> Optional[Dict]:
        """Линковать одного кандидата к street_id. Два тира.

        Tier 1: exact stem tuple (O(1) lookup) — распознаёт падежи и OOV-пропера.
        Tier 2: surface typo (rapidfuzz, высокий cutoff + length-guard) — только
                опечатки орфографии, НЕ семантический матч.
        """
        if not surface:
            return None

        # Tier 1: точный кортеж стемов — ядро распознавания. При промахе для
        # многословных — порядок-независимый матч (Tier 1b: "Застава 2"≡"2 застава").
        if stems:
            hit = self._index.query_stem_tuple(stems)
            source = 'stem_exact'
            if not hit and len(stems) >= 2:
                hit = self._index.query_stem_tuple_sorted(stems)
                source = 'stem_reorder'
            if hit:
                # Если стем указывает на >1 РАЗНОЙ улицы — это over-stem коллизия
                # (Гаваи/Гаванная→"гава"): выбираем по поверхностной близости
                # запроса к алиасу. Для одиночной улицы — прямо (без потери recall).
                if len({e.street_id for e in hit}) > 1:
                    best = max(hit, key=lambda e: fuzz.ratio(surface, e.variant_text))
                else:
                    best = hit[0]
                return {
                    'street_id': best.street_id,
                    'score': _STEM_MATCH_SCORE,
                    'matched_name': best.canonical_name,
                    'text': surface,
                    'source': source,
                    '_span': span,
                }

        # Tier 2: орфо-корректор по surface. fuzz.ratio (а не token_sort) +
        # length-guard: опечатка близка по длине, поэтому "среди"/"Средняя"
        # (разные слова) и "Маяковского"/"Маловского" сюда не проходят, а
        # "чепаевская"/"чапаевская" (DL=1) — да.
        typo_thresh = (
            settings.similarity.surface_typo_threshold * 100
            if settings and settings.similarity
            and getattr(settings.similarity, 'surface_typo_threshold', None) is not None
            else 90.0
        )
        s_phrases, s_meta = self._index.surface_phrases()
        if s_phrases and len(surface) >= 5:
            s_match = rf_process.extractOne(
                surface,
                s_phrases,
                scorer=fuzz.ratio,
                score_cutoff=typo_thresh,
            )
            if s_match:
                cand, score, idx = s_match
                # length-guard: опечатка не меняет длину больше чем на ~20%.
                if abs(len(cand) - len(surface)) <= max(2, int(0.2 * len(surface))):
                    entry = s_meta[idx]
                    return {
                        'street_id': entry.street_id,
                        'score': score / 100.0,
                        'matched_name': entry.canonical_name,
                        'text': surface,
                        'source': 'surface_typo',
                        '_span': span,
                    }

        return None

    # ------------------------------------------------------------------ finalize

    def _finalize(self, best_by_street: Dict[int, Dict]) -> List[Dict]:
        """Dedup по street_id выполнен; max-span резолюция + sort + top-K + очистка."""
        top_k = (
            settings.similarity.max_entities
            if settings and settings.similarity else 3
        )
        # Max-span резолюция: отбрасываем матч, чей токен-спан СТРОГО вложен в
        # другой матч (более длинный) — "Южная" внутри "Южная дорога", "Малая"
        # внутри "Малая Арнаутская". Идём от длинных спанов к коротким.
        kept: List[Dict] = []
        for r in sorted(best_by_street.values(),
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

        # lemmas участвуют только в parallel-фильтрации пунктуации; матч идёт
        # по стемам токенов (см. ниже), поэтому clean_lemmas далее не нужен.
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

        best_by_street: Dict[int, Dict] = {}
        for surface, stem_tuple, start_i, end_i, _size, _gap, is_anchored in candidates:
            if surface in self._stopwords:
                continue
            result = self._link_span(surface, stem_tuple, (start_i, end_i))
            if result is None:
                continue
            if is_anchored:
                result['score'] = min(1.0, result['score'] + boost)
            sid = result['street_id']
            existing = best_by_street.get(sid)
            if existing is None or result['score'] > existing['score']:
                best_by_street[sid] = result

        return self._finalize(best_by_street)
