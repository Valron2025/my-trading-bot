#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Быстрое тестирование разных стратегий"""

import sys
import warnings
from datetime import time as dt_time
import pandas as pd

warnings.filterwarnings('ignore')

# ========== СНАЧАЛА ВСЕ ЗАГЛУШКИ ==========
import types

# Список всех модулей, которые нужно замокать
modules_to_mock = [
    'tbank_client',
    'telegram_notifier',
    'logger',
    'market_analyzer',
    'position_manager',
    'moex_client'
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

    elif module_name == 'logger':
        def mock_info(msg): pass
        def mock_success(msg): pass
        def mock_error(msg, exc_info=False): pass
        def mock_warning(msg): pass
        def mock_debug(msg): pass

        mock_module.info = mock_info
        mock_module.success = mock_success
        mock_module.error = mock_error
        mock_module.warning = mock_warning
        mock_module.debug = mock_debug

        mock_module.bomb = types.SimpleNamespace()
        mock_module.bomb.success = mock_success
        mock_module.bomb.info = mock_info
        mock_module.bomb.error = mock_error
        mock_module.bomb.warning = mock_warning
        mock_module.bomb.debug = mock_debug

    elif module_name == 'market_analyzer':
        mock_module.market_analyzer = types.SimpleNamespace()
        mock_module.market_analyzer.analyze_market_conditions = lambda: None

    elif module_name == 'position_manager':
        mock_module.position_manager = types.SimpleNamespace()
        mock_module.position_manager.add_pending_order = lambda **x: None
        mock_module.position_manager.is_temp_blacklisted = lambda x: False
        mock_module.position_manager.is_temp_skipped = lambda x: False

    sys.modules[module_name] = mock_module

# Дополнительно замокаем config
mock_config = types.ModuleType('config')
mock_config.config = types.SimpleNamespace()
mock_config.config.take_profit_pct = 1.5
mock_config.config.stop_loss_pct = 0.8
mock_config.config.trailing_stop_pct = 0.5
mock_config.config.adaptive_timeout_minutes = 20
sys.modules['config'] = mock_config

# Теперь импортируем бэктестер
from .backtest import ProfessionalBacktester

# Разные стратегии для тестирования
strategies = [
    ("АГРЕССИВНАЯ", 0.15, 0, 1.0, 0.5, 3),
    ("СТАНДАРТНАЯ", 0.10, 1, 1.5, 0.8, 2),
    ("КОНСЕРВАТИВНАЯ", 0.05, 2, 2.0, 1.0, 1),
    ("СКАЛЬПИНГ", 0.10, 0, 0.6, 0.3, 4),
    ("ТРЕНДОВАЯ", 0.10, 2, 2.5, 1.2, 2),
    ("СБАЛАНСИРОВАННАЯ", 0.08, 1, 1.2, 0.6, 2),
    ("СУПЕР-АГРЕССИВНАЯ", 0.20, 0, 0.8, 0.4, 4),
    ("МИКРО-СКАЛЬПИНГ", 0.05, 0, 0.4, 0.2, 5),
]


def test_strategies(ticker: str = "GAZP", days: int = 90) -> list:
    """
    Тестирование разных стратегий для указанного тикера

    Args:
        ticker: Тикер акции (SBER, GAZP, etc.)
        days: Количество дней истории

    Returns:
        list: Результаты тестирования стратегий
    """
    print("=" * 70)
    print(f"🔬 ТЕСТИРОВАНИЕ РАЗНЫХ СТРАТЕГИЙ ДЛЯ {ticker}")
    print("=" * 70)

    results = []

    for name, risk, min_conf, tp, sl, max_pos in strategies:
        print(f"\n{'=' * 50}")
        print(f"📊 {name}")
        print(f"   Риск: {risk * 100:.0f}% | Мин.увер.: {min_conf} | TP: +{tp}% | SL: -{sl}% | Макс.поз.: {max_pos}")
        print(f"{'=' * 50}")

        bt = ProfessionalBacktester(
            initial_balance=100000,
            risk_per_trade=risk,
            min_confidence=min_conf,
            take_profit_pct=tp,
            stop_loss_pct=sl,
            max_positions=max_pos,
            warmup_candles=20,
            days=days
        )

        result = bt.run_single(ticker)

        if 'error' not in result:
            results.append({
                'name': name,
                'trades': result['total_trades'],
                'win_rate': result['win_rate'],
                'profit': result['total_profit'],
                'return_pct': result['total_return'],
                'profit_factor': result.get('profit_factor', 0),
                'final_balance': result['final_balance']
            })
            print(
                f"\n✅ Результат: {result['total_trades']} сделок, Win Rate: {result['win_rate']:.1f}%, Прибыль: {result['total_profit']:+.2f}₽")
        else:
            print(f"\n❌ Ошибка: {result.get('error', 'Unknown')}")

    # Вывод лучших
    print("\n" + "=" * 70)
    print("🏆 РЕЙТИНГ СТРАТЕГИЙ (по прибыли)")
    print("=" * 70)

    if results:
        results.sort(key=lambda x: x['profit'], reverse=True)

        for i, r in enumerate(results, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            print(f"\n{medal} {r['name']}:")
            print(f"   📈 Сделок: {r['trades']} | Win Rate: {r['win_rate']:.1f}%")
            print(f"   💰 Прибыль: {r['profit']:+,.2f}₽ ({r['return_pct']:+.2f}%)")
            print(f"   ⚡ Profit Factor: {r['profit_factor']:.2f}")
            print(f"   💵 Финальный баланс: {r['final_balance']:,.2f}₽")

        # Вывод лучшей стратегии
        best = results[0]
        print("\n" + "=" * 70)
        print(f"🎯 РЕКОМЕНДУЕМЫЕ ПАРАМЕТРЫ:")
        print("=" * 70)

        for s in strategies:
            if s[0] == best['name']:
                print(f"   Стратегия: {best['name']}")
                print(f"   Риск на сделку: {s[1] * 100:.0f}%")
                print(f"   Мин. уверенность: {s[2]}")
                print(f"   Тейк-профит: +{s[3]}%")
                print(f"   Стоп-лосс: -{s[4]}%")
                print(f"   Макс. позиций: {s[5]}")
                break
    else:
        print("\n❌ Нет успешных результатов")

    return results


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "GAZP"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    test_strategies(ticker, days)