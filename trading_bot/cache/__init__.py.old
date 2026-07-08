"""
Cache module - кэширование данных
"""

from .ttl_cache import TTLCache, cached
from .price_cache import PriceCache
from .position_cache import PositionCache
from .validation_cache import ValidationCache

# ========== НОВЫЙ УНИФИЦИРОВАННЫЙ КЭШ ==========
from .unified_cache import (
    UnifiedCache,
    CacheRegistry,
    cached as unified_cached,
    USE_UNIFIED_CACHE,
    print_cache_report
)

# ========== ГЛОБАЛЬНЫЕ ЭКЗЕМПЛЯРЫ КЭШЕЙ (используем TTLCache) ==========
# Кэш для цен (5 секунд) - быстрый TTL для актуальности
price_cache = TTLCache(default_ttl=5, max_size=1000, name="price_cache")

# Кэш для позиций (5 секунд)
positions_cache = TTLCache(default_ttl=5, max_size=200, name="positions_cache")

# Кэш для свечей (30 секунд) - свечи обновляются реже
candles_cache = TTLCache(default_ttl=300, max_size=500, name="candles_cache")

# Кэш для маржи (10 секунд)
margin_cache = TTLCache(default_ttl=30, max_size=10, name="margin_cache")

# Кэш для инструментов (5 минут) - список акций меняется редко
instruments_cache = TTLCache(default_ttl=300, max_size=500, name="instruments_cache")

# Кэш для аналитики (1 час) - фундаментальные данные
analysis_cache = TTLCache(default_ttl=3600, max_size=500, name="analysis_cache")

# Кэш для новостей (30 минут)
news_cache = TTLCache(default_ttl=1800, max_size=50, name="news_cache")

# ========== ЭКСПОРТ ==========
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
    # Новые экспорты
    'UnifiedCache',
    'CacheRegistry',
    'unified_cached',
    'USE_UNIFIED_CACHE',
    'print_cache_report',
]

# Инициализация логгера
try:
    from trading_bot.logger import info
    info("🗂️ Cache module initialized with TTLCache backends")
    if USE_UNIFIED_CACHE:
        info("🗂️ UnifiedCache also available (parallel mode)")
except ImportError:
    pass
