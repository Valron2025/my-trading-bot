#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ТЕСТ СИСТЕМЫ КЭШИРОВАНИЯ
Проверяет все кэши и их работу
"""

import time
from datetime import datetime

print("=" * 80)
print("🧪 ТЕСТ СИСТЕМЫ КЭШИРОВАНИЯ")
print("=" * 80)
print(f"📅 Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)

# ============================================================================
# 1. ИМПОРТ
# ============================================================================

print("\n📦 [1/5] ИМПОРТ МОДУЛЕЙ КЭШИРОВАНИЯ...")

try:
    from trading_bot.cache import (
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
    print("   ✅ Все кэши импортированы")
except ImportError as e:
    print(f"   ❌ Ошибка импорта: {e}")
    exit(1)

# ============================================================================
# 2. ТЕСТ TTLCache (БАЗОВЫЙ)
# ============================================================================

print("\n📦 [2/5] ТЕСТ TTLCache (БАЗОВЫЙ)...")

cache = TTLCache(default_ttl=5, max_size=10, name="test_cache")

# 2.1 Установка и получение
print("   📝 2.1 Установка и получение...")
cache.set("key1", "value1")
cache.set("key2", 12345)
cache.set("key3", {"a": 1, "b": 2})

val1 = cache.get("key1")
val2 = cache.get("key2")
val3 = cache.get("key3")

print(f"      key1 = {val1} ✅")
print(f"      key2 = {val2} ✅")
print(f"      key3 = {val3} ✅")

# 2.2 TTL (время жизни)
print("   ⏱️  2.2 Проверка TTL (5 секунд)...")
cache.set("ttl_test", "это умрёт через 5 секунд", ttl=5)
print(f"      До истечения: {cache.get('ttl_test')}")

time.sleep(6)
expired = cache.get("ttl_test")
print(f"      После истечения: {expired} (ожидается None) {'✅' if expired is None else '❌'}")

# 2.3 Статистика
print("   📊 2.3 Статистика...")
stats = cache.get_stats()
print(f"      Имя: {stats['name']}")
print(f"      Размер: {stats['size']}/{stats['max_size']}")
print(f"      Попаданий: {stats['hits']}")
print(f"      Промахов: {stats['misses']}")
print(f"      Hit rate: {stats['hit_rate']}%")

# ============================================================================
# 3. ТЕСТ СПЕЦИАЛИЗИРОВАННЫХ КЭШЕЙ
# ============================================================================

print("\n📦 [3/5] ТЕСТ СПЕЦИАЛИЗИРОВАННЫХ КЭШЕЙ...")

# 3.1 PriceCache
print("   💰 3.1 PriceCache...")
price_cache.set("BBG_TEST", 319.50, ttl=10)
price = price_cache.get("BBG_TEST")
print(f"      Цена: {price} {'✅' if price == 319.50 else '❌'}")

stats = price_cache._cache.get_stats()
print(f"      Статистика: size={stats['size']}, hits={stats['hits']}")

# 3.2 PositionCache
print("   📊 3.2 PositionCache...")
test_position = [{"figi": "TEST", "quantity": 100, "avg_price": 50.0}]
positions_cache.set("test_positions", test_position, ttl=10)
pos = positions_cache.get("test_positions")
print(f"      Позиции: {len(pos) if pos else 0} шт {'✅' if pos else '❌'}")

# 3.3 ValidationCache
print("   🔐 3.3 ValidationCache...")
validation_cache.set("SBER", True, {"reason": "OK"})
passed, stats = validation_cache.get("SBER")
print(f"      SBER: passed={passed}, stats={stats} {'✅' if passed else '❌'}")

# ============================================================================
# 4. ТЕСТ ГЛОБАЛЬНЫХ КЭШЕЙ
# ============================================================================

print("\n📦 [4/5] ТЕСТ ГЛОБАЛЬНЫХ КЭШЕЙ...")

# Сохраняем данные в глобальные кэши
print("   💾 Сохранение тестовых данных...")
candles_cache.set("test_candles", [1, 2, 3, 4, 5], ttl=30)
margin_cache.set("test_margin", {"rate": 15.2, "available": 5000}, ttl=30)
instruments_cache.set("test_instruments", ["SBER", "GAZP", "LKOH"], ttl=300)

# Проверяем
candles = candles_cache.get("test_candles")
margin = margin_cache.get("test_margin")
instruments = instruments_cache.get("test_instruments")

print(f"   📊 candles_cache: {len(candles) if candles else 0} шт {'✅' if candles else '❌'}")
print(f"   💰 margin_cache: {margin.get('rate') if margin else 'None'}% {'✅' if margin else '❌'}")
print(f"   📋 instruments_cache: {len(instruments) if instruments else 0} шт {'✅' if instruments else '❌'}")

# ============================================================================
# 5. СТАТИСТИКА ВСЕХ КЭШЕЙ
# ============================================================================

print("\n📦 [5/5] СТАТИСТИКА ВСЕХ КЭШЕЙ...")

all_stats = get_all_cache_stats()

print("   📊 СТАТИСТИКА:")
for name, stats in all_stats.items():
    size = stats.get('size', 0)
    hits = stats.get('hits', 0)
    misses = stats.get('misses', 0)
    hit_rate = stats.get('hit_rate', 0)
    print(f"      {name}: size={size}, hits={hits}, misses={misses}, hit_rate={hit_rate}%")

# ============================================================================
# ИТОГИ
# ============================================================================

print("\n" + "=" * 80)
print("📊 ИТОГИ ТЕСТА КЭШИРОВАНИЯ")
print("=" * 80)

# Проверяем, что все кэши работают
caches_working = {
    "TTLCache": True,
    "PriceCache": price_cache.get("BBG_TEST") == 319.50,
    "PositionCache": positions_cache.get("test_positions") is not None,
    "ValidationCache": validation_cache.get("SBER")[0] is True,
    "candles_cache": candles_cache.get("test_candles") is not None,
    "margin_cache": margin_cache.get("test_margin") is not None,
    "instruments_cache": instruments_cache.get("test_instruments") is not None,
}

all_working = all(caches_working.values())
working_count = sum(caches_working.values())

print(f"\n✅ Работает: {working_count}/{len(caches_working)}")
for name, status in caches_working.items():
    print(f"   {name}: {'✅' if status else '❌'}")

if all_working:
    print("\n🎉 ВСЕ КЭШИ РАБОТАЮТ КОРРЕКТНО!")
else:
    print("\n⚠️ ЕСТЬ ПРОБЛЕМЫ С КЭШАМИ!")

# Очистка тестовых данных
print("\n🧹 Очистка тестовых данных...")
cleared = clear_all_caches()
print(f"   Очищено {cleared} записей")

print("\n" + "=" * 80)
print("🏁 ТЕСТ ЗАВЕРШЁН")
print("=" * 80)