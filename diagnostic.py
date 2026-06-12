#!/usr/bin/env python3
# diagnostic.py - Диагностика открытия позиций

import sys
import os
import asyncio
from datetime import datetime

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_bot.config import config
from trading_bot.logger import info, success, error, warning, debug


async def run_diagnostic():
    """Запуск полной диагностики"""

    print("\n" + "=" * 80)
    print("🔧 ДИАГНОСТИКА ОТКРЫТИЯ ПОЗИЦИЙ")
    print("=" * 80 + "\n")

    # ========== 1. ПРОВЕРКА КОНФИГУРАЦИИ ==========
    print("📋 [1/6] ПРОВЕРКА КОНФИГУРАЦИИ")
    print("-" * 50)

    from trading_bot.config import config

    print(f"   long_score_threshold: {config.long_score_threshold}")
    print(f"   short_score_threshold: {config.short_score_threshold}")
    print(f"   use_short: {config.use_short}")
    print(f"   use_margin: {getattr(config, 'use_margin', False)}")
    print(f"   max_positions: {config.max_positions}")
    print(f"   min_trade_amount: {config.min_trade_amount}")
    print(f"   total_capital: {getattr(config, 'total_capital', 0)}")
    print()

    # ========== 2. ПРОВЕРКА API ==========
    print("📋 [2/6] ПРОВЕРКА API Т-БАНКА")
    print("-" * 50)

    from trading_bot.api.tbank_client import tbank

    try:
        available, total_capital, _ = tbank.get_available_funds()
        print(f"   ✅ API доступен")
        print(f"   💰 Капитал: {total_capital:.2f}₽")
        print(f"   💵 Свободно: {available:.2f}₽")
    except Exception as e:
        error(f"   ❌ Ошибка API: {e}")
        return

    # ========== 3. ПРОВЕРКА ПОЗИЦИЙ ==========
    print("\n📋 [3/6] ТЕКУЩИЕ ПОЗИЦИИ")
    print("-" * 50)

    positions = tbank.get_positions(force_refresh=True)
    print(f"   📊 Всего позиций: {len(positions)}")

    for pos in positions:
        figi = pos.get('figi', '')
        ticker = pos.get('ticker', figi[:8])
        qty = pos.get('quantity', 0)
        avg = pos.get('avg_price', 0)
        cur = tbank.get_current_price(figi) or avg
        side = "SHORT" if qty < 0 else "LONG"

        if qty > 0:
            pnl = (cur - avg) * qty
        else:
            pnl = (avg - cur) * abs(qty)

        print(f"      {ticker}: {side} {abs(qty)}шт, цена={avg:.2f}→{cur:.2f}, P&L={pnl:+.2f}₽")

    # ========== 4. ПРОВЕРКА ЛИМИТОВ ==========
    print("\n📋 [4/6] ПРОВЕРКА ЛИМИТОВ")
    print("-" * 50)

    from trading_bot.risk.position_manager import position_manager

    max_positions = 10
    if total_capital < 5000:
        max_positions = 2
    elif total_capital < 10000:
        max_positions = 3
    elif total_capital < 20000:
        max_positions = 4
    elif total_capital < 50000:
        max_positions = 6
    else:
        max_positions = 10

    current_count = len(positions)
    print(f"   📊 Текущих позиций: {current_count}")
    print(f"   📈 Максимум позиций: {max_positions}")
    print(f"   🆕 Свободно мест: {max_positions - current_count}")

    # Проверка маржи
    margin_info = tbank.get_margin_info()
    margin_rate = margin_info.get('margin_rate', 0)
    print(f"   📊 Маржа: {margin_rate:.1f}%")

    if margin_rate > 80:
        warning(f"   ⚠️ Высокая маржа - открытие новых позиций запрещено")

    # ========== 5. ПОИСК КАНДИДАТОВ ==========
    print("\n📋 [5/6] ПОИСК КАНДИДАТОВ (прямой вызов)")
    print("-" * 50)

    from trading_bot.analysis.stock_scanner import StockScanner

    # Создаём сканер
    class MockBot:
        def __init__(self):
            self._get_figi_by_ticker = tbank._get_figi_by_ticker
            self._get_current_price = tbank.get_current_price
            self._calculate_position_size = lambda s, f, sc: 10
            self.position_opener = None
            self._positions_cache = None
            self._validation_cache = None
            self._blocked_figis = None
            self._long_pending = None
            self._short_pending = None
            self._price_cache = None
            self.figi_resolver = None
            self._updating = set()

    scanner = StockScanner(MockBot())

    print(f"   🔍 Запуск сканирования (первые 20 тикеров)...")

    # Получаем список акций
    all_shares = tbank.get_all_shares(limit=500)
    rub_shares = [s for s in all_shares if s.get('currency') == 'rub']

    candidates = []
    processed = 0

    for share in rub_shares[:20]:  # Проверяем только первые 20 для скорости
        ticker = share.get('ticker', '')
        figi = share.get('figi', '')
        lot = share.get('lot', 1)
        name = share.get('name', '')

        if not figi or not ticker:
            continue

        current_price = tbank.get_current_price(figi)
        if not current_price or current_price <= 0:
            continue

        lot_price = current_price * lot
        if lot_price < 500:
            continue

        # Быстрый анализ
        candles = tbank.get_candles(figi, days=2, interval_minutes=5)
        if not candles or len(candles) < 20:
            continue

        from trading_bot.analysis.technical_analyzer import analyzer
        analysis = analyzer.analyze_with_candles(ticker, candles, current_price)

        if not analysis:
            continue

        score = analysis.get('score', 0)

        if score >= 2:
            side = "LONG"
        elif score <= -2 and config.use_short:
            side = "SHORT"
        else:
            continue

        candidates.append({
            'ticker': ticker,
            'figi': figi,
            'score': score,
            'side': side,
            'price': current_price,
            'lot': lot
        })
        processed += 1

    print(f"   ✅ Обработано: {processed}")
    print(f"   🎯 Найдено кандидатов: {len(candidates)}")

    if candidates:
        print(f"\n   🏆 ТОП-5 КАНДИДАТОВ:")
        for i, c in enumerate(candidates[:5], 1):
            print(f"      {i}. {c['ticker']}: score={c['score']}, {c['side']}, цена={c['price']:.2f}₽, лот={c['lot']}")
    else:
        warning(f"   ⚠️ Нет кандидатов!")

    # ========== 6. ПРОВЕРКА СТОП-ЛОССОВ ==========
    print("\n📋 [6/6] ПРОВЕРКА СТОП-ЛОССОВ ДЛЯ ПОЗИЦИЙ")
    print("-" * 50)

    if positions:
        for pos in positions:
            figi = pos.get('figi', '')
            ticker = pos.get('ticker', figi[:8])
            qty = pos.get('quantity', 0)
            avg = pos.get('avg_price', 0)
            current_price = tbank.get_current_price(figi) or avg
            side = "SHORT" if qty < 0 else "LONG"

            # Получаем статус торгов
            try:
                status = tbank.get_trading_status(figi)
                market_available = status.get('market_order_available', False)
                limit_available = status.get('limit_order_available', False)
                api_available = status.get('api_trade_available', False)

                print(f"\n   📊 {ticker} ({side}):")
                print(f"      🏷️ Рыночные заявки: {'✅' if market_available else '❌'}")
                print(f"      📋 Лимитные заявки: {'✅' if limit_available else '❌'}")
                print(f"      🔌 API торговля: {'✅' if api_available else '❌'}")

                # Проверяем поддержку стоп-ордеров
                supports_stops = tbank.supports_stop_orders(figi)
                print(f"      🛑 Биржевые стопы: {'✅' if supports_stops else '❌'}")

            except Exception as e:
                print(f"      ⚠️ Ошибка: {e}")
    else:
        print("   📭 Нет позиций для проверки")

    # ========== ИТОГ ==========
    print("\n" + "=" * 80)
    print("📊 ИТОГ ДИАГНОСТИКИ")
    print("=" * 80)

    if candidates:
        print(f"✅ Найдено {len(candidates)} кандидатов для входа")
        print(f"   💡 Рекомендация: бот должен открывать позиции")
        print(f"   🔧 Если позиции не открываются - проверьте:")
        print(f"      1. config.use_short = {config.use_short}")
        print(f"      2. config.use_margin = {getattr(config, 'use_margin', False)}")
        print(f"      3. Достаточно ли средств для выкупа SHORT")
        print(f"      4. Не блокирует ли open_short_market открытие")
    else:
        warning(f"⚠️ Нет кандидатов для входа")
        print(f"   💡 Возможные причины:")
        print(f"      1. Слишком высокий порог score (сейчас {config.long_score_threshold})")
        print(f"      2. Недостаточно свечей для анализа (нужно 20)")
        print(f"      3. Инструменты отфильтрованы instrument_filter")
        print(f"      4. Слишком высокая минимальная сумма сделки ({config.min_trade_amount}₽)")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(run_diagnostic())