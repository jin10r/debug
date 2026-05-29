"""Предобработка текста сообщений parser.

Две стадии:
  • preprocess_light — мягкая очистка, СОХРАНЯЕТ регистр и пунктуацию. Нужна
    razdel-токенизации (аббревиатуры "ул.", "пр." требуют точек) и сохранения
    границ слов для фонетической стратегии.
  • clean — агрессивная очистка, lowercase + без пунктуации. Применяется к
    alias-именам при сборке phonetic-индекса и для канонизации фрагментов.

Конвейер обработки сообщения:
  1. strip_tail   — отбросить служебный хвост;
  2. preprocess_light — для razdel/морфологии;
  3. clean(name)  — применяется в phonetic_index при сборке вариантов улицы.

`strip_tail` остаётся неизменным — он не зависит от регистра/пунктуации.
"""

import html
import re

# Маркеры служебного хвоста: всё начиная с самого раннего из них отбрасывается.
# Раньше '|' тоже был маркером, но он конфликтует с alias-separator в БД
# («улица|переулок» — synonym, ломалось при появлении в тексте). Удалён.
_TAIL_MARKERS = ('сообщить', 'подписаться')

# HH:MM с разделителем ':' или '.', часы 0-23, минуты 00-59.
# Удаляется до замены пунктуации, иначе '14:30' распалось бы на '14' и '30'.
_TIME_RE = re.compile(r'\b([01]?\d|2[0-3])[:.][0-5]\d\b')
_TAG_RE = re.compile(r'<[^>]+>')
_NON_ALNUM_RE = re.compile(r'[^a-zA-Zа-яА-ЯёЁ0-9]')
_SPACES_RE = re.compile(r'\s+')
# Украинские буквы → русские: і,ї → и; є → е.
_UA_TABLE = str.maketrans('іїєІЇЄ', 'иииИИЕ')

# Украинские окончания → русские эквиваленты (G6). Применяется ПОСЛЕ _UA_TABLE,
# чтобы дополнительно нормализовать прилагательные/существительные:
#   «Балкивська» → «Балковская», «Дерибасівський» → «Дерибасовский»,
#   «Пушкінської» → «Пушкинской».
# Регекспы case-insensitive чтобы покрыть Title Case.
_UA_SUFFIX_FIXES = [
    (re.compile(r'івська\b', re.IGNORECASE), 'овская'),
    (re.compile(r'івський\b', re.IGNORECASE), 'овский'),
    (re.compile(r'івської\b', re.IGNORECASE), 'овской'),
    (re.compile(r'івською\b', re.IGNORECASE), 'овской'),
    (re.compile(r'івському\b', re.IGNORECASE), 'овскому'),
    (re.compile(r'ська\b', re.IGNORECASE), 'ская'),
    (re.compile(r'ський\b', re.IGNORECASE), 'ский'),
    (re.compile(r'ської\b', re.IGNORECASE), 'ской'),
    (re.compile(r'ською\b', re.IGNORECASE), 'ской'),
    (re.compile(r'ському\b', re.IGNORECASE), 'скому'),
    (re.compile(r'цька\b', re.IGNORECASE), 'цкая'),
    (re.compile(r'цький\b', re.IGNORECASE), 'цкий'),
]


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

    СОХРАНЯЕТ регистр и пунктуацию — нужно razdel-токенизации (аббревиатуры
    "ул.", "пр." требуют точек). Возвращает строку, пригодную для подачи в
    mawo_razdel.tokenize.
    """
    if not text:
        return ''

    text = html.unescape(text)
    text = _TAG_RE.sub(' ', text)
    text = _TIME_RE.sub(' ', text)
    text = text.translate(_UA_TABLE)
    for pattern, repl in _UA_SUFFIX_FIXES:
        text = pattern.sub(repl, text)
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
    for pattern, repl in _UA_SUFFIX_FIXES:
        text = pattern.sub(repl, text)
    text = _SPACES_RE.sub(' ', text)
    return text.strip().lower()
