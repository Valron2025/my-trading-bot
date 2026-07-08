#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cache_manager.py - ЕДИНЫЙ УПРАВЛЯЮЩИЙ КЭШЕМ
Объединяет все кэши в один файл для упрощения поддержки
Сохраняет полную обратную совместимость со старыми импортами
"""

import time
import threading
from typing import Dict, Any, Optional, Callable, Tuple, List
from functools import wraps
from datetime import datetime, timedelta

try:
    from trading_bot.logger import debug, info, warning, success
    LOGGER_AVAILABLE = True
except ImportError:
    LOGGER_AVAILABLE = False
    def debug(msg): print(f"🔍 {msg}")
    def info(msg): print(f"ℹ️ {msg}")
    def warning(msg): print(f"⚠️ {msg}")
    def success(msg): print(f"✅ {msg}")


# ============================================================================
# БАЗОВЫЙ TTL КЭШ
# ============================================================================

class TTLCache:
    """Кэш с временем жизни (Time-To-Live)"""

    def __init__(self, default_ttl: int = 60, max_size: int = 1000, name: str = "default"):
        self._cache: Dict[str, Any] = {}
        self._expires: Dict[str, float] = {}
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._name = name
        self._lock = threading.RLock()
        self._stats = {
            "hits": 0, "misses": 0, "sets": 0, "deletes": 0,
            "clears": 0, "expired_removals": 0
        }
        info(f"🗂️ TTLCache '{name}' инициализирован: TTL={default_ttl}с, max_size={max_size}")

    def _cleanup_expired(self, key: Optional[str] = None) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            if key is not None:
                if key in self._expires and now >= self._expires[key]:
                    if key in self._cache:
                        del self._cache[key]
                    del self._expires[key]
                    removed = 1
                    self._stats["expired_removals"] += 1
            else:
                expired_keys = [k for k, exp in self._expires.items() if now >= exp]
                for k in expired_keys:
                    if k in self._cache:
                        del self._cache[k]
                    del self._expires[k]
                    removed += 1
                if removed > 0:
                    self._stats["expired_removals"] += removed
        return removed

    def _enforce_max_size(self):
        with self._lock:
            if len(self._cache) > self._max_size:
                items = sorted(self._expires.items(), key=lambda x: x[1])
                to_remove = len(self._cache) - self._max_size
                for i in range(to_remove):
                    key = items[i][0]
                    if key in self._cache:
                        del self._cache[key]
                    del self._expires[key]

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        if not key:
            return False
        ttl = ttl or self._default_ttl
        with self._lock:
            self._cache[key] = value
            self._expires[key] = time.time() + ttl
            self._stats["sets"] += 1
            if len(self._cache) > self._max_size:
                self._enforce_max_size()
        return True

    def get(self, key: str, default: Any = None) -> Any:
        if not key:
            return default
        with self._lock:
            if key not in self._cache:
                self._stats["misses"] += 1
                return default
            if time.time() >= self._expires.get(key, 0):
                del self._cache[key]
                del self._expires[key]
                self._stats["expired_removals"] += 1
                self._stats["misses"] += 1
                return default
            self._stats["hits"] += 1
            return self._cache[key]

    def delete(self, key: str) -> bool:
        if not key:
            return False
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                if key in self._expires:
                    del self._expires[key]
                self._stats["deletes"] += 1
                return True
        return False

    def clear(self) -> int:
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._expires.clear()
            self._stats["clears"] += 1
            return count

    def exists(self, key: str) -> bool:
        if not key:
            return False
        with self._lock:
            if key not in self._cache:
                return False
            if time.time() >= self._expires.get(key, 0):
                return False
            return True

    def get_ttl(self, key: str) -> int:
        with self._lock:
            if key not in self._expires:
                return -1
            remaining = self._expires[key] - time.time()
            return max(0, int(remaining))

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
            return {
                "name": self._name,
                "size": len(self._cache),
                "max_size": self._max_size,
                "default_ttl": self._default_ttl,
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "hit_rate": round(hit_rate, 2),
                "sets": self._stats["sets"],
                "deletes": self._stats["deletes"],
                "clears": self._stats["clears"],
                "expired_removals": self._stats["expired_removals"]
            }

    def keys(self) -> list:
        with self._lock:
            self._cleanup_expired()
            return list(self._cache.keys())

    def __contains__(self, key: str) -> bool:
        return self.exists(key)

    def __len__(self) -> int:
        with self._lock:
            self._cleanup_expired()
            return len(self._cache)

    def __repr__(self) -> str:
        return f"TTLCache('{self._name}', size={len(self)}/{self._max_size})"


# ============================================================================
# СПЕЦИАЛИЗИРОВАННЫЕ КЭШИ (обёртки над TTLCache)
# ============================================================================

class PriceCache:
    """Кэш для цен с TTL"""

    def __init__(self, default_ttl: int = 5):
        self._cache = TTLCache(default_ttl=default_ttl, max_size=1000, name="price_cache")

    def get(self, figi: str) -> Optional[float]:
        return self._cache.get(figi)

    def set(self, figi: str, price: float, ttl: int = None):
        self._cache.set(figi, price, ttl=ttl)

    def clear(self) -> int:
        return self._cache.clear()

    def invalidate(self, figi: str):
        self._cache.delete(figi)


class PositionCache:
    """Кэш для позиций"""

    def __init__(self, default_ttl: int = 5):
        self._cache = TTLCache(default_ttl=default_ttl, max_size=200, name="positions_cache")

    def get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        return self._cache.get(key)

    def set(self, key: str, data: Any, ttl: Optional[int] = None):
        self._cache.set(key, data, ttl=ttl)

    def clear(self) -> int:
        return self._cache.clear()

    def invalidate(self, key: str):
        self._cache.delete(key)


class ValidationCache:
    """Кэш для валидации тикеров"""

    def __init__(self, default_ttl_hours: int = 24):
        self._default_ttl_hours = default_ttl_hours
        self._cache: Dict[str, Tuple[datetime, bool, Dict]] = {}

    def get(self, ticker: str) -> Tuple[Optional[bool], Optional[Dict]]:
        if ticker not in self._cache:
            return None, None
        timestamp, passed, stats = self._cache[ticker]
        if datetime.now() - timestamp > timedelta(hours=self._default_ttl_hours):
            del self._cache[ticker]
            return None, None
        return passed, stats

    def set(self, ticker: str, passed: bool, stats: Dict):
        self._cache[ticker] = (datetime.now(), passed, stats)

    def clear(self) -> int:
        count = len(self._cache)
        self._cache.clear()
        return count


# ============================================================================
# ДЕКОРАТОР ДЛЯ КЭШИРОВАНИЯ
# ============================================================================

def cached(ttl: int = 60, key_prefix: str = ""):
    def decorator(func: Callable) -> Callable:
        cache = TTLCache(default_ttl=ttl, name=f"decorator_{func.__name__}")
        @wraps(func)
        def wrapper(*args, **kwargs):
            key_parts = [key_prefix, func.__name__]
            key_parts.extend(str(arg) for arg in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = ":".join(key_parts)
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            result = func(*args, **kwargs)
            if result is not None:
                cache.set(cache_key, result, ttl=ttl)
            return result
        return wrapper
    return decorator


# ============================================================================
# ✅ ФУНКЦИЯ ОПТИМИЗАЦИИ TTL (ПЕРЕМЕЩЕНА СЮДА, ПОСЛЕ ОПРЕДЕЛЕНИЯ КЛАССОВ)
# ============================================================================

def get_optimal_cache_ttl() -> Dict[str, int]:
    """Автоматический расчёт оптимальных TTL для кэшей"""
    import psutil
    import os
    
    cpu_count = psutil.cpu_count() or 2
    mem = psutil.virtual_memory()
    total_ram_mb = mem.total / (1024 * 1024)
    is_render = os.environ.get('RENDER', False)
    
    if is_render:
        return {
            'price': 10,
            'positions': 10,
            'candles': 300,
            'margin': 60,
            'instruments': 3600,
            'analysis': 300,
            'news': 1800,
            'trading_status': 120,
        }
    elif cpu_count >= 4 and total_ram_mb > 2048:
        return {
            'price': 5,
            'positions': 5,
            'candles': 120,
            'margin': 30,
            'instruments': 1800,
            'analysis': 180,
            'news': 900,
            'trading_status': 60,
        }
    else:
        return {
            'price': 8,
            'positions': 8,
            'candles': 180,
            'margin': 45,
            'instruments': 3600,
            'analysis': 240,
            'news': 1200,
            'trading_status': 90,
        }


# ============================================================================
# ГЛОБАЛЬНЫЕ ЭКЗЕМПЛЯРЫ
# ============================================================================

_OPTIMAL_TTL = get_optimal_cache_ttl()

price_cache = PriceCache(default_ttl=_OPTIMAL_TTL['price'])
positions_cache = PositionCache(default_ttl=_OPTIMAL_TTL['positions'])

# ✅ ИСПРАВЛЕНО: TTL = 300 СЕКУНД (5 минут) для свечей
candles_cache = TTLCache(default_ttl=300, max_size=500, name="candles_cache")

margin_cache = TTLCache(default_ttl=_OPTIMAL_TTL['margin'], max_size=10, name="margin_cache")
instruments_cache = TTLCache(default_ttl=_OPTIMAL_TTL['instruments'], max_size=500, name="instruments_cache")
analysis_cache = TTLCache(default_ttl=_OPTIMAL_TTL['analysis'], max_size=500, name="analysis_cache")
news_cache = TTLCache(default_ttl=_OPTIMAL_TTL['news'], max_size=50, name="news_cache")
validation_cache = ValidationCache(default_ttl_hours=24)
trading_status_cache = TTLCache(default_ttl=_OPTIMAL_TTL['trading_status'], max_size=200, name="trading_status_cache")


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def get_all_cache_stats() -> Dict[str, Dict]:
    return {
        "price_cache": price_cache._cache.get_stats(),
        "positions_cache": positions_cache._cache.get_stats(),
        "candles_cache": candles_cache.get_stats(),
        "margin_cache": margin_cache.get_stats(),
        "instruments_cache": instruments_cache.get_stats(),
        "analysis_cache": analysis_cache.get_stats(),
        "news_cache": news_cache.get_stats(),
    }


def clear_all_caches() -> int:
    total = 0
    total += price_cache.clear()
    total += positions_cache.clear()
    total += candles_cache.clear()
    total += margin_cache.clear()
    total += instruments_cache.clear()
    total += analysis_cache.clear()
    total += news_cache.clear()
    total += validation_cache.clear()
    info(f"🧹 Очищено {total} записей из всех кэшей")
    return total


# ============================================================================
# ЭКСПОРТ
# ============================================================================

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
]