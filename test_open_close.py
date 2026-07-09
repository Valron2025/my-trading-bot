#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ТЕСТ ОТКРЫТИЯ И ЗАКРЫТИЯ ПОЗИЦИЙ
Запуск: python test_open_close.py
"""

import os
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

print("=" * 80)
print("🔬 ТЕСТ ОТКРЫТИЯ И ЗАКРЫТИЯ ПОЗИЦИЙ")
print("=" * 80)

# ============================================================================
# 1. ПРОВЕРКА МЕТОДОВ БОТА
# ============================================================================

print("\n📦 [1/5] ПРОВЕРКА МЕТОДОВ БОТА...")

try:
    from trading_bot import get_trading_bot
    from trading_bot.api.tbank_client import tbank
    from trading_bot.risk.position_manager import position_manager
    from trading_bot.models import OrderSide
    from trading_bot.logger import info, success, error, warning

    print("   ✅ Все модули загружены")
except ImportError as e:
    print(f"   ❌ Ошибка импорта: {e}")
    sys.exit(1)

# ============================================================================
# 2. ПОЛУЧЕНИЕ БОТА
# ============================================================================

print("\n🤖 [2/5] ПОЛУЧЕНИЕ БОТА...")

try:
    bot = get_trading_bot()
    print(f"   ✅ Бот получен: {bot}")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")
    sys.exit(1)

# ============================================================================
# 3. ПРОВЕРКА МЕТОДОВ ЗАКРЫТИЯ
# ============================================================================

print("\n🔍 [3/5] ПРОВЕРКА МЕТОДОВ ЗАКРЫТИЯ...")

# 3.1. Проверка position_manager методов
methods_to_check = [
    ('remove_position', 'Удаление позиции'),
    ('get_all_positions', 'Получение всех позиций'),
    ('sync_with_broker', 'Синхронизация с брокером'),
    ('close_position', 'Закрытие позиции'),
    ('close_all_positions', 'Закрытие всех позиций'),
]

for method_name, description in methods_to_check:
    if hasattr(position_manager, method_name):
        print(f"   ✅ position_manager.{method_name}() - {description}")
    else:
        print(f"   ❌ position_manager.{method_name}() - ОТСУТСТВУЕТ!")

# 3.2. Проверка trading_loop методов
print("\n   🔄 Проверка TradingLoop методов...")
trading_loop = bot.trading_loop if hasattr(bot, 'trading_loop') else None

if trading_loop:
    methods_loop = [
        ('_check_positions', 'Проверка позиций (трейлинг-стоп)'),
        ('_close_worst_positions', 'Закрытие убыточных позиций'),
        ('_close_weekend_positions', 'Закрытие перед выходными'),
        ('force_cleanup_all_orders', 'Принудительная очистка заявок'),
    ]

    for method_name, description in methods_loop:
        if hasattr(trading_loop, method_name):
            print(f"   ✅ trading_loop.{method_name}() - {description}")
        else:
            print(f"   ❌ trading_loop.{method_name}() - ОТСУТСТВУЕТ!")
else:
    print("   ⚠️ trading_loop не доступен")

# 3.3. Проверка position_closer
print("\n   🔄 Проверка PositionCloser...")
position_closer = bot.position_closer if hasattr(bot, 'position_closer') else None

if position_closer:
    methods_closer = [
        ('close_position_smart', 'Умное закрытие позиции'),
        ('close_worst_positions', 'Закрытие убыточных позиций'),
        ('close_position_by_ticker', 'Закрытие по тикеру'),
    ]

    for method_name, description in methods_closer:
        if hasattr(position_closer, method_name):
            print(f"   ✅ position_closer.{method_name}() - {description}")
        else:
            print(f"   ❌ position_closer.{method_name}() - ОТСУТСТВУЕТ!")
else:
    print("   ⚠️ position_closer не доступен")

# ============================================================================
# 4. СИМУЛЯЦИЯ ОТКРЫТИЯ И ЗАКРЫТИЯ (ТОЛЬКО ПРОВЕРКА МЕТОДОВ)
# ============================================================================

print("\n💻 [4/5] СИМУЛЯЦИЯ ОТКРЫТИЯ И ЗАКРЫТИЯ...")

# 4.1. Получаем тестовый тикер
test_ticker = "SBER"
print(f"\n   📊 Тестовый тикер: {test_ticker}")

# Получаем FIGI
figi = tbank._get_figi_by_ticker(test_ticker)
if figi:
    print(f"   ✅ FIGI для {test_ticker}: {figi}")
else:
    print(f"   ⚠️ Не удалось получить FIGI для {test_ticker}")
    figi = "BBG000B9XRY4"  # SBER
    print(f"   🔄 Используем FIGI по умолчанию: {figi}")

# 4.2. Получаем текущую цену
price = tbank.get_current_price(figi)
if price:
    print(f"   💰 Текущая цена {test_ticker}: {price:.2f}₽")
else:
    print(f"   ⚠️ Не удалось получить цену")
    price = 298.0

# 4.3. Проверяем методы открытия
print("\n   📋 ПРОВЕРКА МЕТОДОВ ОТКРЫТИЯ:")
open_methods = [
    ('open_position_auto', 'Открытие позиции (авто)'),
    ('buy', 'Покупка (рыночная)'),
    ('sell', 'Продажа (рыночная)'),
    ('place_limit_order', 'Лимитная заявка'),
]

for method_name, description in open_methods:
    if hasattr(bot, method_name):
        print(f"   ✅ bot.{method_name}() - {description}")
    elif hasattr(tbank, method_name):
        print(f"   ✅ tbank.{method_name}() - {description}")
    else:
        print(f"   ❌ {method_name}() - ОТСУТСТВУЕТ!")

# 4.4. Проверяем методы закрытия
print("\n   📋 ПРОВЕРКА МЕТОДОВ ЗАКРЫТИЯ:")
close_methods = [
    ('close_position_smart', 'Умное закрытие'),
    ('emergency_close_all_shorts', 'Экстренное закрытие SHORT'),
    ('close_all_positions', 'Закрытие всех позиций'),
    ('remove_position', 'Удаление позиции из менеджера'),
]

for method_name, description in close_methods:
    if hasattr(bot, method_name):
        print(f"   ✅ bot.{method_name}() - {description}")
    elif hasattr(position_manager, method_name):
        print(f"   ✅ position_manager.{method_name}() - {description}")
    elif hasattr(position_closer, method_name):
        print(f"   ✅ position_closer.{method_name}() - {description}")
    else:
        print(f"   ❌ {method_name}() - ОТСУТСТВУЕТ!")

# ============================================================================
# 5. ТЕСТ ТРЕЙЛИНГ-СТОПА (ИМИТАЦИЯ)
# ============================================================================

print("\n🎯 [5/5] ТЕСТ ТРЕЙЛИНГ-СТОПА (СИМУЛЯЦИЯ)...")

try:
    from trading_bot.models import Position
    from datetime import datetime

    # Создаём тестовую позицию
    test_position = Position(
        figi=figi,
        ticker=test_ticker,
        quantity=10,
        avg_price=price,
        side=OrderSide.LONG,
        entry_time=datetime.now()
    )

    # Устанавливаем максимум прибыли
    test_position.max_profit_pct = 2.5  # Была прибыль 2.5%
    test_position.trailing_drawdown_pct = 0.5  # Стоп при откате 0.5%

    print(f"\n   📊 ТЕСТОВАЯ ПОЗИЦИЯ:")
    print(f"      Тикер: {test_ticker}")
    print(f"      Количество: 10 шт")
    print(f"      Цена входа: {price:.2f}₽")
    print(f"      Максимум P&L: +2.5%")
    print(f"      Порог отката: 0.5%")

    # Симулируем разные сценарии
    scenarios = [
        (price * 1.02, "Цена выросла на 2%", 2.0),
        (price * 1.025, "Цена выросла на 2.5% (НОВЫЙ МАКСИМУМ)", 2.5),
        (price * 1.021, "Цена упала на 0.4% от максимума (ОТКАТ 0.4%)", 2.1),
        (price * 1.018, "Цена упала на 0.7% от максимума (ОТКАТ 0.7% > 0.5%)", 1.8),
    ]

    print(f"\n   📈 СИМУЛЯЦИЯ ТРЕЙЛИНГ-СТОПА:")
    print(f"   {'─' * 70}")

    for current_price, description, profit_pct in scenarios:
        if position_manager is None:
            print("   ⚠️ position_manager не доступен")
            break

        # Проверяем условие трейлинг-стопа
        max_profit = getattr(test_position, 'max_profit_pct', profit_pct)
        drawdown = max_profit - profit_pct
        trailing_pct = getattr(test_position, 'trailing_drawdown_pct', 0.5)

        should_close = drawdown > trailing_pct and max_profit > 1.0

        status = "🔴 ЗАКРЫТЬ!" if should_close else "🟢 ОСТАВИТЬ"
        print(f"   {status} {description}")
        print(f"      📊 P&L: {profit_pct:+.2f}%, Откат: {drawdown:.2f}%, Порог: {trailing_pct:.2f}%")

        if should_close:
            print(f"      🎯 СРАБОТАЛ ТРЕЙЛИНГ-СТОП! Позиция будет закрыта.")

    print(f"\n   {'─' * 70}")
    print("   ✅ Тест трейлинг-стопа завершён")

except Exception as e:
    print(f"   ❌ Ошибка теста: {e}")
    import traceback

    traceback.print_exc()

# ============================================================================
# 6. ИТОГОВЫЙ ОТЧЁТ
# ============================================================================

print("\n" + "=" * 80)
print("📊 ИТОГОВЫЙ ОТЧЁТ")
print("=" * 80)

# Проверяем текущие позиции
try:
    positions = position_manager.get_all_positions()
    if positions:
        print(f"\n✅ ТЕКУЩИЕ ПОЗИЦИИ ({len(positions)} шт):")
        for figi, pos in positions.items():
            ticker = pos.ticker if hasattr(pos, 'ticker') else figi[:8]
            qty = pos.quantity if hasattr(pos, 'quantity') else 0
            avg = pos.avg_price if hasattr(pos, 'avg_price') else 0
            side = pos.side.value if hasattr(pos, 'side') else 'UNKNOWN'
            print(f"   📊 {ticker}: {side} {qty}шт по {avg:.2f}₽")
    else:
        print("\n📭 Нет открытых позиций")
except Exception as e:
    print(f"\n⚠️ Ошибка получения позиций: {e}")

print("\n" + "=" * 80)
print("✅ ТЕСТ ЗАВЕРШЁН!")
print("=" * 80)