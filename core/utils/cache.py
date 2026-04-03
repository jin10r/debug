"""
In-memory caching layer with TTL support and LRU eviction

Redis removed - using pure in-memory cache only.
"""
import time
import json
import logging
import asyncio
from typing import Optional, Any, Dict
from collections import OrderedDict

logger = logging.getLogger(__name__)


class CacheEntry:
    """Cache entry with expiration time and LRU tracking."""

    def __init__(self, value: Any, ttl_seconds: int):
        self.value = value
        self.expires_at = time.time() + ttl_seconds
        self.last_accessed = time.time()

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def touch(self):
        """Update last accessed time."""
        self.last_accessed = time.time()


class CacheManager:
    """
    In-memory cache with TTL support and LRU eviction

    Features:
    - Per-key TTL
    - Auto cleanup of expired entries
    - LRU eviction when max_size exceeded
    - Thread-safe operations
    - Statistics tracking
    """

    def __init__(self, redis_url: str = None, max_size: int = 10000):
        # Redis URL ignored - using in-memory cache only
        # OrderedDict для LRU eviction
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._connected = False  # Always False (no Redis)
        self.hits = 0
        self.misses = 0
        self.max_size = max_size
        self._eviction_count = 0
        
        # Блокировка для потокобезопасности
        self._lock = asyncio.Lock()

    async def connect(self, max_retries: int = 3):
        """No-op for in-memory cache"""
        self._connected = False
        logger.info(f"✅ In-memory cache initialized (max_size={self.max_size}, Redis removed)")
        return True

    async def close(self):
        """Clear cache on shutdown"""
        self._cache.clear()
        logger.info("In-memory cache cleared")
    
    def _make_key(self, prefix: str, *args, **kwargs) -> str:
        """Generate cache key from prefix and arguments"""
        parts = [prefix]
        parts.extend(str(arg) for arg in args)
        parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return ":".join(parts)

    def _evict_lru(self, count: int = None):
        """
        Evict least recently used entries.
        
        Args:
            count: Number of entries to evict (default: 10% of max_size)
        """
        if count is None:
            count = max(1, self.max_size // 10)
        
        evicted = 0
        while evicted < count and self._cache:
            # Удаляем самый старый элемент (первый в OrderedDict)
            self._cache.popitem(last=False)
            evicted += 1
        
        self._eviction_count += evicted
        logger.debug(f"LRU eviction: {evicted} entries removed")

    async def getItem(self, key: str) -> Optional[Any]:
        """Get value from in-memory cache with LRU update."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None

            if entry.is_expired():
                del self._cache[key]
                self.misses += 1
                return None

            # Обновляем время доступа для LRU
            entry.touch()
            # Перемещаем в конец OrderedDict (most recently used)
            self._cache.move_to_end(key)
            
            self.hits += 1
            return entry.value

    async def setItem(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in in-memory cache with TTL and LRU eviction."""
        async with self._lock:
            # Если ключ существует - обновляем и перемещаем в конец
            if key in self._cache:
                self._cache[key] = CacheEntry(value, ttl)
                self._cache.move_to_end(key)
                return True
            
            # Проверка на превышение размера
            if len(self._cache) >= self.max_size:
                self._evict_lru()
            
            # Добавляем новый элемент в конец
            self._cache[key] = CacheEntry(value, ttl)
            return True

    async def removeItem(self, key: str) -> bool:
        """Remove value from cache"""
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    async def getItemJSON(self, key: str) -> Optional[Any]:
        """Get JSON value from cache"""
        value = await self.getItem(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"Invalid JSON in cache for key: {key}")
            return None

    async def setItemJSON(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set JSON value in cache"""
        try:
            json_value = json.dumps(value)
            return await self.setItem(key, json_value, ttl)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize value for key {key}: {e}")
            return False

    async def invalidate_events_cache(self):
        """Invalidate all events cache"""
        keys_to_delete = [k for k in self._cache if k.startswith('events:')]
        for key in keys_to_delete:
            del self._cache[key]
        logger.debug(f"Invalidated {len(keys_to_delete)} events cache entries")

    # =====================
    # Events API methods
    # =====================

    async def get_events_geojson(self, time_filter: int, layers: list = None) -> Optional[str]:
        """Get cached GeoJSON events response"""
        key = f"events:geojson:{time_filter}"
        if layers:
            key += f":{','.join(sorted(layers))}"
        return await self.getItem(key)

    async def set_events_geojson(self, time_filter: int, layers: list, data: str, ttl: int = 30) -> bool:
        """Cache GeoJSON events response"""
        key = f"events:geojson:{time_filter}"
        if layers:
            key += f":{','.join(sorted(layers))}"
        return await self.setItem(key, data, ttl)

    async def get_streets_geojson(self) -> Optional[str]:
        """Get cached streets GeoJSON"""
        return await self.getItem("streets:geojson")

    async def set_streets_geojson(self, data: str, ttl: int = 3600) -> bool:
        """Cache streets GeoJSON"""
        return await self.setItem("streets:geojson", data, ttl)

    async def get_stats(self) -> dict:
        """Get cache statistics including LRU metrics."""
        total = self.hits + self.misses
        return {
            'connected': self._connected,
            'backend': 'memory',
            'memory_size': len(self._cache),
            'max_size': self.max_size,
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': self.hits / total if total > 0 else 0,
            'eviction_count': self._eviction_count,
            'utilization': len(self._cache) / self.max_size if self.max_size > 0 else 0
        }
