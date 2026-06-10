"""Кэш для результатов валидации"""

from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional


class ValidationCache:
    """Кэш для валидации тикеров"""

    def __init__(self, default_ttl_hours: int = 24):
        self._cache: Dict[str, Tuple[datetime, bool, Dict]] = {}
        self._default_ttl_hours = default_ttl_hours

    def get(self, ticker: str) -> Tuple[Optional[bool], Optional[Dict]]:
        """Получение результата валидации из кэша"""
        if ticker not in self._cache:
            return None, None

        timestamp, passed, stats = self._cache[ticker]
        if datetime.now() - timestamp > timedelta(hours=self._default_ttl_hours):
            del self._cache[ticker]
            return None, None

        return passed, stats

    def set(self, ticker: str, passed: bool, stats: Dict):
        """Сохранение результата валидации"""
        self._cache[ticker] = (datetime.now(), passed, stats)

    def clear(self):
        """Очистка кэша"""
        self._cache.clear()