#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ДИАГНОСТИКА ТОРГОВОГО БОТА
Упрощённая версия - проверяет только критическое
"""

import sys
import os
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

print("\n" + "=" * 80)
print("🔍 ДИАГНОСТИКА ТОРГОВОГО БОТА (УПРОЩЁННАЯ)")
print("=" * 80)

# ========== 1. ПРОВЕРКА API И БАЛАНСА ==========
print("\n📡 [1/6] ПРОВЕРКА API И БАЛАНСА")
print("-" * 40)

try:
    from trading_bot.api.tbank_client import tbank

    available, total_capital, _ = tbank.get_available_funds()
    print(f"   💰 Капитал: {total_capital:,.2f} ₽")
    print(f"   💵 Свободно: {available:,.2f} ₽")
    print(f"   ✅ API доступен")
except Exception as e:
    print(f"   ❌ Ошибка API: {e}")
    print(f"   ⚠️ Возможно, токен не настроен")

# ========== 2. ПРОВЕРКА ПОЗИЦИЙ У БРОКЕРА ==========
print("\n📊 [2/6] ПОЗИЦИИ У БРОКЕРА")
print("-" * 40)

try:
    from trading_bot.api.tbank_client import tbank

    positions = tbank.get_positions()
    if positions:
        print(f"   📈 Открыто позиций: {len(positions)}")
        for pos in positions:
            figi = pos.get('figi', '')
            # Пробуем получить тикер
            try:
                ticker = tbank._get_ticker_by_figi(figi) or figi[:12]
            except:
                ticker = figi[:12]
            qty = pos.get('quantity', 0)
            avg = pos.get('avg_price', 0)
            side = "LONG" if qty > 0 else "SHORT"
            try:
                cur = tbank.get_current_price(figi)
                if cur:
                    pnl = (cur - avg) * qty if qty > 0 else (avg - cur) * abs(qty)
                    print(f"      {side} {ticker}: {abs(qty)} шт по {avg:.4f}₽, P&L={pnl:+.2f}₽")
                else:
                    print(f"      {side} {ticker}: {abs(qty)} шт по {avg:.4f}₽")
            except:
                print(f"      {side} {ticker}: {abs(qty)} шт по {avg:.4f}₽")
    else:
        print(f"   📭 Нет открытых позиций")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# ========== 3. ПРОВЕРКА МАРЖИ ==========
print("\n📊 [3/6] МАРЖИНАЛЬНЫЕ ПОКАЗАТЕЛИ")
print("-" * 40)

try:
    from trading_bot.api.tbank_client import tbank

    margin_info = tbank.get_margin_info()
    margin_rate = margin_info.get('margin_rate', 0)
    liquid = margin_info.get('liquid_portfolio', 0)
    starting = margin_info.get('starting_margin', 0)

    print(f"   📈 Ликвидный портфель: {liquid:,.2f} ₽")
    print(f"   🔒 Начальная маржа: {starting:,.2f} ₽")
    print(f"   📊 Ставка маржи: {margin_rate:.1f}%")

    if margin_rate > 85:
        print(f"   🔴 КРИТИЧЕСКАЯ МАРЖА! Нужно закрыть позиции")
    elif margin_rate > 70:
        print(f"   🟡 ВЫСОКАЯ МАРЖА, новые позиции ограничены")
    else:
        print(f"   ✅ МАРЖА В НОРМЕ, можно торговать")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# ========== 4. ПРОВЕРКА ТЕКУЩЕЙ СЕССИИ ==========
print("\n⏰ [4/6] ТОРГОВАЯ СЕССИЯ")
print("-" * 40)

try:
    from trading_bot.utils.time_utils import (
        get_moscow_time, is_trading_time,
        get_current_session_name_detailed
    )

    now = get_moscow_time()
    can_trade = is_trading_time()
    session = get_current_session_name_detailed()

    print(f"   🕐 Время МСК: {now.strftime('%H:%M:%S')}")
    print(f"   📊 Сессия: {session}")
    print(f"   🟢 Торговля разрешена: {'ДА' if can_trade else 'НЕТ'}")

    if not can_trade:
        print(f"   ⚠️ Сейчас торги не идут, бот ждёт открытия (10:00 МСК)")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# ========== 5. ПРОВЕРКА OTC СТАТУСА ==========
print("\n🔐 [5/6] ПРОВЕРКА OTC СТАТУСА (ТЕСТОВЫЕ ТИКЕРЫ)")
print("-" * 40)

test_tickers = ["FIXR", "SBER", "GAZP"]

try:
    from trading_bot.api.tbank_client import tbank

    for ticker in test_tickers:
        try:
            figi = tbank._get_figi_by_ticker(ticker)
            if figi:
                is_otc = tbank.is_confirmation_required(figi)
                status = tbank.get_trading_status(figi)
                market_available = status.get('market_order_available', False)

                otc_mark = "🔴 OTC" if is_otc else "🟢 НОРМА"
                market_mark = "✅" if market_available else "⚠️"
                print(f"   {market_mark} {ticker}: {otc_mark}, рыночные={market_available}")
            else:
                print(f"   ❓ {ticker}: FIGI не найден")
        except Exception as e:
            print(f"   ❌ {ticker}: ошибка - {str(e)[:50]}")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# ========== 6. ИТОГОВЫЕ РЕКОМЕНДАЦИИ ==========
print("\n📋 [6/6] ИТОГОВЫЕ РЕКОМЕНДАЦИИ")
print("=" * 80)

print("""
✅ ПРОВЕРЬТЕ В ЛОГАХ RENDER:

   1. Торгуемое ли сейчас время?
   2. Есть ли ошибка 30042 (недостаточно средств)?
   3. Не OTC ли инструмент (требует подтверждения)?
   4. Достаточно ли свободных средств?

🚀 ЕСЛИ БОТ НЕ ОТКРЫВАЕТ ПОЗИЦИИ:

   1. Перезапустите бота на Render
   2. Проверьте, что позиции синхронизированы
   3. Посмотрите полный лог ошибки

📋 БЫСТРЫЕ КОМАНДЫ:

   # Просмотр позиций
   python -c "from trading_bot.api.tbank_client import tbank; print(tbank.get_positions())"

   # Проверка баланса
   python -c "from trading_bot.api.tbank_client import tbank; print(tbank.get_available_funds())"

   # Синхронизация менеджера
   python -c "from trading_bot.risk.position_manager import position_manager; position_manager.sync_and_cleanup()"
""")

print("=" * 80)
print("🔍 Диагностика завершена")
print("=" * 80 + "\n")