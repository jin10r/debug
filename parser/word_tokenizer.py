"""word_tokenizer — разбивка текста на слова по не-буквенным символам.

Текст режется на максимальные run'ы алфавитно-цифровых символов; любой другой
символ (дефис, пунктуация, emoji, слэш, пробел) — разделитель. Это согласует
message-сторону с индекс-стороной: индекс строится из clean(name), а clean()
уже режет по тому же классу символов (_NON_ALNUM_RE). Дефис не склеивает два
названия улиц в один токен, поэтому "Градоначальницкая-Олейника" даёт два слова
и матчер резолвит улицы по отдельности.

Цифры остаются частью слова (порядковые улицы "1-я станция", "5я Люстдорфская").

Token хранит .start/.stop — это позволяет street_matcher скользящим окном по
токенам брать целые фразы (1..N слов) как кандидаты для фуззи-матча.

`tokenize` — модульная функция без состояния (precompiled _WORD_RE).
"""

import re
from dataclasses import dataclass
from typing import List

# Максимальный run алфавитно-цифровых символов (Unicode, без '_').
_WORD_RE = re.compile(r'[^\W_]+', re.UNICODE)


@dataclass
class Token:
    """Токен с позицией в исходной строке."""
    text: str
    start: int
    stop: int


def tokenize(text: str) -> List[Token]:
    """Список Token'ов: максимальные алфавитно-цифровые run'ы.

    Символ '#' словом не становится (не алфавитно-цифровой) и к моменту
    токенизации уже удалён в preprocess_light — теги не влияют на токены.

    Склеивает пары [DIGIT, "я"] в один токен "Nя" (порядковое числительное
    с раздельным суффиксом: "5 я", "7-я" → "5я", "7я"), чтобы sliding-window
    строил кандидаты "5я люстдорфская" и совпадал с индексом (clean() даёт
    "1 я" из "1-я", далее склейка → "1я").
    """
    if not text:
        return []
    raw = [
        Token(text=m.group(0), start=m.start(), stop=m.end())
        for m in _WORD_RE.finditer(text)
    ]
    out: List[Token] = []
    i = 0
    while i < len(raw):
        t = raw[i]
        if (t.text.isdigit()
                and i + 1 < len(raw)
                and raw[i + 1].text.lower() == 'я'):
            next_t = raw[i + 1]
            out.append(Token(text=t.text + 'я', start=t.start,
                             stop=next_t.stop))
            i += 2
        else:
            out.append(t)
            i += 1
    return out
