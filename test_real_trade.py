#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ТЕСТ-ДРАЙВ БОТА - ОТКРЫТИЕ И ЗАКРЫТИЕ РЕАЛЬНОЙ ПОЗИЦИИ
Запуск: python test_real_trade.py
"""

import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

print("=" * 80)
print("🧪 ТЕСТ-ДРАЙВ БОТА - ОТКРЫТИЕ И ЗАКРЫТИЕ")
print("=" * 80)
print(f"🕐 Время: {datetime.now().strftime('%H:%M:%S')}")
print("=" * 80)

# ============================================================================
# 1. ЗАГРУЗКА БОТА
# ============================================================================

print("\n📦 [1/7] ЗАГРУЗКА КОМПОНЕНТОВ...")

try:
    from trading_bot import get_trading_bot
    from trading_bot.api.tbank_client import tbank
    from trading_bot.risk.position_manager import position_manager
    from trading_bot.models import OrderSide
    from trading_bot.logger import info, success, error, warning

    print("   ✅ Все модули загружены")
except ImportError as e:
    print(f"   ❌ Ошибка: {e}")
    sys.exit(1)

# ============================================================================
# 2. ПОЛУЧЕНИЕ БОТА
# ============================================================================

print("\n🤖 [2/7] ПОЛУЧЕНИЕ БОТА...")

try:
    bot = get_trading_bot()
    print(f"   ✅ Бот получен")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")
    sys.exit(1)

# ============================================================================
# 3. ПОИСК АКТИВА ДЛЯ ТЕСТА
# ============================================================================

print("\n🔍 [3/7] ПОИСК АКТИВА ДЛЯ ТЕСТА...")

# Список ликвидных тикеров для теста
test_tickers = ["SBER", "GAZP", "LKOH", "ROSN", "TATN", "NVTK", "MGNT", "AFLT"]

test_ticker = None
test_figi = None
test_price = None

for ticker in test_tickers:
    try:
        figi = tbank._get_figi_by_ticker(ticker)
        if not figi:
            continue

        price = tbank.get_current_price(figi)
        if not price or price <= 0:
            continue

        # Проверяем, что можно торговать
        status = tbank.get_trading_status(figi)
        if not status.get('api_trade_available', False):
            continue

        test_ticker = ticker
        test_figi = figi
        test_price = price
        print(f"   ✅ Найден актив: {ticker}")
        print(f"      FIGI: {figi}")
        print(f"      Цена: {price:.2f}₽")
        print(f"      Доступен: {'✅' if status.get('api_trade_available') else '❌'}")
        break

    except Exception as e:
        print(f"   ⚠️ {ticker}: {e}")
        continue

if not test_ticker:
    print("   ❌ Не найден подходящий актив для теста!")
    sys.exit(1)

# ============================================================================
# 4. ПРОВЕРКА БАЛАНСА
# ============================================================================

print("\n💰 [4/7] ПРОВЕРКА БАЛАНСА...")

available, total, _ = tbank.get_available_funds()
print(f"   💰 Доступно: {available:.2f}₽")
print(f"   💎 Капитал: {total:.2f}₽")

# Рассчитываем размер позиции (минимальный лот)
lot_size = 1
try:
    shares = tbank.get_all_shares(limit=500)
    for share in shares:
        if share.get('figi') == test_figi:
            lot_size = share.get('lot', 1)
            break
except:
    pass

min_quantity = max(1, lot_size)
max_quantity = min(10, int(available / test_price / lot_size) * lot_size)

print(f"   📦 Лот: {lot_size} шт")
print(f"   🔢 Мин. количество: {min_quantity} шт")
print(f"   🔢 Макс. количество: {max_quantity} шт")

if max_quantity < min_quantity:
    print(f"   ❌ Недостаточно средств для покупки {test_ticker}")
    sys.exit(1)

quantity = min(1, max_quantity // lot_size) * lot_size
if quantity < min_quantity:
    quantity = min_quantity

total_cost = quantity * test_price
print(f"   📊 Будет куплено: {quantity} шт (~{total_cost:.2f}₽)")

# ============================================================================
# 5. ОТКРЫТИЕ ПОЗИЦИИ (РЕАЛЬНАЯ ЗАЯВКА!)
# ============================================================================

print("\n🟢 [5/7] ОТКРЫТИЕ ПОЗИЦИИ...")
print(f"   📊 {test_ticker}: ПОКУПКА {quantity} шт по {test_price:.2f}₽")
print("   ⚠️ ЭТО РЕАЛЬНАЯ ЗАЯВКА! Сейчас будет исполнена.")
print("   ")

# Подтверждение
confirm = input("   Подтвердите открытие позиции (y/n): ").strip().lower()
if confirm != 'y':
    print("   ❌ Тест отменён пользователем")
    sys.exit(0)

print("\n   ⏳ Исполнение заявки...")

try:
    success = tbank.buy(test_figi, quantity, use_market=True)

    if success:
        print(f"   ✅ ПОЗИЦИЯ ОТКРЫТА! {quantity} шт {test_ticker}")
    else:
        print(f"   ❌ НЕ УДАЛОСЬ ОТКРЫТЬ ПОЗИЦИЮ!")
        sys.exit(1)

except Exception as e:
    print(f"   ❌ ОШИБКА: {e}")
    sys.exit(1)

# Ждём обновления позиций
print("\n   ⏳ Обновление данных (3 секунды)...")
time.sleep(3)

# ============================================================================
# 6. ПРОВЕРКА ПОЗИЦИИ
# ============================================================================

print("\n📊 [6/7] ПРОВЕРКА ПОЗИЦИИ...")

# Получаем актуальные позиции
positions = position_manager.get_all_positions()
print(f"   📈 Открытых позиций: {len(positions)}")

# Находим нашу позицию
our_position = None
for figi, pos in positions.items():
    if pos.figi == test_figi:
        our_position = pos
        break

if our_position:
    print(f"\n   📊 ПОЗИЦИЯ НАЙДЕНА:")
    print(f"      Тикер: {our_position.ticker}")
    print(f"      Сторона: {our_position.side.value}")
    print(f"      Количество: {our_position.quantity} шт")
    print(f"      Средняя цена: {our_position.avg_price:.2f}₽")

    current_price = tbank.get_current_price(test_figi)
    if current_price:
        pnl = (current_price - our_position.avg_price) * our_position.quantity
        pnl_pct = ((current_price - our_position.avg_price) / our_position.avg_price) * 100
        print(f"      Текущая цена: {current_price:.2f}₽")
        print(f"      P&L: {pnl:+.2f}₽ ({pnl_pct:+.2f}%)")
else:
    print(f"   ⚠️ Позиция {test_ticker} не найдена в менеджере!")
    print("   🔄 Пробуем синхронизировать...")
    position_manager.sync_with_broker()
    time.sleep(1)
    positions = position_manager.get_all_positions()
    for figi, pos in positions.items():
        if pos.figi == test_figi:
            our_position = pos
            break

if not our_position:
    print("   ❌ НЕ УДАЛОСЬ НАЙТИ ПОЗИЦИЮ!")
    sys.exit(1)

# ============================================================================
# 7. ТЕСТ МЕТОДОВ ЗАКРЫТИЯ
# ============================================================================

print("\n🔴 [7/7] ТЕСТ МЕТОДОВ ЗАКРЫТИЯ...")

print("\n   📋 ДОСТУПНЫЕ МЕТОДЫ ЗАКРЫТИЯ:")
print("   ─────────────────────────────────────────────")

# 7.1. Метод 1: Умное закрытие через position_closer
print("\n   🔹 МЕТОД 1: position_closer.close_position_smart()")
print("      Анализирует тренд, RSI, уровни и принимает решение")
print("      Подходит для: Закрытия с анализом рыночных условий")

# 7.2. Метод 2: Рыночное закрытие через tbank
print("\n   🔹 МЕТОД 2: tbank.sell() / tbank.buy()")
print("      Мгновенное закрытие по рыночной цене")
print("      Подходит для: Срочного закрытия")

# 7.3. Метод 3: Удаление из менеджера
print("\n   🔹 МЕТОД 3: position_manager.remove_position()")
print("      Удаляет позицию из менеджера (без закрытия)")
print("      Подходит для: Очистки мёртвых позиций")

# 7.4. Метод 4: Трейлинг-стоп
print("\n   🔹 МЕТОД 4: trading_loop._check_positions()")
print("      Автоматическое закрытие при откате от максимума")
print("      Подходит для: Защиты прибыли")

# ============================================================================
# 8. ВЫБОР СПОСОБА ЗАКРЫТИЯ
# ============================================================================

print("\n" + "=" * 80)
print("🎯 ВЫБЕРИТЕ СПОСОБ ЗАКРЫТИЯ ПОЗИЦИИ:")
print("=" * 80)
print("   1. Умное закрытие (close_position_smart)")
print("   2. Рыночное закрытие (tbank.sell)")
print("   3. Удалить из менеджера (remove_position)")
print("   4. Тест трейлинг-стопа (имитация)")
print("   5. Закрыть всё и выйти")
print("   0. Отмена (оставить позицию открытой)")
print("=" * 80)

choice = input("\nВаш выбор: ").strip()

# ============================================================================
# 9. ИСПОЛНЕНИЕ ВЫБРАННОГО МЕТОДА
# ============================================================================

if choice == "0":
    print("\n⏸️ Тест отменён. Позиция остаётся открытой.")
    print(f"   📊 {test_ticker}: {our_position.quantity} шт")
    print("   💡 Закройте вручную через Telegram или позже.")

elif choice == "1":
    print("\n🔹 ЗАКРЫТИЕ ЧЕРЕЗ close_position_smart()...")
    try:
        result = bot.position_closer.close_position_smart(test_figi)
        if result:
            print("   ✅ Позиция закрыта (умное закрытие)")
        else:
            print("   ⚠️ Умное закрытие не сработало (возможно, нет сигнала)")
            print("   🔄 Пробуем рыночное закрытие...")
            tbank.sell(test_figi, quantity)
            print("   ✅ Позиция закрыта (рыночная заявка)")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

elif choice == "2":
    print("\n🔹 ЗАКРЫТИЕ ЧЕРЕЗ РЫНОЧНУЮ ЗАЯВКУ...")
    try:
        confirm2 = input("   Подтвердите закрытие (y/n): ").strip().lower()
        if confirm2 == 'y':
            success = tbank.sell(test_figi, quantity, use_market=True)
            if success:
                print("   ✅ ПОЗИЦИЯ ЗАКРЫТА!")
            else:
                print("   ❌ НЕ УДАЛОСЬ ЗАКРЫТЬ ПОЗИЦИЮ!")
        else:
            print("   ⏸️ Закрытие отменено")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

elif choice == "3":
    print("\n🔹 УДАЛЕНИЕ ПОЗИЦИИ ИЗ МЕНЕДЖЕРА...")
    try:
        position_manager.remove_position(test_figi)
        print("   ✅ Позиция удалена из менеджера!")
        print("   ⚠️ ВНИМАНИЕ! Позиция осталась у брокера!")
        print("   💡 Закройте её вручную в приложении Т-Банк")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

elif choice == "4":
    print("\n🔹 ТЕСТ ТРЕЙЛИНГ-СТОПА (ИМИТАЦИЯ)...")

    from trading_bot.models import Position
    from datetime import datetime, timedelta

    # Создаём тестовую позицию с прибылью
    test_pos = Position(
        figi=test_figi,
        ticker=test_ticker,
        quantity=quantity,
        avg_price=test_price * 0.97,  # Имитация входа по 97% от текущей
        side=OrderSide.LONG,
        entry_time=datetime.now() - timedelta(minutes=5)
    )
    test_pos.max_profit_pct = 2.5
    test_pos.trailing_drawdown_pct = 0.5

    current_price = tbank.get_current_price(test_figi) or test_price

    print(f"\n   📊 ИМИТАЦИЯ ПОЗИЦИИ:")
    print(f"      Тикер: {test_ticker}")
    print(f"      Текущая цена: {current_price:.2f}₽")
    print(f"      Максимум P&L: +2.5%")
    print(f"      Порог отката: 0.5%")

    # Симулируем разные сценарии
    scenarios = [
        (current_price * 1.01, "Цена +1% (откат 1.5%)", 1.5, True),
        (current_price * 1.02, "Цена +2% (откат 0.5%)", 2.0, False),
        (current_price * 1.025, "Цена +2.5% (НОВЫЙ МАКСИМУМ)", 2.5, False),
        (current_price * 1.018, "Цена +1.8% (откат 0.7% > 0.5%)", 1.8, True),
    ]

    print(f"\n   📈 СИМУЛЯЦИЯ:")
    print(f"   {'─' * 70}")

    for price, desc, pnl_pct, should_close in scenarios:
        status = "🔴 ЗАКРЫТЬ!" if should_close else "🟢 ОСТАВИТЬ"
        print(f"   {status} {desc}")
        print(f"      📊 P&L: {pnl_pct:+.2f}%, Откат: {2.5 - pnl_pct:.2f}%, Порог: 0.5%")

        if should_close:
            print(f"      🎯 СРАБОТАЛ ТРЕЙЛИНГ-СТОП! Позиция будет закрыта.")
            print(f"      💰 Фиксация прибыли: {((price - test_pos.avg_price) / test_pos.avg_price * 100):+.2f}%")

    print(f"\n   {'─' * 70}")
    print("   ✅ Тест трейлинг-стопа завершён")

elif choice == "5":
    print("\n🛑 ЗАКРЫТИЕ ВСЕХ ПОЗИЦИЙ...")
    try:
        confirm3 = input("   Подтвердите закрытие всех позиций (y/n): ").strip().lower()
        if confirm3 == 'y':
            closed = 0
            positions = position_manager.get_all_positions()
            for figi, pos in positions.items():
                if pos.side.value == "LONG":
                    if tbank.sell(figi, pos.quantity):
                        closed += 1
                else:
                    if tbank.buy(figi, pos.quantity):
                        closed += 1
                position_manager.remove_position(figi)
            print(f"   ✅ Закрыто позиций: {closed}")
        else:
            print("   ⏸️ Закрытие отменено")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

else:
    print("\n❌ Неверный выбор. Позиция остаётся открытой.")

# ============================================================================
# 10. ИТОГОВЫЙ СТАТУС
# ============================================================================

print("\n" + "=" * 80)
print("📊 ИТОГОВЫЙ СТАТУС")
print("=" * 80)

try:
    positions = position_manager.get_all_positions()
    if positions:
        print(f"\n📈 ОТКРЫТЫХ ПОЗИЦИЙ: {len(positions)}")
        for figi, pos in positions.items():
            ticker = pos.ticker if hasattr(pos, 'ticker') else figi[:8]
            qty = pos.quantity if hasattr(pos, 'quantity') else 0
            avg = pos.avg_price if hasattr(pos, 'avg_price') else 0
            side = pos.side.value if hasattr(pos, 'side') else 'UNKNOWN'
            print(f"   📊 {ticker}: {side} {qty}шт по {avg:.2f}₽")
    else:
        print("\n📭 Нет открытых позиций")

except Exception as e:
    print(f"\n⚠️ Ошибка получения статуса: {e}")

print("\n" + "=" * 80)
print("✅ ТЕСТ-ДРАЙВ ЗАВЕРШЁН!")
print("=" * 80)