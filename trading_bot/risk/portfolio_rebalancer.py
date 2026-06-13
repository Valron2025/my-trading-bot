# trading_bot/risk/portfolio_rebalancer.py
"""Автоматическая ребалансировка портфеля"""

import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
import threading

from ..logger import info, warning, debug
from ..config import config
from .capital_manager import CapitalManager


@dataclass
class RebalanceAction:
    """Действие ребалансировки"""
    figi: str
    ticker: str
    action: str
    current_size: float
    target_size: float
    reason: str


class PortfolioRebalancer:
    """Автоматическая ребалансировка портфеля"""

    def __init__(self, capital_manager: CapitalManager, bot=None):
        self.cm = capital_manager
        self.bot = bot
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self.max_position_pct = 15.0
        self.min_position_pct = 2.0
        self.stop_loss_pct = 5.0
        self.take_profit_pct = 10.0
        self.trailing_activation = 5.0
        self.trailing_step = 2.0

        self.rebalance_count = 0
        self.last_rebalance = 0
        self.actions_history: List[RebalanceAction] = []
        self._track_positions: Dict[str, Dict] = {}

        info("✅ PortfolioRebalancer инициализирован")

    def start(self):
        """Запуск фоновой ребалансировки"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._rebalance_loop, daemon=True)
        self._thread.start()
        info("🔄 PortfolioRebalancer запущен")

    def stop(self):
        """Остановка ребалансировки"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        info("🛑 PortfolioRebalancer остановлен")

    def _rebalance_loop(self):
        """Фоновый цикл ребалансировки"""
        while self._running:
            try:
                time.sleep(30)

                now = time.time()
                if now - self.last_rebalance < 30:
                    continue

                self.check_and_rebalance()
                self.last_rebalance = now

            except Exception as e:
                if self._running:
                    debug(f"Rebalance error: {e}")

    def check_and_rebalance(self) -> List[RebalanceAction]:
        """Проверка и ребалансировка портфеля"""
        actions = []

        positions = self._get_current_positions()
        if not positions:
            return actions

        for pos in positions:
            action = self._check_position_limits(pos)
            if action:
                actions.append(action)
                self._execute_action(action)

        if self.cm._margin_rate > 70:
            self._reduce_all_positions()

        if actions:
            self.rebalance_count += 1
            info(f"📊 Ребалансировка #{self.rebalance_count}: {len(actions)} действий")

        return actions

    def _get_current_positions(self) -> List[Dict]:
        """Получение текущих позиций с расчётом P&L"""
        positions = []

        try:
            from trading_bot.api.tbank_client import tbank
            broker_positions = tbank.get_positions()

            for pos in broker_positions:
                figi = pos['figi']
                quantity = abs(pos['quantity'])
                avg_price = pos['avg_price']
                side = "SHORT" if pos['quantity'] < 0 else "LONG"

                current_price = tbank.get_current_price(figi)
                if not current_price:
                    continue

                if side == "SHORT":
                    pnl_pct = (avg_price - current_price) / avg_price * 100
                    pnl_amount = (avg_price - current_price) * quantity
                else:
                    pnl_pct = (current_price - avg_price) / avg_price * 100
                    pnl_amount = (current_price - avg_price) * quantity

                position_value = quantity * current_price
                position_pct = (position_value / self.cm.current_capital) * 100 if self.cm.current_capital > 0 else 0

                positions.append({
                    'figi': figi,
                    'quantity': quantity,
                    'avg_price': avg_price,
                    'current_price': current_price,
                    'side': side,
                    'pnl_pct': pnl_pct,
                    'pnl_amount': pnl_amount,
                    'value': position_value,
                    'size_pct': position_pct,
                    'ticker': self._get_ticker_by_figi(figi)
                })
        except Exception as e:
            debug(f"Ошибка получения позиций: {e}")

        return positions

    def _check_position_limits(self, pos: Dict) -> Optional[RebalanceAction]:
        """Проверка лимитов позиции"""
        ticker = pos.get('ticker', pos['figi'][:8])

        if pos['pnl_pct'] <= -self.stop_loss_pct:
            return RebalanceAction(
                figi=pos['figi'],
                ticker=ticker,
                action='close',
                current_size=pos['size_pct'],
                target_size=0,
                reason=f"стоп-лосс: {pos['pnl_pct']:.1f}% ≤ -{self.stop_loss_pct}%"
            )

        if pos['pnl_pct'] >= self.take_profit_pct:
            return RebalanceAction(
                figi=pos['figi'],
                ticker=ticker,
                action='close',
                current_size=pos['size_pct'],
                target_size=0,
                reason=f"тейк-профит: {pos['pnl_pct']:.1f}% ≥ {self.take_profit_pct}%"
            )

        if pos['size_pct'] > self.max_position_pct:
            return RebalanceAction(
                figi=pos['figi'],
                ticker=ticker,
                action='reduce',
                current_size=pos['size_pct'],
                target_size=self.max_position_pct,
                reason=f"превышение лимита: {pos['size_pct']:.1f}% > {self.max_position_pct}%"
            )

        if 0 < pos['size_pct'] < self.min_position_pct:
            return RebalanceAction(
                figi=pos['figi'],
                ticker=ticker,
                action='close',
                current_size=pos['size_pct'],
                target_size=0,
                reason=f"слишком малая позиция: {pos['size_pct']:.1f}% < {self.min_position_pct}%"
            )

        return None

    def _execute_action(self, action: RebalanceAction):
        """Выполнение действия ребалансировки"""
        try:
            from trading_bot.api.tbank_client import tbank

            info(f"🔧 {action.action} {action.ticker}: {action.current_size:.1f}% → {action.target_size:.1f}% ({action.reason})")

            if action.action == 'close':
                if action.figi:
                    if self.bot and hasattr(self.bot, 'close_position'):
                        self.bot.close_position(action.figi, None)
                    else:
                        positions = tbank.get_positions()
                        for pos in positions:
                            if pos['figi'] == action.figi:
                                quantity = abs(pos['quantity'])
                                if pos['quantity'] < 0:
                                    tbank.buy(action.figi, quantity)
                                else:
                                    tbank.sell(action.figi, quantity)
                                break

            elif action.action == 'reduce' and action.current_size > action.target_size:
                positions = tbank.get_positions()
                for pos in positions:
                    if pos['figi'] == action.figi:
                        current_qty = abs(pos['quantity'])
                        ratio = action.target_size / action.current_size
                        new_qty = max(1, int(current_qty * ratio))
                        to_close = current_qty - new_qty

                        if to_close > 0:
                            if pos['quantity'] < 0:
                                tbank.buy(action.figi, to_close)
                            else:
                                tbank.sell(action.figi, to_close)
                        break

            self.actions_history.append(action)

        except Exception as e:
            warning(f"❌ Не удалось выполнить {action.action} {action.ticker}: {e}")

    def _reduce_all_positions(self):
        """Уменьшение всех позиций при высокой марже"""
        warning("🔥 ВЫСОКАЯ МАРЖА! Уменьшаем все позиции на 50%")

        try:
            from trading_bot.api.tbank_client import tbank
            positions = tbank.get_positions()

            for pos in positions:
                quantity = abs(pos['quantity'])
                new_quantity = max(1, quantity // 2)
                to_close = quantity - new_quantity

                if to_close > 0:
                    ticker = self._get_ticker_by_figi(pos['figi'])
                    info(f"   Уменьшаем {ticker}: {quantity} → {new_quantity} шт")

                    if pos['quantity'] < 0:
                        tbank.buy(pos['figi'], to_close)
                    else:
                        tbank.sell(pos['figi'], to_close)
        except Exception as e:
            error(f"Ошибка уменьшения позиций: {e}")

    def _get_ticker_by_figi(self, figi: str) -> str:
        """Получение тикера по FIGI"""
        try:
            from trading_bot.api.tbank_client import tbank
            all_shares = tbank.get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('figi') == figi:
                    return stock.get('ticker', figi[:8])
        except Exception:
            pass
        return figi[:8]

    def get_stats(self) -> Dict:
        """Получение статистики ребалансировки"""
        return {
            'rebalance_count': self.rebalance_count,
            'last_rebalance': self.last_rebalance,
            'actions_count': len(self.actions_history),
            'recent_actions': [
                {
                    'ticker': a.ticker,
                    'action': a.action,
                    'reason': a.reason
                } for a in self.actions_history[-10:]
            ]
        }


# Глобальный экземпляр
_portfolio_rebalancer = None


def get_portfolio_rebalancer(capital_manager: CapitalManager = None, bot=None):
    """Получение глобального экземпляра ребалансировщика"""
    global _portfolio_rebalancer
    if _portfolio_rebalancer is None and capital_manager is not None:
        _portfolio_rebalancer = PortfolioRebalancer(capital_manager, bot)
    return _portfolio_rebalancer