"""Unit-тесты StreetMatcher (sliding-window linker: surface fuzzy tier-1, lemma tier-2/3).

Запуск:
    pytest parser/tests/test_street_matcher.py -v
"""

import os

import pytest

os.environ.setdefault('BOT_TOKEN', 'x')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('JWT_SECRET', 'a' * 32)
os.environ.setdefault('REDIS_PASSWORD', 'z')

from parser.morphology import Morphology
from parser.phonetic_index import PhoneticIndex
from parser.street_matcher import StreetMatcher
from parser.word_tokenizer import tokenize
from parser.text_preprocessor import preprocess_light


ROWS = [
    {'id': 1, 'names': ['Малая Арнаутская']},
    {'id': 2, 'names': ['Преображенская улица']},
    {'id': 3, 'names': ['проспект Шевченко']},
    {'id': 4, 'names': ['1-я станция Фонтана', 'первая станция Фонтана']},
    {'id': 5, 'names': ['Гагаринское плато']},
    {'id': 6, 'names': ['Пастера']},
    {'id': 7, 'names': ['Канатная']},
    {'id': 8, 'names': ['Большая Арнаутская']},
    {'id': 9, 'names': ['Ольгиевский спуск']},
    {'id': 10, 'names': ['Балковская']},
    {'id': 11, 'names': ['Еврейское кладбище']},
    {'id': 12, 'names': ['Шестая']},
    {'id': 13, 'names': ['Атамана Головатого']},
    {'id': 14, 'names': ['Градоначальницкая']},
    {'id': 15, 'names': ['Степана Олейника спуск']},
    {'id': 16, 'names': ['Вице-адмирала Жукова']},
]


@pytest.fixture(scope='module')
def matcher_ready():
    morph = Morphology()
    idx = PhoneticIndex(morph)
    idx.build(ROWS)
    matcher = StreetMatcher(morph, idx)
    matcher._initialized = True
    matcher._stopwords = set()
    return matcher, morph


def _process(matcher_ready, text: str):
    """Вызвать matcher.find_streets со sliding-window поиском."""
    matcher, morph = matcher_ready
    text = preprocess_light(text)
    tokens = tokenize(text)
    lemmas = morph.lemmatize_tokens(tokens)
    return matcher.find_streets(tokens=tokens, lemmas=lemmas)


# ----------------------------------------------------- базовые smoke

def test_find_returns_empty_on_empty_input(matcher_ready):
    matcher, _ = matcher_ready
    assert matcher.find_streets(tokens=[], lemmas=[]) == []


def test_singleword_street_matches_inflected(matcher_ready):
    """Одно-токенная Пастера матчится в любом склонении (phonetic tier-1)."""
    entities = _process(matcher_ready, 'на Пастера встали')
    assert any(e['street_id'] == 6 for e in entities)


def test_multiword_surface_fuzzy_full_phrase(matcher_ready):
    """Многословная улица в им. падеже матчится полной фразой surface_fuzzy tier-1."""
    entities = _process(matcher_ready, 'едут по улице Малая Арнаутская вверх')
    hit = next((e for e in entities if e['street_id'] == 1), None)
    assert hit is not None
    assert hit['source'] == 'surface_fuzzy'
    assert hit['score'] >= 0.85


def test_multiword_inflected_via_lemma_exact(matcher_ready):
    """Косвенный падеж многословной улицы (surface < 85%) → lemma_exact tier-2."""
    entities = _process(matcher_ready, 'едут по Малой Арнаутской вверх')
    hit = next((e for e in entities if e['street_id'] == 1), None)
    assert hit is not None
    assert hit['source'] in ('lemma_exact', 'lemma_fuzzy')


def test_multiword_partial_via_lemma_fuzzy(matcher_ready):
    """Одна лемма многословной улицы → lemma_fuzzy tier-3 находит с пониженным score."""
    entities = _process(matcher_ready, 'патруль на арнаутской')
    hit = next((e for e in entities if e['street_id'] in (1, 8)), None)
    if hit is not None:
        assert hit['source'] in ('lemma_fuzzy', 'lemma_exact')
        assert hit['score'] < 0.90


# ----------------------------------------------------- phonetic robustness

def test_surface_fuzzy_handles_typo(matcher_ready):
    """Опечатки в названии («олгиевский спуст») не мешают surface_fuzzy-матчу."""
    entities = _process(matcher_ready, 'на олгиевский спуст')
    assert any(e['street_id'] == 9 for e in entities)


# ----------------------------------------------------- G2: punctuation noise

def test_hashtag_noise_stripped(matcher_ready):
    """G2: «##Пастера» → пунктуация удаляется _strip_noise, улица находится."""
    entities = _process(matcher_ready, '##Пастера вверх')
    assert any(e['street_id'] == 6 for e in entities)


# ------------------------------------------------ дефис-перекрёстки (word split)

def test_hyphen_intersection_resolves_street(matcher_ready):
    """Перекрёсток через дефис: «Градоначальницкая-Олейника» больше не random.

    Слова режутся по дефису → «градоначальницкая» матчится surface_fuzzy.
    Раньше склеенный токен не дотягивал ни до одного порога → 0 улиц → random.
    """
    entities = _process(matcher_ready, 'Градоначальницкая-Олейника будет ##блокпост')
    assert entities, 'ожидали ≥1 улицу вместо пустого результата (random)'
    assert any(e['street_id'] == 14 for e in entities)


def test_hyphen_both_full_names_intersection(matcher_ready):
    """Обе улицы записаны полными именами через дефис → обе резолвятся."""
    entities = _process(matcher_ready, 'Канатная-Пастера перекрыли')
    ids = {e['street_id'] for e in entities}
    assert {6, 7} <= ids


def test_hyphen_compound_name_still_matches(matcher_ready):
    """Регресс: дефисное составное имя собирается обратно окном 1..3."""
    entities = _process(matcher_ready, 'едут по Вице-адмирала Жукова')
    assert any(e['street_id'] == 16 for e in entities)


def test_ordinal_hyphen_not_broken(matcher_ready):
    """Регресс: «1-я станция Фонтана» — цифра+я склеивается, улица находится."""
    entities = _process(matcher_ready, 'на 1-я станция Фонтана')
    assert any(e['street_id'] == 4 for e in entities)


# ----------------------------------------------------- G6: UA suffix

def test_ua_suffix_normalized(matcher_ready):
    """G6: украинский суффикс -ська → -ская в preprocess_light."""
    entities = _process(matcher_ready, 'Балковська проехал')
    assert any(e['street_id'] == 10 for e in entities)


def test_ua_suffix_rule_directly():
    """G6 unit: regex преобразует -ська → -ская."""
    assert preprocess_light('Балковська') == 'Балковская'
    assert preprocess_light('Преображенська') == 'Преображенская'
    assert preprocess_light('Дерибасівський') == 'Дерибасивский'


# ----------------------------------------------------- общие инварианты

def test_top_k_limit_enforced(matcher_ready):
    """Финальный список ограничен max_entities."""
    from parser import street_matcher as sm
    if sm.settings is None:
        pytest.skip('settings not loaded')
    saved = sm.settings.similarity.max_entities
    try:
        sm.settings.similarity.max_entities = 2
        entities = _process(
            matcher_ready,
            'патруль на малой арнаутской канатной пастера ольгиевском спуске',
        )
        assert len(entities) <= 2
    finally:
        sm.settings.similarity.max_entities = saved


def test_no_duplicate_street_ids(matcher_ready):
    """Один street_id не повторяется в финальном списке."""
    entities = _process(
        matcher_ready,
        'малая арнаутская и Малая Арнаутская и малой арнаутской',
    )
    ids = [e['street_id'] for e in entities]
    assert len(ids) == len(set(ids))


def test_score_in_unit_range(matcher_ready):
    """score ∈ [0, 1]."""
    entities = _process(matcher_ready, 'патруль на малой арнаутской')
    for e in entities:
        assert 0.0 <= e['score'] <= 1.0
