"""Morphology — централизованная работа с mawo_pymorphy3.

Один MorphAnalyzer на процесс (DAWG-словарь ~15-20 МБ RAM, инициализация
неэкономная). Используется street_matcher для лемматизации alias-индекса и
n-грамм, layer_classifier для лемматизации ключевых слов и токенов сообщения.

`Lemma` dataclass — единая единица между токенизацией (razdel) и финальной
обработкой (matcher, classifier). Содержит исходную форму, нормальную форму
и POS-теги (включая распознавание имён собственных через pymorphy3 Geox/Name/Surn).

`ORDINAL_MAP` — порядковые числительные в нормальной форме → арабская цифра.
Покрывает станции Фонтана (1-16) и Люстдорфской (1-10) с запасом до 20.
Конвертирует "пятый" → "5", чтобы "на пятой Фонтана" находило alias "5 ст Фонтана".
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol

import mawo_pymorphy3 as pymorphy3


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
    """Утиная типизация: любой объект с .text — Token из razdel или эквивалент."""
    text: str


class Morphology:
    """Обёртка над mawo_pymorphy3 с распознаванием порядковых числительных."""

    def __init__(self) -> None:
        self._morph = pymorphy3.MorphAnalyzer()

    @property
    def analyzer(self):
        """Сырой MorphAnalyzer (для legacy потребителей вроде layer_classifier)."""
        return self._morph

    def lemmatize_word(self, word: str) -> Lemma:
        """Леммa слова. Цифры возвращаются как есть; порядковые → арабские."""
        if not word:
            return Lemma('', '', '', False)
        if word.isdigit():
            return Lemma(word, word, 'NUMR', False)

        parses = self._morph.parse(word)
        if not parses:
            return Lemma(word, word.lower(), '', False)

        best = parses[0]
        pos = str(best.tag.POS) if best.tag.POS else ''
        normal = best.normal_form

        # Порядковое числительное любого рода/падежа/числа → арабская цифра
        if 'Anum' in best.tag:
            digit = ORDINAL_MAP.get(normal)
            if digit:
                return Lemma(word, digit, 'NUMR', False)

        is_proper = any(tag in best.tag for tag in _PROPER_NOUN_TAGS)
        return Lemma(word, normal, pos, is_proper)

    def lemmatize_tokens(self, tokens: Iterable[_HasText]) -> List[Lemma]:
        """Лемматизирует последовательность токенов (объекты с .text)."""
        return [self.lemmatize_word(t.text) for t in tokens]

    def lemmatize_words(self, words: Iterable[str]) -> List[Lemma]:
        """Лемматизирует последовательность строк."""
        return [self.lemmatize_word(w) for w in words if w]

    def lemma_for_phrase(self, text: str) -> str:
        """Single-shot лемматизация фразы (split → лемма каждого → join).

        Используется street_matcher для канонизации alias-имени в индексе,
        когда токенизация razdel'ом избыточна (alias уже чистый, без пунктуации).
        """
        if not text:
            return ''
        return ' '.join(
            self.lemmatize_word(w).normal_form
            for w in text.split() if w
        )
