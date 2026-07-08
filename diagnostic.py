#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DIAGNOSTIC TOOL - ПОЛНАЯ ПРОВЕРКА БОТА
Использует РЕАЛЬНЫЙ код бота, а не заглушки!
"""

import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# Добавляем корневую директорию проекта
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

print("=" * 80)
print("🔍 ДИАГНОСТИКА ТОРГОВОГО БОТА")
print("=" * 80)
print(f"📂 Проект: {PROJECT_ROOT}")
print(f"📅 Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)

# ============================================================================
# 1. ИМПОРТ ВСЕХ КОМПОНЕНТОВ БОТА
# ============================================================================

print("\n📦 [1/10] ЗАГРУЗКА КОМПОНЕНТОВ БОТА...")

try:
    from trading_bot.config import config

    print("   ✅ config загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки config: {e}")
    sys.exit(1)

try:
    from trading_bot.logger import info, success, error, warning, debug, bomb

    print("   ✅ logger загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки logger: {e}")
    sys.exit(1)

try:
    from trading_bot.api.tbank_client import tbank, price_cache, candles_cache, positions_cache, margin_cache, \
        instruments_cache

    print("   ✅ tbank_client загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки tbank_client: {e}")
    sys.exit(1)

try:
    from trading_bot.cache.cache_manager import TTLCache

    print("   ✅ cache_manager загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки cache_manager: {e}")
    sys.exit(1)

try:
    from trading_bot.core.trading_loop import TradingLoop

    print("   ✅ trading_loop загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки trading_loop: {e}")
    sys.exit(1)

try:
    from trading_bot.core.settings_manager import settings_manager

    print("   ✅ settings_manager загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки settings_manager: {e}")
    sys.exit(1)

try:
    from trading_bot.core.blacklist_manager import blacklist_manager

    print("   ✅ blacklist_manager загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки blacklist_manager: {e}")
    sys.exit(1)

try:
    from trading_bot.core.candle_sync_wrapper import get_candles_sync, get_current_price_sync, is_candle_builder_ready

    print("   ✅ candle_sync_wrapper загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки candle_sync_wrapper: {e}")
    sys.exit(1)

try:
    from trading_bot.utils.time_utils import get_moscow_time, is_trading_time_for_ticker, is_holiday, \
        is_dsvd_trading_time, is_otc_trading_time, is_weekend_trading_time

    print("   ✅ time_utils загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки time_utils: {e}")
    sys.exit(1)

try:
    from trading_bot.utils.figi_resolver import get_figi_resolver

    print("   ✅ figi_resolver загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки figi_resolver: {e}")
    sys.exit(1)

try:
    from trading_bot.analysis.technical_analyzer import analyzer

    print("   ✅ technical_analyzer загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки technical_analyzer: {e}")
    sys.exit(1)

try:
    from trading_bot.analysis.market_analyzer import market_analyzer

    print("   ✅ market_analyzer загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки market_analyzer: {e}")
    sys.exit(1)

try:
    from trading_bot.risk.position_manager import position_manager

    print("   ✅ position_manager загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки position_manager: {e}")
    sys.exit(1)

try:
    from trading_bot.analysis.strategy_engine import StrategyEngine

    print("   ✅ strategy_engine загружен")
except ImportError as e:
    print(f"   ❌ Ошибка загрузки strategy_engine: {e}")
    sys.exit(1)

print("✅ Все компоненты загружены!\n")

# ============================================================================
# 2. ПРОВЕРКА КОНФИГУРАЦИИ
# ============================================================================

print("📋 [2/10] ПРОВЕРКА КОНФИГУРАЦИИ...")

print(f"   🔑 Токен T-Bank: {'✅' if config.tbank_token else '❌ НЕТ!'}")
print(f"   💰 Капитал: {config.total_capital:.2f}₽")
print(f"   📈 TP: {config.take_profit_pct:.1f}%")
print(f"   🛑 SL: {config.stop_loss_pct:.1f}%")
print(f"   🔻 SHORT: {'✅' if config.use_short else '❌'}")
print(f"   📊 Макс. позиций: {config.max_positions}")
print(f"   🎯 LONG порог: ≥ {config.long_score_threshold}")
print(f"   🎯 SHORT порог: ≤ {config.short_score_threshold}")
print()

# ============================================================================
# 3. ПРОВЕРКА КЭШЕЙ
# ============================================================================

print("🗂️ [3/10] ПРОВЕРКА КЭШЕЙ...")

caches = {
    'price_cache': price_cache,
    'candles_cache': candles_cache,
    'positions_cache': positions_cache,
    'margin_cache': margin_cache,
    'instruments_cache': instruments_cache,
}

for name, cache in caches.items():
    if hasattr(cache, 'get_stats'):
        stats = cache.get_stats()
        print(f"   📦 {name}: size={stats.get('size', 0)}, hits={stats.get('hits', 0)}, misses={stats.get('misses', 0)}")
    else:
        print(f"   📦 {name}: {'✅ OK' if cache else '❌ НЕТ'}")

print()

# ============================================================================
# 4. ПРОВЕРКА API Т-БАНКА
# ============================================================================

print("🌐 [4/10] ПРОВЕРКА API Т-БАНКА...")


def measure_api_call(name: str, func, *args, **kwargs) -> Tuple[Any, float]:
    """Измеряет время выполнения API вызова"""
    start = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        status = "✅" if elapsed_ms < 200 else "⚠️" if elapsed_ms < 500 else "❌"
        print(f"   {status} {name}: {elapsed_ms:.0f}ms")
        return result, elapsed_ms
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"   ❌ {name}: ОШИБКА ({elapsed_ms:.0f}ms): {str(e)[:80]}")
        return None, elapsed_ms


# 4.1 Проверка доступных средств
result, funds_time = measure_api_call("get_available_funds", tbank.get_available_funds)
if result and isinstance(result, (tuple, list)) and len(result) >= 2:
    available, total = result[0], result[1]
    print(f"      💰 Капитал: {total:.2f}₽, Свободно: {available:.2f}₽")
elif result:
    print(f"      💰 Результат: {result}")

# 4.2 Проверка позиций
positions, pos_time = measure_api_call("get_positions", tbank.get_positions, force_refresh=True)
if positions:
    print(f"      📊 Позиций: {len(positions)}")
    for pos in positions[:3]:
        ticker = pos.get('ticker', pos.get('figi', 'unknown')[:8])
        qty = pos.get('quantity', 0)
        avg = pos.get('avg_price', 0)
        print(f"         - {ticker}: {qty}шт по {avg:.2f}₽")

# 4.3 Проверка FIGI для тестового тикера
test_ticker = "SBER"
figi, figi_time = measure_api_call(f"get_figi_by_ticker({test_ticker})", tbank._get_figi_by_ticker, test_ticker)
if figi:
    print(f"      🔑 FIGI для {test_ticker}: {figi}")

# 4.4 Проверка цены
price = None
price_time = 0
if figi:
    price, price_time = measure_api_call(f"get_current_price({test_ticker})", tbank.get_current_price, figi)
    if price:
        print(f"      💰 Цена {test_ticker}: {price:.4f}₽")

# 4.5 Проверка свечей
candles = None
candles_time = 0
if figi:
    candles, candles_time = measure_api_call(f"get_candles({test_ticker})", tbank.get_candles, figi, days=2,
                                             interval_minutes=5)
    if candles:
        print(f"      📊 Свечей: {len(candles)} (последняя: {candles[-1][0]:.2f}₽)")

# 4.6 Проверка стакана
if figi:
    orderbook, ob_time = measure_api_call(f"get_orderbook({test_ticker})", tbank.get_orderbook, figi, depth=3)
    if orderbook:
        best_bid = orderbook.get('best_bid', 0)
        best_ask = orderbook.get('best_ask', 0)
        print(f"      📊 Стакан: BID={best_bid:.2f}₽, ASK={best_ask:.2f}₽")

# 4.7 Проверка batch-запроса
if figi:
    batch_result, batch_time = measure_api_call(f"get_last_prices_batch([{test_ticker}])", tbank.get_last_prices_batch,
                                                [figi])
    if batch_result and figi in batch_result:
        print(f"      📦 Batch цена: {batch_result[figi]:.4f}₽")

print()

# ============================================================================
# 5. ПРОВЕРКА КЭШИРОВАНИЯ СВЕЧЕЙ
# ============================================================================

print("📦 [5/10] ПРОВЕРКА КЭШИРОВАНИЯ СВЕЧЕЙ...")

if figi:
    # ========== РУЧНАЯ ПРОВЕРКА КЭША ==========
    print("   🧪 РУЧНАЯ ПРОВЕРКА КЭША:")

    # 1. Получаем свечи через API
    start = time.perf_counter()
    candles_api = tbank.get_candles(figi, days=2, interval_minutes=5)
    time_api = (time.perf_counter() - start) * 1000
    print(f"      📡 API запрос: {time_api:.0f}ms ({len(candles_api)} свечей)")

    # 2. Сохраняем в кэш вручную
    cache_key = f"{figi}_2_5"
    candles_cache.set(cache_key, candles_api, ttl=120)
    print(f"      💾 Ручное сохранение в кэш: {len(candles_api)} свечей")

    # 3. Проверяем кэш
    start = time.perf_counter()
    cached = candles_cache.get(cache_key)
    time_cache = (time.perf_counter() - start) * 1000
    if cached is not None:
        print(f"      📦 Чтение из кэша: {time_cache:.0f}ms ({len(cached)} свечей) ✅")
        print(f"      ✅ Кэш РАБОТАЕТ! Ускорение: {time_api / time_cache:.1f}x")
    else:
        print(f"      ❌ Кэш НЕ ДОСТУПЕН!")

    # 4. Проверяем, что tbank.get_candles() использует кэш
    print("\n   📡 ПРОВЕРКА tbank.get_candles() С КЭШЕМ:")

    # Очищаем кэш для теста
    candles_cache.delete(cache_key)

    # Первый запрос (должен идти в API)
    start = time.perf_counter()
    candles1 = tbank.get_candles(figi, days=2, interval_minutes=5)
    time1 = (time.perf_counter() - start) * 1000
    print(f"      📡 1-й запрос (API): {time1:.0f}ms ({len(candles1)} свечей)")

    # Второй запрос (должен идти из кэша)
    start = time.perf_counter()
    candles2 = tbank.get_candles(figi, days=2, interval_minutes=5)
    time2 = (time.perf_counter() - start) * 1000
    print(f"      📦 2-й запрос (кэш): {time2:.0f}ms ({len(candles2)} свечей)")

    if time2 < time1 * 0.3:
        print(f"      ✅ Кэш РАБОТАЕТ! Ускорение: {time1 / time2:.1f}x")
    else:
        print(f"      ⚠️ Кэш НЕ РАБОТАЕТ или TTL истёк")
        print(f"      💡 Проверьте, что в tbank.get_candles() есть:")
        print(f"         - candles_cache.get(cache_key) - проверка кэша")
        print(f"         - candles_cache.set(cache_key, result, ttl=120) - сохранение в кэш")

print()

# ============================================================================
# 6. ПРОВЕРКА ВРЕМЕНИ ТОРГОВ
# ============================================================================

print("⏰ [6/10] ПРОВЕРКА ВРЕМЕНИ ТОРГОВ...")

now = get_moscow_time()
print(f"   🕐 Текущее время: {now.strftime('%H:%M:%S')}")
print(f"   📅 День недели: {['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС'][now.weekday()]}")

can_trade, reason = is_trading_time_for_ticker(test_ticker)
print(f"   🏛️ Торговля для {test_ticker}: {'✅' if can_trade else '❌'} ({reason})")

print(f"   🎄 Праздник: {'✅' if is_holiday() else '❌'}")
print(f"   🌙 ДСВД: {'✅' if is_dsvd_trading_time() else '❌'}")
print(f"   🌙 OTC: {'✅' if is_otc_trading_time() else '❌'}")
print(f"   🌙 Выходные торги: {'✅' if is_weekend_trading_time() else '❌'}")

print()

# ============================================================================
# 7. ПРОВЕРКА БЛЭК-ЛИСТА
# ============================================================================

print("⛔ [7/10] ПРОВЕРКА БЛЭК-ЛИСТА...")

blocked_list = blacklist_manager.get_blocked_list()
print(f"   📋 Заблокированных тикеров: {len(blocked_list)}")

if blocked_list:
    for ticker in blocked_list[:5]:
        is_blocked, reason = blacklist_manager.is_blocked(ticker)
        print(f"      - {ticker}: {reason}")

# Проверка test_ticker
is_blocked, reason = blacklist_manager.is_blocked(test_ticker)
print(f"   🔍 {test_ticker}: {'⛔ ЗАБЛОКИРОВАН' if is_blocked else '✅ ДОСТУПЕН'}")

print()

# ============================================================================
# 8. ПРОВЕРКА ТЕХНИЧЕСКОГО АНАЛИЗА
# ============================================================================

print("📈 [8/10] ПРОВЕРКА ТЕХНИЧЕСКОГО АНАЛИЗА...")

if figi:
    # Получаем свечи
    candles = tbank.get_candles(figi, days=2, interval_minutes=5)
    if candles and len(candles) >= 20:
        # Получаем цены
        prices = [c[0] for c in candles[-50:]]
        volumes = [c[1] for c in candles[-50:]]

        # Проверяем стратегию
        try:
            engine = StrategyEngine({
                'rsi_period': 7,
                'score_threshold_long': 2,
                'score_threshold_short': -2,
                'take_profit_pct': 1.5,
                'stop_loss_pct': 0.8,
                'use_supertrend': True,
                'use_ichimoku': True,
                'use_dmi_adx': True,
                'use_stochastic': True,
                'use_cci': True,
                'use_psar': True,
                'use_pivots': True,
                'use_vwap': True,
                'use_donchian': True,
                'use_obv': True,
                'use_aroon': True,
            })

            signal = engine.analyze_signal(
                prices=prices,
                volumes=volumes,
                name=test_ticker,
                figi=figi,  # ✅ ПРАВИЛЬНО
                candles=[{'close': p, 'volume': v} for p, v in zip(prices, volumes)]
            )

            print(f"   📊 {test_ticker}:")
            print(f"      Score: {signal.score}")
            print(f"      Сигнал: {signal.recommendation}")
            print(f"      Сигналов: {len(signal.signals)}")
            if signal.signals:
                print(f"      Первый сигнал: {signal.signals[0][:60]}")
            print(f"      RSI: {signal.rsi:.1f}")
            print(f"      TP: {signal.take_profit_pct:.1f}%")
            print(f"      SL: {signal.stop_loss_pct:.1f}%")

        except Exception as e:
            print(f"   ❌ Ошибка технического анализа: {e}")
    else:
        print(f"   ⚠️ Недостаточно свечей для анализа ({len(candles) if candles else 0}/20)")

print()

# ============================================================================
# 9. ПРОВЕРКА СТАТИСТИКИ API
# ============================================================================

print("🌐 [9/10] СТАТИСТИКА API...")

try:
    # Задержки API
    from trading_bot.api.tbank_client import api_monitor

    stats = api_monitor.get_stats()
    if stats:
        print(f"\n   📊 Задержки API (последние измерения):")
        for name, data in sorted(stats.items(), key=lambda x: x[1]['avg_ms'], reverse=True)[:10]:
            status = "✅" if data['avg_ms'] < 200 else "⚠️" if data['avg_ms'] < 500 else "❌"
            print(f"      {status} {name}: ср={data['avg_ms']:.0f}ms, макс={data['max_ms']:.0f}ms, n={data['count']}")
    else:
        print("   📊 Нет данных о задержках API")
except Exception as e:
    print(f"   ⚠️ Не удалось получить статистику API: {e}")

print()

# ============================================================================
# 10. ПРОВЕРКА ВРЕМЕНИ ВЫПОЛНЕНИЯ
# ============================================================================

print("⚡ [10/10] ПРОВЕРКА ПРОИЗВОДИТЕЛЬНОСТИ...")

# Проверка скорости get_current_price с кэшем и без
test_figi = figi or "BBG000B9XRY4"  # SBER

print(f"   📡 Тестирование get_current_price для {test_figi}:")

# Без кэша (принудительно пропускаем кэш)
if hasattr(price_cache, 'delete'):
    price_cache.delete(test_figi)
elif hasattr(price_cache, '_cache'):
    price_cache._cache.delete(test_figi)

start = time.perf_counter()
price1 = tbank.get_current_price(test_figi)
time1 = (time.perf_counter() - start) * 1000

# С кэшем (второй вызов)
start = time.perf_counter()
price2 = tbank.get_current_price(test_figi)
time2 = (time.perf_counter() - start) * 1000

print(f"      Без кэша: {time1:.0f}ms → {price1 if price1 else 'N/A'}")
print(f"      С кэшем:  {time2:.0f}ms → {price2 if price2 else 'N/A'}")
if time2 < time1 * 0.3:
    print(f"      ✅ Кэш работает! Ускорение: {time1 / time2:.1f}x")
else:
    print(f"      ⚠️ Кэш не работает или TTL истёк")

# Проверка candle_sync_wrapper
print(f"\n   📡 Тестирование get_candles_sync для {test_ticker}:")
start = time.perf_counter()
sync_candles = get_candles_sync(test_ticker, interval_minutes=5, days=2)
time_sync = (time.perf_counter() - start) * 1000
print(f"      Время: {time_sync:.0f}ms, свечей: {len(sync_candles) if sync_candles else 0}")

# Проверка candle_builder статуса
try:
    ready = is_candle_builder_ready()
    print(f"\n   🕯️ CandleBuilder готов: {'✅' if ready else '❌'}")
except Exception as e:
    print(f"\n   🕯️ CandleBuilder: ошибка - {e}")

print()

# ============================================================================
# ИТОГОВЫЙ ОТЧЁТ
# ============================================================================

print("=" * 80)
print("📊 ИТОГОВЫЙ ОТЧЁТ ДИАГНОСТИКИ")
print("=" * 80)

# Сбор всех проблем
issues = []
warnings = []

# Проверка кэшей
for name, cache in caches.items():
    if hasattr(cache, 'get_stats'):
        stats = cache.get_stats()
        total = stats.get('hits', 0) + stats.get('misses', 0)
        if total > 10 and stats.get('hit_rate', 0) < 10:
            warnings.append(f"Низкий hit rate у {name}: {stats.get('hit_rate', 0):.1f}%")

# Проверка API задержек
if figi:
    if 'price_time' in locals() and price_time > 500:
        warnings.append(f"Высокая задержка get_current_price: {price_time:.0f}ms")
    if 'candles_time' in locals() and candles_time > 1000:
        warnings.append(f"Высокая задержка get_candles: {candles_time:.0f}ms")

# Проверка кэширования свечей
if figi and 'time2' in locals() and 'time1' in locals():
    if time2 > time1 * 0.5:
        warnings.append("Кэш свечей не работает эффективно")

# Проверка PositionMonitor
try:
    loop_file = PROJECT_ROOT / "trading_bot" / "core" / "trading_loop.py"
    if loop_file.exists():
        with open(loop_file, 'r', encoding='utf-8') as f:
            content = f.read()
            if 'check_interval = 2' in content or 'check_interval = 3' in content:
                warnings.append("PositionMonitor.check_interval всё ещё 2-3 секунды (должно быть 5)")
            elif 'check_interval = 5' in content:
                print("   ✅ PositionMonitor.check_interval = 5 секунд")
            else:
                warnings.append("PositionMonitor.check_interval не найден в коде")
    else:
        warnings.append(f"Файл trading_loop.py не найден: {loop_file}")
except Exception as e:
    warnings.append(f"Не удалось проверить PositionMonitor: {e}")

# Проверка TTL кэша свечей
try:
    candles_ttl = candles_cache.default_ttl if hasattr(candles_cache, 'default_ttl') else None
    if candles_ttl and candles_ttl < 60:
        warnings.append(f"TTL кэша свечей слишком мал: {candles_ttl}с (рекомендуется 120с)")
except Exception as e:
    pass

# Итоговый вывод
print(f"\n📋 Найдено проблем: {len(issues)}")
print(f"⚠️ Найдено предупреждений: {len(warnings)}")

if issues:
    print("\n❌ КРИТИЧЕСКИЕ ПРОБЛЕМЫ:")
    for issue in issues:
        print(f"   - {issue}")

if warnings:
    print("\n⚠️ ПРЕДУПРЕЖДЕНИЯ:")
    for warning in warnings:
        print(f"   - {warning}")

if not issues and not warnings:
    print("\n✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ! Бот работает корректно.")

print("\n" + "=" * 80)
print("📝 РЕКОМЕНДАЦИИ:")
print("=" * 80)

if warnings or issues:
    print("1. Убедитесь, что PositionMonitor.check_interval = 5 секунд")
    print("2. Проверьте TTL кэшей: price_cache=10с, candles_cache=120с")
    print("3. Используйте batch-запросы (get_last_prices_batch) для множественных цен")
    print("4. При медленных API — проверьте интернет-соединение")
else:
    print("✅ Все системы работают штатно!")
    print("📊 Бот готов к торговле.")

print("\n" + "=" * 80)
print("🏁 ДИАГНОСТИКА ЗАВЕРШЕНА")
print("=" * 80)

# Сохранение результата в файл
try:
    result = {
        'timestamp': datetime.now().isoformat(),
        'issues': issues,
        'warnings': warnings,
        'config': {
            'capital': config.total_capital,
            'take_profit': config.take_profit_pct,
            'stop_loss': config.stop_loss_pct,
            'use_short': config.use_short,
            'max_positions': config.max_positions,
        },
        'api': {
            'figi': figi if figi else None,
            'price': price1 if 'price1' in locals() and price1 else None,
            'price_time_ms': price_time if 'price_time' in locals() else None,
            'candles_time_ms': candles_time if 'candles_time' in locals() else None,
        }
    }

    with open('diagnostic_result.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("\n💾 Результат сохранён в diagnostic_result.json")
except Exception as e:
    print(f"\n⚠️ Не удалось сохранить результат: {e}")