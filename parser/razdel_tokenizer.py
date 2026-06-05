"""RazdelTokenizer — токенизация русского текста через mawo-razdel.

mawo_razdel правильно обрабатывает русские аббревиатуры ("ул.", "пр.", "пер."),
инициалы ("А. С. Пушкин"), дефисные слова ("Малая-Арнаутская"), числа с
плавающей точкой ("3.14") — то, что просто `.split()` теряет.

Token хранит .start/.stop — это позволяет street_matcher скользящим окном по
токенам брать целые фразы (1..N слов) как кандидаты для фуззи-матча.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class Token:
    """Токен с позицией в исходной строке."""
    text: str
    start: int
    stop: int
    # True, если токену непосредственно предшествует '#' (тег вида #Name/##Name).
    # Авторы канала так помечают улицы — это high-confidence сигнал для матчера
    # (см. StreetMatcher: bonus к score + обход multiword-penalty).
    is_anchored: bool = False


class RazdelTokenizer:
    """Тонкая обёртка над mawo_razdel.tokenize/sentenize."""

    def __init__(self) -> None:
        # Импорт лениво — чтобы тесты на импорт парсера не падали без razdel.
        from mawo_razdel import tokenize as _tokenize, sentenize as _sentenize
        self._tokenize = _tokenize
        self._sentenize = _sentenize

    def tokenize(self, text: str) -> List[Token]:
        """Список Token'ов с правильными границами для русского текста.

        Помечает `is_anchored` у токенов, перед которыми (без пробела) стоит '#'
        — хэштег-тег улицы. Сам символ '#' остаётся отдельным токеном и
        отсеивается позже префильтром пунктуации (G2).

        Склеивает пары [DIGIT, "я"] в один токен "Nя" (порядковое числительное
        с раздельным суффиксом: "5 я", "7 я" → "5я", "7я"), чтобы sliding-window
        строил кандидаты "5я люстдорфская" вместо ложного "5 я" → "25я".
        """
        if not text:
            return []
        raw = list(self._tokenize(text))
        out: List[Token] = []
        i = 0
        while i < len(raw):
            t = raw[i]
            anchored = t.start > 0 and text[t.start - 1] == '#'
            if (t.text.isdigit()
                    and i + 1 < len(raw)
                    and raw[i + 1].text.lower() == 'я'):
                next_t = raw[i + 1]
                out.append(Token(text=t.text + 'я', start=t.start,
                                 stop=next_t.stop, is_anchored=anchored))
                i += 2
            else:
                out.append(Token(text=t.text, start=t.start, stop=t.stop,
                                 is_anchored=anchored))
                i += 1
        return out

    def sentenize(self, text: str) -> List[str]:
        """Список текстов предложений."""
        if not text:
            return []
        return [s.text for s in self._sentenize(text)]
