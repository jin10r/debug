"""Tests for core/settings.py — _resolve_jwt_secret, load_settings, dataclasses."""
import os
from unittest.mock import patch, MagicMock

import pytest

from conftest import load_module_by_path

settings_mod = load_module_by_path("_settings_under_test", "core/settings.py")
_resolve_jwt_secret = settings_mod._resolve_jwt_secret
load_settings = settings_mod.load_settings
Settings = settings_mod.Settings
DatabaseConfig = settings_mod.DatabaseConfig
BotConfig = settings_mod.BotConfig
AppConfig = settings_mod.AppConfig


# ============================================================
# _resolve_jwt_secret
# ============================================================

class TestResolveJwtSecret:
    def test_valid_secret(self):
        env = MagicMock()
        env.str.return_value = "a" * 32
        assert _resolve_jwt_secret(env) == "a" * 32

    def test_empty_secret_raises(self):
        env = MagicMock()
        env.str.return_value = ""
        with pytest.raises(RuntimeError, match="JWT_SECRET is required"):
            _resolve_jwt_secret(env)

    def test_none_secret_raises(self):
        env = MagicMock()
        env.str.return_value = None
        with pytest.raises(RuntimeError, match="JWT_SECRET is required"):
            _resolve_jwt_secret(env)

    def test_too_short_raises(self):
        env = MagicMock()
        env.str.return_value = "short"
        with pytest.raises(RuntimeError, match="must be >= 32 chars"):
            _resolve_jwt_secret(env)

    def test_insecure_default_your_secret_key_raises(self):
        env = MagicMock()
        env.str.return_value = "your-secret-key"
        with pytest.raises(RuntimeError, match="placeholder"):
            _resolve_jwt_secret(env)

    def test_insecure_default_secret_raises(self):
        env = MagicMock()
        env.str.return_value = "secret"
        with pytest.raises(RuntimeError, match="placeholder"):
            _resolve_jwt_secret(env)

    def test_insecure_default_changeme_raises(self):
        env = MagicMock()
        env.str.return_value = "changeme"
        with pytest.raises(RuntimeError, match="placeholder"):
            _resolve_jwt_secret(env)

    def test_insecure_starts_with_your_secret_raises(self):
        env = MagicMock()
        env.str.return_value = "your-secret-key-change-in-production-min-32-chars"
        with pytest.raises(RuntimeError, match="placeholder"):
            _resolve_jwt_secret(env)


# ============================================================
# DatabaseConfig defaults
# ============================================================

class TestDatabaseConfigDefaults:
    def test_defaults(self):
        db = DatabaseConfig()
        assert db.host == "postgres"
        assert db.port == 5432
        assert db.database == "postgres"
        assert db.user == "postgres"
        assert db.password == ""
        assert db.pool_min_size == 5
        assert db.pool_max_size == 30
        assert db.command_timeout == 60


# ============================================================
# load_settings — mocked env
# ============================================================

class TestLoadSettings:
    def test_load_settings_with_mocked_env(self):
        mock_env = MagicMock()
        mock_env.str.side_effect = lambda key, default=None: {
            "BOT_TOKEN": "123456:ABC",
            "WEBAPP_URL": "https://example.com",
            "REDIRECT_URL": "https://t.me/bot",
            "CHANNEL_ID": "-1002050105527",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "postgres",
            "POSTGRES_DB": "postgres",
        }.get(key, default)
        mock_env.bool.return_value = True
        mock_env.int.return_value = 1080
        mock_env.str.return_value = "socks5"

        with patch.object(settings_mod, "Env", return_value=mock_env):
            s = load_settings(require_jwt=False)

        assert s.bot.token == "123456:ABC"
        assert s.bot.webapp_url == "https://example.com"
        assert s.bot.redirect_url == "https://t.me/bot"
        assert s.db.password == "postgres"

    def test_load_settings_postgres_password_default(self):
        """Current behavior: POSTGRES_PASSWORD defaults to 'postgres'."""
        mock_env = MagicMock()
        mock_env.str.side_effect = lambda key, default=None: {
            "BOT_TOKEN": "123456:ABC",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "postgres",
            "POSTGRES_DB": "postgres",
        }.get(key, default)
        mock_env.bool.return_value = True
        mock_env.int.return_value = 1080
        mock_env.str.return_value = "socks5"

        with patch.object(settings_mod, "Env", return_value=mock_env):
            s = load_settings(require_jwt=False)

        assert s.db.password == "postgres"

    def test_load_settings_channel_id_fallback(self):
        """Current behavior: CHANNEL_ID has hardcoded fallback."""
        mock_env = MagicMock()
        mock_env.str.side_effect = lambda key, default=None: {
            "BOT_TOKEN": "123456:ABC",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "postgres",
            "POSTGRES_DB": "postgres",
        }.get(key, default)
        mock_env.bool.return_value = True
        mock_env.int.return_value = 1080
        mock_env.str.return_value = "socks5"

        with patch.object(settings_mod, "Env", return_value=mock_env):
            s = load_settings(require_jwt=False)

        assert s.bot.channel_id == "-1002050105527"

    def test_load_settings_requires_jwt_secret(self):
        mock_env = MagicMock()
        mock_env.str.return_value = None
        mock_env.bool.return_value = True

        with patch.object(settings_mod, "Env", return_value=mock_env):
            with pytest.raises(ValueError, match="Configuration error"):
                load_settings(require_jwt=True)

    def test_load_settings_jwt_optional_when_not_required(self):
        mock_env = MagicMock()
        mock_env.str.return_value = None
        mock_env.bool.return_value = True

        with patch.object(settings_mod, "Env", return_value=mock_env):
            s = load_settings(require_jwt=False)
        assert s.jwt is None


# ============================================================
# Settings dataclass instantiation
# ============================================================

class TestSettingsDataclass:
    def test_minimal_settings(self):
        s = Settings(
            app=AppConfig(),
            db=DatabaseConfig(),
            bot=BotConfig(token="t", channel_id="-1001"),
        )
        assert s.app.host == "0.0.0.0"
        assert s.app.port == 8080
        assert s.bot.token == "t"
        assert s.bot.channel_id == "-1001"
