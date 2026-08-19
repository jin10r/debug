"""
Telegram WebApp authentication helpers: JWT token issue/verify.

HTTP-эндпоинты аутентификации живут в core/api/auth.py — этот модуль содержит
только переиспользуемые примитивы (генерация/верификация JWT).
"""

from typing import Optional, Dict, Tuple, Any
from collections import OrderedDict
import hashlib
import logging
import time
import uuid as uuid_lib
import jwt

from core.settings import settings
from core.utils.validators import validate_jwt_string, validate_telegram_user_id

logger = logging.getLogger(__name__)


def generate_jwt_tokens(user_data: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    Generate access and refresh JWT tokens.

    Returns:
        (access_token, refresh_token, jti)
        jti — уникальный ID refresh-токена для хранения в БД и single-use проверки.
    """
    user_id = validate_telegram_user_id(user_data.get('id', user_data.get('sub')))
    now = int(time.time())
    jti = str(uuid_lib.uuid4())  # уникальный ID refresh-токена

    access_payload = {
        'sub': str(user_id),
        'first_name': str(user_data.get('first_name', ''))[:100],
        'username': str(user_data.get('username', ''))[:32],
        'iat': now,
        'exp': now + settings.jwt.access_token_ttl,
        'type': 'access'
    }

    refresh_payload = {
        'sub': str(user_id),
        'jti': jti,            # JWT ID — хранится в БД для single-use контроля
        'iat': now,
        'exp': now + settings.jwt.refresh_token_ttl,
        'type': 'refresh'
    }

    access_token = jwt.encode(
        access_payload,
        settings.jwt.secret,
        algorithm=settings.jwt.algorithm
    )

    refresh_token = jwt.encode(
        refresh_payload,
        settings.jwt.secret,
        algorithm=settings.jwt.algorithm
    )

    return access_token, refresh_token, jti


_jwt_token_cache: "OrderedDict[str, dict]" = OrderedDict()
_JWT_CACHE_MAX_SIZE = 10000
_jwt_cache_ttl = 10


def verify_jwt_token(token: str, token_type: str = 'access') -> Optional[Dict]:
    """
    Verify JWT token and return payload with caching.

    Кэширование уменьшает нагрузку на CPU при частых запросах.
    Кэшируются только валидные токены.

    Args:
        token: JWT токен
        token_type: Тип токена ('access' или 'refresh')

    Returns:
        Payload токена или None если токен невалиден
    """
    # Pre-validate token format before touching crypto or cache
    try:
        token = validate_jwt_string(token)
    except ValueError:
        return None

    # Validate token_type argument
    if token_type not in ('access', 'refresh'):
        logger.warning(f"Invalid token_type argument: {token_type}")
        return None

    cache_key = hashlib.sha256(token.encode()).hexdigest() + f":{token_type}"

    if cache_key in _jwt_token_cache:
        cached_result = _jwt_token_cache[cache_key]
        if time.time() - cached_result['timestamp'] < _jwt_cache_ttl:
            _jwt_token_cache.move_to_end(cache_key)
            return cached_result['payload']
        else:
            del _jwt_token_cache[cache_key]

    while len(_jwt_token_cache) >= _JWT_CACHE_MAX_SIZE:
        _jwt_token_cache.popitem(last=False)

    try:
        payload = jwt.decode(
            token,
            settings.jwt.secret,
            algorithms=[settings.jwt.algorithm],
            options={
                'require': ['exp', 'type', 'sub'],
                'verify_signature': True,
                'verify_exp': True,
                'verify_iat': True,
                'verify_nbf': False,
                'verify_aud': False,
            }
        )

        if payload.get('type') != token_type:
            return None

        _jwt_token_cache[cache_key] = {
            'payload': payload,
            'timestamp': time.time()
        }

        return payload

    except jwt.ExpiredSignatureError:
        logger.warning(f"Expired {token_type} token")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid {token_type} token: {e}")
        return None
