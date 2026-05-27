"""StreetMatcher — поиск улиц через NER + лексический фуззи-матч.

Замена lexical_matcher.py. Архитектура двухуровневая:

  T1 [NER] — `loc_spans` от natasha NewsNERTagger лемматизируются и матчатся
             против alias-индекса. Высокая precision: NER исключает контекстные
             прилагательные ("малый автобус" не помечается как LOC).
  T3 [Lex] — fallback по всему лемматизированному тексту через rapidfuzz +
             n-граммы (текущая логика lexical_matcher). Recall-страховка для
             lowercase Telegram-стиля, где NER не работает.

Результаты T1/T3 объединяются: для каждой улицы берётся max(score). Это
сохраняет recall старого матчера при добавленной precision от NER.

Лемматизация делегирована Morphology (один MorphAnalyzer на процесс).
Alias-индекс — два параллельных списка (`_alias_texts`, `_alias_meta`),
синхронизация по индексу.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from rapidfuzz import fuzz, process as rf_process

from .morphology import Lemma, Morphology
from .ner_extractor import Span
from .text_preprocessor import clean

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)


# Порог в шкале rapidfuzz (0-100). settings хранит 0-1, конвертация при использовании.
_DEFAULT_THRESHOLD = 72.0

SIMILARITY_THRESHOLD: float
_raw = (
    settings.similarity.entity_similarity_threshold
    if settings and settings.similarity
    else None
)
if _raw is not None:
    SIMILARITY_THRESHOLD = _raw * 100 if _raw <= 1.0 else float(_raw)
else:
    SIMILARITY_THRESHOLD = _DEFAULT_THRESHOLD

MAX_ENTITIES = 3


class StreetMatcher:
    """NER-first matcher: фильтрует через LOC-спаны, фолбэк на полный лекс. поиск."""

    def __init__(self, morph: Morphology) -> None:
        self._morph = morph
        self._stopwords: Set[str] = set()
        self._alias_texts: List[str] = []          # лемматизированные алиасы
        self._alias_meta: List[Tuple[int, str]] = []  # (street_id, original_name)
        self._initialized = False

    # ---------------------------------------------------------------- initialize

    async def initialize(self, pg_pool) -> bool:
        """Загрузить стоп-слова и построить alias-индекс из streets."""
        try:
            async with pg_pool.acquire() as conn:
                sw_rows = await conn.fetch("SELECT word FROM stopwords")
                self._stopwords = {row['word'].lower() for row in sw_rows}
                logger.info(f"[Street] Loaded {len(self._stopwords)} stopwords")

                street_rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )

            count = self._build_alias_index(street_rows)
            logger.info(
                f"[Street] Indexed {count} aliases from {len(street_rows)} streets"
            )
            self._initialized = True
            return True
        except Exception as exc:
            logger.error(f"[Street] Init failed: {exc}")
            return False

    def _build_alias_index(self, rows) -> int:
        texts: List[str] = []
        meta: List[Tuple[int, str]] = []
        for row in rows:
            street_id: int = row['id']
            names: List[str] = row['names'] or []
            for name in names:
                lemma = self._morph.lemma_for_phrase(clean(name))
                if lemma:
                    texts.append(lemma)
                    meta.append((street_id, name))
        self._alias_texts = texts
        self._alias_meta = meta
        return len(texts)

    async def reindex_all(self, pg_pool) -> int:
        """Перезагрузка alias-индекса (pg_notify streets_updated)."""
        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )
            count = self._build_alias_index(rows)
            logger.info(f"[Street] Reindexed {count} aliases")
            return count
        except Exception as exc:
            logger.error(f"[Street] reindex_all failed: {exc}")
            return 0

    async def reindex_street(self, pg_pool, street_id: int) -> None:
        """Точечная переиндексация одной улицы."""
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, names FROM streets "
                    "WHERE id = $1 AND geom IS NOT NULL",
                    street_id,
                )

            pairs = [
                (t, m) for t, m in zip(self._alias_texts, self._alias_meta)
                if m[0] != street_id
            ]
            if pairs:
                texts, meta = zip(*pairs)
                self._alias_texts = list(texts)
                self._alias_meta = list(meta)
            else:
                self._alias_texts = []
                self._alias_meta = []

            if row:
                for name in (row['names'] or []):
                    lemma = self._morph.lemma_for_phrase(clean(name))
                    if lemma:
                        self._alias_texts.append(lemma)
                        self._alias_meta.append((street_id, name))

            logger.info(f"[Street] Reindexed street {street_id}")
        except Exception as exc:
            logger.error(f"[Street] reindex_street({street_id}) failed: {exc}")

    async def close(self) -> None:
        """No-op — нет внешних ресурсов."""

    # ----------------------------------------------------------- n-gram search

    def _generate_ngrams(self, words: List[str]) -> List[str]:
        """1- и 2-граммы из лемматизированной последовательности слов.

        Для 2-граммов оба слова должны быть значимы (не стоп-слово + len≥2,
        либо цифра). Это исключает шумовые "с переулок", "по малый" и т.п.
        """
        ngrams: List[str] = []
        n = len(words)

        for size in range(1, min(3, n + 1)):
            for i in range(n - size + 1):
                chunk = words[i:i + size]
                if size == 1 and len(chunk) == 1 and chunk[0].isdigit():
                    continue
                qualified = [
                    w for w in chunk
                    if (w not in self._stopwords and len(w) >= 2) or w.isdigit()
                ]
                if len(qualified) >= size:
                    ngrams.append(' '.join(chunk))

        return ngrams

    def _search_in_lemma_text(
        self,
        lemma_text: str,
        score_cutoff: float,
        source_tag: str,
    ) -> Dict[int, Dict]:
        """Лексический фуззи-поиск в уже лемматизированной строке.

        Возвращает {street_id: hit_dict} с внутренним `_adjusted` для сравнения.
        Внешний код объединяет результаты T1/T3 по этому полю.
        """
        if not self._alias_texts:
            return {}
        words = lemma_text.split()
        if not words:
            return {}

        ngrams = self._generate_ngrams(words)
        if not ngrams:
            return {}

        best_by_street: Dict[int, Dict] = {}
        for ngram in ngrams:
            ngram_len = len(ngram.split())

            # 1-грамм — fuzz.ratio (strict);
            # 2+ — token_set_ratio (допускает перестановку).
            scorer = fuzz.ratio if ngram_len == 1 else fuzz.token_set_ratio

            # Length bias: длинные n-граммы получают приоритет.
            # 1-gram ×0.85, 2-gram ×0.90.
            length_bias = 0.85 + 0.05 * min(ngram_len - 1, 3)

            # limit=2: для одного matched_part оставляем максимум 2 кандидата
            # (например «Преображенская» + «пр. Преображенский»-синоним), но
            # не плодим 3+ совпадений с разными street_id для одного слова —
            # это шум вроде «черноморка»→Ильичевск+Люстдорфская+Черноморец.
            matches = rf_process.extract(
                ngram,
                self._alias_texts,
                scorer=scorer,
                score_cutoff=score_cutoff,
                limit=2,
            )
            for _matched_text, score, idx in matches:
                adjusted = score * length_bias
                if adjusted < score_cutoff:
                    continue

                street_id, original_name = self._alias_meta[idx]
                if (
                    street_id not in best_by_street
                    or adjusted > best_by_street[street_id]['_adjusted']
                ):
                    best_by_street[street_id] = {
                        'street_id': street_id,
                        'matched_name': original_name,
                        'text': ngram,
                        'score': adjusted / 100.0,
                        '_adjusted': adjusted,
                        'source': source_tag,
                    }

        return best_by_street

    # ----------------------------------------------------------- public API

    def find_streets(
        self,
        loc_spans: List[Span],
        lemmas: List[Lemma],
        threshold: float = SIMILARITY_THRESHOLD,
        top_k: int = MAX_ENTITIES,
    ) -> List[Dict]:
        """Найти улицы: T1 (NER LOC-спаны) + T3 (полный лемматизированный текст).

        Args:
            loc_spans: LOC-сущности от natasha (могут быть пустыми).
            lemmas: лемматизация всего сообщения (для T3 fallback).
            threshold: порог 0-1 или 0-100; нормализуется к 0-100.
            top_k: максимум результатов.

        Returns:
            list of {street_id, matched_name, text, score, source}, отсортирован
            по score. `source` = 'ner' для T1, 'lexical' для T3.
        """
        if not self._initialized:
            logger.warning("[Street] Not initialized")
            return []
        if not self._alias_texts:
            logger.warning("[Street] Alias index is empty")
            return []

        score_cutoff = threshold if threshold > 1.0 else threshold * 100

        best_by_street: Dict[int, Dict] = {}

        # T1: NER LOC-спаны (если есть)
        for span in loc_spans:
            cleaned = clean(span.text)
            if not cleaned:
                continue
            lemma_text = self._morph.lemma_for_phrase(cleaned)
            if not lemma_text:
                continue
            partial = self._search_in_lemma_text(lemma_text, score_cutoff, 'ner')
            for sid, hit in partial.items():
                if (
                    sid not in best_by_street
                    or hit['_adjusted'] > best_by_street[sid]['_adjusted']
                ):
                    best_by_street[sid] = hit

        # T3: полный лемматизированный текст (всегда — recall-страховка).
        # Если T1 уже нашёл уверенный матч, T3 либо подтвердит, либо добавит
        # вторую улицу из сообщения.
        full_lemma_text = ' '.join(
            l.normal_form for l in lemmas if l.normal_form
        )
        if full_lemma_text:
            partial = self._search_in_lemma_text(
                full_lemma_text, score_cutoff, 'lexical'
            )
            for sid, hit in partial.items():
                if (
                    sid not in best_by_street
                    or hit['_adjusted'] > best_by_street[sid]['_adjusted']
                ):
                    best_by_street[sid] = hit

        # Финализация
        for v in best_by_street.values():
            v.pop('_adjusted', None)

        entities = sorted(
            best_by_street.values(),
            key=lambda x: x['score'],
            reverse=True,
        )[:top_k]

        logger.info(
            f"[Street] Found {len(entities)} "
            f"(T1 spans={len(loc_spans)}): "
            f"{[(e['matched_name'], round(e['score'], 2), e['source']) for e in entities]}"
        )
        return entities
