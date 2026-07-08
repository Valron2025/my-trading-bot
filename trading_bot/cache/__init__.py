"""
Cache module - кэширование данных
"""

# Импортируем всё из cache_manager
from .cache_manager import (
    TTLCache,
    PriceCache,
    PositionCache,
    ValidationCache,
    cached,
    price_cache,
    positions_cache,
    candles_cache,
    margin_cache,
    instruments_cache,
    analysis_cache,
    news_cache,
    validation_cache,
    get_all_cache_stats,
    clear_all_caches,
)

# Для обратной совместимости с кодом, который импортирует unified_cache
USE_UNIFIED_CACHE = False

class UnifiedCache(TTLCache):
    """Алиас для TTLCache (обратная совместимость)"""
    pass

class CacheRegistry:
    """Заглушка для CacheRegistry"""
    _caches = {}

    @classmethod
    def get(cls, name: str, **kwargs):
        if name not in cls._caches:
            cls._caches[name] = TTLCache(name=name, **kwargs)
        return cls._caches[name]

    @classmethod
    def get_all_stats(cls):
        return {name: cache.get_stats() for name, cache in cls._caches.items()}


__all__ = [
    'TTLCache',
    'PriceCache',
    'PositionCache',
    'ValidationCache',
    'cached',
    'price_cache',
    'positions_cache',
    'candles_cache',
    'margin_cache',
    'instruments_cache',
    'analysis_cache',
    'news_cache',
    'validation_cache',
    'get_all_cache_stats',
    'clear_all_caches',
    'UnifiedCache',
    'CacheRegistry',
    'USE_UNIFIED_CACHE',
]