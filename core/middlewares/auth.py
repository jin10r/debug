"""
Telegram WebApp authentication helpers: JWT token issue/verify + Redis manager.

HTTP-эндпоинты аутентификации живут в core/api/auth.py — этот модуль содержит
только переиспользуемые примитивы (генерация/верификация JWT, Redis-менеджер).
"""

from typing import Optional, Dict, Tuple, Any
from collections import OrderedDict
import logging
import time
import jwt

from core.settings import settings

try:
    from redis.asyncio import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


class RedisManager:
    """Redis connection manager for session and nonce storage"""
    _instance = None
    _redis = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_redis(self):
        if not REDIS_AVAILABLE or self._redis is None:
            return None
        return self._redis

    async def connect(self):
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available, using in-memory fallback")
            return None
        try:
            self._redis = Redis.from_url(
                f"redis://{settings.redis.host}:{settings.redis.port}/{settings.redis.db}",
                password=settings.redis.password,
                decode_responses=True
            )
            await self._redis.ping()
            logger.info("Redis connection established")
            return self._redis
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            return None

    async def disconnect(self):
        if self._redis:
            await self._redis.close()
            self._redis = None


async def check_redis_required_connection() -> bool:
    """Проверить доступность Redis. Raises RuntimeError если недоступен."""
    redis = await RedisManager().get_redis()
    if redis is None:
        await RedisManager().connect()
        redis = await RedisManager().get_redis()

    if redis is None:
        raise RuntimeError("Redis is mandatory but not available")

    try:
        await redis.ping()
        return True
    except Exception as e:
        raise RuntimeError(f"Redis ping failed: {e}")


def generate_jwt_tokens(user_data: Dict[str, Any]) -> Tuple[str, str]:
    """
    Generate access and refresh JWT tokens

    Returns:
        (access_token, refresh_token)
    """
    now = int(time.time())

    access_payload = {
        'sub': str(user_data['id']),
        'first_name': user_data.get('first_name', ''),
        'username': user_data.get('username', ''),
        'iat': now,
        'exp': now + settings.jwt.access_token_ttl,
        'type': 'access'
    }

    refresh_payload = {
        'sub': str(user_data['id']),
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

    return access_token, refresh_token


# Кэш верификации JWT — OrderedDict как LRU: вытеснение и обновление O(1),
# без сортировки на горячем пути. Кэшируются только валидные токены.
_jwt_token_cache: "OrderedDict[str, dict]" = OrderedDict()
_JWT_CACHE_MAX_SIZE = 10000
_JWT_CACHE_TTL = 60  # секунд


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
    # Проверка кэша
    cache_key = f"{token}:{token_type}"

    if cache_key in _jwt_token_cache:
        cached_result = _jwt_token_cache[cache_key]
        # Проверка TTL кэша
        if time.time() - cached_result['timestamp'] < _JWT_CACHE_TTL:
            _jwt_token_cache.move_to_end(cache_key)  # LRU: освежаем запись
            return cached_result['payload']
        else:
            # Истёк TTL, удаляем из кэша
            del _jwt_token_cache[cache_key]

    # LRU-вытеснение: выбрасываем самые старые записи — O(1), без сортировки.
    while len(_jwt_token_cache) >= _JWT_CACHE_MAX_SIZE:
        _jwt_token_cache.popitem(last=False)

    # Верификация токена
    try:
        payload = jwt.decode(
            token,
            settings.jwt.secret,
            algorithms=[settings.jwt.algorithm]
        )

        if payload.get('type') != token_type:
            return None

        # Кэширование успешного результата
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
