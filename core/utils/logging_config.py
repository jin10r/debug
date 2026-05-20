"""
Structured JSON logging configuration (Docker-optimized version)

Модуль импортируется и парсером, и app-сервисом. Парсер не имеет aiohttp в
зависимостях — импорт `aiohttp.web` делаем опциональным, middleware
определяется только если aiohttp доступен.
"""
import logging
import json
import sys
import traceback
import uuid
import os
from datetime import datetime
from contextvars import ContextVar

try:
    from aiohttp import web
    _HAS_AIOHTTP = True
except ImportError:
    web = None  # type: ignore[assignment]
    _HAS_AIOHTTP = False


_request_id_var: ContextVar[str] = ContextVar('request_id', default='-')


class JSONFormatter(logging.Formatter):
    """
    Format log records as JSON

    Benefits:
    - Structured logs for easier parsing
    - Compatible with log aggregation systems (ELK, Loki, etc.)
    - Machine-readable format
    """

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'thread': record.thread,
            'thread_name': record.threadName
        }

        # Add request ID if available (from middleware)
        if hasattr(record, 'request_id'):
            log_obj['request_id'] = record.request_id

        # Add user ID if available
        if hasattr(record, 'user_id'):
            log_obj['user_id'] = record.user_id

        # Add any extra fields
        if hasattr(record, 'extra_data'):
            log_obj.update(record.extra_data)

        # Add exception information if present
        if record.exc_info:
            log_obj['exception'] = {
                'type': record.exc_info[0].__name__ if record.exc_info[0] else None,
                'message': str(record.exc_info[1]) if record.exc_info[1] else None,
                'traceback': self.formatException(record.exc_info)
            }

        # Add stack info if present
        if record.stack_info:
            log_obj['stack_info'] = record.stack_info

        return json.dumps(log_obj, ensure_ascii=False, default=str)


class ContextLogger(logging.LoggerAdapter):
    """
    Logger adapter that adds context to all log messages

    Usage:
        logger = ContextLogger(logging.getLogger(__name__), {'request_id': '123'})
        logger.info('User logged in', extra={'user_id': 456})
    """

    def process(self, msg, kwargs):
        # Merge context into extra
        extra = kwargs.get('extra', {})
        extra.update(self.extra)
        kwargs['extra'] = extra
        return msg, kwargs


def setup_logging(
    level: int = logging.INFO,
    json_format: bool = True,
    suppress_noisy_loggers: bool = True
):
    """
    Configure application logging (Docker-optimized version)
    Uses only console output to avoid file permission issues in containers

    Args:
        level: Logging level (logging.INFO, logging.DEBUG, etc.)
        json_format: Use JSON formatter (True) or standard text format (False)
        suppress_noisy_loggers: Suppress output from noisy third-party libraries
    """
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)

    # Set formatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    console_handler.setFormatter(formatter)

    # Configure root logger with console handler only (no file handlers to avoid permission issues)
    logging.root.handlers = [console_handler]
    logging.root.setLevel(level)

    # Suppress noisy loggers
    if suppress_noisy_loggers:
        logging.getLogger("pyrogram").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
        logging.getLogger("aiogram").setLevel(logging.INFO)

    logging.info("Logging configured (console-only)", extra={
        'json_format': json_format,
        'level': logging.getLevelName(level),
        'output': 'console'
    })


# ============================================
# Request ID Middleware (aiohttp-only — определяется лишь если aiohttp доступен)
# ============================================

if not _HAS_AIOHTTP:
    # Stub для окружений без aiohttp (например, parser): попытка реально
    # подключить middleware к aiohttp-приложению вернёт явную ошибку.
    async def logging_middleware(*args, **kwargs):  # type: ignore[misc]
        raise RuntimeError(
            "logging_middleware requires aiohttp, but aiohttp is not installed "
            "in this environment"
        )

else:
    @web.middleware
    async def logging_middleware(request: web.Request, handler):
        """
        Middleware to add request ID to all logs

        Also adds response time and status code logging
        """
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        request['request_id'] = request_id

        # Use ContextVar so request_id is correct in async concurrent requests
        token = _request_id_var.set(request_id)

        # Store original log record factory
        old_factory = logging.getLogRecordFactory()

        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            record.request_id = _request_id_var.get()
            return record

        # Set new factory with request ID
        logging.setLogRecordFactory(record_factory)
        
        # Log request
        logger = logging.getLogger('aiohttp.access')
        logger.info(
            f"{request.method} {request.path}",
            extra={
                'method': request.method,
                'path': request.path,
                'remote': request.remote,
                'user_agent': request.headers.get('User-Agent')
            }
        )
        
        # Time the request
        import time
        start_time = time.time()
        
        try:
            response = await handler(request)
            status = response.status
            
            # Add request ID to response headers
            response.headers['X-Request-ID'] = request_id
            
            return response
        
        except web.HTTPException as e:
            status = e.status
            raise
        
        except Exception as e:
            status = 500
            logger.error(
                f"Unhandled exception in {request.method} {request.path}",
                exc_info=True,
                extra={}
            )
            raise
        
        finally:
            duration = time.time() - start_time
            
            # Log response
            logger.info(
                f"{request.method} {request.path} -> {status}",
                extra={
                    'method': request.method,
                    'path': request.path,
                    'status': status,
                    'duration_ms': round(duration * 1000, 2)
                }
            )
            
            # Restore original factory
            logging.setLogRecordFactory(old_factory)

            # Restore context var
            _request_id_var.reset(token)


# ============================================
# Helper Functions
# ============================================

def get_logger_with_context(name: str, **context) -> ContextLogger:
    """
    Get a logger with context
    
    Example:
        logger = get_logger_with_context(__name__, user_id=123)
        logger.info('User action')  # Will include user_id in all logs
    """
    base_logger = logging.getLogger(name)
    return ContextLogger(base_logger, context)


def log_with_extra(
    logger: logging.Logger,
    level: int,
    message: str,
    **extra_fields
):
    """
    Log a message with extra fields
    
    Example:
        log_with_extra(
            logger, logging.INFO, 
            'User logged in',
            user_id=123, ip='1.2.3.4'
        )
    """
    logger.log(level, message, extra={'extra_data': extra_fields})


# ============================================
# Exception Logging Decorator
# ============================================

def log_exceptions(logger: logging.Logger):
    """
    Decorator to automatically log exceptions from functions
    
    Usage:
        @log_exceptions(logger)
        async def my_function():
            ...
    """
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Exception in {func.__name__}: {e}",
                    exc_info=True,
                    extra={'extra_data': {
                        'function': func.__name__,
                        'args_count': len(args),
                        'kwargs_keys': list(kwargs.keys())
                    }}
                )
                raise
        
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Exception in {func.__name__}: {e}",
                    exc_info=True,
                    extra={'extra_data': {
                        'function': func.__name__
                    }}
                )
                raise
        
        # Return appropriate wrapper
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator
