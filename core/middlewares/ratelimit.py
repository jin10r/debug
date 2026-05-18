"""
Rate limiting middleware for aiohttp — fixed-window counter, O(1) per request.
"""
import time
from aiohttp import web
import logging

logger = logging.getLogger(__name__)

# Endpoints exempt from rate limiting
_EXEMPT_PATHS = frozenset(['/health', '/health/ready', '/health/live', '/health/detailed'])


class RateLimiter:
    """
    Fixed-window rate limiter.

    State per (ip, path): [count, window_start].  All operations are O(1).
    The window resets when `now - window_start > window_seconds`, so a burst
    at the end of one window + start of the next can exceed the limit by 2x —
    acceptable for this use-case; use a sliding-window Redis counter for
    stricter enforcement.
    """

    def __init__(
        self,
        default_limit: int = 60,
        window_seconds: int = 60,
        cleanup_interval: int = 300,
        max_ips_tracked: int = 10_000,
    ):
        self.default_limit = default_limit
        self.window = window_seconds
        self.cleanup_interval = cleanup_interval
        self.max_ips_tracked = max_ips_tracked
        # {(ip, path): [count, window_start]}
        self._counters: dict = {}
        self._last_cleanup = time.monotonic()

        # Per-endpoint overrides: path -> (limit, window_seconds)
        self.endpoint_limits: dict = {
            '/api/events': (120, 60),
            '/api/streets': (30, 60),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_limit(self, path: str) -> tuple:
        return self.endpoint_limits.get(path, (self.default_limit, self.window))

    def _cleanup(self, now: float):
        if now - self._last_cleanup < self.cleanup_interval:
            return
        max_window = max((w for _, w in self.endpoint_limits.values()), default=self.window)
        cutoff = now - max_window
        stale = [k for k, v in self._counters.items() if v[1] < cutoff]
        for k in stale:
            del self._counters[k]
        # Emergency eviction: drop oldest 20 % when over cap
        if len(self._counters) > self.max_ips_tracked:
            excess = sorted(self._counters.items(), key=lambda x: x[1][1])
            evict = int(self.max_ips_tracked * 0.2)
            for k, _ in excess[:evict]:
                del self._counters[k]
            logger.warning(
                "Rate limiter emergency eviction: %d IPs removed, %d remaining",
                evict, len(self._counters),
            )
        self._last_cleanup = now

    def _get_client_ip(self, request: web.Request) -> str:
        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.headers.get('X-Real-IP') or request.remote or '127.0.0.1'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, ip: str, path: str) -> tuple:
        """
        Check and update the counter for (ip, path).

        Returns (allowed: bool, limit: int, remaining: int, reset_at: int).
        """
        now = time.monotonic()
        limit, window = self._get_limit(path)
        key = (ip, path)

        entry = self._counters.get(key)
        if entry is None or now - entry[1] > window:
            self._counters[key] = [1, now]
            self._cleanup(now)
            return True, limit, limit - 1, int(time.time() + window)

        count, start = entry
        if count >= limit:
            reset_at = int(time.time() + (window - (now - start)))
            logger.warning(
                "Rate limit exceeded: %s %s — %d/%d in %ds window",
                ip, path, count, limit, window,
            )
            return False, limit, 0, reset_at

        entry[0] += 1
        remaining = limit - entry[0]
        reset_at = int(time.time() + (window - (now - start)))
        self._cleanup(now)
        return True, limit, remaining, reset_at

    @web.middleware
    async def middleware(self, request: web.Request, handler):
        if request.path in _EXEMPT_PATHS:
            return await handler(request)

        ip = self._get_client_ip(request)
        allowed, limit, remaining, reset_at = self.check(ip, request.path)

        if not allowed:
            _, window = self._get_limit(request.path)
            return web.json_response(
                {
                    'error': 'Rate limit exceeded',
                    'message': f'Maximum {limit} requests per {window} seconds',
                    'retry_after': window,
                },
                status=429,
                headers={'Retry-After': str(window)},
            )

        response = await handler(request)
        response.headers['X-RateLimit-Limit'] = str(limit)
        response.headers['X-RateLimit-Remaining'] = str(remaining)
        response.headers['X-RateLimit-Reset'] = str(reset_at)
        return response
