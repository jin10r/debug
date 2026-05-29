"""Unit-тесты PhoneticIndex (после Phase 1 refactor: P1 — multi-word streets
матчатся только полнофразово, single-word индексация только для одно-токенных).

Запуск:
    pytest parser/tests/test_phonetic_index.py -v
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


def test_build_returns_positive_variant_count(built_index):
    """Индекс непустой после build."""
    assert not built_index.is_empty


def test_singleword_street_matches_inflected_form(built_index):
    """Одно-токенная улица «Пастера» матчится по любой словоформе через 1-gram."""
    for query in ('пастера', 'пастеры', 'пастере', 'пастеру'):
        cands = built_index.query_phonetic(query)
        assert any(c.street_id == 6 for c in cands), \
            f'«{query}» (1-gram) должна найти Пастера (id=6)'


def test_singleword_street_full_phrase(built_index):
    """Одно-токенная «Канатная» тоже находится — там 1-gram = полная фраза."""
    cands = built_index.query_phonetic('канатная')
    assert any(c.street_id == 7 for c in cands)


def test_multiword_street_NOT_matched_by_single_word(built_index):
    """P1: 1-gram «арнаутская» НЕ должен находить Малую Арнаутскую через phonetic.

    Реcall на партиальных мнениях восстанавливается через T3 lemma_fuzzy в матчере.
    """
    cands = built_index.query_phonetic('арнаутская')
    assert not any(c.street_id == 1 for c in cands), \
        'P1: «арнаутская» (1-gram) не должна находить Малую Арнаутскую'


def test_multiword_street_matched_by_full_phrase(built_index):
    """Многословная улица матчится полной фразой (любым склонением)."""
    cands = built_index.query_phonetic('малая арнаутская')
    assert any(c.street_id == 1 for c in cands)
    cands = built_index.query_phonetic('малой арнаутской')
    assert any(c.street_id == 1 for c in cands)


def test_multiword_devoicing_via_full_phrase(built_index):
    """Оглушение работает в составе фразы: «проспект шевченка» матчится."""
    cands = built_index.query_phonetic('проспект шевченка')
    assert any(c.street_id == 3 for c in cands)
    cands = built_index.query_phonetic('проспект шевченко')
    assert any(c.street_id == 3 for c in cands)


def test_query_phonetic_empty_for_unknown_word(built_index):
    """Незнакомое слово возвращает пустой список."""
    cands = built_index.query_phonetic('квантовая электроника')
    assert cands == []


def test_query_lemma_tuple_exact_match(built_index):
    """T3 tier-A: точный кортеж лемм находит улицу."""
    cands = built_index.query_lemma_tuple(('малый', 'арнаутский'))
    assert any(c.street_id == 1 for c in cands)


def test_query_lemma_tuple_miss(built_index):
    """T3 tier-A: несовпадающий кортеж — пустой результат."""
    cands = built_index.query_lemma_tuple(('квантовый',))
    assert cands == []


def test_lemma_phrases_synced_with_meta(built_index):
    """lemma_phrases возвращает параллельные списки одинаковой длины."""
    phrases, meta = built_index.lemma_phrases()
    assert len(phrases) == len(meta)
    assert len(phrases) >= len(SAMPLE_ROWS)


def test_get_lemma_tuple_for_street_returns_canonical(built_index):
    """User#1 reverse-index: street_id → lemma_tuple первого алиаса."""
    assert built_index.get_lemma_tuple_for_street(1) == ('малый', 'арнаутский')
    assert built_index.get_lemma_tuple_for_street(3) == ('проспект', 'шевченко')
    # Single-token street → tuple длины 1 (pymorphy3 даёт «пастер» из «пастера»).
    tup6 = built_index.get_lemma_tuple_for_street(6)
    assert len(tup6) == 1 and tup6[0] in ('пастер', 'пастера')
    # Неизвестный id → пустой.
    assert built_index.get_lemma_tuple_for_street(999) == ()


def test_replace_street_removes_entries():
    """replace_street(id, None) убирает все записи улицы."""
    morph = Morphology()
    idx = PhoneticIndex(morph)
    idx.build(SAMPLE_ROWS)

    # До удаления — Пастера (single-word) находится.
    assert idx.query_phonetic('пастера')
    assert idx.get_lemma_tuple_for_street(6) != ()

    idx.replace_street(6, None)
    assert not idx.query_phonetic('пастера')
    assert idx.get_lemma_tuple_for_street(6) == ()
    # Другие улицы не задеты.
    assert idx.query_phonetic('канатная')


def test_replace_street_updates_existing():
    """replace_street с новой row заменяет содержимое + lemma_tuple reverse index."""
    morph = Morphology()
    idx = PhoneticIndex(morph)
    idx.build(SAMPLE_ROWS)

    # Заменим проспект Шевченко на single-word «Дерибасовская» под тем же id.
    idx.replace_street(3, {'id': 3, 'names': ['Дерибасовская']})

    # Старая фраза «проспект Шевченко» больше не находится.
    cands = idx.query_phonetic('проспект шевченко')
    assert not any(c.street_id == 3 for c in cands)

    # Новая single-word улица находится по 1-gram.
    cands = idx.query_phonetic('дерибасовская')
    assert any(c.street_id == 3 for c in cands)
    tup = idx.get_lemma_tuple_for_street(3)
    assert len(tup) == 1  # pymorphy3 даёт нестандартную лемму, главное — есть


def test_variants_per_street_cap_respected():
    """phonetic_variants_per_street_cap не превышен для длинных имён."""
    morph = Morphology()
    idx = PhoneticIndex(morph)
    rows = [{'id': 99, 'names': ['Малая Преображенская Старая Арнаутская']}]
    idx.build(rows)

    total_entries = sum(
        sum(1 for e in entries if e.street_id == 99)
        for entries in idx._phonetic.values()
    )
    cap = idx._variants_cap()
    assert total_entries <= cap


def test_phonetic_entry_canonical_is_first_name(built_index):
    """canonical_name = первое значение из streets.names."""
    cands = built_index.query_phonetic('первая станция фонтана')
    assert cands, 'индекс должен содержать полнофразовый матч'
    assert any(c.canonical_name == '1-я станция Фонтана' for c in cands)
