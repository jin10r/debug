"""Предобработка текста сообщений parser.

Две стадии:
  • preprocess_light — мягкая очистка, СОХРАНЯЕТ регистр и пунктуацию. Нужна,
    т.к. её результат уходит на фронтенд как description (там регистр/emoji/
    пунктуация должны остаться); токенайзер пунктуацию всё равно отбрасывает.
  • clean — агрессивная очистка, lowercase + без пунктуации. Применяется к
    alias-именам при сборке phonetic-индекса и для канонизации фрагментов.

Конвейер обработки сообщения:
  1. strip_tail   — отбросить служебный хвост;
  2. preprocess_light — для description + токенизации/морфологии;
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
# «б/п», «б\п», «б / п» → «бп»: токенайзер режет по слэшу на отдельные слова,
# из-за чего аббревиатура блокпоста не совпадала с layer-keyword «бп» (traffic).
# Схлопываем до старта токенизации.
_BP_SLASH_RE = re.compile(r'\bб\s*[/\\]\s*п\b', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')
_NON_ALNUM_RE = re.compile(r'[^a-zA-Zа-яА-ЯёЁ0-9]')
_SPACES_RE = re.compile(r'\s+')

# Emoji и пиктограммы. При поиске названий улиц/сущностей это шум: они
# попадают в токены, ломают лемматизацию и смещают границы фраз. Убираются
# ТОЛЬКО на этапе матчинга (см. strip_emoji) — в description, уходящем на
# фронтенд, emoji СОХРАНЯЮТСЯ. Диапазоны покрывают основные emoji-блоки
# Unicode плюс служебные модификаторы (variation selectors, ZWJ, keycap).
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # Misc Symbols/Pictographs … Symbols & Pictographs Ext-A
    "\U0001F000-\U0001F02F"  # Mahjong / Domino
    "\U0001F0A0-\U0001F0FF"  # Playing cards
    "\U00002600-\U000027BF"  # Misc symbols + Dingbats
    "\U00002B00-\U00002BFF"  # Misc Symbols and Arrows
    "\U00002190-\U000021FF"  # Arrows
    "\U0000FE00-\U0000FE0F"  # Variation Selectors
    "\U0001F1E6-\U0001F1FF"  # Regional indicators (флаги)
    "\U0000200D"             # Zero Width Joiner
    "\U000020E3"             # Combining Enclosing Keycap
    "\U00002122\U00002139"   # ™ ℹ
    "]+",
    flags=re.UNICODE,
)
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


def strip_emoji(text: str) -> str:
    """Удалить emoji/пиктограммы — для этапа матчинга названий сущностей.

    НЕ применять к тексту, уходящему на фронтенд: там emoji должны остаться
    в исходном виде. Используется только перед токенизацией/классификацией.
    """
    if not text:
        return ''
    return _SPACES_RE.sub(' ', _EMOJI_RE.sub(' ', text)).strip()


def preprocess_light(text: str) -> str:
    """Мягкая очистка: снять HTML, удалить таймстампы, нормализовать укр. буквы.

    СОХРАНЯЕТ регистр и пунктуацию — результат уходит на фронтенд как description.
    Токенайзер (word_tokenizer.tokenize) сам режет по не-буквенным символам.
    """
    if not text:
        return ''

    text = html.unescape(text)
    text = _TAG_RE.sub(' ', text)
    text = _TIME_RE.sub(' ', text)
    text = _BP_SLASH_RE.sub('бп', text)
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
