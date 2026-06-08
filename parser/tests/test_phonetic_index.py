"""Unit-тесты PhoneticIndex — индекс улиц: surface-фразы (Tier 1) + lemma-кортежи
(Tier 2) + lemma-фразы (Tier 3).

После миграции NER→sliding-window фонетика убрана: индекс больше НЕ матчит сам
(метод query_phonetic удалён). Он только отдаёт параллельные списки, по которым
rapidfuzz гоняет StreetMatcher. Тайпо-устойчивость проверяется в
test_street_matcher.py (surface fuzzy), здесь — структура и точечные запросы индекса.

Запуск (нужен mawo-pymorphy3 для Morphology):
    python -m pytest parser/tests/test_phonetic_index.py -v
"""

import os

import pytest

os.environ.setdefault('BOT_TOKEN', 'x')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('JWT_SECRET', 'a' * 32)
os.environ.setdefault('REDIS_PASSWORD', 'z')

from parser.morphology import Morphology
from parser.phonetic_index import PhoneticEntry, PhoneticIndex


# Фикстура смешанная: 2 одно-токенные улицы (Пастера, Канатная) и 4 многословные.
SAMPLE_ROWS = [
    {'id': 1, 'names': ['Малая Арнаутская']},
    {'id': 2, 'names': ['Преображенская улица']},
    {'id': 3, 'names': ['проспект Шевченко']},
    {'id': 4, 'names': ['1-я станция Фонтана', 'первая станция Фонтана']},
    {'id': 5, 'names': ['Гагаринское плато']},
    {'id': 6, 'names': ['Пастера']},
    {'id': 7, 'names': ['Канатная']},
]


@pytest.fixture(scope='module')
def built_index():
    morph = Morphology()
    idx = PhoneticIndex(morph)
    idx.build(SAMPLE_ROWS)
    return idx


def _has_surface(idx: PhoneticIndex, street_id: int, needle: str = None) -> bool:
    """True если в surface-индексе есть фраза улицы (опц. содержащая needle)."""
    phrases, meta = idx.surface_phrases()
    return any(
        m.street_id == street_id and (needle is None or needle in p)
        for p, m in zip(phrases, meta)
    )


# ----------------------------------------------------------- build / структура

def test_empty_before_build():
    """Свежий индекс пуст."""
    assert PhoneticIndex(Morphology()).is_empty


def test_not_empty_after_build(built_index):
    """Индекс непустой после build."""
    assert not built_index.is_empty


def test_surface_phrases_parallel_lists_synced(built_index):
    """surface_phrases() возвращает параллельные списки одинаковой длины."""
    phrases, meta = built_index.surface_phrases()
    assert len(phrases) == len(meta) >= len(SAMPLE_ROWS)
    # surface — сырые алиасы (lowercase, без пунктуации).
    assert all(p == p.lower() for p in phrases)


def test_lemma_phrases_synced_with_meta(built_index):
    """lemma_phrases() — тоже параллельные списки одинаковой длины."""
    phrases, meta = built_index.lemma_phrases()
    assert len(phrases) == len(meta)
    assert len(phrases) >= len(SAMPLE_ROWS)


# ----------------------------------------------------------- surface (Tier 1)

def test_singleword_street_in_surface_index(built_index):
    """Одно-токенные улицы попадают в surface-индекс целиком."""
    assert _has_surface(built_index, 6, 'пастера')
    assert _has_surface(built_index, 7, 'канатная')


def test_multiword_street_stored_as_full_phrase(built_index):
    """Многословная улица хранится в surface-индексе ПОЛНОЙ фразой, не по словам."""
    phrases, meta = built_index.surface_phrases()
    street1 = [p for p, m in zip(phrases, meta) if m.street_id == 1]
    assert street1, 'у Малой Арнаутской должна быть surface-фраза'
    assert all(' ' in p for p in street1), 'хранится целой фразой, а не отдельными словами'
    assert any('арнаутская' in p and 'малая' in p for p in street1)


def test_all_aliases_indexed(built_index):
    """Оба алиаса многословной улицы (id=4) попадают в surface-индекс."""
    phrases, meta = built_index.surface_phrases()
    s4 = [p for p, m in zip(phrases, meta) if m.street_id == 4]
    assert any('1' in p for p in s4)        # «1-я станция Фонтана»
    assert any('первая' in p for p in s4)   # «первая станция Фонтана»


def test_entry_canonical_is_first_name(built_index):
    """canonical_name любой записи = первое значение из streets.names."""
    phrases, meta = built_index.surface_phrases()
    s4_meta = [m for m in meta if m.street_id == 4]
    assert s4_meta and all(m.canonical_name == '1-я станция Фонтана' for m in s4_meta)


# --------------------------------------------------------- lemma tuple (Tier 2)

def test_query_lemma_tuple_exact_match(built_index):
    """Tier 2: точный кортеж лемм находит улицу."""
    cands = built_index.query_lemma_tuple(('малый', 'арнаутский'))
    assert any(c.street_id == 1 for c in cands)


def test_query_lemma_tuple_miss(built_index):
    """Tier 2: несовпадающий кортеж — пустой результат."""
    assert built_index.query_lemma_tuple(('квантовый',)) == []
    assert built_index.query_lemma_tuple(()) == []


def test_get_lemma_tuple_for_street(built_index):
    """Обратный индекс street_id → lemma_tuple первого алиаса."""
    assert built_index.get_lemma_tuple_for_street(1) == ('малый', 'арнаутский')
    assert built_index.get_lemma_tuple_for_street(3) == ('проспект', 'шевченко')
    # Single-token street → tuple длины 1.
    tup6 = built_index.get_lemma_tuple_for_street(6)
    assert len(tup6) == 1 and tup6[0] in ('пастер', 'пастера')
    # Неизвестный id → пустой.
    assert built_index.get_lemma_tuple_for_street(999) == ()


# --------------------------------------------------------- lemma split (Tier 3)

def test_lemma_phrases_split_single_vs_multi(built_index):
    """lemma_phrases_split разносит одно- и многословные фразы корректно."""
    single_p, single_m, multi_p, multi_m = built_index.lemma_phrases_split()
    assert len(single_p) == len(single_m)
    assert len(multi_p) == len(multi_m)
    assert all(' ' not in p for p in single_p)
    assert all(' ' in p for p in multi_p)
    # Сумма = полный список lemma-фраз.
    all_phrases, _ = built_index.lemma_phrases()
    assert len(single_p) + len(multi_p) == len(all_phrases)


# ----------------------------------------------------------- replace_street

def test_replace_street_removes_entries():
    """replace_street(id, None) убирает все записи улицы, не задевая другие."""
    idx = PhoneticIndex(Morphology())
    idx.build(SAMPLE_ROWS)

    assert _has_surface(idx, 6)
    assert idx.get_lemma_tuple_for_street(6) != ()

    idx.replace_street(6, None)

    assert not _has_surface(idx, 6)
    assert idx.get_lemma_tuple_for_street(6) == ()
    # Другие улицы не задеты.
    assert _has_surface(idx, 7, 'канатная')


def test_replace_street_updates_existing():
    """replace_street с новой row заменяет содержимое + обратный индекс."""
    idx = PhoneticIndex(Morphology())
    idx.build(SAMPLE_ROWS)

    idx.replace_street(3, {'id': 3, 'names': ['Дерибасовская']})

    # Старый кортезь «проспект шевченко» больше не указывает на street 3.
    assert not any(
        c.street_id == 3 for c in idx.query_lemma_tuple(('проспект', 'шевченко'))
    )
    # Новая single-word улица в surface-индексе.
    assert _has_surface(idx, 3, 'дерибасовская')
    assert len(idx.get_lemma_tuple_for_street(3)) == 1


def test_phonetic_entry_shape():
    """PhoneticEntry — frozen dataclass (street_id, canonical_name, variant_text)."""
    e = PhoneticEntry(42, 'Канонічна', 'вариант')
    assert (e.street_id, e.canonical_name, e.variant_text) == (42, 'Канонічна', 'вариант')
    with pytest.raises(Exception):
        e.street_id = 0  # frozen
