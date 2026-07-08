"""Кэш для позиций"""

import time
from typing import Dict, Any, Optional, List


class PositionCache:
    """Кэш позиций с TTL"""

    def __init__(self, default_ttl: int = 5):
        self._cache: Dict[str, tuple] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        """Получение данных из кэша"""
        if key not in self._cache:
            return None

        data, expiry = self._cache[key]
        if time.time() > expiry:
            del self._cache[key]
            return None

        return data

    def set(self, key: str, data: Any, ttl: Optional[int] = None):
        """Сохранение данных в кэш"""
        ttl = ttl or self._default_ttl
        self._cache[key] = (data, time.time() + ttl)

    def clear(self):
        """Очистка кэша"""
        self._cache.clear()