"""Tests for core/utils/telegram_validation — HMAC-SHA256 initData validation."""
import hashlib
import hmac
import importlib
import time
import urllib.parse

tv = importlib.import_module("core.utils.telegram_validation")

BOT = "123456:TEST_abcDEF-ghiJKL_mnoPQR"


def make_init_data(bot_token: str, fields: dict) -> str:
    """Build a correctly-signed initData query string (Telegram scheme)."""
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(list(fields.items()) + [("hash", h)])


def _fresh_fields():
    return {
        "auth_date": str(int(time.time())),
        "query_id": "AAExampleQueryId",
        "user": '{"id":42,"first_name":"T"}',
    }


def test_valid_init_data():
    ok, user = tv.validate_telegram_webapp_data(make_init_data(BOT, _fresh_fields()), BOT)
    assert ok is True
    assert user["id"] == 42


def test_tampered_hash_rejected():
    init = make_init_data(BOT, _fresh_fields())
    flipped = "1" if init[-1] != "1" else "0"
    bad = init[:-1] + flipped
    ok, user = tv.validate_telegram_webapp_data(bad, BOT)
    assert ok is False and user is None


def test_wrong_bot_token_rejected():
    init = make_init_data(BOT, _fresh_fields())
    ok, _ = tv.validate_telegram_webapp_data(init, "999999:DIFFERENT_token")
    assert ok is False


def test_missing_hash_rejected():
    ok, _ = tv.validate_telegram_webapp_data("auth_date=123&user=%7B%7D", BOT)
    assert ok is False


def test_expired_auth_date_rejected():
    fields = _fresh_fields()
    fields["auth_date"] = str(int(time.time()) - 100_000)  # >24h old
    ok, _ = tv.validate_telegram_webapp_data(make_init_data(BOT, fields), BOT, max_age_hours=24)
    assert ok is False


def test_empty_inputs_rejected():
    assert tv.validate_telegram_webapp_data("", BOT) == (False, None)
    assert tv.validate_telegram_webapp_data("x=1", "") == (False, None)
