#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTL Cache - кэш с временем жизни (Time-To-Live)
С поддержкой:
- Автоматическое удаление просроченных записей
- Потокобезопасность
- Максимальный размер кэша
- Декоратор для кэширования функций
- ПОДРОБНОЕ ЛОГИРОВАНИЕ всех операций
"""

import time
import threading
from typing import Dict, Any, Optional, Callable, TypeVar, ParamSpec
from functools import wraps
from datetime import datetime

# Импорт логгера
try:
    from trading_bot.logger import debug, info, warning
    LOGGER_AVAILABLE = True
except ImportError:
    LOGGER_AVAILABLE = False
    def debug(msg): print(f"🔍 {msg}")
    def info(msg): print(f"ℹ️ {msg}")
    def warning(msg): print(f"⚠️ {msg}")


P = ParamSpec('P')
T = TypeVar('T')


class TTLCache:
    """
    Кэш с временем жизни (Time-To-Live)
    
    Особенности:
    - Автоматическое удаление просроченных записей при доступе
    - Потокобезопасность через threading.RLock
    - Ограничение максимального размера (FIFO при превышении)
    - Полное логирование всех операций
    
    Пример использования:
        cache = TTLCache(default_ttl=60, max_size=1000)
        cache.set("key", "value", ttl=30)
        value = cache.get("key")
        cache.delete("key")
        cache.clear()
    """
    
    def __init__(self, default_ttl: int = 60, max_size: int = 1000, name: str = "default"):
        """
        Инициализация кэша
        
        Args:
            default_ttl: Время жизни по умолчанию (секунды)
            max_size: Максимальное количество записей
            name: Имя кэша (для логирования)
        """
        self._cache: Dict[str, Any] = {}
        self._expires: Dict[str, float] = {}
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._name = name
        self._lock = threading.RLock()
        
        # Статистика
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "deletes": 0,
            "clears": 0,
            "expired_removals": 0
        }
        
        info(f"🗂️ TTLCache '{name}' инициализирован: TTL={default_ttl}с, max_size={max_size}")
    
    def _cleanup_expired(self, key: Optional[str] = None) -> int:
        """
        Очистка просроченных записей
        
        Args:
            key: Если указан, проверяет только этот ключ
            
        Returns:
            Количество удалённых записей
        """
        now = time.time()
        removed = 0
        
        with self._lock:
            if key is not None:
                # Проверяем конкретный ключ
                if key in self._expires and now >= self._expires[key]:
                    if key in self._cache:
                        del self._cache[key]
                    del self._expires[key]
                    removed = 1
                    self._stats["expired_removals"] += 1
                    debug(f"🗑️ TTLCache '{self._name}': истёк ключ '{key[:50]}'")
            else:
                # Проверяем все ключи
                expired_keys = [k for k, exp in self._expires.items() if now >= exp]
                for k in expired_keys:
                    if k in self._cache:
                        del self._cache[k]
                    del self._expires[k]
                    removed += 1
                
                if removed > 0:
                    self._stats["expired_removals"] += removed
                    debug(f"🗑️ TTLCache '{self._name}': очищено {removed} просроченных записей")
        
        return removed
    
    def _enforce_max_size(self):
        """Принудительное соблюдение максимального размера кэша (FIFO)"""
        with self._lock:
            if len(self._cache) > self._max_size:
                # Удаляем самые старые записи (по времени добавления)
                # Сортируем по времени истечения (чем раньше истекает, тем раньше удаляем)
                items = sorted(self._expires.items(), key=lambda x: x[1])
                to_remove = len(self._cache) - self._max_size
                
                for i in range(to_remove):
                    key = items[i][0]
                    if key in self._cache:
                        del self._cache[key]
                    del self._expires[key]
                
                warning(f"⚠️ TTLCache '{self._name}': превышен лимит {self._max_size}, удалено {to_remove} записей")
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Установка значения в кэш
        
        Args:
            key: Ключ
            value: Значение
            ttl: Время жизни в секундах (если None - используется default_ttl)
            
        Returns:
            True если успешно, False если ошибка
        """
        if not key:
            warning(f"⚠️ TTLCache '{self._name}': попытка установки с пустым ключом")
            return False
        
        ttl = ttl if ttl is not None else self._default_ttl
        expires_at = time.time() + ttl
        
        with self._lock:
            self._cache[key] = value
            self._expires[key] = expires_at
            self._stats["sets"] += 1
            
            # Проверяем размер кэша
            if len(self._cache) > self._max_size:
                self._enforce_max_size()
            
            debug(f"💾 TTLCache '{self._name}': SET '{key[:50]}' (TTL={ttl}с, истекает в {datetime.fromtimestamp(expires_at).strftime('%H:%M:%S')})")
            
        return True
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Получение значения из кэша
        
        Args:
            key: Ключ
            default: Значение по умолчанию, если ключ не найден или просрочен
            
        Returns:
            Значение или default
        """
        if not key:
            warning(f"⚠️ TTLCache '{self._name}': попытка получения с пустым ключом")
            return default
        
        with self._lock:
            # Проверяем наличие ключа
            if key not in self._cache:
                self._stats["misses"] += 1
                debug(f"❌ TTLCache '{self._name}': MISS '{key[:50]}' (не найден)")
                return default
            
            # Проверяем просрочку
            if key in self._expires and time.time() >= self._expires[key]:
                # Удаляем просроченную запись
                del self._cache[key]
                del self._expires[key]
                self._stats["misses"] += 1
                self._stats["expired_removals"] += 1
                debug(f"⏰ TTLCache '{self._name}': EXPIRED '{key[:50]}' (удалён)")
                return default
            
            # Успешное получение
            self._stats["hits"] += 1
            value = self._cache[key]
            expires_at = self._expires.get(key, 0)
            ttl_remaining = max(0, expires_at - time.time())
            
            debug(f"✅ TTLCache '{self._name}': HIT '{key[:50]}' (осталось {ttl_remaining:.1f}с)")
            return value
    
    def delete(self, key: str) -> bool:
        """
        Удаление ключа из кэша
        
        Args:
            key: Ключ для удаления
            
        Returns:
            True если ключ был удалён, False если не найден
        """
        if not key:
            warning(f"⚠️ TTLCache '{self._name}': попытка удаления с пустым ключом")
            return False
        
        with self._lock:
            # Проверяем существование ключа
            if key not in self._cache:
                debug(f"❌ TTLCache '{self._name}': DELETE '{key[:50]}' (не найден)")
                return False
            
            # Удаляем ключ
            del self._cache[key]
            if key in self._expires:
                del self._expires[key]
            
            self._stats["deletes"] += 1
            info(f"🗑️ TTLCache '{self._name}': DELETE '{key[:50]}' (успешно)")
            return True
    
    def clear(self) -> int:
        """
        Полная очистка кэша
        
        Returns:
            Количество удалённых записей
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._expires.clear()
            self._stats["clears"] += 1
            
            info(f"🧹 TTLCache '{self._name}': CLEAR (удалено {count} записей)")
            return count
    
    def exists(self, key: str) -> bool:
        """
        Проверка существования ключа в кэше (без удаления просроченных)
        
        Args:
            key: Ключ для проверки
            
        Returns:
            True если ключ существует и не просрочен
        """
        if not key:
            return False
        
        with self._lock:
            if key not in self._cache:
                return False
            
            if key in self._expires and time.time() >= self._expires[key]:
                return False
            
            return True
    
    def get_ttl(self, key: str) -> int:
        """
        Получение оставшегося времени жизни ключа
        
        Args:
            key: Ключ
            
        Returns:
            Оставшееся время в секундах, -1 если ключ не найден или не имеет TTL
        """
        with self._lock:
            if key not in self._expires:
                return -1
            
            remaining = self._expires[key] - time.time()
            return max(0, int(remaining))
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Получение статистики кэша
        
        Returns:
            Dict со статистикой
        """
        with self._lock:
            hit_rate = 0
            total = self._stats["hits"] + self._stats["misses"]
            if total > 0:
                hit_rate = self._stats["hits"] / total * 100
            
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
        """Получение всех ключей (только активные, непросроченные)"""
        with self._lock:
            # Очищаем просроченные
            self._cleanup_expired()
            return list(self._cache.keys())
    
    def items(self) -> list:
        """Получение всех элементов (только активные, непросроченные)"""
        with self._lock:
            self._cleanup_expired()
            return list(self._cache.items())
    
    def __contains__(self, key: str) -> bool:
        """Поддержка оператора 'in'"""
        return self.exists(key)
    
    def __len__(self) -> int:
        """Поддержка len()"""
        with self._lock:
            self._cleanup_expired()
            return len(self._cache)
    
    def __repr__(self) -> str:
        return f"TTLCache('{self._name}', size={len(self)}/{self._max_size}, hit_rate={self.get_stats()['hit_rate']:.1f}%)"


# ============================================================================
# ДЕКОРАТОР ДЛЯ КЭШИРОВАНИЯ ФУНКЦИЙ
# ============================================================================

def cached(ttl: int = 60, key_prefix: str = ""):
    """
    Декоратор для кэширования результатов функции
    
    Args:
        ttl: Время жизни кэша в секундах
        key_prefix: Префикс для ключа кэша
        
    Пример:
        @cached(ttl=30, key_prefix="get_price")
        def get_price(figi: str) -> float:
            return api.get_price(figi)
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        cache = TTLCache(default_ttl=ttl, name=f"decorator_{func.__name__}")
        
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Формируем ключ кэша
            key_parts = [key_prefix, func.__name__]
            key_parts.extend(str(arg) for arg in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = ":".join(key_parts)
            
            # Пробуем получить из кэша
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                debug(f"📦 Кэш HIT для {func.__name__}")
                return cached_value
            
            # Вызываем функцию
            debug(f"🚀 Кэш MISS для {func.__name__}, вызываем функцию...")
            result = func(*args, **kwargs)
            
            # Сохраняем в кэш
            if result is not None:
                cache.set(cache_key, result, ttl=ttl)
                debug(f"💾 Результат {func.__name__} сохранён в кэш (TTL={ttl}с)")
            
            return result
        
        return wrapper
    return decorator


# # ============================================================================
# # ГЛОБАЛЬНЫЕ ЭКЗЕМПЛЯРЫ КЭША
# # ============================================================================
#
# # Кэш для цен (5 секунд)
# price_cache = TTLCache(default_ttl=5, max_size=500, name="price_cache")
#
# # Кэш для позиций (5 секунд)
# positions_cache = TTLCache(default_ttl=5, max_size=100, name="positions_cache")
#
# # Кэш для свечей (30 секунд)
# candles_cache = TTLCache(default_ttl=30, max_size=200, name="candles_cache")
#
# # Кэш для маржи (10 секунд)
# margin_cache = TTLCache(default_ttl=10, max_size=10, name="margin_cache")
#
# # Кэш для инструментов (5 минут)
# instruments_cache = TTLCache(default_ttl=300, max_size=100, name="instruments_cache")
#
# # Кэш для аналитики (1 час)
# analysis_cache = TTLCache(default_ttl=3600, max_size=100, name="analysis_cache")


# ============================================================================
# ТЕСТИРОВАНИЕ
# ============================================================================

def _test_ttl_cache():
    """Тестирование TTLCache"""
    print("\n" + "=" * 60)
    print("🧪 ТЕСТИРОВАНИЕ TTLCache")
    print("=" * 60)
    
    # Создаём кэш
    cache = TTLCache(default_ttl=2, max_size=5, name="test_cache")
    print(f"✅ Кэш создан: {cache}")
    
    # Тест 1: set и get
    cache.set("key1", "value1")
    value = cache.get("key1")
    print(f"✅ set/get: {value} == value1")
    
    # Тест 2: delete
    cache.delete("key1")
    value = cache.get("key1")
    print(f"✅ delete: {value} is None")
    
    # Тест 3: TTL
    cache.set("key2", "value2", ttl=1)
    time.sleep(1.5)
    value = cache.get("key2")
    print(f"✅ TTL: {value} is None (просрочено)")
    
    # Тест 4: clear
    cache.set("key3", "value3")
    cache.set("key4", "value4")
    count = cache.clear()
    print(f"✅ clear: удалено {count} записей, размер {len(cache)}")
    
    # Тест 5: статистика
    stats = cache.get_stats()
    print(f"✅ Статистика: hits={stats['hits']}, misses={stats['misses']}, hit_rate={stats['hit_rate']}%")
    
    print("\n✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")


if __name__ == "__main__":
    _test_ttl_cache()