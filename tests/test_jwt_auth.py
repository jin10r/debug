"""Tests for core/middlewares/auth — JWT issue/verify."""
import importlib
import time

import jwt as pyjwt

auth = importlib.import_module("core.middlewares.auth")
from core.settings import settings  # noqa: E402


def test_generate_and_verify_roundtrip():
    access, refresh = auth.generate_jwt_tokens({"id": 42, "first_name": "T", "username": "t"})
    payload = auth.verify_jwt_token(access, "access")
    assert payload is not None
    assert str(payload["sub"]) == "42"

    rpayload = auth.verify_jwt_token(refresh, "refresh")
    assert rpayload is not None


def test_token_type_mismatch_rejected():
    access, refresh = auth.generate_jwt_tokens({"id": 1})
    # a refresh token must NOT validate as an access token
    assert auth.verify_jwt_token(refresh, "access") is None


def test_tampered_token_rejected():
    access, _ = auth.generate_jwt_tokens({"id": 1})
    assert auth.verify_jwt_token(access[:-3] + "xxx", "access") is None


def test_expired_token_rejected():
    secret = settings.jwt.secret
    expired = pyjwt.encode(
        {"sub": "1", "type": "access", "exp": int(time.time()) - 10},
        secret,
        algorithm="HS256",
    )
    assert auth.verify_jwt_token(expired, "access") is None
