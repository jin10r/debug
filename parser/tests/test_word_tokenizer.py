"""Unit-тесты word_tokenizer.tokenize (regex-разбивка по не-буквенным символам).

Запуск (хост, mawo не требуется):
    pytest parser/tests/test_word_tokenizer.py -v
"""

from parser.word_tokenizer import tokenize


def _texts(text):
    return [t.text.lower() for t in tokenize(text)]


def test_hyphen_splits_two_streets():
    assert _texts('Градоначальницкая-Олейника') == ['градоначальницкая', 'олейника']


def test_hyphen_intersection_full_message():
    assert _texts('Градоначальницкая-Олейника будет ##блокпост , свита собирается') == [
        'градоначальницкая', 'олейника', 'будет', 'блокпост', 'свита', 'собирается',
    ]


def test_hashtag_sets_is_anchored():
    toks = tokenize('##блокпост на Пастера')
    by_text = {t.text.lower(): t for t in toks}
    assert by_text['блокпост'].is_anchored is True
    assert by_text['на'].is_anchored is False
    assert by_text['пастера'].is_anchored is False


def test_ordinal_glue_with_space():
    assert _texts('5я Люстдорфская') == ['5я', 'люстдорфская']


def test_ordinal_glue_across_hyphen():
    # "1-я" → clean/split → "1","я" → склейка → "1я"
    assert _texts('1-я станция Фонтана') == ['1я', 'станция', 'фонтана']


def test_compound_name_splits_but_window_reassembles():
    # Дефисное составное имя дробится; окно 1..3 в матчере соберёт фразу обратно,
    # индекс хранит ту же фразу через clean().
    assert _texts('Вице-адмирала Жукова') == ['вице', 'адмирала', 'жукова']


def test_emoji_and_punctuation_are_separators():
    assert _texts('Дерибасовская ❗️, угол Ришельевской!') == [
        'дерибасовская', 'угол', 'ришельевской',
    ]


def test_offsets_match_source():
    text = 'Пастера-Канатная'
    toks = tokenize(text)
    for t in toks:
        assert text[t.start:t.stop] == t.text


def test_empty_text():
    assert tokenize('') == []
