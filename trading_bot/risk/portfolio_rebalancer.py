# trading_bot/risk/portfolio_rebalancer.py
"""Автоматическая ребалансировка портфеля"""

import time
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
import threading

from ..logger import info, warning, debug, error
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
    timestamp: datetime = field(default_factory=datetime.now)


class PortfolioRebalancer:
    """Автоматическая ребалансировка портфеля"""

    def __init__(self, capital_manager: CapitalManager, bot=None):
        self.cm = capital_manager
        self.bot = bot
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()  # ✅ ДОБАВЛЕНО для graceful shutdown

        # ========== НАСТРОЙКИ РЕБАЛАНСИРОВКИ ==========
        self.max_position_pct = 15.0  # Максимальный размер позиции (% от капитала)
        self.min_position_pct = 2.0  # Минимальный размер позиции (% от капитала)
        self.stop_loss_pct = 5.0  # Стоп-лосс (%)
        self.take_profit_pct = 10.0  # Тейк-профит (%)
        self.trailing_activation = 5.0  # Активация трейлинг-стопа (%)
        self.trailing_step = 2.0  # Шаг трейлинг-стопа (%)

        # ========== ЗАЩИТНЫЕ ПАРАМЕТРЫ ==========
        self.max_total_position_pct = 50.0  # Максимальная суммарная доля всех позиций (%)
        self.rebalance_cooldown_seconds = 30  # Минимальный интервал между ребалансировками (сек)
        self.max_actions_per_cycle = 3  # Макс. действий за один цикл

        # Статистика
        self.rebalance_count = 0
        self.last_rebalance = 0
        self.actions_history: List[RebalanceAction] = []
        self._track_positions: Dict[str, Dict] = {}

        # Кэш для тикеров
        self._figi_to_ticker_cache: Dict[str, str] = {}

        # ✅ ДОБАВЛЕНО: защита от дублирования действий
        self._pending_actions: Dict[str, float] = {}  # figi -> timestamp

        # ✅ ДОБАВИТЬ ЭТУ СТРОКУ (опционально)
        self._last_mass_reduce = 0  # ← уберёт жёлтое подчёркивание

        info("✅ PortfolioRebalancer инициализирован")
        info(f"   📊 Настройки: max={self.max_position_pct}%, min={self.min_position_pct}%")
        info(f"   🛡️ SL={self.stop_loss_pct}%, TP={self.take_profit_pct}%")
        info(f"   ⏱️ Cooldown={self.rebalance_cooldown_seconds}с")

    def start_rebalancer(self):
        """Запуск фоновой ребалансировки"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._rebalance_loop, daemon=True)
        self._thread.start()
        info("🔄 PortfolioRebalancer запущен")

    def stop_rebalancer(self):
        """Остановка ребалансировки (переименовано для устранения конфликта)"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        info("🛑 PortfolioRebalancer остановлен")

    def _rebalance_loop(self):
        """Фоновый цикл ребалансировки"""
        while self._running and not self._stop_event.is_set():
            try:
                # Проверяем каждые 10 секунд
                for _ in range(self.rebalance_cooldown_seconds // 10):
                    if self._stop_event.is_set():
                        return
                    time.sleep(10)

                self.check_and_rebalance()

            except Exception as e:
                if self._running:
                    debug(f"Rebalance error: {e}")

    def check_and_rebalance(self) -> List[RebalanceAction]:
        """Проверка и ребалансировка портфеля"""
        actions = []

        # Получаем текущие позиции
        positions = self._get_current_positions()
        if not positions:
            debug("📭 Нет позиций для ребалансировки")
            return actions

        info(f"\n{'─' * 50}")
        info(f"🔄 ПРОВЕРКА РЕБАЛАНСИРОВКИ")
        info(f"   Позиций: {len(positions)}")
        info(f"{'─' * 50}")

        # Проверяем каждую позицию на лимиты
        actions_per_cycle = 0
        for pos in positions:
            if actions_per_cycle >= self.max_actions_per_cycle:
                warning(f"⚠️ Достигнут лимит действий за цикл ({self.max_actions_per_cycle})")
                break

            action = self._check_position_limits(pos)
            if action:
                actions.append(action)
                self._execute_action(action)
                actions_per_cycle += 1

        # Проверка общей экспозиции портфеля
        total_pct = sum(p.get('size_pct', 0) for p in positions)
        if total_pct > self.max_total_position_pct:
            warning(f"⚠️ Суммарная экспозиция {total_pct:.1f}% > {self.max_total_position_pct}%")
            self._reduce_all_positions_by_ratio(self.max_total_position_pct / total_pct)

        # Проверка высокой маржи
        if hasattr(self.cm, '_margin_rate') and self.cm._margin_rate > 70:
            warning(f"🔥 ВЫСОКАЯ МАРЖА: {self.cm._margin_rate:.1f}%! Уменьшаем все позиции")
            self._reduce_all_positions_by_ratio(0.5)

        if actions:
            self.rebalance_count += 1
            info(f"\n✅ Ребалансировка #{self.rebalance_count}: {len(actions)} действий")
        else:
            debug(f"   ✅ Нет действий для ребалансировки")

        return actions

    def _get_current_positions(self) -> List[Dict]:
        """Получение текущих позиций с расчётом P&L"""
        positions = []

        try:
            from trading_bot.api.tbank_client import tbank

            broker_positions = tbank.get_positions()
            if not broker_positions:
                return positions

            # ✅ Безопасное получение капитала
            current_capital = self.cm.current_capital if self.cm and hasattr(self.cm,
                                                                             'current_capital') and self.cm.current_capital > 0 else 10000

            info(f"\n📊 ТЕКУЩИЕ ПОЗИЦИИ (капитал: {current_capital:.0f}₽):")

            for pos in broker_positions:
                figi = pos['figi']
                quantity = abs(pos['quantity'])
                avg_price = pos['avg_price']
                side = "SHORT" if pos['quantity'] < 0 else "LONG"

                if quantity == 0:
                    continue

                current_price = tbank.get_current_price(figi)
                if not current_price or current_price <= 0:
                    debug(f"   ⚠️ Не удалось получить цену для {figi}")
                    continue

                # Расчёт P&L
                if side == "SHORT":
                    pnl_pct = (avg_price - current_price) / avg_price * 100 if avg_price > 0 else 0
                    pnl_amount = (avg_price - current_price) * quantity
                else:
                    pnl_pct = (current_price - avg_price) / avg_price * 100 if avg_price > 0 else 0
                    pnl_amount = (current_price - avg_price) * quantity

                position_value = quantity * current_price
                position_pct = (position_value / current_capital) * 100 if current_capital > 0 else 0

                ticker = self.bot._get_ticker_by_figi(figi) if hasattr(self.bot, '_get_ticker_by_figi') else figi[:8]
                profit_icon = "🟢" if pnl_amount > 0 else "🔴" if pnl_amount < 0 else "⚪"

                info(f"   {profit_icon} {ticker}: {side} {quantity}шт | {avg_price:.2f}→{current_price:.2f} | "
                     f"P&L={pnl_amount:+.0f}₽ ({pnl_pct:+.1f}%) | доля={position_pct:.1f}%")

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
                    'ticker': ticker
                })

        except Exception as e:
            debug(f"Ошибка получения позиций: {e}")

        return positions

    def _is_action_pending(self, figi: str, action_type: str) -> bool:
        """Проверка, не выполнялось ли действие недавно"""
        key = f"{figi}_{action_type}"
        now = time.time()
        if key in self._pending_actions:
            if now - self._pending_actions[key] < 10:  # 10 секунд кулдаун
                return True
        self._pending_actions[key] = now
        return False

    def _check_position_limits(self, pos: Dict) -> Optional[RebalanceAction]:
        """Проверка лимитов позиции"""
        ticker = pos.get('ticker', pos['figi'][:8])

        # ========== 0. ПРОВЕРКА ДУБЛИРОВАНИЯ ==========
        if self._is_action_pending(pos['figi'], 'rebalance'):
            debug(f"   ⏸️ {ticker}: действие уже выполняется, пропускаем")
            return None

        # ========== 1. СТОП-ЛОСС ==========
        if pos['pnl_pct'] <= -self.stop_loss_pct:
            info(f"\n{'🛑' * 40}")
            info(f"🛑 СТОП-ЛОСС ДЛЯ {ticker}!")
            info(f"   Текущий P&L: {pos['pnl_pct']:.1f}% ≤ -{self.stop_loss_pct}%")
            info(f"{'🛑' * 40}")
            return RebalanceAction(
                figi=pos['figi'],
                ticker=ticker,
                action='close',
                current_size=pos['size_pct'],
                target_size=0,
                reason=f"стоп-лосс: {pos['pnl_pct']:.1f}% ≤ -{self.stop_loss_pct}%"
            )

        # ========== 2. ТЕЙК-ПРОФИТ ==========
        if pos['pnl_pct'] >= self.take_profit_pct:
            info(f"\n{'🎯' * 40}")
            info(f"🎯 ТЕЙК-ПРОФИТ ДЛЯ {ticker}!")
            info(f"   Текущий P&L: {pos['pnl_pct']:.1f}% ≥ {self.take_profit_pct}%")
            info(f"{'🎯' * 40}")
            return RebalanceAction(
                figi=pos['figi'],
                ticker=ticker,
                action='close',
                current_size=pos['size_pct'],
                target_size=0,
                reason=f"тейк-профит: {pos['pnl_pct']:.1f}% ≥ {self.take_profit_pct}%"
            )

        # ========== 3. ПРЕВЫШЕНИЕ МАКСИМАЛЬНОГО РАЗМЕРА ==========
        if pos['size_pct'] > self.max_position_pct:
            warning(f"⚠️ {ticker}: превышение лимита {pos['size_pct']:.1f}% > {self.max_position_pct}%")
            return RebalanceAction(
                figi=pos['figi'],
                ticker=ticker,
                action='reduce',
                current_size=pos['size_pct'],
                target_size=self.max_position_pct,
                reason=f"превышение лимита: {pos['size_pct']:.1f}% > {self.max_position_pct}%"
            )

        # ========== 4. СЛИШКОМ МАЛЕНЬКАЯ ПОЗИЦИЯ ==========
        if 0 < pos['size_pct'] < self.min_position_pct:
            info(f"📉 {ticker}: слишком малая позиция {pos['size_pct']:.1f}% < {self.min_position_pct}%, закрываем")
            return RebalanceAction(
                figi=pos['figi'],
                ticker=ticker,
                action='close',
                current_size=pos['size_pct'],
                target_size=0,
                reason=f"слишком малая позиция: {pos['size_pct']:.1f}% < {self.min_position_pct}%"
            )

        # ========== 5. ТРЕЙЛИНГ-СТОП ДЛЯ ПРИБЫЛЬНЫХ ПОЗИЦИЙ ==========
        if pos['pnl_pct'] >= self.trailing_activation:
            should_close = self._check_trailing_stop(pos)
            if should_close:
                return RebalanceAction(
                    figi=pos['figi'],
                    ticker=ticker,
                    action='close',
                    current_size=pos['size_pct'],
                    target_size=0,
                    reason=f"трейлинг-стоп: сработал при откате от максимума"
                )

        return None

    def _check_trailing_stop(self, pos: Dict) -> bool:
        """Проверка трейлинг-стопа для прибыльной позиции"""
        figi = pos['figi']
        current_price = pos['current_price']
        ticker = pos.get('ticker', figi[:8])

        # Инициализируем отслеживание для позиции
        if figi not in self._track_positions:
            self._track_positions[figi] = {
                'highest_price': current_price,
                'trailing_stop_price': current_price * (1 - self.trailing_step / 100)
            }
            info(
                f"   🔻 {ticker}: трейлинг-стоп активирован, стоп={self._track_positions[figi]['trailing_stop_price']:.2f}₽")
            return False

        track = self._track_positions[figi]

        # Обновляем максимальную цену
        if current_price > track['highest_price']:
            old_stop = track['trailing_stop_price']
            track['highest_price'] = current_price
            track['trailing_stop_price'] = current_price * (1 - self.trailing_step / 100)
            info(
                f"   📈 {ticker}: новый максимум {current_price:.2f}₽, стоп {track['trailing_stop_price']:.2f}₽ (был {old_stop:.2f}₽)")
            return False

        # Проверяем, не сработал ли трейлинг-стоп
        if current_price <= track['trailing_stop_price']:
            info(f"\n{'🔔' * 40}")
            info(f"🔔 ТРЕЙЛИНГ-СТОП СРАБОТАЛ ДЛЯ {ticker}!")
            info(f"   Максимум: {track['highest_price']:.2f}₽")
            info(f"   Текущая цена: {current_price:.2f}₽")
            info(f"   Падение: {(1 - current_price / track['highest_price']) * 100:.1f}%")
            info(f"{'🔔' * 40}")
            return True

        return False

    def _execute_action(self, action: RebalanceAction):
        """Выполнение действия ребалансировки"""
        try:
            from trading_bot.api.tbank_client import tbank

            info(f"\n{'─' * 40}")
            info(f"🔧 ВЫПОЛНЕНИЕ ДЕЙСТВИЯ: {action.action.upper()} {action.ticker}")
            info(f"   Текущий размер: {action.current_size:.1f}% → Целевой: {action.target_size:.1f}%")
            info(f"   Причина: {action.reason}")

            if action.action == 'close':
                # Используем основной метод закрытия
                from trading_bot.api.tbank_client import tbank as tbank_client
                result = tbank_client.close_position_with_retry(
                    figi=action.figi,
                    quantity=None,  # определим позже
                    direction="SELL" if action.current_size > 0 else "BUY",
                    max_attempts=3,
                    emergency_slippage=0.05
                )
                if result.get('success'):
                    info(f"   ✅ {action.ticker} закрыт")
                else:
                    warning(f"   ⚠️ Не удалось закрыть {action.ticker}")

            elif action.action == 'reduce':
                self._reduce_position(action.figi, action.ticker, action.current_size, action.target_size, tbank)

            action.timestamp = datetime.now()
            self.actions_history.append(action)

            # Ограничиваем историю
            if len(self.actions_history) > 100:
                self.actions_history = self.actions_history[-50:]

        except Exception as e:
            error(f"❌ Не удалось выполнить {action.action} {action.ticker}: {e}")

    def _reduce_position(self, figi: str, ticker: str, current_size: float, target_size: float, tbank):
        """Уменьшение позиции"""
        try:
            positions = tbank.get_positions()
            for pos in positions:
                if pos['figi'] == figi:
                    current_qty = abs(pos['quantity'])
                    if current_qty == 0:
                        info(f"   ⚠️ Позиция {ticker} уже закрыта")
                        return

                    # Рассчитываем новый размер
                    ratio = target_size / current_size
                    new_qty = max(1, int(current_qty * ratio))
                    to_close = current_qty - new_qty

                    if to_close > 0:
                        info(f"   📉 Уменьшение {ticker}: {current_qty} → {new_qty} шт (-{to_close})")

                        if pos['quantity'] < 0:  # SHORT
                            success = tbank.buy(figi, to_close)
                        else:  # LONG
                            success = tbank.sell(figi, to_close)

                        if success:
                            info(f"   ✅ Позиция {ticker} уменьшена")
                        else:
                            warning(f"   ⚠️ Не удалось уменьшить {ticker}")
                    break
        except Exception as e:
            error(f"❌ Ошибка уменьшения {ticker}: {e}")

    def _reduce_all_positions_by_ratio(self, ratio: float):
        """Уменьшение всех позиций на указанный коэффициент"""
        if ratio >= 0.95:
            return

        # ✅ ДОБАВИТЬ ПРОВЕРКУ - не выполнять слишком часто
        if hasattr(self, '_last_mass_reduce') and time.time() - self._last_mass_reduce < 60:
            debug("   ⏸️ Массовое уменьшение уже выполнялось менее минуты назад")
            return
        self._last_mass_reduce = time.time()

        info(f"\n{'⚠️' * 40}")
        info(f"⚠️ МАССОВОЕ УМЕНЬШЕНИЕ ВСЕХ ПОЗИЦИЙ на {ratio * 100:.0f}%")
        info(f"{'⚠️' * 40}")

        try:
            from trading_bot.api.tbank_client import tbank
            positions = tbank.get_positions()

            closed_count = 0
            for pos in positions:
                quantity = abs(pos['quantity'])
                if quantity == 0:
                    continue

                new_quantity = max(1, int(quantity * ratio))
                to_close = quantity - new_quantity

                if to_close > 0:
                    ticker = tbank(pos['figi'])
                    info(f"   Уменьшаем {ticker}: {quantity} → {new_quantity} шт (закрываем {to_close})")

                    if pos['quantity'] < 0:
                        if tbank.buy(pos['figi'], to_close):
                            closed_count += 1
                    else:
                        if tbank.sell(pos['figi'], to_close):
                            closed_count += 1

            info(f"✅ Массовое уменьшение завершено: изменено {closed_count} позиций")

        except Exception as e:
            error(f"❌ Ошибка массового уменьшения: {e}")

    #     def _get_ticker_by_figi(self, figi: str) -> str:
    #         """Получение тикера по FIGI с кэшированием"""
    #         if figi in self._figi_to_ticker_cache:
    #             return self._figi_to_ticker_cache[figi]

    #         try:
    #             from trading_bot.api.tbank_client import tbank
    #             all_shares = tbank.get_all_shares(limit=500)
    #             for stock in all_shares:
    #                 if stock.get('figi') == figi:
    #                     ticker = stock.get('ticker', figi[:8])
    #                     self._figi_to_ticker_cache[figi] = ticker
    #                     return ticker
    #         except Exception:
    #             pass

    #         self._figi_to_ticker_cache[figi] = figi[:8]
    #         return figi[:8]

    def get_stats(self) -> Dict:
        """Получение статистики ребалансировки"""
        return {
            'rebalance_count': self.rebalance_count,
            'last_rebalance': self.last_rebalance,
            'actions_count': len(self.actions_history),
            'is_running': self._running,
            'config': {
                'max_position_pct': self.max_position_pct,
                'min_position_pct': self.min_position_pct,
                'stop_loss_pct': self.stop_loss_pct,
                'take_profit_pct': self.take_profit_pct,
                'trailing_activation': self.trailing_activation,
                'trailing_step': self.trailing_step,
                'max_total_position_pct': self.max_total_position_pct,
                'rebalance_cooldown_seconds': self.rebalance_cooldown_seconds
            },
            'recent_actions': [
                {
                    'ticker': a.ticker,
                    'action': a.action,
                    'reason': a.reason,
                    'timestamp': a.timestamp.isoformat() if hasattr(a, 'timestamp') else None
                } for a in self.actions_history[-10:]
            ]
        }

    def update_settings(self, **kwargs):
        """Обновление настроек ребалансировки"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                info(f"⚙️ {key} = {value}")


# Глобальный экземпляр
_portfolio_rebalancer = None


def get_portfolio_rebalancer(capital_manager: CapitalManager = None, bot=None):
    """Получение глобального экземпляра ребалансировщика"""
    global _portfolio_rebalancer
    if _portfolio_rebalancer is None and capital_manager is not None:
        _portfolio_rebalancer = PortfolioRebalancer(capital_manager, bot)
    return _portfolio_rebalancer