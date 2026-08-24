"""
Tests for WebView validation system
"""
import pytest
import json
import hmac
import hashlib
import time
import urllib.parse
from unittest.mock import patch, MagicMock
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from core.api.auth import (
    get_validation_config_handler,
    validate_init_handler
)
from core.settings import Settings, AppConfig, BotConfig, JWTConfig


class TestWebViewValidation(AioHTTPTestCase):
    """Test suite for webview validation logic"""

    async def get_application(self):
        """Create test application"""
        app = web.Application()
        app.router.add_get('/api/validation-config', get_validation_config_handler)
        app.router.add_post('/api/validate-init', validate_init_handler)
        return app

    def _create_valid_init_data(self, bot_token: str, user_id: int = 123456) -> str:
        """Create valid Telegram initData with correct HMAC signature.

        По спецификации Telegram data_check_string считается по URL-DECODED
        значениям (сервер декодирует через parse_qs), а query string несёт
        percent-encoded форму. Подписываем декодированные значения.
        """
        auth_date = int(time.time())
        user_json = json.dumps({
            'id': user_id,
            'first_name': 'Test',
            'username': 'testuser'
        })

        # Build data_check_string with RAW (decoded) values (sorted alphabetically)
        params = {
            'auth_date': str(auth_date),
            'user': user_json,
        }
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(params.items()))

        # Calculate HMAC
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256
        ).digest()

        hash_value = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        # Build init_data query string with URL-encoded user value
        user_encoded = urllib.parse.quote(user_json)
        init_data = f"auth_date={auth_date}&user={user_encoded}&hash={hash_value}"
        return init_data

    @unittest_run_loop
    async def test_validation_config_strict_mode(self):
        """Test config endpoint returns strict mode settings"""
        with patch('core.api.auth.settings') as mock_settings:
            mock_settings.app.telegram_webview_validation = True
            mock_settings.bot.redirect_url = 'https://t.me/test_bot'
            
            resp = await self.client.request('GET', '/api/validation-config')
            assert resp.status == 200
            
            data = await resp.json()
            assert data['telegram_webview_validation'] is True
            assert data['redirect_url'] == 'https://t.me/test_bot'

    @unittest_run_loop
    async def test_validation_config_dev_mode(self):
        """Test config endpoint returns dev mode settings"""
        with patch('core.api.auth.settings') as mock_settings:
            mock_settings.app.telegram_webview_validation = False
            mock_settings.bot.redirect_url = None
            
            resp = await self.client.request('GET', '/api/validation-config')
            assert resp.status == 200
            
            data = await resp.json()
            assert data['telegram_webview_validation'] is False

    @unittest_run_loop
    async def test_validate_init_dev_mode_accepts_any(self):
        """Test that dev mode accepts any request without validation"""
        with patch('core.api.auth.settings') as mock_settings:
            mock_settings.app.telegram_webview_validation = False
            mock_settings.jwt.access_token_ttl = 900
            
            with patch('core.api.auth.generate_jwt_tokens') as mock_jwt:
                mock_jwt.return_value = ('access_token', 'refresh_token', 'test-jti')
                
                resp = await self.client.request(
                    'POST',
                    '/api/validate-init',
                    json={'init_data': ''}
                )
                
                assert resp.status == 200
                data = await resp.json()
                assert data['valid'] is True
                assert data['user']['id'] == '123456789'  # dev-пользователь
                assert 'access_token' in data

    @unittest_run_loop
    async def test_validate_init_strict_mode_missing_init_data(self):
        """Test that strict mode rejects missing init_data"""
        with patch('core.api.auth.settings') as mock_settings:
            mock_settings.app.telegram_webview_validation = True
            
            resp = await self.client.request(
                'POST',
                '/api/validate-init',
                json={'init_data': ''}
            )
            
            assert resp.status == 401
            data = await resp.json()
            assert data['valid'] is False
            assert 'invalid' in data['error'].lower()

    @unittest_run_loop
    async def test_validate_init_strict_mode_invalid_signature(self):
        """Test that strict mode rejects invalid HMAC signature"""
        with patch('core.api.auth.settings') as mock_settings:
            mock_settings.app.telegram_webview_validation = True
            mock_settings.bot.token = 'test_bot_token'
            
            # Invalid init_data (wrong hash)
            invalid_init_data = 'auth_date=123&user={"id":1}&hash=invalid'
            
            resp = await self.client.request(
                'POST',
                '/api/validate-init',
                json={'init_data': invalid_init_data}
            )
            
            assert resp.status == 401
            data = await resp.json()
            assert data['valid'] is False

    @unittest_run_loop
    async def test_validate_init_strict_mode_valid_signature(self):
        """Test that strict mode accepts valid HMAC signature"""
        bot_token = 'test_bot_token_123'
        
        with patch('core.api.auth.settings') as mock_settings:
            mock_settings.app.telegram_webview_validation = True
            mock_settings.bot.token = bot_token
            mock_settings.jwt.access_token_ttl = 900
            
            with patch('core.api.auth.generate_jwt_tokens') as mock_jwt:
                mock_jwt.return_value = ('access_token', 'refresh_token', 'test-jti')
                
                valid_init_data = self._create_valid_init_data(bot_token)
                
                resp = await self.client.request(
                    'POST',
                    '/api/validate-init',
                    json={'init_data': valid_init_data}
                )
                
                assert resp.status == 200
                data = await resp.json()
                assert data['valid'] is True
                assert 'access_token' in data
                assert data['user']['id'] == 123456

    @unittest_run_loop
    async def test_validate_init_strict_mode_expired_init_data(self):
        """Test that strict mode rejects expired init_data (>24 hours)"""
        bot_token = 'test_bot_token_123'
        
        with patch('core.api.auth.settings') as mock_settings:
            mock_settings.app.telegram_webview_validation = True
            mock_settings.bot.token = bot_token
            
            # Create init_data with old timestamp (25 hours ago) — sign decoded values
            old_timestamp = int(time.time()) - (25 * 3600)
            user_json = json.dumps({'id': 123456, 'first_name': 'Test'})
            user_encoded = urllib.parse.quote(user_json)
            
            params = {
                'auth_date': str(old_timestamp),
                'user': user_json,  # decoded value в data_check_string
            }
            data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(params.items()))
            
            secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
            hash_value = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
            
            expired_init_data = f"auth_date={old_timestamp}&user={user_encoded}&hash={hash_value}"
            
            resp = await self.client.request(
                'POST',
                '/api/validate-init',
                json={'init_data': expired_init_data}
            )
            
            assert resp.status == 401
            data = await resp.json()
            assert data['valid'] is False


class TestWebViewValidationIntegration:
    """Integration tests for complete validation flow"""
    
    def test_frontend_gate_logic_strict_mode(self):
        """Test frontend gate.js logic in strict mode"""
        # This would be a Selenium/Playwright test in real scenario
        # Here we just verify the logic flow
        
        config = {
            'telegram_webview_validation': True,
            'redirect_url': 'https://t.me/bot'
        }
        
        # Simulate: not in Telegram WebView
        is_telegram = False
        
        # Expected: should redirect
        if config['telegram_webview_validation'] and not is_telegram:
            should_redirect = True
            redirect_target = config['redirect_url']
        else:
            should_redirect = False
            redirect_target = None
        
        assert should_redirect is True
        assert redirect_target == 'https://t.me/bot'
    
    def test_frontend_gate_logic_dev_mode(self):
        """Test frontend gate.js logic in dev mode"""
        config = {
            'telegram_webview_validation': False,
            'redirect_url': None
        }
        
        # Simulate: not in Telegram WebView
        is_telegram = False
        
        # Expected: should NOT redirect
        if config['telegram_webview_validation'] and not is_telegram:
            should_redirect = True
        else:
            should_redirect = False
        
        assert should_redirect is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
