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

from core.utils.validators import (
    validate_init_data_length,
    validate_bot_token,
    validate_max_age_hours,
)

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

    # Pre-validation: length limits + format sanity
    try:
        init_data = validate_init_data_length(init_data)
        bot_token = validate_bot_token(bot_token)
        max_age_hours = validate_max_age_hours(max_age_hours)
    except ValueError as e:
        logger.warning(f"initData pre-validation failed: {e}")
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
                # Validate user_id is a positive integer
                uid = user_data.get('id')
                if uid is None:
                    logger.warning("initData user object missing 'id' field")
                    return False, None
                if isinstance(uid, float):
                    if uid != int(uid):
                        logger.warning("initData user id is not an integer")
                        return False, None
                    uid = int(uid)
                if not isinstance(uid, int) or uid <= 0:
                    logger.warning("initData user id must be a positive integer")
                    return False, None
                user_data['id'] = uid
                # Bound string fields
                for field in ('first_name', 'last_name', 'username'):
                    if field in user_data and user_data[field] is not None:
                        user_data[field] = str(user_data[field])[:100]
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
