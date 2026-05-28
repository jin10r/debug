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


def _threshold_score_cutoff() -> float:
    """Прочитать порог из settings и привести к шкале rapidfuzz (0-100).

    settings хранит 0-1; rapidfuzz score_cutoff ожидает 0-100.
    Читается per-call, чтобы env-изменения подхватывались без reload модуля.
    Fallback на 75.0 (соответствует SimilarityConfig default).
    """
    if settings and settings.similarity:
        raw = settings.similarity.entity_similarity_threshold
        return raw * 100 if raw <= 1.0 else float(raw)
    return 75.0


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
        """Полная пересборка alias-индекса.

        Atomic swap: оба списка собираются в локальные переменные, затем
        присваиваются self.* без точек suspension между присваиваниями.
        Asyncio single-thread + отсутствие await между присваиваниями = atomic
        для других корутин в том же loop.
        """
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
        # Atomic swap — два sync-присваивания, asyncio не прервёт между ними.
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
        """Точечная переиндексация одной улицы.

        Atomic: собираем новые списки в locals, затем единое присваивание.
        Между read-операциями self._alias_texts/_alias_meta и записью не
        должно быть await — иначе concurrent reader увидит inconsistent state.
        """
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, names FROM streets "
                    "WHERE id = $1 AND geom IS NOT NULL",
                    street_id,
                )

            # Снапшот существующих списков + фильтр обновляемой улицы
            new_texts: List[str] = []
            new_meta: List[Tuple[int, str]] = []
            for t, m in zip(self._alias_texts, self._alias_meta):
                if m[0] != street_id:
                    new_texts.append(t)
                    new_meta.append(m)

            # Добавление новой версии улицы (если она существует)
            if row:
                for name in (row['names'] or []):
                    lemma = self._morph.lemma_for_phrase(clean(name))
                    if lemma:
                        new_texts.append(lemma)
                        new_meta.append((street_id, name))

            # Atomic swap — никакого await между двумя присваиваниями
            self._alias_texts = new_texts
            self._alias_meta = new_meta

            logger.info(f"[Street] Reindexed street {street_id}")
        except Exception as exc:
            logger.error(f"[Street] reindex_street({street_id}) failed: {exc}")

    async def close(self) -> None:
        """No-op — нет внешних ресурсов."""

    # ----------------------------------------------------------- n-gram search

    def _generate_ngrams(self, words: List[str]) -> List[str]:
        """1- и 2-граммы из лемматизированной последовательности слов.

        Для 2-граммов оба слова должны быть значимы (не стоп-слово +
        len ≥ entity_min_word_length, либо цифра). Это исключает шумовые
        "с переулок", "по малый" и т.п.
        """
        min_len = (
            settings.similarity.entity_min_word_length
            if settings and settings.similarity else 2
        )
        ngrams: List[str] = []
        n = len(words)

        for size in range(1, min(3, n + 1)):
            for i in range(n - size + 1):
                chunk = words[i:i + size]
                if size == 1 and len(chunk) == 1 and chunk[0].isdigit():
                    continue
                qualified = [
                    w for w in chunk
                    if (w not in self._stopwords and len(w) >= min_len) or w.isdigit()
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

        # Калибровочные параметры (тюнятся через env / SimilarityConfig)
        if settings and settings.similarity:
            sim = settings.similarity
            bias_1g, bias_2g = sim.length_bias_1gram, sim.length_bias_2gram
            extract_limit = sim.max_candidates_per_ngram
        else:
            bias_1g, bias_2g = 0.85, 0.90
            extract_limit = 2

        best_by_street: Dict[int, Dict] = {}
        for ngram in ngrams:
            ngram_len = len(ngram.split())

            # 1-грамм — fuzz.ratio (strict);
            # 2+ — token_set_ratio (допускает перестановку).
            scorer = fuzz.ratio if ngram_len == 1 else fuzz.token_set_ratio

            # Length bias: 1-gram ×bias_1g, 2-gram ×bias_2g.
            length_bias = bias_1g if ngram_len == 1 else bias_2g

            # extract_limit ограничивает шум: для одного n-грама rapidfuzz
            # может вернуть много похожих алиасов («черноморка»→Ильичевск+
            # Люстдорфская+Черноморец). Тюнинг полностью через env
            # MAX_CANDIDATES_PER_NGRAM (default 2): меньше = чище matches,
            # больше = выше recall для ambiguous слов.
            matches = rf_process.extract(
                ngram,
                self._alias_texts,
                scorer=scorer,
                score_cutoff=score_cutoff,
                limit=extract_limit,
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
    ) -> List[Dict]:
        """Найти улицы: T1 (NER LOC-спаны) + T3 (полный лемматизированный текст).

        Args:
            loc_spans: LOC-сущности от natasha (могут быть пустыми).
            lemmas: лемматизация всего сообщения (для T3 fallback).

        Threshold и top_k всегда читаются из settings (env-override через
        ENTITY_SIMILARITY_THRESHOLD и MAX_ENTITIES). Бывшие параметры были
        dead-code: callers их не передавали.

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

        # Подхватываем калибровку из settings per-call (env-overrides не требуют
        # reload модуля).
        score_cutoff = _threshold_score_cutoff()
        top_k = (
            settings.similarity.max_entities
            if settings and settings.similarity else 3
        )

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
