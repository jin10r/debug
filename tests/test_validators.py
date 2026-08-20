"""Tests for core/utils/validators.py — pure functions, no heavy deps."""
import re

import pytest

from conftest import load_module_by_path

val = load_module_by_path("_validators_under_test", "core/utils/validators.py")
validate_string = val.validate_string
validate_int = val.validate_int
validate_iso_timestamp = val.validate_iso_timestamp
validate_layers = val.validate_layers
validate_init_data_length = val.validate_init_data_length
validate_bot_token = val.validate_bot_token
validate_max_age_hours = val.validate_max_age_hours
validate_jwt_string = val.validate_jwt_string
validate_telegram_user_id = val.validate_telegram_user_id


# ============================================================
# validate_string
# ============================================================

class TestValidateString:
    def test_valid_input(self):
        assert validate_string("hello", "field", max_len=10) == "hello"

    def test_not_string_raises(self):
        with pytest.raises(ValueError, match="must be a string"):
            validate_string(123, "field", max_len=10)

    def test_null_bytes_rejected(self):
        with pytest.raises(ValueError, match="null bytes"):
            validate_string("hello\x00world", "field", max_len=20)

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="at least"):
            validate_string("ab", "field", max_len=10, min_len=3)

    def test_too_long_raises(self):
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_string("toolong", "field", max_len=5)

    def test_strips_whitespace(self):
        assert validate_string("  hello  ", "field", max_len=10) == "hello"


# ============================================================
# validate_int
# ============================================================

class TestValidateInt:
    def test_valid_int(self):
        assert validate_int(5, "field", 0, 10) == 5

    def test_valid_string_int(self):
        assert validate_int("5", "field", 0, 10) == 5

    def test_float_rejected(self):
        with pytest.raises(ValueError, match="integer"):
            validate_int(3.14, "field", 0, 10)

    def test_float_with_zero_fraction_accepted(self):
        assert validate_int(5.0, "field", 0, 10) == 5

    def test_below_min_raises(self):
        with pytest.raises(ValueError, match=">="):
            validate_int(-1, "field", 0, 10)

    def test_above_max_raises(self):
        with pytest.raises(ValueError, match="<="):
            validate_int(11, "field", 0, 10)

    def test_bool_rejected(self):
        with pytest.raises(ValueError, match="not boolean"):
            validate_int(True, "field", 0, 10)


# ============================================================
# validate_iso_timestamp
# ============================================================

class TestValidateIsoTimestamp:
    def test_valid_iso(self):
        from datetime import datetime, timezone
        dt = validate_iso_timestamp("2024-01-01T12:00:00Z")
        assert dt.tzinfo is not None

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="not a valid ISO-8601"):
            validate_iso_timestamp("not-a-date")

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="invalid length"):
            validate_iso_timestamp("2024")

    def test_too_long_raises(self):
        with pytest.raises(ValueError, match="invalid length"):
            validate_iso_timestamp("2024-01-01T00:00:00" + "0" * 40)

    def test_null_bytes_rejected(self):
        with pytest.raises(ValueError, match="null bytes"):
            validate_iso_timestamp("2024-01-01T00:00:00\x00")

    def test_naive_gets_utc_tz(self):
        dt = validate_iso_timestamp("2024-01-01 12:00:00")
        assert dt.tzinfo is not None


# ============================================================
# validate_layers
# ============================================================

class TestValidateLayers:
    def test_none_returns_none(self):
        assert validate_layers(None) is None

    def test_valid_list(self):
        result = validate_layers(["bus", "cops"])
        assert result == ["bus", "cops"]

    def test_empty_list_returns_empty_list(self):
        assert validate_layers([]) == []

    def test_not_list_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            validate_layers("bus")

    def test_too_many_raises(self):
        with pytest.raises(ValueError, match="exceeds maximum of 16"):
            validate_layers(["layer" + str(i) for i in range(17)])

    def test_invalid_chars_raises(self):
        with pytest.raises(ValueError, match="invalid characters"):
            validate_layers(["INVALID"])

    def test_empty_string_in_list_raises(self):
        with pytest.raises(ValueError, match="is empty"):
            validate_layers(["bus", ""])


# ============================================================
# validate_init_data_length
# ============================================================

class TestValidateInitDataLength:
    def test_valid_short(self):
        assert validate_init_data_length("a=1&b=2") == "a=1&b=2"

    def test_too_long_raises(self):
        long_str = "x" * 4097
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_init_data_length(long_str)

    def test_null_bytes_rejected(self):
        with pytest.raises(ValueError, match="null bytes"):
            validate_init_data_length("a=1\x00b=2")

    def test_strips_whitespace(self):
        assert validate_init_data_length("  a=1  ") == "a=1"


# ============================================================
# validate_bot_token
# ============================================================

class TestValidateBotToken:
    def test_valid_token(self):
        token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        assert validate_bot_token(token) == token

    def test_not_string_raises(self):
        with pytest.raises(ValueError, match="must be a string"):
            validate_bot_token(123)

    def test_too_long_raises(self):
        token = "1:" + "a" * 257
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_bot_token(token)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="invalid format"):
            validate_bot_token("not a token!")

    def test_null_bytes_rejected(self):
        with pytest.raises(ValueError, match="null bytes"):
            validate_bot_token("123:abc\x00def")


# ============================================================
# validate_max_age_hours
# ============================================================

class TestValidateMaxAgeHours:
    def test_valid(self):
        assert validate_max_age_hours(24) == 24

    def test_zero_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            validate_max_age_hours(0)

    def test_above_max_raises(self):
        with pytest.raises(ValueError, match="<= 720"):
            validate_max_age_hours(721)


# ============================================================
# validate_jwt_string
# ============================================================

class TestValidateJwtString:
    def test_valid_jwt(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        assert validate_jwt_string(token) == token

    def test_not_string_raises(self):
        with pytest.raises(ValueError, match="must be a string"):
            validate_jwt_string(123)

    def test_too_long_raises(self):
        token = "a." * 5000 + ".b"
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_jwt_string(token)

    def test_wrong_part_count_raises(self):
        with pytest.raises(ValueError, match="3 parts"):
            validate_jwt_string("only.two")

    def test_null_bytes_rejected(self):
        with pytest.raises(ValueError, match="null bytes"):
            validate_jwt_string("a.b\x00c")


# ============================================================
# validate_telegram_user_id
# ============================================================

class TestValidateTelegramUserId:
    def test_valid_int(self):
        assert validate_telegram_user_id(42) == 42

    def test_valid_string_int(self):
        assert validate_telegram_user_id("42") == 42

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError, match="not a valid Telegram user ID"):
            validate_telegram_user_id("abc")

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            validate_telegram_user_id(-1)

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            validate_telegram_user_id(0)

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="must be int or string"):
            validate_telegram_user_id(3.14)


# ============================================================
# MAX_MEDIA_FILE_BYTES constant (Task 1.3 fix)
# ============================================================

class TestMaxMediaFileBytes:
    def test_constant_is_10mb(self):
        assert val.MAX_MEDIA_FILE_BYTES == 10 * 1024 * 1024
