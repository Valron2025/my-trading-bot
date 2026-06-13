"""Кэш для цен с TTL"""

import time
from typing import Dict, Optional


class PriceCache:
    """Кэш цен с временем жизни"""

    def __init__(self, default_ttl: int = 5):
        self._cache: Dict[str, tuple] = {}  # figi -> (price, expiry)
        self._default_ttl = default_ttl

    def get(self, figi: str) -> Optional[float]:
        """Получение цены из кэша"""
        if figi not in self._cache:
            return None

        price, expiry = self._cache[figi]
        if time.time() > expiry:
            del self._cache[figi]
            return None

        return price

    def set(self, figi: str, price: float, ttl: int = None):
        """Сохранение цены в кэш"""
        ttl = ttl or self._default_ttl
        self._cache[figi] = (price, time.time() + ttl)

    def clear(self):
        """Очистка кэша"""
        self._cache.clear()

    def invalidate(self, figi: str):
        """Инвалидация конкретного ключа"""
        if figi in self._cache:
            del self._cache[figi]

# Глобальный экземпляр
price_cache = PriceCache()