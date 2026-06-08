"""Morphology — централизованная работа с mawo_pymorphy3.

Один MorphAnalyzer на процесс (DAWG-словарь ~15-20 МБ RAM, инициализация
неэкономная). Используется street_matcher для лемматизации alias-индекса и
n-грамм, layer_classifier для лемматизации ключевых слов и токенов сообщения.

`Lemma` dataclass — единая единица между токенизацией и финальной
обработкой (matcher, classifier). Содержит исходную форму, нормальную форму
и POS-теги (включая распознавание имён собственных через pymorphy3 Geox/Name/Surn).

`ORDINAL_MAP` — порядковые числительные в нормальной форме → арабская цифра.
Покрывает станции Фонтана (1-16) и Люстдорфской (1-10) с запасом до 20.
Конвертирует "пятый" → "5", чтобы "на пятой Фонтана" находило alias "5 ст Фонтана".
"""

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol

import mawo_pymorphy3 as pymorphy3

# "5я", "7я" и т.д. — порядковое числительное с суффиксом без дефиса.
_DIGIT_ORDINAL_RE = re.compile(r'^(\d+)[яЯ]$')


ORDINAL_MAP = {
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
    'двадцать первый': '21',
    'двадцать второй': '22',
    'двадцать третий': '23',
    'двадцать четвёртый': '24', 'двадцать четвертый': '24',
    'двадцать пятый': '25',
    'двадцать шестой': '26',
    'двадцать седьмой': '27',
    'двадцать восьмой': '28',
    'двадцать девятый': '29',
    'тридцатый': '30',
}

# Грамматические теги pymorphy3, указывающие на имя собственное / топоним.
_PROPER_NOUN_TAGS = frozenset({'Name', 'Surn', 'Patr', 'Geox', 'Orgn'})


@dataclass
class Lemma:
    """Лемма с грамматической разметкой."""
    surface: str         # исходная словоформа
    normal_form: str     # нормальная форма (или цифра для порядкового числительного)
    pos: str             # NOUN, ADJF, VERB, PREP, ...
    is_proper: bool      # имя собственное / топоним


class _HasText(Protocol):
    """Утиная типизация: любой объект с .text — Token из word_tokenizer или эквивалент."""
    text: str


class Morphology:
    """Обёртка над mawo_pymorphy3 с распознаванием порядковых числительных."""

    # Размер LRU-кэша лемматизации. Слова с message-частотой ~15 уник./сообщ.
    # → ~150 сообщений/sec при ширине стрима → кэш-hit ~80% на повторах
    # топонимов и common-words. ~10K записей × ~100 bytes = ~1MB RAM.
    _LEMMA_CACHE_MAX = 10000
    # Фраз обычно меньше (~1000 алиасов × несколько вариантов), но lemma_for_phrase
    # вызывается в _build_alias_index при каждом reindex_all → выигрыш ощутим.
    _PHRASE_CACHE_MAX = 2000

    def __init__(self) -> None:
        self._morph = pymorphy3.MorphAnalyzer()
        # OrderedDict как LRU: O(1) вытеснение через popitem(last=False),
        # O(1) обновление позиции через move_to_end. Кэшируем по нижнему
        # регистру — pymorphy3 не различает Малой/малой/МАЛОЙ.
        self._lemma_cache: "OrderedDict[str, Lemma]" = OrderedDict()
        self._phrase_cache: "OrderedDict[str, str]" = OrderedDict()

    @property
    def analyzer(self):
        """Сырой MorphAnalyzer (для legacy потребителей вроде layer_classifier)."""
        return self._morph

    def lemmatize_word(self, word: str) -> Lemma:
        """Леммa слова. Цифры возвращаются как есть; порядковые → арабские.

        LRU-кэш: повторные слова возвращаются мгновенно без вызова pymorphy3
        (который ~50µs/слово). На реальном корпусе hit-rate ~70-85%.
        """
        if not word:
            return Lemma('', '', '', False)

        # Cache lookup. Кэшируем по lowercase ключу.
        key = word.lower()
        cached = self._lemma_cache.get(key)
        if cached is not None:
            self._lemma_cache.move_to_end(key)
            # Surface берём от исходного слова — регистр может отличаться
            return Lemma(word, cached.normal_form, cached.pos, cached.is_proper)

        if word.isdigit():
            result = Lemma(word, word, 'NUMR', False)
            self._cache_store(key, result)
            return result

        m = _DIGIT_ORDINAL_RE.match(word)
        if m:
            result = Lemma(word, m.group(1), 'NUMR', False)
            self._cache_store(key, result)
            return result

        parses = self._morph.parse(word)
        if not parses:
            result = Lemma(word, key, '', False)
            self._cache_store(key, result)
            return result

        best = parses[0]
        pos = str(best.tag.POS) if best.tag.POS else ''
        normal = best.normal_form

        # Порядковое числительное любого рода/падежа/числа → арабская цифра
        if 'Anum' in best.tag:
            digit = ORDINAL_MAP.get(normal)
            if digit:
                result = Lemma(word, digit, 'NUMR', False)
                self._cache_store(key, result)
                return result

        is_proper = any(tag in best.tag for tag in _PROPER_NOUN_TAGS)
        result = Lemma(word, normal, pos, is_proper)
        self._cache_store(key, result)
        return result

    def _cache_store(self, key: str, lemma: Lemma) -> None:
        """LRU-вставка с вытеснением при превышении лимита."""
        self._lemma_cache[key] = lemma
        while len(self._lemma_cache) > self._LEMMA_CACHE_MAX:
            self._lemma_cache.popitem(last=False)

    def lemmatize_tokens(self, tokens: Iterable[_HasText]) -> List[Lemma]:
        """Лемматизирует последовательность токенов (объекты с .text)."""
        return [self.lemmatize_word(t.text) for t in tokens]

    def lemmatize_words(self, words: Iterable[str]) -> List[Lemma]:
        """Лемматизирует последовательность строк."""
        return [self.lemmatize_word(w) for w in words if w]

    def lemma_for_phrase(self, text: str) -> str:
        """Single-shot лемматизация фразы (split → лемма каждого → join).

        Используется street_matcher для канонизации alias-имени в индексе,
        когда отдельная токенизация избыточна (alias уже чистый, без пунктуации).

        Phrase-level LRU cache (2000): reindex_all обрабатывает ~1000 алиасов;
        при reload без cache каждый раз заново лемматизируется. С кешем —
        instant hit на повторных вызовах.
        """
        if not text:
            return ''

        cached = self._phrase_cache.get(text)
        if cached is not None:
            self._phrase_cache.move_to_end(text)
            return cached

        result = ' '.join(
            self.lemmatize_word(w).normal_form
            for w in text.split() if w
        )
        self._phrase_cache[text] = result
        while len(self._phrase_cache) > self._PHRASE_CACHE_MAX:
            self._phrase_cache.popitem(last=False)
        return result
