# trading_bot/risk/capital_manager.py
"""Управление капиталом с наращиванием и динамическими позициями"""

from typing import Dict, Tuple
from dataclasses import dataclass
from datetime import datetime

from ..logger import info, success, warning, debug


@dataclass
class PositionInfo:
    """Информация о позиции"""
    figi: str
    ticker: str
    side: str
    quantity: int
    entry_price: float
    current_price: float
    size_pct: float
    pnl_pct: float
    pnl_amount: float
    entry_time: datetime


class CapitalManager:
    """Управление капиталом с наращиванием"""

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.peak_capital = initial_capital
        self.drawdown = 0.0

        self.positions: Dict[str, PositionInfo] = {}
        self.trades_history = []
        self.consecutive_wins = 0
        self.consecutive_losses = 0

        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_profit = 0
        self.total_loss = 0

        self._margin_rate = 0
        self._margin_update_time = 0

        self.daily_pnl = 0
        self.last_day = datetime.now().date()

        info(f"💰 CapitalManager инициализирован: капитал {initial_capital:.0f}₽")

    def update_capital(self, new_capital: float):
        """Обновление текущего капитала"""
        self.current_capital = new_capital

        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

        if self.peak_capital > 0:
            self.drawdown = (self.peak_capital - self.current_capital) / self.peak_capital * 100

    def update_margin_rate(self, margin_rate: float):
        """Обновление ставки маржи"""
        self._margin_rate = margin_rate

    def calculate_position_size(
            self,
            score: int,
            volatility: float = 0.01,
            is_short: bool = False
    ) -> Dict:
        """Расчёт размера позиции (% от капитала)"""

        # Базовый размер от капитала
        if self.current_capital < 5000:
            base_pct = 3.0
        elif self.current_capital < 10000:
            base_pct = 5.0
        elif self.current_capital < 25000:
            base_pct = 7.0
        elif self.current_capital < 50000:
            base_pct = 8.0
        else:
            base_pct = 10.0

        # Множитель по силе сигнала
        abs_score = abs(score)
        if abs_score >= 8:
            signal_mult = 2.0
        elif abs_score >= 6:
            signal_mult = 1.6
        elif abs_score >= 4:
            signal_mult = 1.3
        elif abs_score >= 2:
            signal_mult = 1.0
        else:
            signal_mult = 0.7

        # Корректировка по просадке
        if self.drawdown > 15:
            dd_mult = 0.3
        elif self.drawdown > 10:
            dd_mult = 0.5
        elif self.drawdown > 5:
            dd_mult = 0.7
        else:
            dd_mult = 1.0

        # Корректировка по серии убытков
        if self.consecutive_losses >= 3:
            loss_mult = 0.3
        elif self.consecutive_losses >= 2:
            loss_mult = 0.5
        elif self.consecutive_losses >= 1:
            loss_mult = 0.7
        else:
            loss_mult = 1.0

        # Корректировка по волатильности
        if volatility > 0.025:
            vol_mult = 0.5
        elif volatility > 0.015:
            vol_mult = 0.7
        elif volatility > 0.01:
            vol_mult = 0.9
        else:
            vol_mult = 1.0

        # Корректировка по марже (для SHORT)
        margin_mult = 1.0
        if is_short:
            if self._margin_rate > 70:
                margin_mult = 0.3
            elif self._margin_rate > 50:
                margin_mult = 0.6
            elif self._margin_rate > 30:
                margin_mult = 0.8

        # Итоговый размер
        position_pct = base_pct * signal_mult * dd_mult * loss_mult * vol_mult * margin_mult

        # Ограничения
        min_pct = 2.0
        max_pct = 15.0
        position_pct = max(min_pct, min(max_pct, position_pct))

        # Сумма в рублях
        position_amount = self.current_capital * position_pct / 100

        return {
            'size_pct': round(position_pct, 1),
            'amount': position_amount,
            'signal_mult': signal_mult,
            'dd_mult': dd_mult,
            'loss_mult': loss_mult,
            'vol_mult': vol_mult,
            'margin_mult': margin_mult,
            'reason': f"капитал={self.current_capital:.0f}₽, просадка={self.drawdown:.1f}%, убытки={self.consecutive_losses}"
        }

    def get_max_positions(self) -> int:
        """Динамический лимит позиций"""
        if self.current_capital >= self.initial_capital * 2:
            return 8
        elif self.current_capital >= self.initial_capital * 1.5:
            return 6
        elif self.current_capital >= self.initial_capital * 1.2:
            return 5
        else:
            return 3

    def can_open_position(
            self,
            required_capital: float,
            current_positions: int
    ) -> Tuple[bool, str]:
        """Проверка возможности открытия новой позиции"""
        max_positions = self.get_max_positions()

        if current_positions >= max_positions:
            return False, f"лимит позиций ({max_positions}) достигнут"

        if required_capital > self.current_capital * 0.7:
            return False, f"не хватает капитала: нужно {required_capital:.0f}₽"

        if self._margin_rate > 85:
            return False, f"критическая маржа ({self._margin_rate:.1f}%)"

        if self.drawdown > 20:
            return False, f"слишком большая просадка ({self.drawdown:.1f}%)"

        return True, "OK"

    def record_trade(self, profit_pct: float, profit_amount: float):
        """Запись результата сделки"""
        self.total_trades += 1

        if profit_amount > 0:
            self.winning_trades += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.total_profit += profit_amount
        else:
            self.losing_trades += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.total_loss += abs(profit_amount)

        self.current_capital += profit_amount
        self.update_capital(self.current_capital)

        today = datetime.now().date()
        if today != self.last_day:
            self.daily_pnl = 0
            self.last_day = today
        self.daily_pnl += profit_amount

        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        info(f"📊 Капитал: {self.current_capital:.0f}₽ | Win Rate: {win_rate:.1f}% | "
             f"Просадка: {self.drawdown:.1f}%")

    def get_stats(self) -> Dict:
        """Получение статистики"""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        profit_factor = self.total_profit / self.total_loss if self.total_loss > 0 else float('inf')

        return {
            'current_capital': self.current_capital,
            'initial_capital': self.initial_capital,
            'total_return': (self.current_capital - self.initial_capital) / self.initial_capital * 100,
            'drawdown': self.drawdown,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': round(win_rate, 1),
            'profit_factor': round(profit_factor, 2),
            'consecutive_wins': self.consecutive_wins,
            'consecutive_losses': self.consecutive_losses,
            'daily_pnl': self.daily_pnl,
            'max_positions': self.get_max_positions()
        }

    def should_reduce_positions(self) -> bool:
        """Проверка, нужно ли уменьшить позиции"""
        return (self.drawdown > 10 or
                self._margin_rate > 70 or
                self.consecutive_losses >= 2)