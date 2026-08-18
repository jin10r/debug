"""GeoMatcher — поиск по единому справочнику geo (улицы + нас.пункты + POI).

Два тира:
  Tier 1 [Stem exact] — точное совпадение кортежа стемов из PhoneticIndex.
  Tier 2 [Surface typo] — rapidfuzz по сырым алиасам как корректор опечаток.
  Tier 3 [Semantic] — ONNX rubert-tiny2 для серой зоны 0.70-0.85.

Порядок приоритета типов: settlement (village/town) > street > остальные.
"""

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from rapidfuzz import fuzz
from rapidfuzz import process as rf_process

from .morphology import Lemma, Morphology
from .phonetic_index import PhoneticIndex
from .word_tokenizer import Token

if TYPE_CHECKING:
    from .phonetic_index import PhoneticEntry
    from .semantic_matcher import SemanticMatcher

try:
    from core.settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

Candidate = Tuple[str, Tuple[str, ...], int, int, int, bool, bool]


def _fuzzy_match(query: str, phrases: list, threshold: float):
    """Нечёткий поиск по списку фраз с пороговым значением схожести."""
    try:
        return rf_process.extractOne(
            query, phrases, scorer=fuzz.WRatio, score_cutoff=threshold,
        )
    except Exception:
        return None


def _batch_fuzzy_match(queries: list, phrases: list, threshold: float):
    """Batch fuzzy matching: один IPC на всё сообщение.

    rapidfuzz.process.extract(queries=list, ...) в 3.9.x молча возвращает
    пустой список (баг версии), поэтому каждый запрос матчим отдельным
    extractOne — иначе Tier 2 мёртв на проде (ProcessPoolExecutor).
    """
    results = {}
    for q in queries:
        try:
            match = rf_process.extractOne(
                q, phrases, scorer=fuzz.WRatio, score_cutoff=threshold,
            )
            if match:
                results[q] = match
        except Exception as e:
            logger.warning(f"Batch fuzzy match failed for {q!r}: {e}")
    return results


def _typo_len_guard(surface: str) -> int:
    """Допустимая разница длин surface↔кандидат для Tier 2 (орфо-корректор).

    Старый max(2, 20%) резал реальные опечатки длинных имён
    («Туристическая»→«Туристская», diff=3). Для поверхностей >=10 символов
    допуск расширен до max(3, 25%); короткие остаются на max(2, 20%).
    """
    if len(surface) >= 10:
        return max(3, int(0.25 * len(surface)))
    return max(2, int(0.2 * len(surface)))

_LOC_PREPS: frozenset = frozenset({
    'на', 'по', 'в', 'у', 'до',
    'від', 'біля',
    'около', 'возле', 'вдоль',
})

# Шумовые служебные токены («ст.», «ул.», «г.» + суффиксы порядковых),
# пропускаемые слайдинг-окном: «11 ст. Фонтана» → ключ (11, фонтана),
# совпадающий с именем «11 Фонтана» из справочника.
_NOISE_TOKENS: frozenset = frozenset({
    'ст', 'ул', 'вул', 'пр', 'пер', 'ш', 'им', 'г', 'го', 'й', 'ій', 'йй',
})

# Типы в порядке приоритета: settlement выше street
class GeoMatcher:
    """Поиск по geo таблице: кандидаты → geo_id через surface/lemma индекс."""

    def __init__(self, morph: Morphology, index: PhoneticIndex) -> None:
        """Инициализация матчера с морфологией и фонетическим индексом."""
        self._morph = morph
        self._index = index
        self._initialized = False
        self._stopwords: Set[str] = set()
        # geo_id → type (street/village/town/...): пробрасывается в кандидатов,
        # чтобы pre-filter (midpoint/type_hint) мог работать.
        self._geo_types: Dict[int, str] = {}
        self._executor: Optional[ProcessPoolExecutor] = None
        self._semantic_matcher: Optional["SemanticMatcher"] = None

    def set_semantic_matcher(self, matcher: "SemanticMatcher") -> None:
        """Подключение семантического валидатора кандидатов."""
        self._semantic_matcher = matcher

    async def initialize(self, pg_pool) -> bool:
        """Загрузка geo-данных, построение индекса, инициализация стоп-слов."""
        try:
            async with pg_pool.acquire() as conn:
                geo_rows = await conn.fetch(
                    "SELECT id, names, type FROM geo WHERE geom IS NOT NULL"
                )
                sw_rows = await conn.fetch("SELECT word FROM stopwords")

            await asyncio.to_thread(self._index.build, geo_rows)
            self._geo_types = {row['id']: row['type'] for row in geo_rows if row.get('type')}
            self._stopwords = {row['word'].strip().lower() for row in sw_rows if row['word']}
            self._executor = ProcessPoolExecutor(max_workers=4)
            logger.info(f"[Geo] Loaded {len(self._stopwords)} stopwords, {len(geo_rows)} objects, ProcessPoolExecutor initialized")
            self._initialized = True
            return True
        except Exception as exc:
            logger.error(f"[Geo] Init failed: {exc}")
            return False

    async def reindex_all(self, pg_pool) -> int:
        """Полная перестройка индекса geo-объектов."""
        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, names, type FROM geo WHERE geom IS NOT NULL"
                )
            self._geo_types = {row['id']: row['type'] for row in rows if row.get('type')}
            count = await asyncio.to_thread(self._index.build, rows)
            logger.info(f"[Geo] Reindexed {count} variants")
            return count
        except Exception as exc:
            logger.error(f"[Geo] reindex_all failed: {exc}")
            return 0

    async def reindex_geo(self, pg_pool, geo_id: int) -> None:
        """Обновление индекса для одного geo-объекта по ID."""
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, names, type FROM geo WHERE id = $1 AND geom IS NOT NULL",
                    geo_id,
                )
            row_dict = dict(row) if row else None
            if row_dict:
                self._geo_types[geo_id] = row_dict.get('type') or ''
            else:
                self._geo_types.pop(geo_id, None)  # объект удалён/geom NULL
            await asyncio.to_thread(
                self._index.replace_street, geo_id, row_dict
            )
        except Exception as exc:
            logger.error(f"[Geo] reindex_geo({geo_id}) failed: {exc}")

    async def close(self) -> None:
        """Завершение работы: остановка пула потоков."""
        if self._executor:
            self._executor.shutdown(wait=True)
            logger.info("[Geo] ProcessPoolExecutor shutdown")
        self._executor = None
        self._semantic_matcher = None

    def _punctuation_set(self) -> Set[str]:
        """Набор символов пунктуации для фильтрации токенов."""
        if settings and settings.similarity:
            return set(getattr(settings.similarity, 'punctuation_tokens', ()))
        return {'#', '/', ',', '.', '(', ')', '!', '?', '-', '«', '»', '"', ':', ';'}

    def _is_short_settlement(self, entry: "PhoneticEntry") -> bool:
        """Guard Tier 2: короткие settlement-имена (<=6 символов) не матчатся.

        Короткие топонимы («Малое», «Петрово») частотны по всей области —
        distant-спаривание через орфо-корректор даёт пин за десятки км.
        Длинные имена остаются — их анти-list (SQL) отсекает по дистанции.
        """
        return (
            self._geo_types.get(entry.street_id) in ('village', 'town')
            and len(entry.canonical_name or '') <= 6
        )

    def _strip_noise(self, tokens: List[Token], lemmas: List[Lemma]) -> Tuple[List[Token], List[Lemma]]:
        """Удаление шумовых (пунктуационных) токенов из последовательности."""
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
        """Генерация N-грамм с предфильтрацией: начинаем только с якорей."""
        if max_window is None:
            max_window = (
                settings.similarity.max_sliding_window
                if settings and settings.similarity else 3
            )
        # NOISE-gap: шумовые токены («ст.», «ул.», «г.», суффиксы порядковых)
        # выкидываются ДО генерации окон — «11 ст. Фонтана» даёт окна как
        # «11 Фонтана», и Tier 1 попадает в ключ справочника напрямую.
        sig_tokens: List[Token] = []
        sig_stems: List[str] = []
        for t, s in zip(clean_tokens, clean_stems):
            if (t.text or '').strip().lower() in _NOISE_TOKENS:
                continue
            sig_tokens.append(t)
            sig_stems.append(s)
        clean_tokens, clean_stems = sig_tokens, sig_stems
        if not clean_tokens:
            return []
        out = []
        seen: Set[Tuple[int, int]] = set()
        n = len(clean_tokens)
        for start_i in range(n):
            current_stem = clean_stems[start_i]
            prev_text = clean_tokens[start_i - 1].text.lower() if start_i > 0 else ''
            is_anchored = prev_text in _LOC_PREPS

            # Предфильтр-якорь: пропускаем позицию, если её одиночный токен не
            # может быть началом матча. Помимо точного стема учитываем:
            #  - has_stem_anywhere: стем входит в ЛЮБОЙ (в т.ч. многословный)
            #    ключ индекса — иначе "Застава 2" в начале сообщения терялось
            #    (стем 'застав' есть только в паре ('2', 'застав'));
            #  - длину поверхности >= 5: кандидат для Tier 2 (орфо-корректор),
            #    который матчит по сырому surface, а не по стему — опечатка в
            #    начале сообщения иначе не доходила до Tier 2 вовсе.
            if (not is_anchored and current_stem
                    and not self._index.has_stem(current_stem)
                    and not self._index.has_stem_anywhere(current_stem)
                    and len(clean_tokens[start_i].text) < 5):
                continue

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

    async def _link_span_tier1(self, surface: str, stems: Tuple[str, ...], span: Tuple[int, int]) -> Optional[Dict]:
        """Только Tier 1: точный стем-матч. Tier-2 вынесен в batch."""
        if not surface or not stems:
            return None

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
                'score': fuzz.ratio(surface, best.variant_text) / 100.0,
                'matched_name': best.canonical_name,
                'text': surface,
                'source': source,
                '_span': span,
            }
        return None

    async def _link_span(self, surface: str, stems: Tuple[str, ...], span: Tuple[int, int]) -> Optional[Dict]:
        """Поиск geo-объекта по тексту: Tier 1 (стемы) → Tier 2 (опечатки).

        NOTE: в find_geo() Tier 2 выполняется батчем (tier2_queries), этот метод
        используется только извне/тестами — держать guards консистентными.
        """
        if not surface:
            return None

        result = await self._link_span_tier1(surface, stems, span)
        if result:
            return result

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
                        and surface[:3] == cand[:3]
                        and abs(len(cand) - len(surface)) <= _typo_len_guard(surface)):
                    entry = s_meta[idx]
                    if self._is_short_settlement(entry):
                        return None
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
        """Дедупликация, сортировка и возврат top-K найденных geo-объектов."""
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
            r['type'] = self._geo_types.get(r['geo_id'], '')
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
        text: Optional[str] = None,
    ) -> List[Dict]:
        """Поиск по geo таблице. Возвращает List[Dict] с geo_id/score/matched_name.

        text — ПОЛНЫЙ исходный текст сообщения: передаётся семантической модели
        для валидации кандидатов «серой зоны» (0.70–0.85). Если не задан —
        семантический фильтр не вызывается (режим совместимости/тестов).

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
        tier2_queries = []
        tier2_meta = []

        for surface, stem_tuple, start_i, end_i, _size, _gap, is_anchored in candidates:
            if surface in self._stopwords:
                continue

            result = await self._link_span_tier1(surface, stem_tuple, (start_i, end_i))
            if result:
                result['_anchored'] = is_anchored
                gid = result['geo_id']
                existing = best_by_geo.get(gid)
                if existing is None or result['score'] > existing['score']:
                    best_by_geo[gid] = result
            else:
                s_phrases, s_meta = self._index.surface_phrases()
                if s_phrases and len(surface) >= 5:
                    tier2_queries.append(surface)
                    tier2_meta.append({
                        'surface': surface,
                        'span': (start_i, end_i),
                        'is_anchored': is_anchored,
                    })

        if tier2_queries:
            typo_thresh = (
                settings.similarity.surface_typo_threshold * 100
                if settings and settings.similarity
                and getattr(settings.similarity, 'surface_typo_threshold', None) is not None
                else 90.0
            )
            s_phrases, s_meta = self._index.surface_phrases()

            if self._executor:
                loop = asyncio.get_event_loop()
                batch_results = await loop.run_in_executor(
                    self._executor,
                    _batch_fuzzy_match,
                    tier2_queries,
                    s_phrases,
                    typo_thresh
                )
            else:
                # Без пула потоков (тесты, деградация, сбой инициализации
                # executor) — синхронный Tier 2. Иначе typo-кандидаты молча
                # отбрасывались: batch-блок был целиком завязан на executor.
                batch_results = {}
                for surface in tier2_queries:
                    m = _fuzzy_match(surface, s_phrases, typo_thresh)
                    if m:
                        batch_results[surface] = m

            for i, surface in enumerate(tier2_queries):
                if surface in batch_results:
                    match, score, idx = batch_results[surface]
                    if (surface[0] == match[0]
                            and surface[:3] == match[:3]
                            and abs(len(match) - len(surface)) <= _typo_len_guard(surface)):
                        entry = s_meta[idx]
                        if self._is_short_settlement(entry):
                            continue
                        meta = tier2_meta[i]
                        result = {
                            'geo_id': entry.street_id,
                            'score': score / 100.0,
                            'matched_name': entry.canonical_name,
                            'text': surface,
                            'source': 'surface_typo',
                            '_span': meta['span'],
                            '_anchored': meta['is_anchored'],
                        }
                        gid = result['geo_id']
                        existing = best_by_geo.get(gid)
                        if existing is None or result['score'] > existing['score']:
                            best_by_geo[gid] = result

        if self._semantic_matcher and best_by_geo and text:
            semantic_threshold = (
                settings.similarity.semantic_accept_threshold
                if settings and settings.similarity else None
            )
            candidates_list = list(best_by_geo.values())
            filtered_candidates = self._semantic_matcher.filter_candidates(
                candidates_list, text, semantic_threshold=semantic_threshold,
            )
            best_by_geo = {c["geo_id"]: c for c in filtered_candidates}
            logger.debug(
                f"[Geo] Semantic filter (full text): {len(candidates_list)} -> "
                f"{len(filtered_candidates)} candidates"
            )

        # Prepositional boost — ПОСЛЕ семантической валидации: не выталкивает
        # кандидатов серой зоны (0.70–0.85) за порог confident (0.85) до того,
        # как их проверит модель. Сортировка и скоринг используют boosted score.
        for r in best_by_geo.values():
            if r.pop('_anchored', False):
                r['score'] = min(1.0, r['score'] + boost)

        return self._finalize(best_by_geo)
