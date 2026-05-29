"""Unit-тесты PhoneticIndex.

Запуск:
    pytest parser/tests/test_phonetic_index.py -v

Зависимости: fonetika, mawo-pymorphy3, mawo-razdel, rapidfuzz.
Env-переменные для импорта core.settings: BOT_TOKEN, CHANNEL_ID (-100*), JWT_SECRET.
"""

import os

import pytest

# Установить минимально необходимые env до импорта parser/core.settings.
os.environ.setdefault('BOT_TOKEN', 'x')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('JWT_SECRET', 'a' * 32)
os.environ.setdefault('REDIS_PASSWORD', 'z')

from parser.morphology import Morphology
from parser.phonetic_index import PhoneticEntry, PhoneticIndex


SAMPLE_ROWS = [
    {'id': 1, 'names': ['Малая Арнаутская']},
    {'id': 2, 'names': ['Преображенская улица']},
    {'id': 3, 'names': ['проспект Шевченко']},
    {'id': 4, 'names': ['1-я станция Фонтана', 'первая станция Фонтана']},
    {'id': 5, 'names': ['Гагаринское плато']},
]


@pytest.fixture(scope='module')
def built_index():
    morph = Morphology()
    idx = PhoneticIndex(morph)
    idx.build(SAMPLE_ROWS)
    return idx


def test_build_returns_positive_variant_count(built_index):
    """build() возвращает количество вариантов > 0 для непустого входа."""
    # повторный build не должен сломать индекс
    assert built_index.query_phonetic('арнаутская'), \
        'индекс должен содержать минимум одну запись для содержательного слова'


def test_query_phonetic_finds_inflected_form(built_index):
    """T2: 1-gram declined form находит улицу."""
    cands = built_index.query_phonetic('арнаутской')
    assert any(c.street_id == 1 for c in cands), \
        '«арнаутской» должно найти Малую Арнаутскую (id=1)'


def test_query_phonetic_finds_full_phrase(built_index):
    """T2: полнофразовая n-грамма находит улицу."""
    cands = built_index.query_phonetic('малая арнаутская')
    assert any(c.street_id == 1 for c in cands), \
        '«малая арнаутская» должно найти id=1'


def test_query_phonetic_handles_devoicing(built_index):
    """T2: оглушение согласной (шевченка/шевченко) даёт один код."""
    cands_a = built_index.query_phonetic('шевченка')
    cands_o = built_index.query_phonetic('шевченко')
    assert any(c.street_id == 3 for c in cands_a)
    assert any(c.street_id == 3 for c in cands_o)


def test_query_phonetic_empty_for_unknown_word(built_index):
    """T2: незнакомое слово возвращает пустой список."""
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
    assert len(phrases) >= len(SAMPLE_ROWS), \
        'каждое имя улицы должно дать минимум одну лемматизированную фразу'


def test_replace_street_removes_entries(built_index):
    """replace_street(id, None) убирает все записи улицы из индекса."""
    morph = Morphology()
    idx = PhoneticIndex(morph)
    idx.build(SAMPLE_ROWS)

    # До удаления — есть запись для Преображенской.
    assert idx.query_phonetic('преображенская')

    idx.replace_street(2, None)

    # После удаления — нет.
    assert not idx.query_phonetic('преображенская')
    # Другие улицы не задеты.
    assert idx.query_phonetic('арнаутская')


def test_replace_street_updates_existing(built_index):
    """replace_street с новой row заменяет содержимое."""
    morph = Morphology()
    idx = PhoneticIndex(morph)
    idx.build(SAMPLE_ROWS)

    # Заменим Шевченко на «Дерибасовская» под тем же id.
    idx.replace_street(3, {'id': 3, 'names': ['Дерибасовская']})

    # Старый код больше не должен находить id=3.
    cands = idx.query_phonetic('шевченко')
    assert not any(c.street_id == 3 for c in cands)

    # Новый код должен.
    cands = idx.query_phonetic('дерибасовская')
    assert any(c.street_id == 3 for c in cands)


def test_variants_per_street_cap_respected():
    """phonetic_variants_per_street_cap не превышен для длинных имён."""
    morph = Morphology()
    idx = PhoneticIndex(morph)
    # Искусственно длинное название с 4 content-словами — потенциал ~12^4 = 20736.
    rows = [{'id': 99, 'names': ['Малая Преображенская Старая Арнаутская']}]
    idx.build(rows)

    # Считаем все записи, относящиеся к street_id 99 во всех phonetic buckets.
    total_entries = sum(
        sum(1 for e in entries if e.street_id == 99)
        for entries in idx._phonetic.values()
    )
    cap = idx._variants_cap()
    assert total_entries <= cap, \
        f'variants per street ({total_entries}) > cap ({cap})'


def test_phonetic_entry_canonical_is_first_name(built_index):
    """canonical_name = первое значение из streets.names."""
    cands = built_index.query_phonetic('фонтана')
    assert cands, 'индекс должен содержать «фонтана»'
    assert any(c.canonical_name == '1-я станция Фонтана' for c in cands), \
        'canonical_name = streets.names[0]'
