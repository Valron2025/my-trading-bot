#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Универсальная оптимизация параметров с учётом капитала"""

import time
import warnings
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from itertools import product
import pandas as pd

warnings.filterwarnings('ignore')

from .backtest import ProfessionalBacktester


class UnifiedParameterOptimizer:
    """Универсальный оптимизатор с учётом капитала и комиссий"""

    def __init__(self, ticker: str, capital: float = 50000, days: int = 90):
        self.ticker = ticker.upper()
        self.capital = capital
        self.days = days
        self.results = []
        self.best_params = None
        self.best_result = None
        self.best_score = -float('inf')
        self.cached_data = None

        # Получаем комиссию
        from trading_bot.api.tbank_client import tbank
        tariff_name, commission = tbank.get_user_tariff()
        self.commission = commission
        print(f"📊 Комиссия: {commission * 100:.3f}% (тариф: {tariff_name})")

        Path("optimization_results").mkdir(exist_ok=True)
        Path("backtest_results").mkdir(exist_ok=True)

    def _get_adaptive_param_grid(self) -> Dict[str, List]:
        """Адаптивная сетка параметров в зависимости от капитала"""
        base_params = {
            'min_confidence': [0, 1, 2],
            'max_positions': [1, 2, 3, 4],  # ← здесь определён
            'trailing_stop_pct': [0.1, 0.2, 0.3, 0.4, 0.5]
        }

        if self.capital < 5000:
            return {**base_params,
                'risk_per_trade': [0.03, 0.04, 0.05],
                'take_profit_pct': [1.2, 1.5, 1.8, 2.0],
                'stop_loss_pct': [0.6, 0.7, 0.8, 0.9],
                'min_trade_amount': [150, 200, 250],
                'max_positions': [1, 2]
            }
        elif self.capital < 15000:
            return {**base_params,
                'risk_per_trade': [0.05, 0.06, 0.07, 0.08],
                'take_profit_pct': [0.8, 1.0, 1.2, 1.5],
                'stop_loss_pct': [0.4, 0.5, 0.6, 0.7],
                'min_trade_amount': [200, 300, 400],
                'max_positions': [1, 2, 3]
            }
        elif self.capital < 50000:
            return {**base_params,
                'risk_per_trade': [0.06, 0.08, 0.10, 0.12],
                'take_profit_pct': [0.6, 0.8, 1.0, 1.2],
                'stop_loss_pct': [0.3, 0.4, 0.5, 0.6],
                'min_trade_amount': [300, 400, 500, 600],
                'max_positions': [2, 3, 4]
            }
        else:
            return {**base_params,
                'risk_per_trade': [0.08, 0.10, 0.12, 0.15, 0.18],
                'take_profit_pct': [0.4, 0.5, 0.6, 0.8],
                'stop_loss_pct': [0.2, 0.25, 0.3, 0.4],
                'min_trade_amount': [500, 800, 1000, 1500],
                'max_positions': [3, 4, 5],
                'timeout_minutes': [3, 4, 5, 6, 8, 10]
            }

    def _calculate_score(self, result: Dict, params: Dict) -> float:
        """Расчёт оценки с учётом капитала и комиссий"""
        profit_factor = result.get('profit_factor', 0)
        win_rate = result.get('win_rate', 0)
        total_return = result.get('total_return', 0)
        total_trades = result.get('total_trades', 0)
        avg_win = result.get('avg_win', 0)
        avg_loss = result.get('avg_loss', 0)
        sharpe = result.get('sharpe_ratio', 0)

        score = (profit_factor * 25 + win_rate * 2.5 + total_return * 12 + sharpe * 30)

        if profit_factor > 1.5:
            score *= 1.2
        if win_rate > 60:
            score *= 1.1
        if total_trades >= 20:
            score *= 1.08
        elif total_trades >= 10:
            score *= 1.04

        if avg_loss != 0:
            profit_ratio = avg_win / abs(avg_loss)
            if profit_ratio > 2:
                score *= 1.15
            elif profit_ratio > 1.5:
                score *= 1.07

        if self.commission > 0.0005:
            if total_trades < 15:
                score *= 1.05
            tp = params.get('take_profit_pct', 1.0)
            if tp > 1.0:
                score *= 1.03

        if total_return < 0:
            score *= 0.3
        elif total_return < 3:
            score *= 0.7

        if result.get('max_drawdown', 0) > 15:
            score *= 0.7

        if self.capital < 10000:
            min_trade = params.get('min_trade_amount', 200)
            if min_trade > self.capital * 0.1:
                score *= 0.6
            elif min_trade > self.capital * 0.05:
                score *= 0.85

        return score

    def _load_cached_data(self) -> Optional[pd.DataFrame]:
        """Загрузка и кэширование данных"""
        if self.cached_data is None:
            temp_backtester = ProfessionalBacktester(
                initial_balance=self.capital,
                days=self.days,
                commission_pct=self.commission
            )
            self.cached_data = temp_backtester.load_historical_data(self.ticker)
        return self.cached_data

    def optimize(self, verbose: bool = True) -> Optional[Dict[str, Any]]:
        """Основной процесс оптимизации"""
        print(f"\n{'=' * 70}")
        print(f"🔧 ОПТИМИЗАЦИЯ ДЛЯ {self.ticker}")
        print(f"   Капитал: {self.capital:,.0f}₽")
        print(f"   Период: {self.days} дней")
        print(f"   Начало: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 70}\n")

        param_grid = self._get_adaptive_param_grid()

        total = 1
        for v in param_grid.values():
            total *= len(v)
        print(f"📊 Тестируется {total} комбинаций параметров...\n")

        cached_df = self._load_cached_data()
        if cached_df is None:
            print(f"❌ Нет данных для {self.ticker}")
            return None

        print(f"✅ Данные загружены: {len(cached_df)} свечей\n")

        i = 0
        keys = list(param_grid.keys())
        values = [param_grid[k] for k in keys]

        for combo in product(*values):
            i += 1
            params = dict(zip(keys, combo))

            if 'stop_loss_pct' in params and 'take_profit_pct' in params:
                if params['stop_loss_pct'] >= params['take_profit_pct']:
                    continue

            if verbose and (i % 20 == 0 or i == total):
                print(f"   Прогресс: {i}/{total} ({i / total * 100:.1f}%)")

            time.sleep(0.3)

            backtester = ProfessionalBacktester(
                initial_balance=self.capital,
                risk_per_trade=params.get('risk_per_trade', 0.1),
                min_confidence=params.get('min_confidence', 1),
                take_profit_pct=params.get('take_profit_pct', 1.0),
                stop_loss_pct=params.get('stop_loss_pct', 0.5),
                max_positions=params.get('max_positions', 2),
                warmup_candles=30,
                days=self.days,
                commission_pct=self.commission
            )

            backtester.historical_data = cached_df.copy()
            result = backtester.run_single(self.ticker)

            if 'error' not in result and result.get('total_trades', 0) >= 5:
                score = self._calculate_score(result, params)
                result['score'] = score
                result['params'] = params
                self.results.append(result)

                if score > self.best_score:
                    self.best_score = score
                    self.best_params = params
                    self.best_result = result

                    if verbose:
                        print(f"\n   🏆 [{i}/{total}] НОВЫЙ ЛУЧШИЙ! Score: {score:.2f}")
                        print(f"      Сделок: {result['total_trades']} | Win Rate: {result['win_rate']:.1f}%")
                        print(f"      P&L: {result['total_profit']:+.2f}₽ ({result['total_return']:+.1f}%)")
                        print(f"      PF: {result['profit_factor']:.2f}")

        return self._get_best_parameters()

    def _get_best_parameters(self) -> Optional[Dict[str, Any]]:
        """Вывод лучших параметров"""
        if not self.best_params:
            print("\n❌ Не найдено успешных комбинаций параметров")
            return None

        print(f"\n{'=' * 70}")
        print(f"🏆 ЛУЧШИЕ ПАРАМЕТРЫ ДЛЯ {self.ticker}")
        print(f"   Капитал: {self.capital:,.0f}₽")
        print(f"{'=' * 70}")

        print(f"\n✅ ОСНОВНЫЕ ПАРАМЕТРЫ:")
        if 'risk_per_trade' in self.best_params:
            print(f"   Риск на сделку: {self.best_params['risk_per_trade'] * 100:.0f}%")
        print(f"   Мин. уверенность: score ≥ {self.best_params.get('min_confidence', 0)}")
        print(f"   Тейк-профит: +{self.best_params.get('take_profit_pct', 0):.1f}%")
        print(f"   Стоп-лосс: -{self.best_params.get('stop_loss_pct', 0):.1f}%")
        print(f"   Макс. позиций: {self.best_params.get('max_positions', 0)}")

        if 'min_trade_amount' in self.best_params:
            print(f"   Мин. сумма сделки: {self.best_params['min_trade_amount']:.0f}₽")

        if self.best_result:
            print(f"\n📊 ДОСТИГНУТЫЕ РЕЗУЛЬТАТЫ:")
            print(f"   Сделок: {self.best_result['total_trades']}")
            print(f"   Win Rate: {self.best_result['win_rate']:.1f}%")
            print(f"   Прибыль: {self.best_result['total_profit']:+.2f}₽")
            print(f"   Доходность: {self.best_result['total_return']:+.2f}%")
            print(f"   Profit Factor: {self.best_result['profit_factor']:.2f}")
            print(f"   Score: {self.best_score:.2f}")

        self._save_results()
        return {
            'best_params': self.best_params,
            'best_result': self.best_result,
            'best_score': self.best_score,
            'risk_level': self._get_risk_level()
        }

    def _save_results(self):
        """Сохранение результатов"""
        try:
            output = {
                'ticker': self.ticker,
                'capital': self.capital,
                'days': self.days,
                'commission': self.commission,
                'timestamp': datetime.now().isoformat(),
                'best_params': self.best_params,
                'best_result': {k: v for k, v in self.best_result.items() if k not in ['params', 'score']} if self.best_result else None,
                'best_score': self.best_score,
                'risk_level': self._get_risk_level(),
                'top_10_results': [
                    {
                        'score': r.get('score'),
                        'params': r.get('params'),
                        'total_trades': r.get('total_trades'),
                        'win_rate': r.get('win_rate'),
                        'profit_factor': r.get('profit_factor'),
                        'total_return': r.get('total_return')
                    }
                    for r in sorted(self.results, key=lambda x: x.get('score', -999), reverse=True)[:10]
                ]
            }

            filename = f"optimization_results/{self.ticker}_optimization_{self.capital:.0f}_{self.days}d.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(output, f, indent=2, default=str, ensure_ascii=False)

            print(f"\n💾 Результаты сохранены: {filename}")

        except Exception as e:
            print(f"⚠️ Ошибка сохранения: {e}")

    def _get_risk_level(self) -> str:
        if not self.best_params:
            return "НЕ ОПРЕДЕЛЁН"
        risk = self.best_params.get('risk_per_trade', 0)
        if risk > 0.12:
            return "АГРЕССИВНЫЙ"
        elif risk > 0.08:
            return "УМЕРЕННЫЙ"
        else:
            return "КОНСЕРВАТИВНЫЙ"

    def _save_optimization_history(self):
        """Сохранение полной истории оптимизации"""
        from pathlib import Path
        import pandas as pd

        history_file = Path(f"optimization_results/{self.ticker}_history.csv")
        history_file.parent.mkdir(exist_ok=True)

        if self.results:
            df = pd.DataFrame(self.results)
            df.to_csv(history_file, index=False)
            print(f"💾 История оптимизации: {history_file}")


def optimize_with_validation(ticker: str, capital: float = 50000, days: int = 90):
    """Оптимизация с валидацией на out-of-sample данных"""
    print(f"\n{'=' * 70}")
    print(f"🎯 ОПТИМИЗАЦИЯ С ВАЛИДАЦИЕЙ ДЛЯ {ticker}")
    print(f"{'=' * 70}")

    train_days = int(days * 0.7)
    optimizer = UnifiedParameterOptimizer(ticker, capital, train_days)
    train_result = optimizer.optimize()

    if not train_result:
        return None

    print(f"\n{'=' * 70}")
    print(f"🔍 ВАЛИДАЦИЯ НА ОСТАВШИХСЯ {days - train_days} ДНЯХ")
    print(f"{'=' * 70}")

    test_backtester = ProfessionalBacktester(
        initial_balance=capital,
        **train_result['best_params'],
        days=days,
        commission_pct=optimizer.commission
    )

    test_result = test_backtester.run_single(ticker)

    print(f"\n📊 РЕЗУЛЬТАТЫ ВАЛИДАЦИИ:")
    print(f"   Сделок: {test_result.get('total_trades', 0)}")
    print(f"   Win Rate: {test_result.get('win_rate', 0):.1f}%")
    print(f"   Прибыль: {test_result.get('total_profit', 0):+.2f}₽")
    print(f"   Profit Factor: {test_result.get('profit_factor', 0):.2f}")

    return {
        'train': train_result,
        'test': test_result,
        'validation_passed': test_result.get('profit_factor', 0) > 1.1
    }


def run_optimization_for_portfolio(tickers: List[str], days: int = 90) -> Dict:
    """Запуск оптимизации для портфеля тикеров"""
    print(f"\n{'=' * 70}")
    print(f"🚀 ЗАПУСК ОПТИМИЗАЦИИ ДЛЯ ПОРТФЕЛЯ")
    print(f"{'=' * 70}")

    results = {}

    for ticker in tickers:
        print(f"\n📊 ОПТИМИЗАЦИЯ ДЛЯ {ticker}")
        optimizer = UnifiedParameterOptimizer(ticker, capital=50000, days=days)
        result = optimizer.optimize()
        if result:
            results[ticker] = result

            print(f"\n✅ Рекомендации для {ticker}:")
            params = result['best_params']
            print(f"   Риск: {params.get('risk_per_trade', 0) * 100:.0f}%")
            print(f"   TP/SL: +{params.get('take_profit_pct', 0):.1f}%/-{params.get('stop_loss_pct', 0):.1f}%")

    output_file = Path("optimization_results/portfolio_recommendations.json")
    output_file.parent.mkdir(exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, default=str, ensure_ascii=False)

    print(f"\n💾 Результаты сохранены: {output_file}")

    return results


# Для обратной совместимости
ParameterOptimizer = UnifiedParameterOptimizer


if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("📊 УНИВЕРСАЛЬНЫЙ ОПТИМИЗАТОР ПАРАМЕТРОВ")
    print("=" * 70)

    if len(sys.argv) > 1:
        ticker = sys.argv[1]
        capital = float(sys.argv[2]) if len(sys.argv) > 2 else 50000
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 90
        validate = '--validate' in sys.argv
        portfolio = '--portfolio' in sys.argv

        if portfolio:
            tickers = ticker.split(',') if ',' in ticker else [ticker]
            run_optimization_for_portfolio(tickers, days)
        elif validate:
            optimize_with_validation(ticker, capital, days)
        else:
            optimizer = UnifiedParameterOptimizer(ticker, capital, days)
            optimizer.optimize()
    else:
        print("""
Использование:
  python -m trading_bot.backtest.parameter_optimizer GAZP 5000 90
  python -m trading_bot.backtest.parameter_optimizer SBER 15000 60 --validate
  python -m trading_bot.backtest.parameter_optimizer GAZP,SBER,YNDX 50000 90 --portfolio

Параметры:
  ticker    - тикер акции или список через запятую
  capital   - капитал в рублях
  days      - дней истории (по умолчанию 90)
  --validate- добавить валидацию на out-of-sample данных
  --portfolio- оптимизация для портфеля тикеров

Примеры:
  python -m trading_bot.backtest.parameter_optimizer SBER 5000 90
  python -m trading_bot.backtest.parameter_optimizer GAZP 150000 60 --validate
  python -m trading_bot.backtest.parameter_optimizer GAZP,SBER,YNDX 100000 60 --portfolio
        """)