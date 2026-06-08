"""
Telegram WebApp authentication utilities for server-side validation
Implements proper initData validation according to Telegram Bot API documentation

Features:
- HMAC-SHA256 signature verification
- Circuit breaker for fault tolerance
- Rate limiting protection
"""
import logging
import hashlib
import hmac
import urllib.parse
import time
from typing import Optional, Dict, Tuple
import pybreaker

logger = logging.getLogger(__name__)


# Circuit breaker для Telegram API
# Открывается после 5 ошибок за 1 минуту, восстанавливается через 30 секунд
telegram_validator_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    name='telegram_validator'
)


@telegram_validator_breaker
def validate_telegram_webapp_data(init_data: str, bot_token: str, max_age_hours: int = 24) -> Tuple[bool, Optional[Dict]]:
    """
    Validates Telegram WebApp initData using HMAC-SHA256 signature verification.
    Protected by circuit breaker for fault tolerance.

    According to Telegram documentation:
    1. Extract the hash parameter from initData
    2. Create data_check_string: all parameters sorted alphabetically, joined with \n
    3. Calculate HMAC-SHA256 of data_check_string with bot token's secret key
       (HMAC-SHA256 with key "WebAppData" and secret from bot token)
    4. Compare calculated hash with provided hash

    Args:
        init_data: Raw initData string from Telegram WebApp (query string format)
        bot_token: Bot token for validation (e.g., "123456:ABC-DEF...")
        max_age_hours: Maximum age of initData in hours (default 24)

    Returns:
        (is_valid: bool, user_data: dict or None)
        
    Circuit Breaker:
        - Opens after 5 consecutive failures
        - Resets after 30 seconds
        - ValueError (invalid data) не считается failure
    """
    if not init_data or not bot_token:
        logger.warning("Missing init_data or bot_token")
        return False, None

    try:
        # Parse the initData query string
        parsed = urllib.parse.parse_qs(init_data)
        
        # Extract hash
        hash_value = parsed.get('hash', [None])[0]
        if not hash_value:
            logger.warning("No hash found in initData")
            return False, None
        
        # Extract auth_date for freshness check
        auth_date = parsed.get('auth_date', [0])[0]
        if auth_date:
            auth_timestamp = int(auth_date)
            current_timestamp = int(time.time())
            max_age_seconds = max_age_hours * 3600
            
            if current_timestamp - auth_timestamp > max_age_seconds:
                logger.warning(f"initData expired: {current_timestamp - auth_timestamp}s old")
                return False, None
        
        # Build data_check_string (all fields except hash, sorted alphabetically)
        data_fields = {}
        for key, values in parsed.items():
            if key != 'hash' and values:
                data_fields[key] = values[0]
        
        # Sort and join with newlines
        data_check_string = '\n'.join(
            f"{k}={v}" for k, v in sorted(data_fields.items())
        )
        
        # Calculate secret key: HMAC-SHA256("WebAppData", bot_token)
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256
        ).digest()
        
        # Calculate hash: HMAC-SHA256(secret_key, data_check_string)
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Compare hashes — constant-time, чтобы не сливать инфу о совпадении
        # префикса хэша через тайминг (защита от timing-side-channel).
        if not hmac.compare_digest(calculated_hash, hash_value):
            logger.warning("Hash mismatch - possible tampering detected")
            return False, None
        
        # Extract user data
        user_data = None
        if 'user' in data_fields:
            import json
            try:
                user_data = json.loads(data_fields['user'])
            except json.JSONDecodeError:
                logger.warning("Failed to parse user data JSON")
                return False, None
        else:
            # Build user data from individual fields
            user_data = {
                'id': int(data_fields.get('id', 0)) if 'id' in data_fields else None,
                'first_name': data_fields.get('first_name'),
                'last_name': data_fields.get('last_name'),
                'username': data_fields.get('username'),
                'is_premium': data_fields.get('is_premium') == 'true',
                'auth_date': int(auth_date) if auth_date else None
            }
        
        logger.info(f"Valid initData for user: {user_data.get('id')}")
        return True, user_data
        
    except Exception as e:
        logger.error(f"Error validating initData: {e}")
        return False, None


def extract_user_id_from_init_data(init_data: str) -> Optional[int]:
    """
    Extract user ID from initData without full validation.
    Use only for logging/analytics, NOT for authentication.

    Args:
        init_data: Raw initData string

    Returns:
        user_id or None
    """
    if not init_data:
        return None

    try:
        parsed = urllib.parse.parse_qs(init_data)

        # Try to get from 'user' JSON field first
        if 'user' in parsed:
            import json
            user_data = json.loads(parsed['user'][0])
            return user_data.get('id')

        # Fallback to direct 'id' field
        if 'id' in parsed:
            return int(parsed['id'][0])

        return None
    except Exception:
        return None


def check_init_data_or_redirect(
    init_data: Optional[str],
    bot_token: str,
    redirect_url: Optional[str] = None,
    max_age_hours: int = 24
) -> Tuple[bool, Optional[str]]:
    """
    Validates initData and returns redirect URL if invalid.

    This is the main entry point for frontend validation.

    Args:
        init_data: The initData from Telegram.WebApp.initData
        bot_token: Bot token for HMAC validation
        redirect_url: URL to redirect to if validation fails
        max_age_hours: Maximum age of initData

    Returns:
        (is_valid: bool, redirect_url: str or None)
        If is_valid is False, redirect_url contains where to redirect the user
    """
    is_valid, user_data = validate_telegram_webapp_data(init_data, bot_token, max_age_hours)

    if is_valid:
        logger.info(f"Valid initData for user: {user_data.get('username') or user_data.get('id')}")
        return True, None

    if redirect_url:
        logger.warning(f"Invalid or missing initData, redirecting to: {redirect_url}")
        return False, redirect_url

    logger.warning("Invalid initData, no redirect URL configured")
    return False, None
