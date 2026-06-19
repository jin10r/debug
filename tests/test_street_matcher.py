"""Регрессионный харнесс морфологического распознавателя улиц.

Строит индекс прямо из `postgres/data/streets.csv` + `stopwords.csv` (без БД) и
проверяет ключевые свойства распознавателя на кейсах из боевого экспорта:

  • recall падежей коротких OOV-имён (Гаванной → Гаванная) — то, что ломалось;
  • отсутствие ложных матчей на обычных словах (среди≠Средняя, металлик≠
    Металлистов, Маяковского≠Маловского, "дорога на"≠Южная дорога);
  • разрешение over-stem коллизий по surface (Гаванная→150, не Гаваи→149);
  • орфо-корректор (Tier 2) ловит опечатки (Раскидпйловская→Раскидайловская).

Тесты SKIP-аются, если в окружении нет тяжёлых runtime-зависимостей парсера
(mawo_pymorphy3 / rapidfuzz / snowballstemmer) — см. pytest.importorskip.
"""

import csv
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("mawo_pymorphy3")
pytest.importorskip("rapidfuzz")
pytest.importorskip("snowballstemmer")

ROOT = Path(__file__).resolve().parent.parent
csv.field_size_limit(10_000_000)  # streets.csv: очень длинные WKT-поля

# parser/__init__.py тянет asyncpg/kurigram; подменяем пакет стабом с __path__,
# чтобы относительные импорты сабмодулей резолвились без __init__.
if "parser" not in sys.modules:
    _pkg = types.ModuleType("parser")
    _pkg.__path__ = [str(ROOT / "parser")]
    sys.modules["parser"] = _pkg

from parser.morphology import Morphology              # noqa: E402
from parser.phonetic_index import PhoneticIndex       # noqa: E402
from parser.street_matcher import StreetMatcher        # noqa: E402
from parser.word_tokenizer import tokenize             # noqa: E402
from parser.text_preprocessor import (                  # noqa: E402
    preprocess_light, strip_tail, is_promotional,
)


def _load_streets():
    rows, name2id = [], {}
    with open(ROOT / "postgres/data/streets.csv", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        sid = 0
        for r in rd:
            if not r or not r[0].strip():
                continue
            sid += 1
            names = r[0].split("|")
            rows.append({"id": sid, "names": names})
            name2id.setdefault(names[0], sid)
    return rows, name2id


def _load_stopwords():
    stop = set()
    with open(ROOT / "postgres/data/stopwords.csv", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            if r and r[0].strip():
                stop.add(r[0].strip().lower())
    return stop


@pytest.fixture(scope="module")
def matcher():
    morph = Morphology()
    index = PhoneticIndex(morph)
    rows, name2id = _load_streets()
    index.build(rows)
    m = StreetMatcher(morph, index)
    m._initialized = True
    m._stopwords = _load_stopwords()
    m._morph = morph
    m._name2id = name2id  # convenience for tests
    return m


def _ids(matcher, text):
    pre = preprocess_light(strip_tail(text or ""))
    toks = tokenize(pre)
    lemmas = matcher._morph.lemmatize_tokens(toks)
    return {e["street_id"] for e in matcher.find_streets(tokens=toks, lemmas=lemmas)}


# --------------------------------------------------------------- recall (падежи)

@pytest.mark.parametrize("text", [
    "На Гаванной опасно, с блокпост побежали искать. На всей гаванной опасно",
    "Опущенные собрались с Гаванной и поехали по Маяковского",
    "Гаванная блокпост",  # номинатив
])
def test_gavannaya_recall(matcher, text):
    """Косвенный падеж короткого OOV-имени должен находиться (это ломалось)."""
    assert matcher._name2id["Гаванная"] in _ids(matcher, text)


def test_oblique_long_name(matcher):
    """Длинное имя в косвенном падеже — Ланжероновскую → Ланжероновская."""
    assert matcher._name2id["Ланжероновская"] in _ids(
        matcher, "Не поворачивайте на Ланжероновскую, там перехватчики"
    )


# ------------------------------------------------- precision (нет ложных матчей)

def test_no_fp_common_word_sredi(matcher):
    """'среди' (предлог) не должно матчиться на улицу Средняя."""
    assert matcher._name2id["Средняя"] not in _ids(
        matcher, "куча перехватчиков среди припаркованных машин"
    )


def test_no_fp_metallik(matcher):
    """'металлик' (цвет авто) не должно матчиться на Металлистов."""
    assert matcher._name2id["Металлистов"] not in _ids(
        matcher, "темный металлик номер с 866 начинается"
    )


def test_no_fp_homograph_mayakovsky(matcher):
    """'Маяковского' (нет в данных) не должно снапаться на Маловского."""
    assert matcher._name2id["Маловского"] not in _ids(
        matcher, "собрались и поехали по Маяковского"
    )


def test_no_fp_doroga_na(matcher):
    """'дорога на' не должно матчиться на 'Южная дорога'."""
    assert matcher._name2id["Южная дорога"] not in _ids(
        matcher, "где гоночка дорога на 7-й, тормозят копи"
    )


# ---------------------------------------------------- over-stem collision resolve

def test_stem_collision_resolved(matcher):
    """Гаваи(149) и Гаванная(150) → один стем 'гава'; surface-разрешение → 150."""
    ids = _ids(matcher, "Гаванная блокпост")
    assert matcher._name2id["Гаванная"] in ids
    assert matcher._name2id["Гаваи"] not in ids


# ------------------------------------------------------------- typo-corrector

def test_surface_typo(matcher):
    """Орфо-корректор (Tier 2) ловит реальную опечатку."""
    assert matcher._name2id["Раскидайловская"] in _ids(
        matcher, "Раскидпйловская белый т4 катается против движения"
    )


# ------------------------------------------------------------ Phase B: word-order

def test_word_order_independent(matcher):
    """'Застава 2' ≡ '2 застава' (Tier 1b, порядок-независимый)."""
    sid = matcher._name2id["2 застава"]
    assert sid in _ids(matcher, "Застава 2 в сторону ленпоселка")
    assert sid in _ids(matcher, "в сторону 2 заставы")


# ------------------------------------------------------- Phase B: spelled ordinals

@pytest.mark.parametrize("text,name", [
    ("в сторону Второй Заставы блокпост", "2 застава"),
    ("на пятой Фонтана блокпост", "5 Фонтана"),
])
def test_spelled_ordinal(matcher, text, name):
    """Словесные порядковые в косвенном падеже → цифра (второй→2, пятой→5)."""
    assert matcher._name2id[name] in _ids(matcher, text)


# ----------------------------------------------------------- Phase B: relevance gate

@pytest.mark.parametrize("text,expected", [
    ("ПЛАТНУЮ ПОДПИСКУ НА КАНАЛ, не нарветесь на мошенников, отписаться", True),
    ("Лучший канал t.me/something, присоединяйтесь", True),
    ("Подпишись на @some_channel", True),
    ("Гайдара блокпост в сторону 25й", False),
    ("На Гаванной опасно, перехватчики среди машин", False),
])
def test_is_promotional(text, expected):
    """Реклама ловится (ссылки/хендлы/подписка), реальные репорты — нет."""
    assert is_promotional(text) is expected


# ----------------------------------------------------------- Phase B: max-span dedup

def test_max_span_drops_subspan():
    """Под-спан, вложенный в более длинный матч, отбрасывается.

    Синтетический мини-индекс: 'Дерибасовская' (1 слово) и 'Большая
    Дерибасовская' (2 слова). На 'по Большой Дерибасовской' остаётся только
    длинный матч — короткий под-спан 'Дерибасовской' подавляется.
    """
    morph = Morphology()
    index = PhoneticIndex(morph)
    index.build([
        {"id": 1, "names": ["Дерибасовская"]},
        {"id": 2, "names": ["Большая Дерибасовская"]},
    ])
    m = StreetMatcher(morph, index)
    m._initialized = True
    m._stopwords = set()
    m._morph = morph
    toks = tokenize(preprocess_light("поехал по Большой Дерибасовской"))
    ids = {e["street_id"] for e in m.find_streets(tokens=toks, lemmas=morph.lemmatize_tokens(toks))}
    assert ids == {2}
