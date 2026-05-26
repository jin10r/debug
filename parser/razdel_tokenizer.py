"""RazdelTokenizer — токенизация русского текста через mawo-razdel.

mawo_razdel правильно обрабатывает русские аббревиатуры ("ул.", "пр.", "пер."),
инициалы ("А. С. Пушкин"), дефисные слова ("Малая-Арнаутская"), числа с
плавающей точкой ("3.14") — то, что просто `.split()` теряет.

Token хранит .start/.stop — это позволяет street_matcher выравнивать LOC-спаны
от NER с границами токенов и брать целые фразы как кандидаты для фуззи-матча.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class Token:
    """Токен с позицией в исходной строке."""
    text: str
    start: int
    stop: int


class RazdelTokenizer:
    """Тонкая обёртка над mawo_razdel.tokenize/sentenize."""

    def __init__(self) -> None:
        # Импорт лениво — чтобы тесты на импорт парсера не падали без razdel.
        from mawo_razdel import tokenize as _tokenize, sentenize as _sentenize
        self._tokenize = _tokenize
        self._sentenize = _sentenize

    def tokenize(self, text: str) -> List[Token]:
        """Список Token'ов с правильными границами для русского текста."""
        if not text:
            return []
        return [
            Token(text=t.text, start=t.start, stop=t.stop)
            for t in self._tokenize(text)
        ]

    def sentenize(self, text: str) -> List[str]:
        """Список текстов предложений."""
        if not text:
            return []
        return [s.text for s in self._sentenize(text)]
