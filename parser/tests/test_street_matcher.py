"""Unit-тесты StreetMatcher (T2 phonetic + T3 lemma merge logic).

Запуск:
    pytest parser/tests/test_street_matcher.py -v

Не подключаемся к БД — индекс собирается из синтетических rows.
"""

import os

import pytest

os.environ.setdefault('BOT_TOKEN', 'x')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('JWT_SECRET', 'a' * 32)
os.environ.setdefault('REDIS_PASSWORD', 'z')

from parser.morphology import Morphology
from parser.phonetic_index import PhoneticIndex
from parser.razdel_tokenizer import RazdelTokenizer
from parser.street_matcher import StreetMatcher


@pytest.fixture(scope='module')
def matcher_ready():
    morph = Morphology()
    idx = PhoneticIndex(morph)
    rows = [
        {'id': 1, 'names': ['Малая Арнаутская']},
        {'id': 2, 'names': ['Преображенская улица']},
        {'id': 3, 'names': ['проспект Шевченко']},
        {'id': 4, 'names': ['1-я станция Фонтана', 'первая станция Фонтана']},
        {'id': 5, 'names': ['Гагаринское плато']},
    ]
    idx.build(rows)

    matcher = StreetMatcher(morph, idx)
    matcher._initialized = True
    # Стоп-слова — пустое множество для предсказуемости теста.
    matcher._stopwords = set()
    return matcher, morph


def _process(matcher_ready, text: str):
    matcher, morph = matcher_ready
    tokenizer = RazdelTokenizer()
    tokens = tokenizer.tokenize(text)
    lemmas = morph.lemmatize_tokens(tokens)
    return matcher.find_streets(tokens=tokens, lemmas=lemmas)


def test_find_returns_empty_on_empty_input(matcher_ready):
    matcher, _ = matcher_ready
    assert matcher.find_streets(tokens=[], lemmas=[]) == []


def test_phonetic_matches_inflected_street(matcher_ready):
    """T2: «патруль на арнаутской» → id=1, source=phonetic."""
    entities = _process(matcher_ready, 'патруль на арнаутской')
    assert any(
        e['street_id'] == 1 and e['source'] == 'phonetic'
        for e in entities
    ), f'не нашли Малую Арнаутскую: {entities}'


def test_phonetic_matches_full_phrase(matcher_ready):
    """T2: полнофразовая n-грамма «преображенская улица»."""
    entities = _process(matcher_ready, 'едут по преображенской улице')
    assert any(e['street_id'] == 2 for e in entities), \
        f'не нашли Преображенскую: {entities}'


def test_lemma_fallback_when_phonetic_misses(matcher_ready):
    """T3 tier-A: лемматический точный матч когда T2 не сработал.

    Делаем гарантированный пропуск T2 через отключение phonetic_enabled
    и смотрим, что леммо-fallback находит улицу.
    """
    from parser import street_matcher as sm
    if sm.settings is None:
        pytest.skip('settings not loaded')
    saved = sm.settings.similarity.phonetic_enabled
    try:
        sm.settings.similarity.phonetic_enabled = False
        entities = _process(matcher_ready, 'патруль на малой арнаутской')
        assert any(
            e['street_id'] == 1 and e['source'].startswith('lemma')
            for e in entities
        ), f'leмма-fallback не сработал: {entities}'
    finally:
        sm.settings.similarity.phonetic_enabled = saved


def test_top_k_limit_enforced(matcher_ready):
    """Финальный список ограничен MAX_ENTITIES."""
    from parser import street_matcher as sm
    if sm.settings is None:
        pytest.skip('settings not loaded')
    saved = sm.settings.similarity.max_entities
    try:
        sm.settings.similarity.max_entities = 2
        entities = _process(
            matcher_ready,
            'арнаутская преображенская шевченко гагаринское плато фонтана',
        )
        assert len(entities) <= 2
    finally:
        sm.settings.similarity.max_entities = saved


def test_no_duplicate_street_ids(matcher_ready):
    """Один street_id не должен повторяться в финальном списке."""
    entities = _process(
        matcher_ready,
        'арнаутская малая арнаутская малой арнаутской',
    )
    ids = [e['street_id'] for e in entities]
    assert len(ids) == len(set(ids)), f'дубликаты: {ids}'


def test_score_in_unit_range(matcher_ready):
    """score ∈ [0, 1]."""
    entities = _process(matcher_ready, 'патруль на арнаутской')
    for e in entities:
        assert 0.0 <= e['score'] <= 1.0


def test_phonetic_priority_on_tie(matcher_ready):
    """При одинаковом adjusted score phonetic > lemma."""
    # Сообщение, в котором n-грамма и phonetic и lemma матчатся в одну улицу.
    entities = _process(matcher_ready, 'арнаутская')
    # T2 ловит "арнаутская"→id=1. T3 tier-B может тоже дать lemma_fuzzy.
    # Источник должен быть phonetic (приоритет на равенстве).
    hit = next((e for e in entities if e['street_id'] == 1), None)
    assert hit is not None
    assert hit['source'] == 'phonetic'
