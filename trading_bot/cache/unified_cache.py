#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unified_cache.py - НОВЫЙ УНИФИЦИРОВАННЫЙ КЭШ
⚠️ НЕ УДАЛЯЕТ старые кэши! Работает параллельно.
Для миграции используйте флаг USE_UNIFIED_CACHE=False
"""

import time
import threading
import os
from typing import Dict, Any, Optional, Callable, TypeVar, ParamSpec
from functools import wraps
from datetime import datetime

try:
    from trading_bot.logger import debug, info, warning, success
    LOGGER_AVAILABLE = True
except ImportError:
    LOGGER_AVAILABLE = False
    def debug(msg): print(f"🔍 {msg}")
    def info(msg): print(f"ℹ️ {msg}")
    def warning(msg): print(f"⚠️ {msg}")
    def success(msg): print(f"✅ {msg}")

P = ParamSpec('P')
T = TypeVar('T')

# Флаг использования унифицированного кэша (можно отключить через env)
USE_UNIFIED_CACHE = os.getenv('USE_UNIFIED_CACHE', 'false').lower() == 'true'


class UnifiedCache:
    """
    НОВЫЙ унифицированный кэш с временем жизни
    Работает параллельно со старыми кэшами
    """
    
    def __init__(self, default_ttl: int = 60, max_size: int = 1000, name: str = "default", enabled: bool = True):
        self._cache: Dict[str, Any] = {}
        self._expires: Dict[str, float] = {}
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._name = name
        self._enabled = enabled and USE_UNIFIED_CACHE
        self._lock = threading.RLock()
        
        self._stats = {
            "hits": 0, "misses": 0, "sets": 0, "deletes": 0,
            "clears": 0, "expired_removals": 0, "size_limit_hits": 0
        }
        
        if self._enabled:
            info(f"🗂️ UnifiedCache '{name}' инициализирован (TTL={default_ttl}с)")
        else:
            debug(f"⏸️ UnifiedCache '{name}' отключён (USE_UNIFIED_CACHE=false)")
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        if not self._enabled or not key:
            return False
        
        ttl = ttl or self._default_ttl
        with self._lock:
            self._cache[key] = value
            self._expires[key] = time.time() + ttl
            self._stats["sets"] += 1
            return True
    
    def get(self, key: str, default: Any = None) -> Any:
        if not self._enabled or not key:
            return default
        
        with self._lock:
            if key not in self._cache:
                self._stats["misses"] += 1
                return default
            
            if time.time() >= self._expires.get(key, 0):
                del self._cache[key]
                del self._expires[key]
                self._stats["expired_removals"] += 1
                return default
            
            self._stats["hits"] += 1
            return self._cache[key]
    
    def delete(self, key: str) -> bool:
        if not self._enabled:
            return False
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                del self._expires[key]
                self._stats["deletes"] += 1
                return True
        return False
    
    def clear(self) -> int:
        if not self._enabled:
            return 0
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._expires.clear()
            self._stats["clears"] += 1
            return count
    
    def exists(self, key: str) -> bool:
        if not self._enabled:
            return False
        return key in self._cache and time.time() < self._expires.get(key, 0)
    
    def get_stats(self) -> Dict[str, Any]:
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
        return {
            "name": self._name,
            "enabled": self._enabled,
            "size": len(self._cache),
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": round(hit_rate, 2),
            "sets": self._stats["sets"],
        }
    
    def __contains__(self, key: str) -> bool:
        return self.exists(key)
    
    def __len__(self) -> int:
        return len(self._cache)


def cached(ttl: int = 60, cache_name: str = "default", key_prefix: str = ""):
    """Декоратор для кэширования функций"""
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        cache = None
        
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            nonlocal cache
            if cache is None:
                cache = CacheRegistry.get(cache_name, auto_create=True)
            
            if not cache._enabled:
                return func(*args, **kwargs)
            
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


class CacheRegistry:
    """Реестр кэшей"""
    _caches: Dict[str, UnifiedCache] = {}
    _lock = threading.Lock()
    
    @classmethod
    def get(cls, name: str, default_ttl: int = 60, max_size: int = 1000, auto_create: bool = True) -> UnifiedCache:
        with cls._lock:
            if name not in cls._caches and auto_create:
                cls._caches[name] = UnifiedCache(default_ttl=default_ttl, max_size=max_size, name=name)
            return cls._caches.get(name)
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict]:
        return {name: cache.get_stats() for name, cache in cls._caches.items()}
    
    @classmethod
    def clear_all(cls) -> int:
        total = 0
        for cache in cls._caches.values():
            total += cache.clear()
        return total


def print_cache_report():
    """Вывод отчёта по кэшам"""
    print("\n" + "=" * 70)
    print("📊 ОТЧЁТ ПО УНИФИЦИРОВАННЫМ КЭШАМ")
    print("=" * 70)
    
    stats = CacheRegistry.get_all_stats()
    if not stats:
        print("   Нет активных кэшей")
        return
    
    print(f"\n{'Кэш':<15} {'Вкл':<5} {'Размер':<8} {'Hits':<10} {'Misses':<10} {'Hit Rate':<10}")
    print("-" * 70)
    
    for name, stat in sorted(stats.items()):
        enabled = "✅" if stat['enabled'] else "❌"
        print(f"{name:<15} {enabled:<5} {stat['size']:<8} {stat['hits']:<10} {stat['misses']:<10} {stat['hit_rate']:<9.1f}%")
    
    print("=" * 70)


# Создаём стандартные кэши (если включены)
if USE_UNIFIED_CACHE:
    price_cache = CacheRegistry.get("price", default_ttl=5, max_size=500)
    positions_cache = CacheRegistry.get("positions", default_ttl=5, max_size=100)
    candles_cache = CacheRegistry.get("candles", default_ttl=30, max_size=200)
    margin_cache = CacheRegistry.get("margin", default_ttl=10, max_size=10)
    instruments_cache = CacheRegistry.get("instruments", default_ttl=300, max_size=100)
else:
    # Заглушки для обратной совместимости
    class StubCache:
        def set(self, *args, **kwargs): pass
        def get(self, *args, **kwargs): return None
        def clear(self): return 0
        def __contains__(self, *args): return False
        def __len__(self): return 0
    
    price_cache = StubCache()
    positions_cache = StubCache()
    candles_cache = StubCache()
    margin_cache = StubCache()
    instruments_cache = StubCache()


if __name__ == "__main__":
    print_cache_report()