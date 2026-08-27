"""Circuit breaker for protecting against overload (e.g., DB, external services).

Usage:
    breaker = CircuitBreaker(failure_threshold=5, timeout=60.0)
    result = await breaker.call(some_async_function, arg1, arg2)
"""

import asyncio
import logging
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failures exceeded threshold, blocking requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Circuit breaker for protecting against overload."""

    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = CircuitState.CLOSED
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self.last_failure_time and \
                   (asyncio.get_running_loop().time() - self.last_failure_time) > self.timeout:
                    logger.info("Circuit breaker: transitioning to HALF_OPEN")
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise Exception("Circuit breaker is OPEN")

        try:
            result = await func(*args, **kwargs)
            async with self._lock:
                if self.state == CircuitState.HALF_OPEN:
                    logger.info("Circuit breaker: transitioning to CLOSED")
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
            return result
        except Exception as e:
            async with self._lock:
                self.failure_count += 1
                self.last_failure_time = asyncio.get_running_loop().time()

                if self.failure_count >= self.failure_threshold:
                    if self.state != CircuitState.OPEN:
                        logger.error(f"Circuit breaker: transitioning to OPEN after {self.failure_count} failures")
                        self.state = CircuitState.OPEN
            raise
