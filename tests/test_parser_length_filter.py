"""Tests for parser-side truncation for geo (B6).

Verifies:
  * core/settings exposes parser.max_text_length == 380
  * text <= max_text_length survives preprocessing unchanged
  * long text is truncated to head + tail (location usually sits at either
    end), NOT replaced by the old "слишком длиннное…" placeholder
"""
from conftest import load_module_by_path

settings = load_module_by_path("_settings_under_test", "core/settings.py").settings
tp = load_module_by_path("_tp_under_test", "core/utils/text_preprocessor.py")
truncate_for_geo = tp.truncate_for_geo


def test_parser_max_text_length_is_380():
    assert settings.parser.max_text_length == 380


def test_long_text_keeps_head_and_tail():
    long_text = "блокпост на Туристской " + "детали " * 100 + "возле 7 км"
    result = truncate_for_geo(long_text, settings.parser.max_text_length)
    assert len(result) <= settings.parser.max_text_length + 3
    assert "Туристской" in result
    assert "7 км" in result
    assert "слишком длиннное" not in result


def test_short_text_survives():
    short_text = "ДТП на Дерибасовской"
    assert truncate_for_geo(short_text, settings.parser.max_text_length) == short_text


def test_exactly_max_length_survives():
    text = "A" * settings.parser.max_text_length
    assert truncate_for_geo(text, settings.parser.max_text_length) == text