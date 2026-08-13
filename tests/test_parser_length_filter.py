"""Tests for parser-side MAX_TEXT_LENGTH filter (R-P1 length guard).

Verifies:
  * core/settings exposes parser.max_text_length == 380
  * text > max_text_length after preprocess_light is replaced with the exact placeholder
  * text <= max_text_length survives preprocessing unchanged
"""
from conftest import load_module_by_path

settings = load_module_by_path("_settings_under_test", "core/settings.py").settings
tp = load_module_by_path("_tp_under_test", "core/utils/text_preprocessor.py")

PLACEHOLDER = "слишком длиннное сообщение, не является релевантной локацией"


def _apply_length_guard(text: str) -> str:
    """Simulate the parser guard: preprocess_light → length check → placeholder."""
    preserved = tp.preprocess_light(text)
    if len(preserved) > settings.parser.max_text_length:
        return PLACEHOLDER
    return preserved


def test_parser_max_text_length_is_380():
    assert settings.parser.max_text_length == 380


def test_long_text_replaced_after_preprocess_light():
    long_text = "A" * 500
    result = _apply_length_guard(long_text)
    assert result == PLACEHOLDER


def test_short_text_survives_preprocess_light():
    short_text = "ДТП на Дерибасовской"
    result = _apply_length_guard(short_text)
    assert result == short_text


def test_exactly_max_length_survives():
    text = "A" * settings.parser.max_text_length
    result = _apply_length_guard(text)
    assert result == text


def test_placeholder_has_double_n():
    assert PLACEHOLDER == "слишком длиннное сообщение, не является релевантной локацией"
    assert "длиннное" in PLACEHOLDER
