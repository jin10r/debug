"""
CSRF Protection Middleware

Protects against Cross-Site Request Forgery attacks by validating CSRF tokens.
Works in conjunction with JWT authentication.

Features:
- Stateless CSRF token generation using HMAC
- Token validation for state-changing methods (POST, PUT, DELETE, PATCH)
- Exemption for safe methods (GET, HEAD, OPTIONS)
- Integration with session tokens
"""
import hashlib
import hmac
import logging
import time
from typing import Optional, Tuple
from aiohttp import web

from core.settings import settings

logger = logging.getLogger(__name__)


# HTTP методы которые требуют CSRF проверки
UNSAFE_METHODS = {'POST', 'PUT', 'DELETE', 'PATCH'}

# HTTP методы которые не требуют CSRF проверки
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}

# Исключения из CSRF проверки (пути)
CSRF_EXEMPT_PATHS = {
    '/health',
    '/health/live',
    '/health/ready',
    '/health/detailed',
    '/metrics',
    '/api/validation-config',
    '/api/validate-init',
    '/api/auth/refresh',
    '/ws',
}

# Время жизни CSRF токена (в секундах)
CSRF_TOKEN_TTL = 3600  # 1 час


def generate_csrf_token(session_token: str, timestamp: int = None) -> str:
    """
    Генерация CSRF токена на основе session token.
    
    Использует HMAC-SHA256 для создания токена привязанного к сессии.
    
    Args:
        session_token: JWT session токен пользователя
        timestamp: Временная метка (для тестирования)
    
    Returns:
        CSRF токен в формате: {timestamp}.{signature}
    """
    if timestamp is None:
        timestamp = int(time.time())
    
    # Создаём подпись на основе session token и timestamp
    message = f"{session_token}:{timestamp}"
    signature = hmac.new(
        settings.jwt.secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()[:32]
    
    return f"{timestamp}.{signature}"


def verify_csrf_token(csrf_token: str, session_token: str) -> Tuple[bool, str]:
    """
    Проверка CSRF токена.
    
    Args:
        csrf_token: Токен из запроса
        session_token: Session токен пользователя
    
    Returns:
        (is_valid, error_message)
    """
    if not csrf_token or not session_token:
        return False, "Missing CSRF token or session token"
    
    try:
        # Разбираем токен
        parts = csrf_token.split('.')
        if len(parts) != 2:
            return False, "Invalid CSRF token format"
        
        timestamp_str, provided_signature = parts
        
        # Проверяем timestamp
        try:
            timestamp = int(timestamp_str)
        except ValueError:
            return False, "Invalid CSRF timestamp"
        
        # Проверяем время жизни токена
        current_time = int(time.time())
        if current_time - timestamp > CSRF_TOKEN_TTL:
            return False, "CSRF token expired"
        
        # Генерируем ожидаемую подпись
        expected_signature = generate_csrf_token(session_token, timestamp).split('.')[1]
        
        # Сравниваем подписи (constant-time comparison)
        if not hmac.compare_digest(provided_signature, expected_signature):
            return False, "Invalid CSRF signature"
        
        return True, ""
        
    except Exception as e:
        logger.error(f"CSRF verification error: {e}")
        return False, f"CSRF verification failed: {str(e)}"


def extract_csrf_token(request: web.Request) -> Optional[str]:
    """
    Извлечение CSRF токена из запроса.
    
    Приоритет:
    1. X-CSRF-Token заголовок
    2. X-XSRF-Token заголовок  
    3. csrf_token в теле запроса (для form-data)
    """
    # Проверка заголовков
    csrf_token = request.headers.get('X-CSRF-Token')
    if csrf_token:
        return csrf_token
    
    csrf_token = request.headers.get('X-XSRF-Token')
    if csrf_token:
        return csrf_token
    
    # Для JSON запросов - тело уже прочитано middleware
    # CSRF токен должен быть в заголовке
    
    return None


def is_path_exempt(path: str) -> bool:
    """Проверка является ли путь исключением из CSRF проверки."""
    # Точное совпадение
    if path in CSRF_EXEMPT_PATHS:
        return True
    
    # Проверка префиксов (для вложенных путей)
    for exempt_path in CSRF_EXEMPT_PATHS:
        if path.startswith(exempt_path + '/') or path.startswith(exempt_path + '?'):
            return True
    
    return False


@web.middleware
async def csrf_middleware(request: web.Request, handler):
    """
    Middleware для CSRF защиты.
    
    Проверяет CSRF токены для всех unsafe методов.
    Добавляет CSRF токен в ответы для GET запросов.
    """
    path = request.path
    method = request.method
    
    # Проверка является ли метод безопасным
    if method in SAFE_METHODS:
        response = await handler(request)
        
        # Добавляем CSRF токен в ответ для GET запросов
        # Токен генерируется на основе session token
        if method == 'GET' and not is_path_exempt(path):
            session_token = request.cookies.get('session_token')
            if session_token:
                csrf_token = generate_csrf_token(session_token)
                response.headers['X-CSRF-Token'] = csrf_token
        
        return response
    
    # Проверка является ли путь исключением
    if is_path_exempt(path):
        logger.debug(f"CSRF exempt path: {path}")
        return await handler(request)
    
    # Для unsafe методов требуем CSRF токен
    if method in UNSAFE_METHODS:
        csrf_token = extract_csrf_token(request)
        session_token = request.cookies.get('session_token')
        
        # Если нет session token - проверка не нужна (нет авторизации)
        if not session_token:
            # Но если есть CSRF токен без session - это подозрительно
            if csrf_token:
                logger.warning(f"CSRF token without session token from {request.remote}")
            
            return await handler(request)
        
        # Проверяем CSRF токен
        if not csrf_token:
            logger.warning(f"Missing CSRF token for {method} {path} from {request.remote}")
            return web.json_response(
                {
                    'error': 'CSRF token missing',
                    'code': 'CSRF_TOKEN_MISSING',
                    'message': 'This request requires CSRF protection. Include X-CSRF-Token header.'
                },
                status=403,
                headers={'X-CSRF-Error': 'token_missing'}
            )
        
        is_valid, error_message = verify_csrf_token(csrf_token, session_token)
        
        if not is_valid:
            logger.warning(
                f"Invalid CSRF token for {method} {path} from {request.remote}: {error_message}"
            )
            return web.json_response(
                {
                    'error': 'CSRF token invalid',
                    'code': 'CSRF_TOKEN_INVALID',
                    'message': error_message
                },
                status=403,
                headers={'X-CSRF-Error': 'token_invalid'}
            )
        
        logger.debug(f"CSRF token validated for {method} {path}")
    
    return await handler(request)


# ============================================
# Helper функции для использования в коде
# ============================================

def get_csrf_headers(csrf_token: str) -> dict:
    """
    Получить заголовки для CSRF защищённого запроса.
    
    Usage:
        headers = get_csrf_headers(csrf_token)
        response = await fetch('/api/events', {
            method: 'POST',
            headers: {**headers, 'Content-Type': 'application/json'}
        })
    """
    return {
        'X-CSRF-Token': csrf_token,
        'X-XSRF-Token': csrf_token,
    }


async def get_csrf_token_from_response(response: web.Response) -> Optional[str]:
    """
    Извлечь CSRF токен из ответа сервера.
    
    Usage:
        response = await fetch('/api/config')
        csrf_token = await get_csrf_token_from_response(response)
    """
    return response.headers.get('X-CSRF-Token')
