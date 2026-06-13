"""
Cache module - кэширование данных
"""

from .ttl_cache import TTLCache, cached
from .price_cache import PriceCache
from .position_cache import PositionCache
from .validation_cache import ValidationCache

# Создаём глобальные экземпляры кэшей
price_cache = TTLCache(default_ttl=5, max_size=500, name="price_cache")
positions_cache = TTLCache(default_ttl=5, max_size=100, name="positions_cache")
candles_cache = TTLCache(default_ttl=30, max_size=200, name="candles_cache")
margin_cache = TTLCache(default_ttl=10, max_size=10, name="margin_cache")
instruments_cache = TTLCache(default_ttl=300, max_size=100, name="instruments_cache")

# Экспортируем всё необходимое
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
]

# Инициализация логгера
try:
    from trading_bot.logger import info
    info("🗂️ Cache module initialized")
except ImportError:
    pass