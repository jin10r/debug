"""Предобработка текста сообщений parser.

Чистые функции без морфологии — единственное место предобработки текста
(раньше логика дублировалась между monitoring.py и message_processor.py).

Конвейер обработки сообщения (порядок фиксирован):
  1. strip_tail — отбросить служебный хвост сообщения;
  2. clean      — снять HTML, удалить время, пунктуацию, привести к нижнему
                  регистру;
  3. определение слоя (layer_classifier) — над очищенным текстом;
  4. если текст пустой или длиннее MAX_TEXT_LENGTH — поиск улиц пропускается.
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


def clean(text: str) -> str:
    """Нормализовать текст: снять HTML и время, убрать пунктуацию, lower-case.

    Порядок шагов важен: время удаляется до замены пунктуации на пробелы.
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
