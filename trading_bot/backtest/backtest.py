#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Профессиональный бэктестер с автоматической оптимизацией параметров"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import requests
import warnings
import time
from itertools import product
import pandas as pd

from ..risk.capital_manager import CapitalManager
from ..logger import info, success, warning
from trading_bot.config import config
from trading_bot.models import StockAnalysis
from trading_bot.analysis.strategy_engine import StrategyEngine

warnings.filterwarnings('ignore')

# Проверка наличия matplotlib для графиков
try:
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("⚠️ Для графиков установите: pip install matplotlib")

_COMMISSION_CACHE = None


def get_cached_commission():
    """Кэшированное получение комиссии (один раз)"""
    global _COMMISSION_CACHE
    if _COMMISSION_CACHE is None:
        try:
            from trading_bot.api.tbank_client import tbank
            tariff_name, auto_commission = tbank.get_user_tariff()
            _COMMISSION_CACHE = auto_commission
            print(f"📊 Комиссия зафиксирована: {_COMMISSION_CACHE * 100:.3f}% (тариф: {tariff_name})")
        except Exception as e:
            _COMMISSION_CACHE = 0.003
            print(f"⚠️ Используем комиссию по умолчанию: 0.3%")
    return _COMMISSION_CACHE


class AdvancedBacktester:
    """Продвинутый бэктестер с учётом динамического управления капиталом"""

    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.capital_manager = CapitalManager(initial_capital)
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = [initial_capital]
        self.historical_data = None

        self.commission_pct = 0.0005
        self.slippage_pct = 0.0001

        self.stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_profit': 0,
            'total_loss': 0,
            'max_drawdown': 0,
            'max_drawdown_pct': 0,
            'sharpe_ratio': 0,
            'win_rate': 0,
            'profit_factor': 0,
            'avg_win': 0,
            'avg_loss': 0
        }

        info(f"📊 AdvancedBacktester: капитал {initial_capital:.0f}₽")

    def run_backtest(
            self,
            signals: List[Dict],
            prices: List[float],
            volumes: List[int],
            timestamps: List[datetime]
    ) -> Dict:
        """Запуск бэктеста с динамическим управлением капиталом"""
        self.trades = []
        self.equity_curve = [self.initial_capital]

        position = None
        position_size = 0
        entry_price = 0
        entry_time = None
        side = None

        for i, signal in enumerate(signals):
            current_price = prices[i]
            current_time = timestamps[i]

            if signal['action'] in ['BUY', 'SELL'] and position is None:
                score = abs(signal.get('score', 3))
                volatility = self._calculate_volatility(prices[max(0, i - 20):i + 1])

                position_config = self.capital_manager.calculate_position_size(
                    score=score,
                    volatility=volatility,
                    is_short=(signal['action'] == 'SELL')
                )

                position_amount = position_config['amount']
                quantity = int(position_amount / current_price / 100) * 100

                if quantity > 0:
                    side = 'SHORT' if signal['action'] == 'SELL' else 'LONG'
                    position_size = quantity
                    entry_price = current_price
                    entry_time = current_time

                    commission = position_size * current_price * self.commission_pct
                    self.capital_manager.current_capital -= commission
                    info(f"  Вход {side}: {position_size} шт по {current_price:.2f}₽")

            elif position is not None and self._should_close(signal, position, current_price):
                if side == 'LONG':
                    pnl = (current_price - entry_price) * position_size
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:
                    pnl = (entry_price - current_price) * position_size
                    pnl_pct = (entry_price - current_price) / entry_price * 100

                commission = position_size * current_price * self.commission_pct
                commission_slippage = position_size * current_price * self.slippage_pct
                total_costs = commission + commission_slippage
                net_pnl = pnl - total_costs

                self.trades.append({
                    'entry_time': entry_time,
                    'exit_time': current_time,
                    'side': side,
                    'quantity': position_size,
                    'entry_price': entry_price,
                    'exit_price': current_price,
                    'pnl': net_pnl,
                    'pnl_pct': pnl_pct,
                    'hold_minutes': (current_time - entry_time).total_seconds() / 60
                })

                self.capital_manager.record_trade(pnl_pct, net_pnl)
                info(f"  Выход {side}: {position_size} шт по {current_price:.2f}₽ | P&L: {net_pnl:+.2f}₽ ({pnl_pct:+.1f}%)")

                position = None
                position_size = 0

            current_equity = self.capital_manager.current_capital
            if position_size > 0:
                unrealized = (current_price - entry_price) * position_size if side == 'LONG' else (entry_price - current_price) * position_size
                current_equity += unrealized

            self.equity_curve.append(current_equity)

        self._calculate_stats()
        return self.stats

    def _should_close(self, signal: Dict, position: str, current_price: float) -> bool:
        if signal['action'] == 'HOLD':
            return False
        if position == 'LONG' and signal['action'] == 'SELL':
            return True
        if position == 'SHORT' and signal['action'] == 'BUY':
            return True
        return False

    def _calculate_volatility(self, prices: List[float]) -> float:
        if len(prices) < 2:
            return 0.01
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        return np.std(returns) if returns else 0.01

    def _calculate_stats(self):
        if not self.trades:
            return

        total_trades = len(self.trades)
        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        losing_trades = [t for t in self.trades if t['pnl'] < 0]

        self.stats['total_trades'] = total_trades
        self.stats['winning_trades'] = len(winning_trades)
        self.stats['losing_trades'] = len(losing_trades)
        self.stats['win_rate'] = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
        self.stats['total_profit'] = sum(t['pnl'] for t in winning_trades) if winning_trades else 0
        self.stats['total_loss'] = abs(sum(t['pnl'] for t in losing_trades)) if losing_trades else 0
        self.stats['profit_factor'] = self.stats['total_profit'] / self.stats['total_loss'] if self.stats['total_loss'] > 0 else float('inf')
        self.stats['avg_win'] = self.stats['total_profit'] / len(winning_trades) if winning_trades else 0
        self.stats['avg_loss'] = self.stats['total_loss'] / len(losing_trades) if losing_trades else 0

        peak = self.equity_curve[0]
        max_dd_pct = 0
        for value in self.equity_curve:
            if value > peak:
                peak = value
            dd_pct = ((peak - value) / peak) * 100 if peak > 0 else 0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        self.stats['max_drawdown_pct'] = max_dd_pct
        self.stats['final_capital'] = self.equity_curve[-1]
        self.stats['total_return'] = (self.equity_curve[-1] - self.initial_capital) / self.initial_capital * 100

        success(f"\n📊 РЕЗУЛЬТАТЫ БЭКТЕСТА:")
        success(f"   📈 Сделок: {self.stats['total_trades']}")
        success(f"   🎯 Win Rate: {self.stats['win_rate']:.1f}%")
        success(f"   💰 Общая прибыль: {self.stats['total_profit']:.2f}₽")
        success(f"   ⚡ Profit Factor: {self.stats['profit_factor']:.2f}")
        success(f"   📈 Доходность: {self.stats['total_return']:+.1f}%")
        success(f"   📉 Макс. просадка: {self.stats['max_drawdown_pct']:.1f}%")

    def get_report(self) -> str:
        report = [
            "=" * 60,
            "📊 ОТЧЁТ БЭКТЕСТА",
            "=" * 60,
            f"Начальный капитал: {self.initial_capital:.0f}₽",
            f"Конечный капитал: {self.stats.get('final_capital', self.initial_capital):.0f}₽",
            f"Доходность: {self.stats.get('total_return', 0):+.1f}%",
            "",
            "📈 СТАТИСТИКА СДЕЛОК:",
            f"   Всего сделок: {self.stats.get('total_trades', 0)}",
            f"   Win Rate: {self.stats.get('win_rate', 0):.1f}%",
            f"   Profit Factor: {self.stats.get('profit_factor', 0):.2f}",
            "",
            "📊 РИСК-МЕНЕДЖМЕНТ:",
            f"   Макс. просадка: {self.stats.get('max_drawdown_pct', 0):.1f}%",
            "=" * 60
        ]
        return "\n".join(report)


class ProfessionalBacktester:
    """Профессиональный бэктестер с автоподбором параметров"""

    def __init__(self,
                 initial_balance: float = 100000,
                 risk_per_trade: float = 0.1,
                 min_confidence: int = 0,
                 take_profit_pct: Optional[float] = None,
                 stop_loss_pct: Optional[float] = None,
                 max_positions: int = 2,
                 commission_pct: Optional[float] = None,
                 slippage_pct: float = 0.0001,
                 warmup_candles: int = 30,
                 days: int = 90,
                 engine: StrategyEngine = None):

        self.initial_balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.min_confidence = min_confidence
        self.take_profit_pct = take_profit_pct or config.take_profit_pct
        self.stop_loss_pct = stop_loss_pct or config.stop_loss_pct
        self.max_positions = max_positions

        self.commission_pct = commission_pct if commission_pct is not None else get_cached_commission()
        self.slippage_pct = slippage_pct
        self.warmup_candles = warmup_candles
        self.days = days

        self.balance = initial_balance
        self.positions: List[Dict] = []
        self.trades: List[Dict] = []
        self.equity_curve: List[Dict] = []

        Path("backtest_results").mkdir(exist_ok=True)

        if engine:
            self.engine = engine
        else:
            engine_config = {
                'score_threshold_long': min_confidence if min_confidence > 0 else 0,
                'score_threshold_short': -min_confidence if min_confidence > 0 else 10,
                'take_profit_pct': take_profit_pct or config.take_profit_pct,
                'stop_loss_pct': stop_loss_pct or config.stop_loss_pct,
                'position_size_pct': risk_per_trade,
                'max_hold_minutes': 20
            }
            self.engine = StrategyEngine(engine_config)

        self.historical_data = None

        print(f"📊 Комиссия: {self.commission_pct * 100:.3f}%")
        print(f"📊 Проскальзывание: {self.slippage_pct * 100:.2f}%")

    def _fetch_candles_moex(self, ticker: str, interval_minutes: int = 5, days: int = 90) -> Optional[pd.DataFrame]:
        ticker = ticker.upper()
        interval_map = {1: 1, 5: 5, 10: 10, 15: 15, 30: 30, 60: 60}
        moex_interval = interval_map.get(interval_minutes, 5)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/tqbr/securities/{ticker}/candles.json"
        params = {
            'interval': moex_interval,
            'from': start_date.strftime("%Y-%m-%d"),
            'till': end_date.strftime("%Y-%m-%d"),
            'start': 0
        }

        try:
            time.sleep(0.2)
            response = requests.get(url, params=params, timeout=30)
            if response.status_code != 200:
                return None

            data = response.json()
            candles_data = data.get('candles', {})
            columns = candles_data.get('columns', [])
            rows = candles_data.get('data', [])

            if not columns or not rows:
                return None

            try:
                close_idx = columns.index('close')
                volume_idx = columns.index('volume')
            except ValueError:
                return None

            candles = []
            for row in rows:
                if len(row) > max(close_idx, volume_idx):
                    close = float(row[close_idx]) if row[close_idx] else 0
                    volume = float(row[volume_idx]) if row[volume_idx] else 0
                    if close > 0:
                        candles.append((close, volume))

            if not candles:
                return None

            df = pd.DataFrame(candles, columns=['close', 'volume'])
            freq_map = {1: '1min', 5: '5min', 15: '15min', 30: '30min', 60: '1h'}
            freq = freq_map.get(interval_minutes, '5min')
            df['time'] = pd.date_range(start=start_date, periods=len(df), freq=freq)
            df['open'] = df['close'].shift(1).fillna(df['close'])
            df['high'] = df[['open', 'close']].max(axis=1)
            df['low'] = df[['open', 'close']].min(axis=1)
            return df
        except Exception:
            return None

    def load_historical_data(self, ticker: str) -> Optional[pd.DataFrame]:
        # Используем кэш если есть
        if self.historical_data is not None:
            return self.historical_data

        print(f"📡 Запрос данных MOEX для {ticker} ({self.days} дней)...")
        for interval in [15, 5, 1]:
            df = self._fetch_candles_moex(ticker, interval_minutes=interval, days=self.days)
            if df is not None and len(df) >= self.warmup_candles + 20:
                print(f"✅ MOEX: {len(df)} свечей ({interval}мин)")
                self.historical_data = df  # <-- СОХРАНЯЕМ В КЭШ
                return df
        print(f"❌ Нет данных для {ticker}")
        return None

    def _calculate_position_size(self, price: float) -> int:
        min_trade_value = 5000
        position_value = max(self.balance * self.risk_per_trade, min_trade_value)
        return max(1, int(position_value / price))

    def _apply_slippage(self, price: float, side: str) -> float:
        if side == "BUY":
            return price * (1 + self.slippage_pct)
        return price * (1 - self.slippage_pct)

    def run_single(self, ticker: str) -> Dict[str, Any]:
        print(f"\n{'=' * 70}")
        print(f"🚀 БЭКТЕСТ {ticker}")
        print(f"   Баланс: {self.initial_balance:,.0f}₽ | Риск: {self.risk_per_trade * 100:.0f}%")
        print(f"   Тейк: +{self.take_profit_pct:.1f}% | Стоп: -{self.stop_loss_pct:.1f}%")
        print(f"   Мин. уверенность: {self.min_confidence}")
        print(f"   Макс. позиций: {self.max_positions}")
        print(f"{'=' * 70}\n")

        df = self.load_historical_data(ticker)
        if df is None or df.empty:
            return {"error": f"Нет данных для {ticker}"}

        self.balance = self.initial_balance
        self.positions = []
        self.trades = []
        self.equity_curve = []

        total_signals = 0
        rejected_by_balance = 0
        long_signals_count = 0
        short_signals_count = 0

        for i, row in df.iterrows():
            current_price = row['close']
            current_time = row['time']
            prices_history = df['close'].iloc[:i + 1].tolist()
            volumes_history = df['volume'].iloc[:i + 1].tolist() if 'volume' in df else [1] * len(prices_history)

            signal_result = self.engine.analyze_signal(prices_history, volumes_history, ticker)

            analysis = StockAnalysis(
                figi=ticker, name=ticker,
                score=signal_result.score,
                buy_signal=signal_result.buy_signal,
                sell_signal=signal_result.sell_signal,
                recommendation=signal_result.recommendation,
                signals=signal_result.signals,
                rsi=signal_result.rsi,
                macd=signal_result.macd,
                volume_ratio=signal_result.volume_ratio
            )

            current_equity = self.balance + sum(pos['quantity'] * current_price for pos in self.positions)
            self.equity_curve.append({'time': current_time, 'equity': current_equity, 'price': current_price})

            for pos in self.positions[:]:
                profit_pct = (current_price - pos['price']) / pos['price'] * 100 if pos['side'] == 'LONG' else (pos['price'] - current_price) / pos['price'] * 100
                if profit_pct <= -self.stop_loss_pct:
                    self._close_position(pos, current_price, current_time, "STOP")
                elif profit_pct >= self.take_profit_pct:
                    self._close_position(pos, current_price, current_time, "TAKE")

            if len(self.positions) < self.max_positions:
                has_long = analysis.buy_signal and analysis.score >= self.min_confidence
                has_short = analysis.sell_signal and analysis.score <= -self.min_confidence

                if has_long:
                    long_signals_count += 1
                if has_short:
                    short_signals_count += 1

                if has_long and has_short:
                    if abs(analysis.score) >= 2:
                        has_short = False
                    else:
                        has_short = False

                if has_long:
                    total_signals += 1
                    exec_price = self._apply_slippage(current_price, "BUY")
                    quantity = self._calculate_position_size(current_price)
                    cost = quantity * exec_price
                    total_cost = cost + cost * self.commission_pct

                    if total_cost <= self.balance:
                        self._open_position(ticker, current_price, current_time, "LONG", analysis)
                    else:
                        rejected_by_balance += 1

                elif has_short:
                    total_signals += 1
                    exec_price = self._apply_slippage(current_price, "SELL")
                    quantity = self._calculate_position_size(current_price)
                    cost = quantity * exec_price
                    total_cost = cost + cost * self.commission_pct

                    if total_cost <= self.balance:
                        self._open_position(ticker, current_price, current_time, "SHORT", analysis)
                    else:
                        rejected_by_balance += 1

        for pos in self.positions[:]:
            self._close_position(pos, df.iloc[-1]['close'], df.iloc[-1]['time'], "END")

        print(f"\n📊 Статистика сигналов:")
        print(f"   Всего сигналов: {total_signals}")
        print(f"   LONG: {long_signals_count}, SHORT: {short_signals_count}")
        print(f"   Отклонено по балансу: {rejected_by_balance}")

        stats = self._get_stats(ticker)
        self.export_results(ticker, stats)
        return stats

    def _open_position(self, ticker: str, price: float, time: datetime, side: str, analysis: StockAnalysis):
        quantity = self._calculate_position_size(price)
        exec_price = self._apply_slippage(price, "BUY" if side == "LONG" else "SELL")
        cost = quantity * exec_price
        commission = cost * self.commission_pct
        total_cost = cost + commission

        if total_cost <= self.balance:
            self.positions.append({
                'ticker': ticker, 'price': exec_price, 'quantity': quantity,
                'side': side, 'entry_time': time, 'score': analysis.score
            })
            self.balance -= total_cost
            print(f"🟢 {side}: {quantity} {ticker} @ {exec_price:.2f}₽ (score:{analysis.score})")
            print(f"   Стоимость={cost:.2f}, комиссия={commission:.2f}, списание={total_cost:.2f}")
            print(f"   Баланс был: {self.balance + total_cost:.2f}, стал: {self.balance:.2f}")

    def _close_position(self, pos: Dict, price: float, time: datetime, reason: str):
        exec_price = self._apply_slippage(price, "SELL" if pos['side'] == "LONG" else "BUY")
        close_value = exec_price * pos['quantity']
        commission_close = close_value * self.commission_pct

        if pos['side'] == 'LONG':
            gross_profit = (exec_price - pos['price']) * pos['quantity']
        else:
            gross_profit = (pos['price'] - exec_price) * pos['quantity']

        entry_commission = pos['price'] * pos['quantity'] * self.commission_pct
        total_commission = entry_commission + commission_close
        net_profit = gross_profit - total_commission

        self.balance += (close_value - commission_close)

        self.trades.append({
            'ticker': pos['ticker'], 'side': pos['side'],
            'entry': pos['price'], 'exit': exec_price,
            'profit': net_profit, 'profit_pct': net_profit / (pos['price'] * pos['quantity']) * 100,
            'reason': reason, 'score': pos['score']
        })

        self.positions.remove(pos)
        color = "🟢" if net_profit > 0 else "🔴"
        print(f"{color} {pos['side']} {pos['ticker']} {reason}: {net_profit:+.2f}₽")

    def _get_stats(self, ticker: str) -> Dict[str, Any]:
        if not self.trades:
            return {"error": "Нет сделок"}

        df_trades = pd.DataFrame(self.trades)
        winning = df_trades[df_trades['profit'] > 0]

        total_profit = df_trades['profit'].sum()
        total_return = total_profit / self.initial_balance * 100
        win_rate = len(winning) / len(df_trades) * 100 if len(df_trades) > 0 else 0

        gross_profit = winning['profit'].sum() if len(winning) > 0 else 0
        gross_loss = abs(df_trades[df_trades['profit'] < 0]['profit'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        long_trades = len(df_trades[df_trades['side'] == 'LONG'])
        short_trades = len(df_trades[df_trades['side'] == 'SHORT'])
        long_profit = df_trades[df_trades['side'] == 'LONG']['profit'].sum() if long_trades > 0 else 0
        short_profit = df_trades[df_trades['side'] == 'SHORT']['profit'].sum() if short_trades > 0 else 0

        print(f"\n{'=' * 70}")
        print(f"📊 РЕЗУЛЬТАТЫ БЭКТЕСТА ДЛЯ {ticker}")
        print(f"{'=' * 70}")
        print(f"📈 Сделок: {len(df_trades)} (LONG: {long_trades}, SHORT: {short_trades})")
        print(f"💰 LONG: {long_profit:+.2f}₽ | SHORT: {short_profit:+.2f}₽")
        print(f"🎯 Win Rate: {win_rate:.1f}%")
        print(f"💰 Прибыль: {total_profit:+,.2f}₽ ({total_return:+.1f}%)")
        print(f"⚡ Profit Factor: {profit_factor:.2f}")
        print(f"💰 Конечный баланс: {self.balance:,.2f}₽")
        print(f"{'=' * 70}")

        return {
            'ticker': ticker,
            'total_trades': len(df_trades),
            'long_trades': long_trades,
            'short_trades': short_trades,
            'long_profit': long_profit,
            'short_profit': short_profit,
            'win_rate': win_rate,
            'total_profit': total_profit,
            'total_return': total_return,
            'profit_factor': profit_factor,
            'final_balance': self.balance
        }

    def export_results(self, ticker: str, stats: Dict[str, Any]):
        if 'error' in stats:
            return

        Path("backtest_results").mkdir(exist_ok=True)

        if hasattr(self, 'trades') and self.trades:
            df_trades = pd.DataFrame(self.trades)
            for attempt in range(3):
                try:
                    df_trades.to_csv(f"backtest_results/{ticker}_trades.csv", index=False)
                    print(f"💾 Сохранено: backtest_results/{ticker}_trades.csv")
                    break
                except PermissionError:
                    print(f"⚠️ Файл занят, попытка {attempt + 1}/3...")
                    time.sleep(1)

        stats_df = pd.DataFrame([{
            'ticker': ticker,
            'total_trades': len(self.trades),
            'win_rate': stats.get('win_rate', 0),
            'total_profit': stats.get('total_profit', 0),
            'total_return': stats.get('total_return', 0),
            'profit_factor': stats.get('profit_factor', 0),
            'final_balance': stats.get('final_balance', 0)
        }])

        for attempt in range(3):
            try:
                stats_df.to_csv(f"backtest_results/{ticker}_stats.csv", index=False)
                print(f"💾 Статистика: backtest_results/{ticker}_stats.csv")
                break
            except PermissionError:
                print(f"⚠️ Файл stats.csv занят, попытка {attempt + 1}/3...")
                time.sleep(1)

    def plot_equity_curve(self, ticker: str):
        if not self.equity_curve:
            print("Нет данных equity curve")
            return

        if not MATPLOTLIB_AVAILABLE:
            print("⚠️ Установите matplotlib для графиков: pip install matplotlib")
            return

        try:
            df_eq = pd.DataFrame(self.equity_curve)
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

            ax1.plot(df_eq['time'], df_eq['equity'], 'g-', linewidth=1.5)
            ax1.axhline(y=self.initial_balance, color='gray', linestyle='--', alpha=0.5)
            ax1.set_title(f'Equity Curve - {ticker}')
            ax1.set_ylabel('Balance (₽)')
            ax1.grid(True, alpha=0.3)

            ax2.plot(df_eq['time'], df_eq['price'], 'b-', linewidth=1)
            ax2.set_title(f'Price - {ticker}')
            ax2.set_ylabel('Price (₽)')
            ax2.set_xlabel('Time')
            ax2.grid(True, alpha=0.3)

            plt.tight_layout()
            plt.savefig(f'backtest_results/{ticker}_equity.png')
            print(f"📈 График сохранён: backtest_results/{ticker}_equity.png")
            plt.show()
        except Exception as e:
            print(f"❌ Ошибка при построении графика: {e}")


class ParameterOptimizer:
    """Автоматическая оптимизация параметров бэктестера"""

    def __init__(self, ticker: str, days: int = 90):
        self.ticker = ticker
        self.days = days
        self.results = []

    def optimize(self) -> Dict[str, Any]:
        print(f"\n{'=' * 70}")
        print(f"🔧 ОПТИМИЗАЦИЯ ПАРАМЕТРОВ ДЛЯ {self.ticker}")
        print(f"   Период: {self.days} дней")
        print(f"{'=' * 70}")

        param_grid = {
            'risk_per_trade': [0.05, 0.1, 0.15],
            'min_confidence': [0, 1, 2, 3],
            'take_profit_pct': [0.5, 0.8, 1.0, 1.5, 2.0],
            'stop_loss_pct': [0.3, 0.5, 0.8, 1.0]
        }

        print("📡 Загрузка исторических данных...")
        temp_backtester = ProfessionalBacktester(days=self.days)
        cached_df = temp_backtester.load_historical_data(self.ticker)

        if cached_df is None:
            print(f"❌ Нет данных для {self.ticker}")
            return None

        print(f"✅ Данные загружены: {len(cached_df)} свечей\n")

        best_result = None
        best_score = -float('inf')

        total = len(param_grid['risk_per_trade']) * len(param_grid['min_confidence']) * \
                len(param_grid['take_profit_pct']) * len(param_grid['stop_loss_pct'])

        print(f"📊 Тестируется {total} комбинаций...\n")

        i = 0
        for risk, conf, tp, sl in product(
                param_grid['risk_per_trade'],
                param_grid['min_confidence'],
                param_grid['take_profit_pct'],
                param_grid['stop_loss_pct']
        ):
            i += 1

            if sl >= tp:
                print(f"⏭️ [{i}/{total}] Пропуск: SL={sl}% >= TP={tp}%")
                continue

            print(f"🔬 [{i}/{total}] риск={risk*100:.0f}% | score>={conf} | TP=+{tp}% | SL=-{sl}%")

            backtester = ProfessionalBacktester(
                initial_balance=100000,
                risk_per_trade=risk,
                min_confidence=conf,
                take_profit_pct=tp,
                stop_loss_pct=sl,
                max_positions=2,
                warmup_candles=30,
                days=self.days
            )

            original_load = backtester.load_historical_data
            backtester.load_historical_data = lambda x: cached_df.copy() if x == self.ticker else original_load(x)

            result = backtester.run_single(self.ticker)

            if 'error' not in result and result['total_trades'] >= 3:
                score = result['profit_factor'] * 20 + result['win_rate'] * 2 + result['total_return'] * 10

                if result['profit_factor'] > 1.5:
                    score *= 1.2
                if result['win_rate'] > 60:
                    score *= 1.1
                if result['total_trades'] >= 10:
                    score *= 1.05
                elif result['total_trades'] < 5:
                    score *= 0.9
                if result['total_return'] < 0:
                    score *= 0.5

                result['score'] = score
                result['params'] = {
                    'risk_per_trade': risk,
                    'min_confidence': conf,
                    'take_profit_pct': tp,
                    'stop_loss_pct': sl
                }
                self.results.append(result)

                if score > best_score:
                    best_score = score
                    best_result = result
                    self._save_best_params_to_file(result)
                    print(f"   🏆 НОВЫЙ ЛУЧШИЙ! Сделок: {result['total_trades']}, Win: {result['win_rate']:.1f}%, P&L: {result['total_profit']:+.2f}₽, Score: {score:.2f}")
                else:
                    print(f"   → Сделок: {result['total_trades']}, Win: {result['win_rate']:.1f}%, P&L: {result['total_profit']:+.2f}₽")
            elif 'error' not in result:
                print(f"   → ⚠️ Сделок: {result['total_trades']} (меньше 3)")
            else:
                print(f"   → ❌ {result['error']}")

        return self._get_best_parameters(best_result)

    def _save_best_params_to_file(self, best_result: Dict):
        try:
            import json
            params_file = Path("backtest_results/optimized_params.json")

            # ИСПРАВЛЕНО: правильное открытие файла
            all_params = {}
            if params_file.exists():
                with open(params_file, 'r', encoding='utf-8') as f:
                    all_params = json.load(f)

            all_params[self.ticker] = {
                'risk_per_trade': best_result['params']['risk_per_trade'],
                'min_confidence': best_result['params']['min_confidence'],
                'take_profit_pct': best_result['params']['take_profit_pct'],
                'stop_loss_pct': best_result['params']['stop_loss_pct'],
                'max_positions': best_result.get('max_positions', 2),
                'trailing_stop_pct': 0.3,
                'timestamp': datetime.now().isoformat(),
                'win_rate': best_result.get('win_rate', 0),
                'profit_factor': best_result.get('profit_factor', 0),
                'total_trades': best_result.get('total_trades', 0)
            }
            with open(params_file, 'w', encoding='utf-8') as f:
                json.dump(all_params, f, indent=2, ensure_ascii=False)
            print(f"💾 Параметры сохранены в backtest_results/optimized_params.json")
        except Exception as e:
            print(f"⚠️ Ошибка сохранения: {e}")

    def _get_best_parameters(self, best_result: Dict) -> Optional[Dict[str, Any]]:
        print(f"\n{'='*70}")
        print(f"🏆 ЛУЧШИЕ ПАРАМЕТРЫ ДЛЯ {self.ticker}")
        print(f"{'='*70}")

        if best_result:
            params = best_result['params']
            print(f"✅ Риск на сделку: {params['risk_per_trade']*100:.0f}%")
            print(f"✅ Мин. уверенность: score >= {params['min_confidence']}")
            print(f"✅ Тейк-профит: +{params['take_profit_pct']:.1f}%")
            print(f"✅ Стоп-лосс: -{params['stop_loss_pct']:.1f}%")
            print(f"\n📊 Достигнутые результаты:")
            print(f"   Сделок: {best_result['total_trades']}")
            print(f"   Win Rate: {best_result['win_rate']:.1f}%")
            print(f"   Прибыль: {best_result['total_profit']:+.2f}₽ ({best_result['total_return']:+.1f}%)")
            print(f"   Profit Factor: {best_result['profit_factor']:.2f}")
            return {'best_params': params, 'best_result': best_result, 'all_results': sorted(self.results, key=lambda x: x.get('score', -999), reverse=True)[:10]}
        else:
            print("❌ Не найдено успешных комбинаций")
            return None

    def export_results(self):
        if self.results:
            Path("backtest_results").mkdir(exist_ok=True)
            df = pd.DataFrame(self.results)
            df.to_csv(f"backtest_results/{self.ticker}_optimization.csv", index=False)
            print(f"\n💾 Результаты оптимизации: backtest_results/{self.ticker}_optimization.csv")


if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("🤖 АВТОМАТИЧЕСКАЯ ОПТИМИЗАЦИЯ БЭКТЕСТЕРА")
    print("=" * 70)

    if len(sys.argv) > 1:
        ticker = sys.argv[1]
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 90

        optimizer = ParameterOptimizer(ticker, days)
        best = optimizer.optimize()

        if best:
            optimizer.export_results()

            print(f"\n{'=' * 70}")
            print(f"🚀 ЗАПУСК С ЛУЧШИМИ ПАРАМЕТРАМИ")
            print(f"{'=' * 70}")

            best_params = best['best_params']
            backtester = ProfessionalBacktester(
                initial_balance=100000,
                risk_per_trade=best_params['risk_per_trade'],
                min_confidence=best_params['min_confidence'],
                take_profit_pct=best_params['take_profit_pct'],
                stop_loss_pct=best_params['stop_loss_pct'],
                max_positions=2,
                warmup_candles=30,
                days=days
            )

            final_stats = backtester.run_single(ticker)
            if 'error' not in final_stats:
                backtester.export_results(ticker, final_stats)
    else:
        print("Использование: python -m trading_bot.backtest.backtest SBER 90")
        print("Примеры: python -m trading_bot.backtest.backtest GAZP 60")
        print("         python -m trading_bot.backtest.backtest LKOH 90")