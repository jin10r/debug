"""
Rate limiting middleware for aiohttp
"""
import time
from collections import defaultdict
from aiohttp import web
import logging

logger = logging.getLogger(__name__)

class RateLimiter:
    """
    Token bucket rate limiter with sliding window
    
    Features:
    - Per-IP limiting
    - Configurable limits per endpoint
    - Graceful degradation
    - Memory efficient (auto cleanup)
    """
    
    def __init__(
        self,
        default_limit: int = 60,
        window_seconds: int = 60,
        cleanup_interval: int = 60,  # Уменьшено с 300 до 60 секунд
        max_ips_tracked: int = 10000  # Максимум отслеживаемых IP
    ):
        self.default_limit = default_limit
        self.window = window_seconds
        self.requests = defaultdict(list)  # IP -> [timestamps]
        self.cleanup_interval = cleanup_interval
        self.last_cleanup = time.time()
        self.max_ips_tracked = max_ips_tracked
        
        # Endpoint-specific limits
        self.endpoint_limits = {
            '/api/events': (120, 60),           # 120 req/min
            '/api/streets': (30, 60),           # 30 req/min (static)
        }
    
    def _cleanup_old_requests(self):
        """Remove expired request records to prevent memory leak"""
        now = time.time()
        
        # Cleanup old records every N seconds
        if now - self.last_cleanup < self.cleanup_interval:
            return
        
        cutoff = now - max(window for _, window in self.endpoint_limits.values())
        initial_count = len(self.requests)
        
        for ip in list(self.requests.keys()):
            self.requests[ip] = [ts for ts in self.requests[ip] if ts > cutoff]
            if not self.requests[ip]:
                del self.requests[ip]
        
        # Emergency cleanup if too many IPs tracked
        if len(self.requests) > self.max_ips_tracked:
            # Remove oldest 20% of IPs
            ips_to_remove = sorted(
                self.requests.items(),
                key=lambda x: min(x[1]) if x[1] else 0
            )[:int(self.max_ips_tracked * 0.2)]
            
            for ip, _ in ips_to_remove:
                del self.requests[ip]
            
            logger.warning(
                f"Rate limiter emergency cleanup: removed {len(ips_to_remove)} oldest IPs. "
                f"Total: {len(self.requests)}/{self.max_ips_tracked}"
            )
        
        self.last_cleanup = now
        cleaned = initial_count - len(self.requests)
        if cleaned > 0:
            logger.info(
                f"Rate limiter cleanup: {len(self.requests)} IPs tracked "
                f"({cleaned} removed)"
            )
    
    def _get_client_ip(self, request: web.Request) -> str:
        """Extract client IP, handling proxies"""
        # Check X-Forwarded-For header (if behind nginx/proxy)
        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        
        # Check X-Real-IP header
        real_ip = request.headers.get('X-Real-IP')
        if real_ip:
            return real_ip
        
        # Fallback to direct connection IP
        return request.remote or '127.0.0.1'
    
    async def check_rate_limit(self, request: web.Request) -> bool:
        """
        Check if request is within rate limit
        
        Returns:
            True if allowed, False if rate limit exceeded
        """
        client_ip = self._get_client_ip(request)
        path = request.path
        now = time.time()
        
        # Get limit for this endpoint
        limit, window = self.endpoint_limits.get(
            path,
            (self.default_limit, self.window)
        )
        
        # Filter requests within window
        cutoff = now - window
        recent_requests = [ts for ts in self.requests[client_ip] if ts > cutoff]
        
        # Check limit
        if len(recent_requests) >= limit:
            logger.warning(
                f"Rate limit exceeded: {client_ip} - {len(recent_requests)}/{limit} "
                f"requests in {window}s for {path}"
            )
            return False
        
        # Record this request
        recent_requests.append(now)
        self.requests[client_ip] = recent_requests
        
        # Periodic cleanup
        self._cleanup_old_requests()
        
        return True
    
    @web.middleware
    async def middleware(self, request: web.Request, handler):
        """aiohttp middleware for rate limiting"""
        
        # Skip rate limiting for health checks
        if request.path in ['/health', '/health/ready', '/health/live']:
            return await handler(request)
        
        # Check rate limit
        if not await self.check_rate_limit(request):
            path = request.path
            limit, window = self.endpoint_limits.get(
                path,
                (self.default_limit, self.window)
            )
            
            return web.json_response(
                {
                    'error': 'Rate limit exceeded',
                    'message': f'Maximum {limit} requests per {window} seconds',
                    'retry_after': window
                },
                status=429,
                headers={'Retry-After': str(window)}
            )
        
        # Add rate limit info to response headers
        response = await handler(request)
        client_ip = self._get_client_ip(request)
        path = request.path
        limit, window = self.endpoint_limits.get(path, (self.default_limit, self.window))
        
        cutoff = time.time() - window
        remaining = limit - len([ts for ts in self.requests[client_ip] if ts > cutoff])
        
        response.headers['X-RateLimit-Limit'] = str(limit)
        response.headers['X-RateLimit-Remaining'] = str(max(0, remaining))
        response.headers['X-RateLimit-Reset'] = str(int(time.time() + window))
        
        return response
