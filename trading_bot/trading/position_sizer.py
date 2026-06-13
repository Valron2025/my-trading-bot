# trading_bot/trading/position_sizer.py
"""Расчёт размера позиции с учётом маржи и рисков"""

from datetime import time as dt_time
from typing import Optional

from ..config import config
from ..models import StockCandidate, OrderSide
from ..logger import info, error, warning, debug
from ..utils.time_utils import get_moscow_time
from ..risk.capital_manager import CapitalManager


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


class PositionSizer:
    """Расчёт размера позиции с учётом маржинальной торговли и управления капиталом"""

    def __init__(self, bot):
        self.bot = bot
        self.capital_manager: Optional[CapitalManager] = None

    def init_capital_manager(self, total_capital: float):
        """Инициализация менеджера капитала"""
        if self.capital_manager is None:
            self.capital_manager = CapitalManager(total_capital)
            info("✅ CapitalManager инициализирован")
        else:
            self.capital_manager.update_capital(total_capital)

    def calculate(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
        """Расчёт размера позиции с учётом стороны и силы сигнала"""
        try:
            _, total_capital, _ = _get_tbank().get_available_funds()
            self.init_capital_manager(total_capital)
        except Exception:
            pass

        if stock.side == OrderSide.SHORT:
            return self._calculate_short(stock, available_funds, score)
        else:
            return self._calculate_long(stock, available_funds, score)

    def _calculate_long(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
        """Расчёт размера LONG позиции - С УЧЁТОМ МАРЖИ"""
        price = stock.price
        lot = stock.lot
        ticker = stock.ticker

        available, total, _ = _get_tbank().get_available_funds()
        margin_info = _get_tbank().get_margin_info()
        margin_rate = margin_info.get('margin_rate', 0)
        available_margin = margin_info.get('available_margin', 0)

        # ✅ ИТОГО ДОСТУПНО С УЧЁТОМ МАРЖИ
        total_available = total + available_margin

        if self.capital_manager:
            self.capital_manager.update_margin_rate(margin_rate)

        volatility = self._get_volatility(ticker)

        # ========== 1. РАСЧЁТ БАЗОВОГО РАЗМЕРА ==========
        if self.capital_manager:
            position_config = self.capital_manager.calculate_position_size(
                score=score,
                volatility=volatility,
                is_short=False
            )
            position_pct = position_config['size_pct'] / 100
            info(f"   📐 Размер по risk-менеджменту: {position_config['size_pct']:.1f}%")
        else:
            # Адаптивный процент в зависимости от капитала
            if total < 3000:
                position_pct = 0.15
            elif total < 5000:
                position_pct = 0.12
            elif total < 10000:
                position_pct = 0.10
            else:
                position_pct = 0.08

        # ✅ ИСПОЛЬЗУЕМ total_available ВМЕСТО total
        max_position_value = total_available * position_pct

        info(f"   💰 Собственные: {total:.0f}₽")
        if available_margin > 0:
            info(f"   📈 Маржа: +{available_margin:.0f}₽ → {total_available:.0f}₽")
        info(f"   📊 Размер: {position_pct * 100:.1f}% = {max_position_value:.0f}₽")

        # Корректировка по марже
        if margin_rate > 50:
            max_position_value = max_position_value * 0.7
            info(f"   📊 Маржа {margin_rate:.1f}% → уменьшаем до {max_position_value:.0f}₽")

        quantity = int(max_position_value / price)

        original_quantity = quantity

        # Корректировка по лоту
        if lot > 1:
            quantity = (quantity // lot) * lot

        # Проверка минимального лота
        if quantity < lot:
            lot_cost = lot * price
            if lot_cost <= total * 0.5:
                quantity = lot
                info(f"   📦 Берём минимальный лот: {lot} шт ({lot_cost:.0f}₽)")
            else:
                error(f"   ❌ Лот {lot} шт стоит {lot_cost:.0f}₽ > 50% капитала")
                return 0

        total_cost = quantity * price
        stop_loss_price = price * (1 - config.stop_loss_pct / 100)
        take_profit_price = price * (1 + config.take_profit_pct / 100)

        # Проверка: достаточно ли средств для закрытия при стопе
        required_for_close = quantity * stop_loss_price
        if required_for_close > available * 0.8:
            max_safe_amount = available * 0.8
            max_safe_quantity = int(max_safe_amount / stop_loss_price)
            if lot > 1:
                max_safe_quantity = (max_safe_quantity // lot) * lot
            if max_safe_quantity >= lot:
                quantity = max_safe_quantity
                total_cost = quantity * price
                warning(f"   ⚠️ Уменьшено до {quantity} шт для безопасного стопа")
            else:
                warning(f"   ⚠️ Недостаточно средств для безопасного стопа")
                return 0

        remaining_after = available - total_cost
        min_remaining = total * 0.05

        if remaining_after < min_remaining and quantity > lot:
            quantity -= lot
            total_cost = quantity * price
            warning(f"   ⚠️ Уменьшено до {quantity} шт (резерв {remaining_after:.0f}₽ < {min_remaining:.0f}₽)")

        # ========== КРАСИВОЕ ЛОГИРОВАНИЕ ==========
        info(f"\n{'─' * 45}")
        info(f"📊 LONG ПОЗИЦИЯ: {ticker}")
        info(f"   💰 Цена:           {price:.2f}₽")
        info(f"   📦 Лот:            {lot} шт")
        info(f"   💵 Капитал:        {total:.0f}₽")
        info(f"   📊 Размер:         {position_pct * 100:.1f}% = {max_position_value:.0f}₽")
        info(f"   🔢 Количество:     {original_quantity} → {quantity} шт")
        info(f"   💰 Сумма сделки:   {total_cost:.0f}₽")
        info(f"   🛑 Стоп-лосс:      {stop_loss_price:.2f}₽ (-{config.stop_loss_pct}%)")
        info(f"   🎯 Тейк-профит:    {take_profit_price:.2f}₽ (+{config.take_profit_pct}%)")
        info(f"   💰 Остаток:        {remaining_after:.0f}₽")
        info(f"{'─' * 45}")

        return quantity

    def _calculate_short(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
        """
        Расчёт размера SHORT позиции - С ПРОВЕРКОЙ СРЕДСТВ ДЛЯ ЗАКРЫТИЯ
        """
        price = stock.price
        lot = stock.lot
        ticker = stock.ticker

        available, total, _ = _get_tbank().get_available_funds()
        margin_info = _get_tbank().get_margin_info()
        available_margin = margin_info.get('available_margin', 0)
        margin_rate = margin_info.get('margin_rate', 0)
        used_margin = margin_info.get('used_margin', 0)

        total_available = available + available_margin

        # ========== 1. ПРОВЕРКА ДОСТУПНОСТИ SHORT ==========
        if total < config.min_capital_for_short:
            info(f"   🔻 SHORT недоступен: капитал {total:.0f}₽ < {config.min_capital_for_short}₽")
            return 0

        if margin_rate > 70:
            warning(f"   🔻 SHORT недоступен: маржа {margin_rate:.1f}% > 70%")
            return 0

        min_required = total * 0.1
        if available < min_required:
            warning(f"   🔻 Недостаточно средств: нужно ~{min_required:.0f}₽, есть {available:.0f}₽")
            return 0

        # ========== 2. РАСЧЁТ БАЗОВОГО РАЗМЕРА ==========
        if self.capital_manager:
            volatility = self._get_volatility(ticker)
            position_config = self.capital_manager.calculate_position_size(
                score=score,
                volatility=volatility,
                is_short=True
            )
            short_pct = position_config['size_pct'] / 100
            info(f"   📐 Размер SHORT по risk-менеджменту: {position_config['size_pct']:.1f}%")
        else:
            if total < 10000:
                short_pct = 0.05
            elif total < 30000:
                short_pct = 0.08
            else:
                short_pct = 0.12

        max_position_value = min(
            available_margin - used_margin,
            total_available * 0.3,
            total * short_pct
        )

        if max_position_value <= 0:
            warning(f"   🔻 Нет доступной маржи для SHORT")
            return 0

        # ========== 3. РАСЧЁТ КОЛИЧЕСТВА ==========
        quantity = int(max_position_value / price)
        original_quantity = quantity

        # Корректировка по лоту
        if lot > 1:
            quantity = (quantity // lot) * lot

        # Проверка минимального лота
        if quantity < lot:
            lot_cost = lot * price
            if lot_cost <= total * 0.3:
                quantity = lot
                info(f"   📦 Берём минимальный лот для SHORT: {lot} шт ({lot_cost:.0f}₽)")
            else:
                error(f"   ❌ SHORT: лот {lot} шт слишком дорогой ({lot_cost:.0f}₽)")
                return 0

        # ========== 4. ПРОВЕРКА СРЕДСТВ ДЛЯ ЗАКРЫТИЯ SHORT ==========
        worst_case_price = price * 1.10  # Худший случай: рост 10%
        buy_back_cost = quantity * worst_case_price * 1.05  # +5% запас

        if buy_back_cost > available * 0.9:
            warning(f"   ⚠️ Недостаточно средств для закрытия SHORT при +10%")
            warning(f"   Нужно: {buy_back_cost:.0f}₽, Доступно: {available:.0f}₽")

            # Пробуем уменьшить количество
            max_safe_qty = int(available * 0.8 / worst_case_price)
            if max_safe_qty >= lot:
                new_qty = (max_safe_qty // lot) * lot
                warning(f"   Уменьшаем SHORT: {quantity} → {new_qty} шт")
                quantity = new_qty
                buy_back_cost = quantity * worst_case_price * 1.05
            else:
                error(
                    f"   ❌ SHORT невозможен: даже {lot} лот требует {worst_case_price * lot:.0f}₽ для закрытия, а доступно {available:.0f}₽")
                return 0

        # Проверка запаса прочности
        remaining_after = available - buy_back_cost
        min_remaining = total * 0.05

        if remaining_after < min_remaining:
            error(f"   🔻 После SHORT останется всего {remaining_after:.0f}₽ (нужно > {min_remaining:.0f}₽)")
            return 0

        total_cost = quantity * price
        stop_loss_price = price * (1 + config.stop_loss_pct / 100)
        take_profit_price = price * (1 - config.take_profit_pct / 100)

        # ========== КРАСИВОЕ ЛОГИРОВАНИЕ ==========
        info(f"\n{'─' * 45}")
        info(f"📊 SHORT ПОЗИЦИЯ: {ticker}")
        info(f"   💰 Цена:           {price:.2f}₽")
        info(f"   📦 Лот:            {lot} шт")
        info(f"   💵 Капитал:        {total:.0f}₽")
        info(f"   📈 Маржа:          {margin_rate:.1f}% (доступно {available_margin:.0f}₽)")
        info(f"   📊 Размер:         {short_pct * 100:.1f}% = {max_position_value:.0f}₽")
        info(f"   🔢 Количество:     {original_quantity} → {quantity} шт")
        info(f"   💰 Сумма сделки:   {total_cost:.0f}₽")
        info(f"   🛑 Стоп-лосс:      {stop_loss_price:.2f}₽ (+{config.stop_loss_pct}%)")
        info(f"   🎯 Тейк-профит:    {take_profit_price:.2f}₽ (-{config.take_profit_pct}%)")
        info(f"   💪 Запас:          {remaining_after:.0f}₽")
        info(f"{'─' * 45}")

        return quantity

    def _get_volatility(self, ticker: str) -> float:
        """Получение волатильности для инструмента"""
        try:
            from trading_bot.analysis.technical_analyzer import analyzer
            candles = analyzer.fetch_candles(ticker, days=5)
            if len(candles) >= 20:
                prices = [c[0] for c in candles[-20:]]
                returns = []
                for i in range(1, len(prices)):
                    if prices[i - 1] > 0:
                        returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
                if returns:
                    return sum(abs(r) for r in returns) / len(returns)
        except Exception as e:
            debug(f"Ошибка расчёта волатильности для {ticker}: {e}")
        return 0.01

    def _get_minutes_to_end(self):
        """Получение минут до конца сессии"""
        now = get_moscow_time()
        current_time = now.time()

        MAIN_END = dt_time(18, 59)
        EVENING_END = dt_time(23, 49, 59)

        if current_time <= MAIN_END:
            end_time = now.replace(hour=18, minute=59, second=0, microsecond=0)
            minutes_left = (end_time - now).total_seconds() / 60
            return minutes_left, "main"
        else:
            end_time = now.replace(hour=23, minute=49, second=59, microsecond=0)
            minutes_left = (end_time - now).total_seconds() / 60
            return minutes_left, "evening"