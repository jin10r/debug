"""Generic retry utility with exponential backoff.

Usage:
    result = await retry_with_backoff(
        func=my_async_function,
        args=(arg1, arg2),
        max_attempts=5,
        retryable_exceptions=(ConnectionError, TimeoutError),
    )
"""

import asyncio
import logging
from typing import Any, Callable, Optional, Tuple

from common.db.base import RETRYABLE_EXCEPTIONS

logger = logging.getLogger(__name__)


async def retry_with_backoff(
    func: Callable,
    args: tuple = (),
    kwargs: Optional[dict] = None,
    max_attempts: int = 5,
    max_transient_attempts: int = 8,
    retryable_exceptions: tuple = RETRYABLE_EXCEPTIONS,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    on_retry: Optional[Callable] = None,
    label: str = "",
) -> Any:
    """Execute func with exponential backoff on transient errors.

    Args:
        func: Async callable to execute.
        args: Positional arguments for func.
        kwargs: Keyword arguments for func.
        max_attempts: Max attempts for non-transient errors.
        max_transient_attempts: Max attempts for transient errors (higher).
        retryable_exceptions: Exception types considered transient/retryable.
        base_delay: Base delay in seconds (doubles each retry).
        max_delay: Maximum delay cap in seconds.
        on_retry: Optional callback(attempt, exception, delay) called before each retry.
        label: Label for log messages (e.g., message_id).

    Returns:
        Result of func() on success.

    Raises:
        The last exception if all attempts are exhausted.
    """
    kwargs = kwargs or {}
    last_exception = None

    for attempt in range(1, max(max_attempts, max_transient_attempts) + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            transient = isinstance(e, retryable_exceptions)
            limit = max_transient_attempts if transient else max_attempts

            if attempt < limit:
                delay = min(base_delay ** attempt, max_delay)
                if on_retry:
                    on_retry(attempt, e, delay)
                else:
                    logger.warning(
                        f"{label}: attempt {attempt} failed "
                        f"({type(e).__name__}: {e}); retry in {delay}s"
                    )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"{label}: failed after {attempt} attempts: {e}"
                )
                raise

    raise last_exception  # unreachable, but satisfies type checkers
