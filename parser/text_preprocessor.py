"""Предобработка текста сообщений parser.

Две стадии:
  • preprocess_light — мягкая очистка, СОХРАНЯЕТ регистр и пунктуацию.
    Нужна для NER (NewsNERTagger ожидает естественный текст) и razdel-токенизации
    (аббревиатуры "ул.", "пр." требуют точек).
  • clean — агрессивная очистка, lowercase + без пунктуации. Применяется только
    к маленьким фрагментам (LOC-спанам от NER или lemma index) — для канонизации
    под лексический фуззи-матч.

Конвейер обработки сообщения:
  1. strip_tail   — отбросить служебный хвост;
  2. preprocess_light — для NER/razdel/морфологии;
  3. clean(span)  — применяется в street_matcher к отдельным кандидатам.

`strip_tail` остаётся неизменным — он не зависит от регистра/пунктуации.
"""

import html
import re

# Сообщение длиннее этого порога не считается релевантной локацией:
# поиск улиц пропускается, событию назначается стратегия 'random'.
MAX_TEXT_LENGTH = 380

# Маркеры служебного хвоста: всё начиная с самого раннего из них отбрасывается.
_TAIL_MARKERS = ('сообщить', 'подписаться', '|')

# HH:MM с разделителем ':' или '.', часы 0-23, минуты 00-59.
# Удаляется до замены пунктуации, иначе '14:30' распалось бы на '14' и '30'.
_TIME_RE = re.compile(r'\b([01]?\d|2[0-3])[:.][0-5]\d\b')
_TAG_RE = re.compile(r'<[^>]+>')
_NON_ALNUM_RE = re.compile(r'[^a-zA-Zа-яА-ЯёЁ0-9]')
_SPACES_RE = re.compile(r'\s+')
# Украинские буквы → русские: і,ї → и; є → е.
_UA_TABLE = str.maketrans('іїє', 'иие')


def strip_tail(text: str) -> str:
    """Отбросить хвост сообщения начиная с самого раннего служебного маркера."""
    if not text:
        return ''

    lowered = text.lower()
    cut = len(text)
    for marker in _TAIL_MARKERS:
        pos = lowered.find(marker)
        if pos != -1 and pos < cut:
            cut = pos

    return text[:cut].strip()


def preprocess_light(text: str) -> str:
    """Мягкая очистка: снять HTML, удалить таймстампы, нормализовать укр. буквы.

    СОХРАНЯЕТ регистр и пунктуацию — это нужно NER'у и razdel-токенизации.
    Возвращает строку, пригодную для подачи в natasha.Doc и mawo_razdel.tokenize.
    """
    if not text:
        return ''

    text = html.unescape(text)
    text = _TAG_RE.sub(' ', text)
    text = _TIME_RE.sub(' ', text)
    text = text.translate(_UA_TABLE)
    text = _SPACES_RE.sub(' ', text)
    return text.strip()


def clean(text: str) -> str:
    """Агрессивная нормализация: убрать пунктуацию, lower-case.

    Применяется к небольшим фрагментам (LOC-спаны, alias-имена улиц) для
    приведения к канонической форме перед лексическим фуззи-матчем.
    """
    if not text:
        return ''

    text = html.unescape(text)
    text = _TAG_RE.sub(' ', text)
    text = _TIME_RE.sub(' ', text)
    text = _NON_ALNUM_RE.sub(' ', text)
    text = text.translate(_UA_TABLE)
    text = _SPACES_RE.sub(' ', text)
    return text.strip().lower()
