"""StreetMatcher — phonetic-first matcher + lemma fallback + контекстная верификация.

Стратегии (после рефакторинга Phase 1–6):

  T2 [Phonetic] — каждый кандидат (подряд n-gram + gap-n-gram) метафонизируется
                  целиком, ищется в индексе, верифицируется покомпонентным
                  rapidfuzz с динамическим порогом по размеру.
  T3 [Lemma]    — для кандидатов без T2-хита: tier-A exact lemma-tuple,
                  tier-B rapidfuzz по списку лемматизированных фраз.

  Multi-word confirmation (User#1) — когда n-gram покрывает только часть
  многословной улицы, ищется недостающая ref-лемма в окне сообщения. При
  подтверждении — бонус, без — штраф к score.

Слияние T2 ∪ T3 — max score по street_id; при равенстве phonetic > lemma_exact
> lemma_fuzzy. Финальный top-K = MAX_ENTITIES.

Состояние индекса вынесено в PhoneticIndex; матчер хранит только stop-words.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Sequence, Set, Tuple

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


def _max_token_ratio(query: str, choice: str, **_kwargs) -> int:
    """R1: scorer для 1-gram запроса против multi-word phrase.

    Возвращает max(fuzz.ratio(query, token)) по всем токенам phrase. Это
    устраняет substring-FP от partial_ratio (напр. «мент» ⊂ «элемент» давало
    100 → совпадение, теперь даёт ratio('мент','элемент')≈57 → отсев).
    """
    if not query or not choice:
        return 0
    best = 0
    for token in choice.split():
        score = fuzz.ratio(query, token)
        if score > best:
            best = score
    return best


# Кандидат: (surface_text, lemma_tuple, start_i, end_i, size, is_gap, is_anchored)
# end_i — позиция последнего токена (включительно), нужна для confirmation window.
# is_gap — True для gap-n-gram (несмежные токены); используется в логировании.
# is_anchored — True, если хотя бы один токен кандидата помечен '#' (хэштег-тег).
Candidate = Tuple[str, Tuple[str, ...], int, int, int, bool, bool]


def _lemma_cutoff(size: int = 0) -> float:
    """Порог rapidfuzz для T3 tier-B (леммо-fuzzy). 0-100.

    R4: 1-gram имеет более жёсткий порог (0.80 по умолчанию), чтобы отсечь
    borderline FP «сторону → Героев Обороны» (raw≈75 × bias 0.85 → 0.7508).
    """
    if settings and settings.similarity:
        if size == 1:
            raw = getattr(
                settings.similarity, 'entity_similarity_threshold_1gram', 0.80
            )
        else:
            raw = settings.similarity.entity_similarity_threshold
        return raw * 100 if raw <= 1.0 else float(raw)
    return 80.0 if size == 1 else 75.0


def _length_bias(size: int) -> float:
    """Length-bias для n-граммы из `size` слов. 0-1."""
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
    """Phonetic-first matcher: T2 (Metaphone) + T3 (lemma fallback) + confirmation."""

    def __init__(self, morph: Morphology, index: PhoneticIndex) -> None:
        self._morph = morph
        self._index = index
        self._stopwords: Set[str] = set()
        self._initialized = False

    # ---------------------------------------------------------------- initialize

    async def initialize(self, pg_pool) -> bool:
        try:
            async with pg_pool.acquire() as conn:
                sw_rows = await conn.fetch("SELECT word FROM stopwords")
                self._stopwords = {row['word'].lower() for row in sw_rows}
                logger.info(f"[Street] Loaded {len(self._stopwords)} stopwords")

                street_rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )

            await asyncio.to_thread(self._index.build, street_rows)
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

    # ----------------------------------------------------------- token filtering

    def _punctuation_set(self) -> Set[str]:
        """Набор «шумных» токенов из настроек (G2)."""
        if settings and settings.similarity:
            return set(getattr(settings.similarity, 'punctuation_tokens', ()))
        return {'#', '/', ',', '.', '(', ')', '!', '?', '-', '«', '»', '"', ':', ';'}

    def _generic_suffixes(self) -> Set[str]:
        """Набор generic-суффиксов из настроек (G1)."""
        if settings and settings.similarity:
            return set(getattr(settings.similarity, 'generic_suffixes', ()))
        return set()

    def _strip_noise(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
    ) -> Tuple[List[Token], List[Lemma]]:
        """G2: префильтр пунктуации/хэштегов из tokens+lemmas (параллельно)."""
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

    # --------------------------------------------------------- candidates gen

    def _generate_candidates(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
    ) -> List[Candidate]:
        """Подряд n-grams + gap-n-grams с фильтрами P2/G1.

        Фильтры для 1-gram (одиночный токен):
          • P2: чистая цифра — skip;
          • G1: generic-суффикс (проверка по LEMMA, не surface) — skip;
          • короткий токен (<3 символов) — skip (предлоги «на», «по» дают шум
            через partial_ratio в tier-B).
        """
        if not tokens:
            return []

        max_n = (
            settings.similarity.max_phonetic_ngram_length
            if settings and settings.similarity else 4
        )
        max_n = max(1, min(max_n, len(tokens)))
        max_gap = (
            settings.similarity.max_token_gap
            if settings and settings.similarity else 3
        )
        generics = self._generic_suffixes()
        n_tokens = len(tokens)

        def _is_blocked_singleton(surface: str, lemma: str) -> bool:
            """1-gram-блок: цифра / generic-суффикс / короткий (<3) токен.

            R2: дополнительно блокируем токены, чья LEMMA — чистая цифра
            (даёт FP «третьей» → lemma «3» → partial-match «13 Фонтана»
            через ORDINAL_MAP).
            """
            if surface.isdigit():
                return True  # P2 (surface)
            if lemma and lemma.isdigit():
                return True  # R2 (lemma post-ORDINAL_MAP)
            if surface in generics or lemma in generics:
                return True  # G1 (и surface, и lemma)
            if len(surface) < 3:
                return True  # короткие токены = preposition/частица
            return False

        out: List[Candidate] = []

        # (1) Подряд n-grams 1..max_n
        for size in range(1, max_n + 1):
            for i in range(n_tokens - size + 1):
                slice_t = tokens[i:i + size]
                slice_l = lemmas[i:i + size]
                surfaces = [t.text.lower() for t in slice_t]
                lemma_list = [l.normal_form for l in slice_l]

                anchored = any(t.is_anchored for t in slice_t)
                if size == 1 and _is_blocked_singleton(surfaces[0], lemma_list[0]):
                    continue
                if not any(self._is_qualified_token(s) for s in surfaces):
                    continue

                surface_text = ' '.join(surfaces)
                lemma_tuple = tuple(l for l in lemma_list if l)
                out.append((surface_text, lemma_tuple, i, i + size - 1, size,
                            False, anchored))

        # (2) Gap-n-grams (User#3): пары токенов с разрывом 1..max_gap.
        # Логически gap-gram = 2-gram, поэтому НЕ применяем 1-gram-блокировку
        # (`_is_blocked_singleton`) — generic-суффикс на одном конце (напр.
        # «спуске») допустим, если второй конец содержательный («Ольгиевским»).
        # Блокируем только: оба токена цифры; оба generic; ни один не qualified.
        if max_gap > 0:
            for i in range(n_tokens):
                surf_i = tokens[i].text.lower()
                lem_i = lemmas[i].normal_form
                # Защита от слишком коротких/мусорных стартов левого края.
                if surf_i.isdigit() or len(surf_i) < 3:
                    continue
                for j in range(i + 2, min(i + max_gap + 2, n_tokens)):
                    surf_j = tokens[j].text.lower()
                    lem_j = lemmas[j].normal_form
                    if surf_j.isdigit() or len(surf_j) < 3:
                        continue
                    if (surf_i in generics and surf_j in generics) or \
                       (lem_i in generics and lem_j in generics):
                        continue
                    # хотя бы один qualified (не stopword)
                    if not (self._is_qualified_token(surf_i) or
                            self._is_qualified_token(surf_j)):
                        continue
                    surface_text = f'{surf_i} {surf_j}'
                    lemma_tuple = tuple(l for l in (lem_i, lem_j) if l)
                    anchored = tokens[i].is_anchored or tokens[j].is_anchored
                    out.append((surface_text, lemma_tuple, i, j, 2, True, anchored))

        return out

    # ------------------------------------------------------- per-word scoring

    def _score_pair_wise(
        self,
        ngram_text: str,
        variant_text: str,
        size: int,
        metaphone_matched: bool,
    ) -> Tuple[float, float]:
        """Покомпонентный rapidfuzz (User#2.1) с dynamic threshold (User#2.2).

        Возвращает (raw_score 0-100, threshold 0-100).
        raw_score = min(per-word fuzz.ratio) − штраф за расхождение длин.
        Caller сравнивает raw_score с threshold; threshold уже учитывает
        size и metaphone softening.
        """
        if not (settings and settings.similarity):
            # Fallback на старое поведение: token_sort_ratio + base 85
            base = fuzz.token_sort_ratio(ngram_text, variant_text)
            return float(base), 85.0
        sim = settings.similarity

        n_words = sorted(ngram_text.split())
        v_words = sorted(variant_text.split())
        if not n_words or not v_words:
            return 0.0, 100.0

        common = min(len(n_words), len(v_words))
        per_word = [
            fuzz.ratio(n_words[i], v_words[i]) for i in range(common)
        ]
        missing = abs(len(n_words) - len(v_words))
        # min для строгости (одно очень плохое слово портит фразу), штраф 5
        # за каждое «лишнее» слово в любую сторону.
        raw = min(per_word) - missing * 5.0
        raw = max(0.0, raw)

        # Threshold: base − (size − 1) × step, с metaphone softening.
        base_raw = (
            getattr(sim, 'phonetic_match_threshold_1gram', 0.95)
            if size == 1
            else sim.phonetic_match_threshold
        )
        threshold = (
            base_raw - (size - 1) * sim.dynamic_threshold_step
        )
        if metaphone_matched:
            threshold -= sim.metaphone_softening
        # Нижняя граница — per_word_threshold.
        threshold = max(threshold, sim.per_word_threshold)
        return raw, threshold * 100

    # ----------------------------------------------------- multiword confirm

    def _confirm_multiword(
        self,
        street_id: int,
        ngram_lemmas: Tuple[str, ...],
        ngram_start: int,
        ngram_end: int,
        all_lemmas: List[Lemma],
        anchored: bool = False,
    ) -> float:
        """User#1: поиск недостающих ref-лемм улицы в окне сообщения.

        Возвращает delta для raw_score:
          • bonus > 0 — все/часть отсутствующих ref-лемм найдены в окне;
          • 0.0 — confirmation не требуется (одно-токенная улица или
            n-gram уже покрывает все ref-леммы);
          • penalty < 0 — ни одна из отсутствующих ref-лемм не найдена
            (вероятный FP).

        Если `anchored` (кандидат из хэштег-тега) — penalty НЕ применяется:
        человек явно тегнул улицу, ждать второе ref-слово не требуется. Bonus
        за найденные ref-леммы при этом сохраняется.
        """
        if not (settings and settings.similarity):
            return 0.0
        sim = settings.similarity

        ref = self._index.get_lemma_tuple_for_street(street_id)
        # Одно-токенные улицы — confirmation не применяется.
        if len(ref) <= 1:
            return 0.0

        ngram_lemma_set = set(ngram_lemmas)
        missing = [l for l in ref if l not in ngram_lemma_set]
        if not missing:
            return 0.0  # n-gram уже покрывает все ref-леммы

        def _no_confirm() -> float:
            """Нет подтверждения: для тегнутого кандидата — без штрафа."""
            return 0.0 if anchored else -sim.multiword_unconfirmed_penalty

        # Окно поиска вокруг matched n-gram (с обеих сторон), ИСКЛЮЧАЯ сами
        # токены n-gram [ngram_start..ngram_end]. Иначе недостающая ref-лемма
        # ложно «подтверждается» fuzzy-совпадением с собственным словом
        # кандидата (напр. ref «александра» ≈ ngram «александровка» → false
        # confirm вместо штрафа → FP «александровка → Александра Невского»).
        window = sim.multiword_confirm_window
        lo = max(0, ngram_start - window)
        hi = min(len(all_lemmas), ngram_end + 1 + window)
        window_lemmas = [
            l.normal_form
            for idx, l in enumerate(all_lemmas[lo:hi], start=lo)
            if l.normal_form and not (ngram_start <= idx <= ngram_end)
        ]
        if not window_lemmas:
            return _no_confirm()

        confirm_threshold = sim.multiword_confirm_threshold * 100
        confirmed = 0
        for ref_lemma in missing:
            if ref_lemma in window_lemmas:
                confirmed += 1
                continue
            for wl in window_lemmas:
                if fuzz.ratio(ref_lemma, wl) >= confirm_threshold:
                    confirmed += 1
                    break

        if confirmed == 0:
            return _no_confirm()
        # Пропорционально доле подтверждённых ref-лемм.
        return sim.multiword_confirm_bonus * (confirmed / len(missing))

    # ----------------------------------------------------- merge / candidates

    def _merge_candidate(
        self,
        best: Dict[int, Dict],
        street_id: int,
        adjusted: float,
        source: str,
        text: str,
        matched_name: str,
        span: Tuple[int, int] = (-1, -1),
    ) -> None:
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
                '_span': span,
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
                '_span': span,
                'source': source,
            })

    def _phonetic_pass(
        self,
        candidates: Sequence[Candidate],
        all_lemmas: List[Lemma],
        best: Dict[int, Dict],
    ) -> Set[Tuple[int, int]]:
        """T2: phonetic поиск + per-word verify + multiword confirm.

        Возвращает множество (start_i, end_i) кандидатов, для которых был
        хит — это исключает их позиции из T3 fallback.
        """
        if not (settings and settings.similarity and settings.similarity.phonetic_enabled):
            return set()

        anchor_bonus = (
            settings.similarity.hashtag_anchor_bonus
            if getattr(settings.similarity, 'hashtag_anchor_enabled', False)
            else 0.0
        )

        covered: Set[Tuple[int, int]] = set()
        for surface_text, lemma_tuple, start_i, end_i, size, is_gap, anchored in candidates:
            entries = self._index.query_phonetic(surface_text)
            if not entries:
                continue
            for entry in entries:
                raw, threshold = self._score_pair_wise(
                    surface_text, entry.variant_text, size,
                    metaphone_matched=True,
                )
                if raw < threshold:
                    continue
                # User#1: confirmation для многословных улиц (тег обходит penalty)
                delta = self._confirm_multiword(
                    entry.street_id, lemma_tuple, start_i, end_i, all_lemmas,
                    anchored=anchored,
                )
                if anchored:
                    delta += anchor_bonus
                # Cap raw at 100 после бонуса — confirmation усиливает слабые
                # матчи до порога, но не должен поднимать «идеальный» 1-gram
                # выше точного 2-gram match. Penalty (<0) cap-у не подлежит.
                final_raw = raw + delta
                if delta > 0:
                    final_raw = min(100.0, final_raw)
                if final_raw < threshold:
                    continue
                adjusted = final_raw * _length_bias(size)
                self._merge_candidate(
                    best, entry.street_id, adjusted,
                    'phonetic', surface_text, entry.canonical_name,
                    span=(start_i, end_i),
                )
                covered.add((start_i, end_i))
        return covered

    def _lemma_pass(
        self,
        candidates: Sequence[Candidate],
        covered: Set[Tuple[int, int]],
        all_lemmas: List[Lemma],
        best: Dict[int, Dict],
    ) -> None:
        """T3: exact lemma-tuple → rapidfuzz lemma-phrase fallback."""
        if not (settings and settings.similarity and settings.similarity.lemma_fallback_enabled):
            return
        phrases, phrase_meta = self._index.lemma_phrases()
        extract_limit = (
            settings.similarity.max_candidates_per_ngram
            if settings and settings.similarity else 2
        )
        anchor_bonus = (
            settings.similarity.hashtag_anchor_bonus
            if getattr(settings.similarity, 'hashtag_anchor_enabled', False)
            else 0.0
        )

        for surface_text, lemma_tuple, start_i, end_i, size, is_gap, anchored in candidates:
            if (start_i, end_i) in covered:
                continue
            if not lemma_tuple:
                continue

            # Два порога:
            #   • pre_cutoff — для rapidfuzz pre-filter (R4: жёсткий 1-gram 0.80
            #     отсекает substring-FP типа «сторону → Героев Обороны»);
            #   • post_cutoff — для adjusted score после length_bias (базовый 0.75
            #     не убивает legitimate typo recovery типа «Балковчкая»).
            pre_cutoff = _lemma_cutoff(size)
            post_cutoff = _lemma_cutoff(0)  # 0 → базовый 0.75

            # Tier-A: exact match по кортежу лемм.
            tier_a = self._index.query_lemma_tuple(lemma_tuple)
            if tier_a:
                for entry in tier_a:
                    delta = self._confirm_multiword(
                        entry.street_id, lemma_tuple, start_i, end_i, all_lemmas,
                        anchored=anchored,
                    )
                    if anchored:
                        delta += anchor_bonus
                    final_raw = 100.0 + delta
                    if delta > 0:
                        final_raw = min(100.0, final_raw)
                    adjusted = final_raw * _length_bias(size)
                    if adjusted < post_cutoff:
                        continue
                    self._merge_candidate(
                        best, entry.street_id, adjusted,
                        'lemma_exact', surface_text, entry.canonical_name,
                        span=(start_i, end_i),
                    )
                continue

            # Tier-B: rapidfuzz по списку лемматизированных фраз.
            if not phrases:
                continue
            lemma_text = ' '.join(lemma_tuple)
            # R1: Разделяем phrases на single-word и multi-word; для каждого
            # типа — свой scorer.
            #   • 1-gram vs single-word phrase: fuzz.ratio (strict).
            #   • 1-gram vs multi-word phrase: _max_token_ratio (max ratio по
            #     каждому отдельному токену phrase). Заменяет partial_ratio,
            #     который давал substring-FP типа «мент» ⊂ «элемент».
            #   • 2+ gram: token_sort_ratio (строже token_set_ratio).
            if size == 1:
                # Split предвычислен в PhoneticIndex (build/replace) — не
                # пересобираем на каждый 1-gram кандидат.
                (single_phrases, single_meta,
                 multi_phrases, multi_meta) = self._index.lemma_phrases_split()
                groups = [
                    (single_phrases, single_meta, fuzz.ratio),
                    (multi_phrases, multi_meta, _max_token_ratio),
                ]
            else:
                groups = [(phrases, phrase_meta, fuzz.token_sort_ratio)]

            for grp_phrases, grp_meta, scorer in groups:
                if not grp_phrases:
                    continue
                matches = rf_process.extract(
                    lemma_text,
                    grp_phrases,
                    scorer=scorer,
                    score_cutoff=pre_cutoff,  # R4 strict pre-filter
                    limit=extract_limit,
                )
                for _matched_text, score, m_idx in matches:
                    entry: PhoneticEntry = grp_meta[m_idx]
                    delta = self._confirm_multiword(
                        entry.street_id, lemma_tuple, start_i, end_i, all_lemmas,
                        anchored=anchored,
                    )
                    if anchored:
                        delta += anchor_bonus
                    final_raw = score + delta
                    if delta > 0:
                        final_raw = min(100.0, final_raw)
                    adjusted = final_raw * _length_bias(size)
                    if adjusted < post_cutoff:  # базовый 0.75 после bias
                        continue
                    self._merge_candidate(
                        best, entry.street_id, adjusted,
                        'lemma_fuzzy', surface_text, entry.canonical_name,
                        span=(start_i, end_i),
                    )

    # ----------------------------------------------------------- public API

    def find_streets(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
    ) -> List[Dict]:
        """T2 (phonetic + confirm) + T3 (lemma fallback) над n-grams + gap-grams."""
        if not self._initialized:
            logger.warning("[Street] Not initialized")
            return []
        if self._index.is_empty:
            logger.warning("[Street] Index is empty")
            return []
        if not tokens or not lemmas:
            return []

        # G2: префильтр шумных токенов (хэштеги, пунктуация).
        clean_tokens, clean_lemmas = self._strip_noise(tokens, lemmas)
        if not clean_tokens:
            return []

        candidates = self._generate_candidates(clean_tokens, clean_lemmas)
        if not candidates:
            return []

        best_by_street: Dict[int, Dict] = {}
        covered = self._phonetic_pass(candidates, clean_lemmas, best_by_street)
        self._lemma_pass(candidates, covered, clean_lemmas, best_by_street)

        return self._finalize(best_by_street, len(candidates), covered)

    def _finalize(
        self,
        best_by_street: Dict[int, Dict],
        n_candidates: int,
        covered: Set,
        source_label: str = "Street",
    ) -> List[Dict]:
        """Span-subsumption + top-K + очистка служебных полей. Общий хвост для
        n-gram-пути (find_streets) и NER-пути (find_streets_from_ner)."""
        top_k = (
            settings.similarity.max_entities
            if settings and settings.similarity else 3
        )

        # Span-subsumption: подавляем результат, чей token-span ВЛОЖЕН в span
        # другого результата с не меньшим score, либо совпадает с ним и слабее.
        # Снимает FP-партиалы, конкурирующие с более длинным/точным матчем за те
        # же токены: «приморский»(Приморская) ⊂ «приморский…суд»(Приморский суд),
        # «ольгиевский»(Ольгиевская) ⊂ «ольгиевский спуск», а также same-span
        # проигравших («Польский спуск» при «Ольгиевский спуск»).
        results = list(best_by_street.values())
        suppressed: Set[int] = set()
        for i, a in enumerate(results):
            a0, a1 = a['_span']
            if a0 < 0:
                continue
            for j, b in enumerate(results):
                if i == j or j in suppressed:
                    continue
                b0, b1 = b['_span']
                if b0 < 0:
                    continue
                same = (a0, a1) == (b0, b1)
                a_in_b = b0 <= a0 and a1 <= b1
                if not a_in_b:
                    continue
                if same:
                    # одинаковый span — слабейший (по score, затем priority) уходит
                    if (b['_adjusted'], b['_priority']) > (a['_adjusted'], a['_priority']):
                        suppressed.add(i)
                        break
                elif b['_adjusted'] >= a['_adjusted']:
                    # a строго внутри b и b не слабее — a это партиал, подавляем
                    suppressed.add(i)
                    break
        results = [r for k, r in enumerate(results) if k not in suppressed]

        for v in results:
            v.pop('_adjusted', None)
            v.pop('_priority', None)
            v.pop('_span', None)

        entities = sorted(
            results,
            key=lambda x: x['score'],
            reverse=True,
        )[:top_k]

        # Стата по source для последующей калибровки.
        source_stats = {}
        for e in entities:
            source_stats[e['source']] = source_stats.get(e['source'], 0) + 1
        logger.info(
            f"[{source_label}] Found {len(entities)} (candidates={n_candidates}, "
            f"T2-covered={len(covered)}, sources={source_stats}): "
            f"{[(e['matched_name'], round(e['score'], 2), e['source']) for e in entities]}"
        )
        return entities

    # ------------------------------------------------------- NER-driven path

    def _candidates_from_spans(
        self,
        clean_tokens: List[Token],
        clean_lemmas: List[Lemma],
        ner_spans: Sequence[Tuple[int, int]],
    ) -> List[Candidate]:
        """NER char-спаны → Candidate-кортежи (как подряд-n-gram, без перебора).

        Каждый спан отображается на непрерывный диапазон clean-токенов по
        пересечению char-офсетов: токен входит, если token.start < ce и
        token.stop > cs. Заменяет _generate_candidates: матчер линкует только
        то, что нашла модель, а не все n-gram.
        """
        out: List[Candidate] = []
        seen: Set[Tuple[int, int]] = set()
        n = len(clean_tokens)

        def emit(start_i: int, end_i: int) -> None:
            if start_i < 0 or end_i >= n or end_i < start_i:
                return
            if (start_i, end_i) in seen:
                return
            seen.add((start_i, end_i))
            slice_t = clean_tokens[start_i:end_i + 1]
            slice_l = clean_lemmas[start_i:end_i + 1]
            surface_text = ' '.join(t.text.lower() for t in slice_t)
            lemma_tuple = tuple(l.normal_form for l in slice_l if l.normal_form)
            anchored = any(t.is_anchored for t in slice_t)
            out.append((surface_text, lemma_tuple, start_i, end_i,
                        end_i - start_i + 1, False, anchored))

        for cs, ce in ner_spans:
            if ce <= cs:
                continue
            idxs = [k for k in range(n)
                    if clean_tokens[k].start < ce and clean_tokens[k].stop > cs]
            if not idxs:
                continue
            start_i, end_i = idxs[0], idxs[-1]
            # Базовый спан + ±1-токен расширения: NER даёт ОДИН спан, и если он на
            # токен короче газеттира («Люстдорфской»→«1 Люстдорфской») или разорван
            # («малой … Арнаутской»), точная линковка рассыпается. Расширения
            # конкурируют в _finalize: более длинный/точный матч вытесняет партиал,
            # а лишние кандидаты без матча отсекаются порогом confirm.
            emit(start_i, end_i)
            emit(start_i - 1, end_i)      # приклеить предыдущий токен (номер/«малой»)
            emit(start_i, end_i + 1)      # приклеить следующий токен (тип/хвост)
        return out

    def find_streets_from_ner(
        self,
        tokens: List[Token],
        lemmas: List[Lemma],
        ner_spans: Sequence[Tuple[int, int]],
    ) -> List[Dict]:
        """NER-путь: спаны от модели → линковка теми же T2/T3-проходами.

        Контракт идентичен find_streets (List[Dict] с street_id/score/text/...),
        но кандидаты берутся из NER, а не из n-gram-перебора. Пустые спаны →
        пустой результат (вызывающий уйдёт в strategy='random').
        """
        if not self._initialized:
            logger.warning("[NER-Street] Not initialized")
            return []
        if self._index.is_empty:
            logger.warning("[NER-Street] Index is empty")
            return []
        if not tokens or not lemmas or not ner_spans:
            return []

        clean_tokens, clean_lemmas = self._strip_noise(tokens, lemmas)
        if not clean_tokens:
            return []

        candidates = self._candidates_from_spans(clean_tokens, clean_lemmas, ner_spans)
        if not candidates:
            return []

        best_by_street: Dict[int, Dict] = {}
        covered = self._phonetic_pass(candidates, clean_lemmas, best_by_street)
        self._lemma_pass(candidates, covered, clean_lemmas, best_by_street)

        return self._finalize(best_by_street, len(candidates), covered,
                              source_label="NER-Street")
