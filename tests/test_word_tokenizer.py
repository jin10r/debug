"""Tests for parser/word_tokenizer.tokenize (pure, no heavy deps)."""
from conftest import load_module_by_path

wt = load_module_by_path("_wt_under_test", "parser/word_tokenizer.py")
tokenize = wt.tokenize


def texts(toks):
    return [t.text for t in toks]


def test_empty_and_blank():
    assert tokenize("") == []
    assert tokenize("   ") == []


def test_basic_split_and_positions():
    s = "Дерибасовская улица"
    toks = tokenize(s)
    assert texts(toks) == ["Дерибасовская", "улица"]
    # non-fused tokens map back to the source span exactly
    for t in toks:
        assert s[t.start:t.stop] == t.text


def test_hyphen_separates_two_streets():
    assert texts(tokenize("Градоначальницкая-Олейника")) == [
        "Градоначальницкая",
        "Олейника",
    ]


def test_punctuation_and_slash_are_separators():
    assert texts(tokenize("ДТП, на/у (Пушкинской)!")) == [
        "ДТП",
        "на",
        "у",
        "Пушкинской",
    ]


def test_digit_ya_fusion():
    assert texts(tokenize("5 я Люстдорфская")) == ["5я", "Люстдорфская"]
    assert texts(tokenize("7-я")) == ["7я"]
    assert texts(tokenize("1-я станция")) == ["1я", "станция"]


def test_digits_kept_inside_words():
    assert texts(tokenize("h1 патруль")) == ["h1", "патруль"]
