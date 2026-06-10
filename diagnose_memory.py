#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Диагностика утечек памяти для Render (бесплатный тариф 512MB)"""

import sys
import os
import gc
import time
import tracemalloc
import psutil
from pathlib import Path

# Добавляем проект в путь
sys.path.insert(0, str(Path(__file__).parent))


def get_memory_usage() -> dict:
    """Получение текущего использования памяти"""
    process = psutil.Process()
    memory_info = process.memory_info()

    return {
        'rss_mb': memory_info.rss / 1024 / 1024,  # Реальная память
        'vms_mb': memory_info.vms / 1024 / 1024,  # Виртуальная память
        'percent': process.memory_percent(),
        'cpu_percent': process.cpu_percent(interval=0.5)
    }


def diagnose_imports():
    """Диагностика памяти после импортов"""
    print("\n" + "=" * 60)
    print("📊 ДИАГНОСТИКА 1: ИМПОРТЫ МОДУЛЕЙ")
    print("=" * 60)

    memory_before = get_memory_usage()
    print(f"   Память ДО импортов: {memory_before['rss_mb']:.1f} MB")

    # Импортируем основные модули
    import trading_bot.config
    import trading_bot.logger
    import trading_bot.api.tbank_client
    import trading_bot.bot

    memory_after = get_memory_usage()
    print(f"   Память ПОСЛЕ импортов: {memory_after['rss_mb']:.1f} MB")
    print(f"   📈 Рост: +{memory_after['rss_mb'] - memory_before['rss_mb']:.1f} MB")

    return memory_after['rss_mb'] - memory_before['rss_mb']


def diagnose_caches():
    """Диагностика использования кэшей"""
    print("\n" + "=" * 60)
    print("📊 ДИАГНОСТИКА 2: КЭШИ")
    print("=" * 60)

    from trading_bot.cache import TTLCache
    from trading_bot.api.tbank_client import tbank

    memory_before = get_memory_usage()
    print(f"   Память ДО инициализации кэшей: {memory_before['rss_mb']:.1f} MB")

    # Получаем все кэши
    caches = []
    for attr_name in dir(tbank):
        attr = getattr(tbank, attr_name)
        if isinstance(attr, TTLCache):
            caches.append(attr_name)
            stats = attr.get_stats() if hasattr(attr, 'get_stats') else {
                'size': len(attr._cache) if hasattr(attr, '_cache') else 0}
            print(f"   📦 Кэш '{attr_name}': max_size={getattr(attr, 'max_size', '?')}")

    # Загружаем данные в кэши
    print("\n   🔄 Загрузка данных в кэши...")

    # Загружаем акции
    shares = tbank.get_all_shares(limit=500)
    print(f"      Акций загружено: {len(shares)}")

    # Получаем позиции
    positions = tbank.get_positions()
    print(f"      Позиций: {len(positions)}")

    # Получаем маржу
    margin = tbank.get_margin_info()

    memory_after = get_memory_usage()
    print(f"\n   Память ПОСЛЕ загрузки: {memory_after['rss_mb']:.1f} MB")
    print(f"   📈 Рост: +{memory_after['rss_mb'] - memory_before['rss_mb']:.1f} MB")

    return memory_after['rss_mb'] - memory_before['rss_mb']


def diagnose_full_cycle():
    """Диагностика полного цикла работы бота"""
    print("\n" + "=" * 60)
    print("📊 ДИАГНОСТИКА 3: ПОЛНЫЙ ЦИКЛ (30 секунд)")
    print("=" * 60)

    import asyncio
    from trading_bot.bot import trading_bot

    memory_samples = []
    gc.collect()

    print("\n   🔄 Запуск мониторинга памяти...")

    async def run_cycle():
        nonlocal memory_samples
        start_time = time.time()

        # Запускаем бота в фоне
        # Не запускаем реальную торговлю, только инициализацию

        # Симулируем работу
        for i in range(30):
            # Получаем позиции
            positions = trading_bot._get_positions(force_refresh=(i % 5 == 0))

            # Получаем маржу
            margin = trading_bot.get_margin_status()

            # Собираем образец памяти каждые 5 секунд
            if i % 5 == 0:
                mem = get_memory_usage()
                memory_samples.append(mem['rss_mb'])
                print(f"      [{i}s] Память: {mem['rss_mb']:.1f} MB | CPU: {mem['cpu_percent']:.0f}%")

            await asyncio.sleep(1)

        return memory_samples

    # Запускаем
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        samples = loop.run_until_complete(run_cycle())
    finally:
        loop.close()

    if samples:
        print(f"\n   📊 Статистика памяти за 30 сек:")
        print(f"      Минимум: {min(samples):.1f} MB")
        print(f"      Максимум: {max(samples):.1f} MB")
        print(f"      Рост: {samples[-1] - samples[0]:.1f} MB")

        if samples[-1] - samples[0] > 50:
            print(f"      ⚠️ ОБНАРУЖЕНА УТЕЧКА ПАМЯТИ! Рост {samples[-1] - samples[0]:.1f} MB за 30 сек")
        else:
            print(f"      ✅ Утечки не обнаружено")

    return samples[-1] if samples else 0


def optimize_config():
    """Создание оптимизированной конфигурации для 512MB"""
    print("\n" + "=" * 60)
    print("📊 ДИАГНОСТИКА 4: ОПТИМИЗАЦИЯ КОНФИГУРАЦИИ")
    print("=" * 60)

    # Проверяем текущие настройки
    from trading_bot.config import config

    print("\n   📋 ТЕКУЩИЕ НАСТРОЙКИ:")
    print(f"      use_short: {config.use_short}")
    print(f"      max_positions: {config.max_positions}")
    print(f"      price_cache_ttl: {config.price_cache_ttl}")
    print(f"      min_trade_amount: {config.min_trade_amount}")

    # Рекомендации
    print("\n   💡 РЕКОМЕНДАЦИИ ДЛЯ 512MB:")
    print(f"      1. Уменьшить max_positions: {config.max_positions} → 3")
    print(f"      2. Отключить SHORT: {config.use_short} → False")
    print(f"      3. Увеличить price_cache_ttl: {config.price_cache_ttl}с → 10с")
    print(f"      4. Уменьшить min_trade_amount: {config.min_trade_amount}₽ → 200₽")

    # Создаём оптимизированный файл настроек
    optimized_settings = {
        "use_short": False,
        "max_positions": 3,
        "price_cache_ttl": 10,
        "min_trade_amount": 200,
        "adaptive_position_size_pct": 0.05,
        "adaptive_timeout_minutes": 15,
        "adaptive_cycle_seconds": 60,
        "use_fundamental_in_trading": False,
        "use_correlation_analysis": False,
        "use_mtf_analysis": False
    }

    settings_file = Path("bot_settings_optimized.json")
    import json
    with open(settings_file, 'w', encoding='utf-8') as f:
        json.dump(optimized_settings, f, indent=2, ensure_ascii=False)

    print(f"\n   ✅ Оптимизированный файл создан: {settings_file}")
    print(f"   🔧 Чтобы применить: cp bot_settings_optimized.json bot_settings.json")

    return optimized_settings


def diagnose_garbage_collector():
    """Диагностика сборщика мусора"""
    print("\n" + "=" * 60)
    print("📊 ДИАГНОСТИКА 5: СБОРЩИК МУСОРА")
    print("=" * 60)

    # Текущие настройки GC
    gc_threshold = gc.get_threshold()
    print(f"\n   📋 Текущие настройки GC: {gc_threshold}")

    # Количество объектов
    gc.collect()
    objects_before = len(gc.get_objects())
    print(f"   📦 Объектов в памяти: {objects_before:,}")

    # Рекомендации
    print("\n   💡 РЕКОМЕНДАЦИИ:")
    print(f"      1. Установить более агрессивный GC: gc.set_threshold(500, 5, 2)")
    print(f"      2. Добавить принудительный gc.collect() каждый цикл")

    # Создаём оптимизированный запуск
    startup_code = '''
# Добавьте в начало run_production.py:
import gc
gc.set_threshold(500, 5, 2)  # Более агрессивная сборка мусора

# И в основном цикле:
if cycle_count % 10 == 0:
    gc.collect()
'''
    print(f"\n   📝 Код для оптимизации GC:\n{startup_code}")

    return objects_before


def main():
    """Основная диагностика"""
    print("\n" + "=" * 60)
    print("🔍 ДИАГНОСТИКА ПАМЯТИ ДЛЯ RENDER (512MB)")
    print("=" * 60)

    # Начинаем трассировку памяти
    tracemalloc.start()

    # Начальная память
    initial_memory = get_memory_usage()
    print(f"\n💾 НАЧАЛЬНОЕ СОСТОЯНИЕ:")
    print(f"   RAM: {initial_memory['rss_mb']:.1f} MB")
    print(f"   Лимит Render: 512 MB")
    print(f"   Доступно: {512 - initial_memory['rss_mb']:.1f} MB")

    if initial_memory['rss_mb'] > 400:
        print(f"\n⚠️ КРИТИЧЕСКИ МАЛО ПАМЯТИ! Уже использовано {initial_memory['rss_mb']:.0f} MB из 512 MB")

    # Диагностика
    import_size = diagnose_imports()
    cache_size = diagnose_caches()

    # Полный цикл только если есть запас памяти
    if initial_memory['rss_mb'] + import_size + cache_size < 450:
        cycle_memory = diagnose_full_cycle()
    else:
        print("\n⚠️ Пропускаем полный цикл (недостаточно памяти)")
        cycle_memory = 0

    # Оптимизация
    optimize_config()
    diagnose_garbage_collector()

    # Итоговая память
    final_memory = get_memory_usage()
    tracemalloc.stop()

    # Финальный отчёт
    print("\n" + "=" * 60)
    print("📊 ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 60)

    print(f"\n   Начальная память: {initial_memory['rss_mb']:.1f} MB")
    print(f"   После импортов: +{import_size:.1f} MB")
    print(f"   После кэшей: +{cache_size:.1f} MB")
    if cycle_memory > 0:
        print(f"   После цикла: +{cycle_memory:.1f} MB")
    print(f"   Конечная память: {final_memory['rss_mb']:.1f} MB")

    if final_memory['rss_mb'] > 512:
        print(f"\n❌ ПРЕВЫШЕНИЕ ЛИМИТА! Нужно {final_memory['rss_mb']:.0f} MB > 512 MB")
        print(f"\n🔧 РЕШЕНИЯ:")
        print(f"   1. Применить оптимизированную конфигурацию")
        print(f"   2. Отключить фундаментальный анализ")
        print(f"   3. Уменьшить размеры кэшей")
        print(f"   4. Установить более агрессивный GC")
    else:
        print(f"\n✅ ПАМЯТИ ДОСТАТОЧНО! {final_memory['rss_mb']:.0f} MB < 512 MB")
        print(f"\n🚀 Бот должен работать на бесплатном тарифе")

    # Сохраняем отчёт
    report_file = Path("memory_diagnosis_report.txt")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(f"Memory Diagnosis Report\n")
        f.write(f"======================\n\n")
        f.write(f"Initial: {initial_memory['rss_mb']:.1f} MB\n")
        f.write(f"After imports: +{import_size:.1f} MB\n")
        f.write(f"After caches: +{cache_size:.1f} MB\n")
        f.write(f"Final: {final_memory['rss_mb']:.1f} MB\n")
        f.write(f"Limit: 512 MB\n")
        f.write(f"Status: {'PASS' if final_memory['rss_mb'] <= 512 else 'FAIL'}\n")

    print(f"\n📄 Отчёт сохранён: {report_file}")

    return 0 if final_memory['rss_mb'] <= 512 else 1


if __name__ == "__main__":
    sys.exit(main())