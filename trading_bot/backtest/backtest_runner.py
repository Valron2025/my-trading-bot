#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск бэктеста - с правильным порядком моков"""

import sys
import warnings
import pandas as pd

warnings.filterwarnings('ignore')

# ========== ЗАГЛУШКИ ДЛЯ ВСЕХ МОДУЛЕЙ ==========
import types

modules_to_mock = [
    'tbank_client',
    'telegram_notifier',
    'logger',
    'market_analyzer',
    'position_manager',
    'moex_client',
    'config',
    'technical_analyzer'
]

for module_name in modules_to_mock:
    mock_module = types.ModuleType(module_name)

    if module_name == 'tbank_client':
        mock_module.tbank = types.SimpleNamespace()
        mock_module.tbank.get_current_price = lambda x: None
        mock_module.tbank.get_all_shares = lambda **x: []
        mock_module.tbank.get_candles = lambda **x: []

    elif module_name == 'moex_client':
        mock_module.moex_client = types.SimpleNamespace()
        mock_module.moex_client.get_candles = lambda **x: []

    elif module_name == 'technical_analyzer':
        mock_module.analyzer = types.SimpleNamespace()
        mock_module.analyzer.analyze_stock = lambda **x: None

    elif module_name == 'config':
        mock_module.config = types.SimpleNamespace()
        mock_module.config.take_profit_pct = 1.5
        mock_module.config.stop_loss_pct = 0.8
        mock_module.config.trailing_stop_pct = 0.5
        mock_module.config.adaptive_timeout_minutes = 20

    elif module_name == 'logger':
        def mock_info(msg): pass
        def mock_success(msg): pass
        def mock_error(msg, exc_info=False): pass
        def mock_warning(msg): pass
        def mock_debug(msg): pass
        def mock_trade(ticker, direction, qty, price): pass
        def mock_trade_profit(ticker, profit_pct, profit_amount): pass
        def mock_balance(amount): pass
        def mock_sep(title=None): pass

        mock_module.info = mock_info
        mock_module.success = mock_success
        mock_module.error = mock_error
        mock_module.warning = mock_warning
        mock_module.debug = mock_debug
        mock_module.trade = mock_trade
        mock_module.trade_profit = mock_trade_profit
        mock_module.balance = mock_balance
        mock_module.sep = mock_sep

        mock_module.bomb = types.SimpleNamespace()
        mock_module.bomb.success = mock_success
        mock_module.bomb.info = mock_info
        mock_module.bomb.error = mock_error
        mock_module.bomb.warning = mock_warning
        mock_module.bomb.debug = mock_debug

    sys.modules[module_name] = mock_module

# Импортируем бэктестер
from .backtest import ProfessionalBacktester, ParameterOptimizer


def get_auto_commission() -> float:
    """Автоматическое получение комиссии по тарифу пользователя"""
    try:
        from trading_bot.api.tbank_client import tbank
        tariff_name, commission = tbank.get_user_tariff()
        print(f"📊 Используется комиссия {commission * 100:.2f}% (тариф: {tariff_name})")
        return commission
    except Exception:
        print("⚠️ Используется комиссия по умолчанию: 0.3%")
        return 0.003


def run_backtest(ticker: str, days: int = 90, optimize: bool = False) -> dict:
    """Запуск бэктеста для указанного тикера"""
    if optimize:
        optimizer = ParameterOptimizer(ticker, days)
        best = optimizer.optimize()
        if best:
            optimizer.export_results()
            return best
        return {"error": "Optimization failed"}
    else:
        commission = get_auto_commission()

        bt = ProfessionalBacktester(
            initial_balance=100000,
            risk_per_trade=0.1,
            min_confidence=0,
            take_profit_pct=1.0,
            stop_loss_pct=0.5,
            max_positions=3,
            warmup_candles=20,
            days=days,
            commission_pct=commission
        )

        result = bt.run_single(ticker)

        if 'error' not in result:
            bt.export_results(ticker, result)

        return result


if __name__ == "__main__":
    print("=" * 70)
    print("🤖 ЗАПУСК БЭКТЕСТЕРА")
    print("=" * 70)

    if len(sys.argv) > 1:
        ticker = sys.argv[1]
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 90

        if len(sys.argv) > 3 and sys.argv[3] == 'optimize':
            optimizer = ParameterOptimizer(ticker, days)
            best = optimizer.optimize()
            if best:
                optimizer.export_results()
        else:
            commission = get_auto_commission()

            bt = ProfessionalBacktester(
                initial_balance=100000,
                risk_per_trade=0.1,
                min_confidence=0,
                take_profit_pct=1.0,
                stop_loss_pct=0.5,
                max_positions=3,
                warmup_candles=20,
                days=days,
                commission_pct=commission
            )

            result = bt.run_single(ticker)
            if 'error' not in result:
                print("\n" + "=" * 70)
                print("📊 ИТОГОВЫЕ РЕЗУЛЬТАТЫ")
                print("=" * 70)
                print(f"📈 Сделок: {result['total_trades']}")

                if hasattr(bt, 'trades') and bt.trades:
                    long_trades = [t for t in bt.trades if t['side'] == 'LONG']
                    short_trades = [t for t in bt.trades if t['side'] == 'SHORT']
                    print(f"🟢 LONG сделок: {len(long_trades)}")
                    print(f"🔴 SHORT сделок: {len(short_trades)}")

                    long_profit = sum(t['profit'] for t in long_trades)
                    short_profit = sum(t['profit'] for t in short_trades)
                    print(f"💰 Прибыль LONG: {long_profit:+.2f}₽")
                    print(f"💰 Прибыль SHORT: {short_profit:+.2f}₽")

                print(f"🎯 Win Rate: {result['win_rate']:.1f}%")
                print(f"💰 Общая прибыль: {result['total_profit']:+.2f}₽ ({result['total_return']:+.2f}%)")
                print(f"💵 Финальный баланс: {result['final_balance']:,.2f}₽")

                try:
                    bt.plot_equity_curve(ticker)
                except Exception:
                    pass
    else:
        print("=" * 70)
        print("📋 ИСПОЛЬЗОВАНИЕ:")
        print("=" * 70)
        print()
        print("  python -m trading_bot.backtest.backtest_runner GAZP 90           # обычный тест")
        print("  python -m trading_bot.backtest.backtest_runner GAZP 90 optimize  # с оптимизацией")
        print()
        print("Примеры:")
        print("  python -m trading_bot.backtest.backtest_runner SBER 60")
        print("  python -m trading_bot.backtest.backtest_runner LKOH 90")
        print("  python -m trading_bot.backtest.backtest_runner TATN 120 optimize")