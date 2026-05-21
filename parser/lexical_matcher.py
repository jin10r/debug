"""LexicalMatcher — поиск улиц через mawo_pymorphy3 + rapidfuzz.

Заменяет rubert-tiny2 + pgvector. Поиск полностью локальный (CPU, без ONNX).

Пайплайн на каждое сообщение:
  1. Лемматизация текста через mawo_pymorphy3 (включая конвертацию порядковых числительных в цифры)
  2. Генерация n-грамм 1-4 слова
  3. rapidfuzz token_set_ratio каждого n-грамма против лемматизированных aliases из БД
  4. Дедупликация по street_id, топ-N по score

Пример:
  "на пятой фонтана дтп"
    → лемматизация: "на 5 фонтан дтп"
    → n-граммы: ["5 фонтан", "фонтан", "5", ...]
    → rapidfuzz vs "5 ст фонтан" → score 100  ← winner
    → rapidfuzz vs "7 ст фонтан" → score ~73  ← ниже порога

  "едут с 4й фонтана"
    → лемматизация: "ехать с 4 фонтан"   ← "4й" (Anum) → "4"
    → n-граммы: ["4 фонтан", ...]
    → token_set_ratio("4 фонтан", "4 ст фонтан") = 100  ← winner
"""

import logging
import re
from typing import Dict, List, Optional, Set, Tuple

import mawo_pymorphy3 as pymorphy3
from rapidfuzz import process as rf_process, fuzz

from .text_preprocessor import clean

# Числово-буквенные порядковые ("4й", "10-й") → числовой префикс
_DIGIT_PREFIX_RE = re.compile(r'^(\d+)')

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Порог схожести в шкале rapidfuzz (0-100).
# Если из settings приходит 0-1, конвертируется внутри async_find_entities.
_DEFAULT_THRESHOLD = 72.0

SIMILARITY_THRESHOLD: float
_raw = (
    settings.similarity.entity_similarity_threshold
    if settings and settings.similarity
    else None
)
if _raw is not None:
    # settings хранит порог в шкале 0-1 → конвертируем в 0-100
    SIMILARITY_THRESHOLD = _raw * 100 if _raw <= 1.0 else float(_raw)
else:
    SIMILARITY_THRESHOLD = _DEFAULT_THRESHOLD

MAX_ENTITIES = 3

# Маппинг: нормальная форма порядкового числительного → строка цифры.
# Покрывает станции Фонтана (1-16) и Люстдорфской (1-10) с запасом до 20.
ORDINAL_MAP: Dict[str, str] = {
    'первый': '1',
    'второй': '2',
    'третий': '3',
    'четвёртый': '4', 'четвертый': '4',
    'пятый': '5',
    'шестой': '6',
    'седьмой': '7',
    'восьмой': '8',
    'девятый': '9',
    'десятый': '10',
    'одиннадцатый': '11',
    'двенадцатый': '12',
    'тринадцатый': '13',
    'четырнадцатый': '14',
    'пятнадцатый': '15',
    'шестнадцатый': '16',
    'семнадцатый': '17',
    'восемнадцатый': '18',
    'девятнадцатый': '19',
    'двадцатый': '20',
}

class LexicalMatcher:
    """Поиск улиц через морфологическую нормализацию + нечёткое строковое совпадение."""

    def __init__(self) -> None:
        self._morph = pymorphy3.MorphAnalyzer()
        self._stopwords: Set[str] = set()
        # Два параллельных списка — тексты и мета. Индекс синхронизирован.
        self._alias_texts: List[str] = []
        self._alias_meta: List[Tuple[int, str]] = []  # (street_id, original_name)
        self._initialized = False

    @property
    def morph(self):
        """Общий MorphAnalyzer — переиспользуется LayerClassifier (один на процесс)."""
        return self._morph

    # ---------------------------------------------------------------- initialize

    async def initialize(self, pg_pool) -> bool:
        """Загружает стоп-слова и лемматизирует все aliases из БД."""
        try:
            async with pg_pool.acquire() as conn:
                sw_rows = await conn.fetch("SELECT word FROM stopwords")
                self._stopwords = {row['word'].lower() for row in sw_rows}
                logger.info(f"[Lexical] Loaded {len(self._stopwords)} stopwords")

                street_rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )

            count = self._build_alias_index(street_rows)
            logger.info(f"[Lexical] Indexed {count} aliases from {len(street_rows)} streets")
            self._initialized = True
            return True

        except Exception as exc:
            logger.error(f"[Lexical] Init failed: {exc}")
            return False

    def _build_alias_index(self, rows) -> int:
        """Строит индекс лемматизированных aliases. Потокобезопасен при замене атомарно."""
        texts: List[str] = []
        meta: List[Tuple[int, str]] = []

        for row in rows:
            street_id: int = row['id']
            names: List[str] = row['names'] or []
            for name in names:
                lemma = self._lemmatize_phrase(name)
                if lemma:
                    texts.append(lemma)
                    meta.append((street_id, name))

        self._alias_texts = texts
        self._alias_meta = meta
        return len(texts)

    # --------------------------------------------------------- reindex (compat)

    async def reindex_all(self, pg_pool) -> int:
        """Перезагружает alias-индекс из БД (вызывается при изменении таблицы streets)."""
        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, names FROM streets WHERE geom IS NOT NULL"
                )
            count = self._build_alias_index(rows)
            logger.info(f"[Lexical] Reindexed {count} aliases")
            return count
        except Exception as exc:
            logger.error(f"[Lexical] reindex_all failed: {exc}")
            return 0

    async def reindex_street(self, pg_pool, street_id: int) -> None:
        """Обновляет aliases для одной улицы (после pg_notify streets_updated)."""
        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, names FROM streets WHERE id = $1 AND geom IS NOT NULL",
                    street_id,
                )

            # Удаляем все старые entries для этого street_id
            pairs = [
                (t, m) for t, m in zip(self._alias_texts, self._alias_meta)
                if m[0] != street_id
            ]
            if pairs:
                self._alias_texts, self._alias_meta = zip(*pairs)  # type: ignore[assignment]
                self._alias_texts = list(self._alias_texts)
                self._alias_meta = list(self._alias_meta)
            else:
                self._alias_texts = []
                self._alias_meta = []

            # Добавляем новые (если улица ещё существует)
            if row:
                for name in (row['names'] or []):
                    lemma = self._lemmatize_phrase(name)
                    if lemma:
                        self._alias_texts.append(lemma)
                        self._alias_meta.append((street_id, name))

            logger.info(f"[Lexical] Reindexed street {street_id}")
        except Exception as exc:
            logger.error(f"[Lexical] reindex_street({street_id}) failed: {exc}")

    async def close(self) -> None:
        """No-op — нет внешних ресурсов для освобождения."""

    # --------------------------------------------------- morphology / lemmatize

    def _lemmatize_word(self, word: str) -> str:
        """Лемматизирует одно слово; порядковые числительные конвертирует в цифры."""
        if word.isdigit():
            return word

        parses = self._morph.parse(word)
        if not parses:
            return word

        best = parses[0]

        # Порядковое числительное любого рода/падежа/числа → арабская цифра
        if 'Anum' in best.tag:
            # Словесное: "четвёртый" → "4"
            digit = ORDINAL_MAP.get(best.normal_form)
            if digit:
                return digit
            # Цифровое: "4й", "4-й" → "4" (ORDINAL_MAP не покрывает числовые формы)
            m = _DIGIT_PREFIX_RE.match(word)
            if m:
                return m.group(1)

        return best.normal_form

    def _lemmatize_phrase(self, text: str) -> str:
        """Очищает (text_preprocessor.clean) и лемматизирует фразу целиком."""
        words = clean(text).split()
        return ' '.join(self._lemmatize_word(w) for w in words if w)

    # -------------------------------------------------------- ngram generation

    def _generate_ngrams(self, words: List[str]) -> List[str]:
        """Генерирует n-граммы длиной 1-4 из лемматизированного текста.

        Условие включения: хотя бы одно слово в чанке не является стоп-словом
        и имеет длину >= 2. Одиночные цифры допустимы только в составе
        многословных n-грамм.
        """
        ngrams: List[str] = []
        n = len(words)

        for size in range(1, min(5, n + 1)):
            for i in range(n - size + 1):
                chunk = words[i:i + size]
                meaningful = [
                    w for w in chunk
                    if w not in self._stopwords and len(w) >= 2
                ]
                # Для размера 1: одиночная цифра не является значимым словом
                if size == 1 and len(chunk) == 1 and chunk[0].isdigit():
                    continue
                if meaningful:
                    ngrams.append(' '.join(chunk))

        return ngrams

    # --------------------------------------------------------- entity search

    async def async_find_entities(
        self,
        text: str,
        top_k: int = MAX_ENTITIES,
        threshold: float = SIMILARITY_THRESHOLD,
        pg_pool=None,           # не используется — поиск локальный
        event_location: Optional[tuple] = None,
    ) -> List[Dict]:
        """Находит улицы в тексте через лемматизацию + rapidfuzz.

        Args:
            text: Исходный текст сообщения.
            top_k: Максимальное количество результатов.
            threshold: Порог схожести. 0-1 → конвертируется в 0-100 автоматически.
            pg_pool: Игнорируется (compat).
            event_location: Игнорируется (compat).

        Returns:
            Список dict с ключами: street_id, matched_name, text, score, source.
        """
        if not self._initialized:
            logger.warning("[Lexical] Not initialized")
            return []

        if not self._alias_texts:
            logger.warning("[Lexical] Alias index is empty")
            return []

        # Нормализация порога: settings хранит 0-1, rapidfuzz работает с 0-100
        score_cutoff = threshold if threshold > 1.0 else threshold * 100

        # Лемматизация текста сообщения
        lemma_phrase = self._lemmatize_phrase(text)
        words = lemma_phrase.split()
        if not words:
            return []

        logger.debug(f"[Lexical] '{text}' → '{lemma_phrase}'")

        # Генерация n-грамм
        ngrams = self._generate_ngrams(words)
        if not ngrams:
            return []

        logger.debug(f"[Lexical] ngrams ({len(ngrams)}): {ngrams}")

        # Нечёткий поиск: для каждого n-грамма берём лучший совпадающий alias
        best_by_street: Dict[int, Dict] = {}

        for ngram in ngrams:
            ngram_len = len(ngram.split())

            # Унiграммы: fuzz.ratio — не допускает "дом" → "дом мебели" = 100
            # Мультиграммы: token_set_ratio — допускает перестановку слов
            scorer = fuzz.ratio if ngram_len == 1 else fuzz.token_set_ratio

            # Коэффициент длины: длинные n-граммы получают приоритет над короткими.
            # 1-gram: ×0.85 · 2-gram: ×0.90 · 3-gram: ×0.95 · 4-gram: ×1.00
            # Эффект: "хутор"→"Хуторская" 71×0.85=60 < порог; "червонный хутор"→96×0.90=86 ✓
            length_bias = 0.85 + 0.05 * min(ngram_len - 1, 3)

            matches = rf_process.extract(
                ngram,
                self._alias_texts,
                scorer=scorer,
                score_cutoff=score_cutoff,
                limit=10,
            )
            for _matched_text, score, idx in matches:
                adjusted = score * length_bias
                if adjusted < score_cutoff:
                    continue  # пересматриваем порог после корректировки

                street_id, original_name = self._alias_meta[idx]
                if street_id not in best_by_street or adjusted > best_by_street[street_id]['_adjusted']:
                    best_by_street[street_id] = {
                        'street_id': street_id,
                        'matched_name': original_name,
                        'text': ngram,
                        'score': adjusted / 100.0,  # нормализуем в 0..1 для compat
                        '_adjusted': adjusted,       # внутренний ключ для сравнения
                        'source': 'lexical',
                    }

        # Убираем внутренний служебный ключ перед возвратом
        for v in best_by_street.values():
            v.pop('_adjusted', None)

        entities = sorted(
            best_by_street.values(),
            key=lambda x: x['score'],
            reverse=True,
        )[:top_k]

        logger.info(
            f"[Lexical] Found {len(entities)}: "
            f"{[(e['matched_name'], round(e['score'], 2)) for e in entities]}"
        )
        return entities
