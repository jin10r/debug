"""
Telegram WebApp authentication with HMAC validation and JWT tokens
Uses centralized validation from core.utils.telegram_validation
"""

from typing import Optional, Dict, Tuple, Any
import logging
import time
import jwt
from aiohttp import web

from core.settings import settings
from core.utils.telegram_validation import validate_telegram_webapp_data

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


# Кэш для верификации JWT токенов
# Кэшируем только валидные токены, чтобы избежать кэширования атак
_jwt_token_cache = {}
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
            return cached_result['payload']
        else:
            # Истёк TTL, удаляем из кэша
            del _jwt_token_cache[cache_key]
    
    # Очистка кэша при превышении размера (LRU-like)
    if len(_jwt_token_cache) >= _JWT_CACHE_MAX_SIZE:
        # Удаляем 10% самых старых записей
        sorted_keys = sorted(
            _jwt_token_cache.keys(),
            key=lambda k: _jwt_token_cache[k]['timestamp']
        )
        for key in sorted_keys[:_JWT_CACHE_MAX_SIZE // 10]:
            del _jwt_token_cache[key]
    
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





def parse_init_data(init_data: str) -> Dict[str, str]:
    """
    Parse Telegram initData query string into dictionary.
    
    Args:
        init_data: Raw initData string (query string format)
        
    Returns:
        Dictionary of key-value pairs
    """
    import urllib.parse
    parsed = urllib.parse.parse_qs(init_data)
    # parse_qs returns lists, flatten to single values
    return {k: v[0] if v else '' for k, v in parsed.items()}


async def validate_init_data_endpoint(request: web.Request) -> web.Response:
    """API endpoint to validate Telegram initData and issue JWT tokens.

    Security:
    - Validates HMAC-SHA256 signature
    - Checks for replay attacks using Redis
    - Rate limiting per IP

    Returns:
        JSON with valid/invalid status, user data, and JWT tokens
    """
    try:
        data = await request.json()
        init_data = data.get('init_data')

        # Validation disabled: allow any request (dev mode)
        if not settings.app.telegram_validation_enabled:
            logger.info('[DEV MODE] Telegram validation disabled, allowing request without verification')
            
            # Generate tokens for a test/dev user
            user_data = {
                'id': '123456789',  # Test user ID
                'first_name': 'Dev',
                'username': 'dev_user'
            }
            
            # Try to extract user info from init_data if provided (no verification)
            if init_data:
                try:
                    from parser.monitoring import parse_init_data as telegram_parse_init_data
                    params = telegram_parse_init_data(init_data)
                    user_json = params.get('user')
                    if user_json:
                        import json
                        user_info = json.loads(user_json)
                        if isinstance(user_info, dict):
                            user_data = {
                                'id': str(user_info.get('id', '123456789')),
                                'first_name': user_info.get('first_name', 'Dev'),
                                'username': user_info.get('username')
                            }
                except Exception as e:
                    logger.warning(f'Could not parse init_data in dev mode: {e}')
            
            # Generate tokens without verification
            access_token, refresh_token = generate_jwt_tokens(user_data)
            
            return web.json_response({
                'valid': True,
                'user': {
                    'id': user_data['id'],
                    'first_name': user_data['first_name'],
                    'username': user_data.get('username')
                },
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_in': settings.jwt.access_token_ttl
            })

        if not init_data:
            logger.warning("Validation request missing init_data")
            return web.json_response(
                {'valid': False, 'error': 'Missing init_data'},
                status=400
            )

        # Rate limiting
        client_ip = request.headers.get('X-Forwarded-For', request.remote) or request.remote
        if not await check_rate_limit(client_ip):
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            return web.json_response(
                {'valid': False, 'error': 'Rate limit exceeded'},
                status=429
            )

        # Parse to get hash for replay check
        params = parse_init_data(init_data)
        hash_value = params.get('hash')

        if not hash_value:
            logger.warning("Validation request missing hash")
            return web.json_response(
                {'valid': False, 'error': 'Missing hash'},
                status=400
            )

        # Check for replay attack
        try:
            if await is_hash_used(hash_value):
                logger.warning(f"Replay attack detected: hash {hash_value[:16]}... already used")
                return web.json_response(
                    {'valid': False, 'error': 'Init data already used (replay attack detected)'},
                    status=401
                )
        except Exception as e:
            logger.error(f"Replay check failed: {e}")
            return web.json_response(
                {'valid': False, 'error': 'Security check failed'},
                status=503
            )

        # Validate with HMAC using centralized validation
        is_valid, user_data = validate_telegram_webapp_data(
            init_data,
            settings.bot.token
        )

        if not is_valid:
            logger.warning(f"Invalid initData from user")
            return web.json_response(
                {'valid': False, 'error': 'Invalid init data'},
                status=401
            )

        # Mark hash as used (prevent replay)
        try:
            await mark_hash_used(hash_value)
        except Exception as e:
            logger.error(f"Failed to mark hash as used: {e}")
            # Don't fail the request, but log the error

        # Generate tokens
        access_token, refresh_token = generate_jwt_tokens(user_data)

        logger.info(f"Successfully validated initData for user: {user_data.get('id')}")

        return web.json_response({
            'valid': True,
            'user': {
                'id': user_data['id'],
                'first_name': user_data['first_name'],
                'username': user_data['username']
            },
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_in': settings.jwt.access_token_ttl
        })

    except web.HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.exception(f"Validation endpoint error: {e}")
        return web.json_response(
            {'valid': False, 'error': 'Server error'},
            status=500
        )


async def refresh_token_endpoint(request: web.Request) -> web.Response:
    """API endpoint to refresh access token"""
    try:
        data = await request.json()
        refresh_token = data.get('refresh_token')

        if not refresh_token:
            return web.json_response(
                {'error': 'Missing refresh_token'},
                status=400
            )

        new_access_token = await refresh_access_token(refresh_token)

        if not new_access_token:
            return web.json_response(
                {'error': 'Invalid refresh token'},
                status=401
            )

        return web.json_response({
            'access_token': new_access_token,
            'expires_in': settings.jwt.access_token_ttl
        })

    except Exception as e:
        logger.error(f"Refresh token error: {e}")
        return web.json_response(
            {'error': 'Server error'},
            status=500
        )
