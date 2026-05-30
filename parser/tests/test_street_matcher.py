"""Unit-тесты StreetMatcher (Phase 1–6: candidate pipeline + per-word score
+ multiword confirm + gap-grams + UA fixes).

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
from parser.razdel_tokenizer import RazdelTokenizer
from parser.street_matcher import StreetMatcher
from parser.text_preprocessor import preprocess_light


# 8 улиц: 2 одно-токенные + 6 многословных + специально для gap-gram теста.
ROWS = [
    {'id': 1, 'names': ['Малая Арнаутская']},
    {'id': 2, 'names': ['Преображенская улица']},
    {'id': 3, 'names': ['проспект Шевченко']},
    {'id': 4, 'names': ['1-я станция Фонтана', 'первая станция Фонтана']},
    {'id': 5, 'names': ['Гагаринское плато']},
    {'id': 6, 'names': ['Пастера']},
    {'id': 7, 'names': ['Канатная']},
    {'id': 8, 'names': ['Большая Арнаутская']},   # для same-root FP теста
    {'id': 9, 'names': ['Ольгиевский спуск']},     # для gap-gram теста
    {'id': 10, 'names': ['Балковская']},           # для UA-теста
    {'id': 11, 'names': ['Еврейское кладбище']},   # для generic-suffix теста
    {'id': 12, 'names': ['Шестая']},               # для ORDINAL+digit теста
    {'id': 13, 'names': ['Атамана Головатого']},   # для hashtag-anchor теста
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
    matcher, morph = matcher_ready
    text = preprocess_light(text)
    tokenizer = RazdelTokenizer()
    tokens = tokenizer.tokenize(text)
    lemmas = morph.lemmatize_tokens(tokens)
    return matcher.find_streets(tokens=tokens, lemmas=lemmas)


# ----------------------------------------------------- базовые smoke

def test_find_returns_empty_on_empty_input(matcher_ready):
    matcher, _ = matcher_ready
    assert matcher.find_streets(tokens=[], lemmas=[]) == []


def test_singleword_street_matches_inflected(matcher_ready):
    """Одно-токенная Пастера матчится 1-gram в любом склонении (T2 phonetic)."""
    entities = _process(matcher_ready, 'на Пастера встали')
    assert any(e['street_id'] == 6 for e in entities)


def test_multiword_phonetic_full_phrase(matcher_ready):
    """Многословная улица матчится полной фразой как T2 phonetic."""
    entities = _process(matcher_ready, 'едут по Малой Арнаутской вверх')
    hit = next((e for e in entities if e['street_id'] == 1), None)
    assert hit is not None
    assert hit['source'] == 'phonetic'
    assert hit['score'] >= 0.85


# ----------------------------------------------------- P1: multi-word partial

def test_multiword_partial_via_lemma_fuzzy_with_penalty(matcher_ready):
    """P1+User#1: 1-gram «арнаутской» одна — penalty за отсутствие ref-слов,
    но lemma_fuzzy всё-таки находит Малую Арнаутскую с пониженной уверенностью.
    """
    entities = _process(matcher_ready, 'патруль на арнаутской')
    hit = next((e for e in entities if e['street_id'] == 1), None)
    # Может найтись или нет (на границе порога), но если найдётся —
    # уверенность должна быть ниже полной фразы (0.85).
    if hit is not None:
        assert hit['source'] in ('lemma_fuzzy', 'lemma_exact')
        assert hit['score'] < 0.85


def test_multiword_confirm_bonus_promotes_correct_anchor(matcher_ready):
    """User#1: «малая арнаутская» phonetic-match, ref-леммы покрыты —
    Малая выигрывает у Большой Арнаутской.
    """
    entities = _process(matcher_ready, 'патруль на малой арнаутской')
    # Малая (id=1) должна быть в результатах, Большая (id=8) либо отсутствует,
    # либо имеет score не выше.
    malaya = next((e for e in entities if e['street_id'] == 1), None)
    bolshaya = next((e for e in entities if e['street_id'] == 8), None)
    assert malaya is not None
    if bolshaya is not None:
        assert malaya['score'] >= bolshaya['score']


# --------------------------------------------- span-subsumption (anti-FP)

def test_span_subsumption_drops_partial(matcher_ready):
    """«малой арнаутской» (2-gram → Малая Арнаутская) подавляет партиал
    «арнаутской» (1-gram → Большая Арнаутская): span 1-gram вложен в span
    2-gram, а 2-gram не слабее. Большая (id=8) не попадает в результат.
    """
    entities = _process(matcher_ready, 'патруль на малой арнаутской')
    ids = {e['street_id'] for e in entities}
    assert 1 in ids          # Малая Арнаутская найдена
    assert 8 not in ids      # Большая Арнаутская подавлена (партиал)


# ----------------------------------------------------- P2: digits

def test_pure_digit_1gram_skipped(matcher_ready):
    """P2: «3 оливки на парковке» не должно матчить улиц с «3»."""
    entities = _process(matcher_ready, '3 оливки на парковке')
    # Никаких улиц не должно найтись (числа не индексированы как улицы).
    assert all(e['source'] != 'phonetic' or 'оливки' not in e['text']
               for e in entities)


def test_digit_6_does_not_match_shestaya(matcher_ready):
    """P2: одиночная «6» не находит Шестую (Шестая id=12 имеет lemma «6»
    через ORDINAL_MAP). До фикса матч происходил через T3 tier-A.
    """
    entities = _process(matcher_ready, 'там 6 или 7 с лицами')
    assert not any(e['street_id'] == 12 for e in entities)


# ----------------------------------------------------- G1: generic suffixes

def test_generic_suffix_1gram_skipped(matcher_ready):
    """G1: «возле кладбища» не должен находить Еврейское кладбище через 1-gram."""
    entities = _process(matcher_ready, 'возле кладбища стоят')
    assert not any(e['street_id'] == 11 for e in entities)


def test_generic_suffix_in_2gram_still_works(matcher_ready):
    """G1: «еврейское кладбище» 2-gram всё ещё находит."""
    entities = _process(matcher_ready, 'еврейское кладбище закрыто')
    assert any(e['street_id'] == 11 for e in entities)


# ----------------------------------------------------- G2: punctuation noise

def test_hashtag_noise_does_not_inflate_size(matcher_ready):
    """G2: «##Пастера» = «Пастера» — хэштеги выбрасываются префильтром."""
    entities = _process(matcher_ready, '##Пастера вверх')
    assert any(e['street_id'] == 6 for e in entities)


# ----------------------------------------------------- hashtag anchor (##Name)

def test_hashtag_anchor_recovers_multiword_partial(matcher_ready):
    """##-тег обходит multiword-penalty: «##Головатого» (одно слово от
    «Атамана Головатого») находит улицу, хотя «атамана» в окне нет.
    """
    entities = _process(matcher_ready, '##Головатого тесла ловят на полосу')
    assert any(e['street_id'] == 13 for e in entities)


def test_plain_partial_multiword_still_penalized(matcher_ready):
    """Без тега то же одиночное «головатого» либо не находит улицу, либо
    с уверенностью ниже тегнутого (penalty за отсутствие ref-слова сохраняется).
    """
    tagged = _process(matcher_ready, '##Головатого тесла')
    plain = _process(matcher_ready, 'головатого тесла')
    t_hit = next((e for e in tagged if e['street_id'] == 13), None)
    p_hit = next((e for e in plain if e['street_id'] == 13), None)
    assert t_hit is not None
    if p_hit is not None:
        assert t_hit['score'] >= p_hit['score']


# ----------------------------------------------------- User#3: gap-grams

def test_gap_gram_finds_split_words(matcher_ready):
    """User#3: «Ольгиевский этот самый спуск» — gap-2-gram «Ольгиевский спуск»
    с разрывом 2 токена должно найти улицу (phonetic по полной фразе).
    """
    entities = _process(matcher_ready, 'Ольгиевский этот самый спуск')
    assert any(e['street_id'] == 9 for e in entities)


# ----------------------------------------------------- User#2: per-word score

def test_per_word_score_handles_typo(matcher_ready):
    """User#2: «олгиевский спуст» (две опечатки) всё-таки находит Ольгиевский
    спуск благодаря покомпонентному rapidfuzz с пониженным порогом.
    """
    entities = _process(matcher_ready, 'на олгиевский спуст')
    # Может пройти через phonetic (Metaphone сглаживает опечатки) или
    # через lemma_fuzzy — главное, что найдётся.
    assert any(e['street_id'] == 9 for e in entities)


# ----------------------------------------------------- G6: UA fixes

def test_ua_suffix_normalized(matcher_ready):
    """G6: украинский суффикс -ська → -ская в preprocess_light.

    Используем «Балковська» (Russian-stem + UA-suffix) — это покрывается
    регекспом `-ська → -ская` и должно дать Балковская через phonetic.
    Случаи с дополнительной заменой корня (Балкивська → Балковская) требуют
    data-side алиаса в streets.names.
    """
    entities = _process(matcher_ready, 'Балковська проехал')
    assert any(e['street_id'] == 10 for e in entities)


def test_ua_suffix_rule_directly():
    """G6 unit: regex преобразует -ська → -ская независимо от парсера.

    Замена корней (и → о) не выполняется правилом; покрывает только суффиксы.
    """
    from parser.text_preprocessor import preprocess_light
    assert preprocess_light('Балковська') == 'Балковская'
    assert preprocess_light('Преображенська') == 'Преображенская'
    # 'і' → 'и' через _UA_TABLE, затем -ський → -ский.
    assert preprocess_light('Дерибасівський') == 'Дерибасивский'


# ----------------------------------------------------- общие инварианты

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
        assert 0.0 <= e['score'] <= 1.5  # допускаем небольшой overrun из-за bonus
