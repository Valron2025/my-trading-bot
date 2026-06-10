"""Расчёт размера позиции с учётом маржи и рисков - ПОЛНАЯ ПРОДАКШН ВЕРСИЯ"""

from datetime import time as dt_time
from typing import Optional, List, Tuple

from ..config import config
from ..models import StockCandidate, OrderSide
from ..logger import info, error, warning, debug
from ..utils.time_utils import get_moscow_time
from ..risk.capital_manager import CapitalManager
from ..risk.advanced_risk_manager import advanced_risk_manager, TradeRecord
from trading_bot.cache import TTLCache


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


class PositionSizer:
    """Расчёт размера позиции с учётом маржинальной торговли и управления капиталом"""

    def __init__(self, bot):
        self.bot = bot
        self._position_cache = TTLCache(default_ttl=60, max_size=100, name="position_size_cache")
        self.capital_manager: Optional[CapitalManager] = None
        self.current_positions: List[dict] = []

    def update_positions(self, positions: List[dict]):
        """Обновление списка открытых позиций"""
        self.current_positions = positions

    def init_capital_manager(self, total_capital: float):
        """Инициализация менеджера капитала"""
        if self.capital_manager is None:
            self.capital_manager = CapitalManager(total_capital)
            info("✅ CapitalManager инициализирован")
        else:
            self.capital_manager.update_capital(total_capital)

    def calculate_cached(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
        """Расчёт размера позиции с кэшированием"""
        cache_key = f"{stock.ticker}_{stock.price:.2f}_{available_funds:.0f}_{score}_{stock.side.value}"

        cached = self._position_cache.get(cache_key)
        if cached is not None:
            debug(f"📦 Cache hit for position size: {stock.ticker}")
            return cached

        result = self.calculate(stock, available_funds, score)
        if result > 0:
            self._position_cache.set(cache_key, result, ttl=30)

        return result

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

    # ========== LONG ПОЗИЦИИ ==========

    def _calculate_long(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
        """Расчёт размера LONG позиции - ПОЛНОСТЬЮ АВТОМАТИЧЕСКИЙ"""
        from trading_bot.core.settings_manager import settings_manager
        import time

        price = stock.price
        lot = stock.lot
        ticker = stock.ticker

        available = available_funds
        _, total, _ = _get_tbank().get_available_funds()
        margin_info = _get_tbank().get_margin_info()
        margin_rate = margin_info.get('margin_rate', 0)
        available_margin = margin_info.get('available_margin', 0)

        info(f"\n{'═' * 50}")
        info(f"🔧 РАСЧЁТ LONG ПОЗИЦИИ: {ticker}")
        info(f"   💰 Свободно: {available_funds:.0f}₽")
        info(f"   💰 Общий капитал: {total:.0f}₽")
        info(f"{'═' * 50}")

        # ========== 1. ПРОВЕРКА ДОСТУПНОСТИ ИНСТРУМЕНТА (С БЛОКИРОВКОЙ) ==========
        # Инициализируем кэш для заблокированных тикеров
        if not hasattr(self, '_long_blocked_until'):
            self._long_blocked_until = {}

        # Проверяем, не в чёрном ли списке
        if ticker in self._long_blocked_until:
            if time.time() < self._long_blocked_until[ticker]:
                remaining = int(self._long_blocked_until[ticker] - time.time())
                warning(f"   🔻 LONG {ticker} временно заблокирован (ещё {remaining // 60} мин)")
                return 0
            else:
                del self._long_blocked_until[ticker]

        try:
            trading_status = _get_tbank().get_trading_status(stock.figi)
            if not trading_status.get('api_trade_available', False):
                warning(f"   🔻 LONG {ticker}: API торговля недоступна")
                self._long_blocked_until[ticker] = time.time() + 3600  # блокируем на 1 час
                return 0

            # Проверяем OTC
            if _get_tbank().is_confirmation_required(stock.figi):
                warning(f"   🔻 LONG {ticker}: OTC инструмент, требуется подтверждение сделок")
                self._long_blocked_until[ticker] = time.time() + 3600
                return 0

        except Exception as e:
            debug(f"   ⚠️ Ошибка проверки доступности {ticker}: {e}")
            return 0

        # ========== 2. РАСЧЁТ БАЗОВОГО РАЗМЕРА ==========
        # Резерв
        cash_reserve_pct = getattr(config, 'cash_reserve_pct', 0.20)
        min_cash_balance = getattr(config, 'min_cash_balance', 500.0)
        reserved_amount = max(total * cash_reserve_pct, min_cash_balance)
        available_for_trading = max(0, total - reserved_amount)

        use_margin = settings_manager.get('use_margin', False)
        total_available = available_for_trading + (available_margin if use_margin else 0)

        info(f"   💰 Резерв: {reserved_amount:.0f}₽ ({cash_reserve_pct * 100:.0f}%)")
        info(f"   💰 Доступно: {available_for_trading:.0f}₽")
        if use_margin:
            info(f"   💰 Маржинальная торговля ВКЛЮЧЕНА")

        if self.capital_manager:
            self.capital_manager.update_margin_rate(margin_rate)

        volatility = self._get_volatility(ticker)

        # Базовый процент (адаптация под капитал)
        if total < 3000:
            base_pct = 0.05
            max_pct = 0.10
        elif total < 7000:
            base_pct = 0.08
            max_pct = 0.15
        elif total < 15000:
            base_pct = 0.10
            max_pct = 0.20
        else:
            base_pct = 0.12
            max_pct = 0.25

        # Множитель сигнала
        if score >= 6:
            score_multiplier = 1.5
        elif score >= 4:
            score_multiplier = 1.3
        elif score >= 2:
            score_multiplier = 1.1
        elif score >= 1:
            score_multiplier = 1.0
        elif score == 0:
            score_multiplier = 0.7
        else:
            score_multiplier = 0.5

        position_pct = base_pct * score_multiplier

        # Корректировка по волатильности
        if volatility > 0.02:
            position_pct *= 0.7
        elif volatility > 0.015:
            position_pct *= 0.85
        elif volatility < 0.005:
            position_pct *= 1.2

        # Корректировка по марже
        if margin_rate > 70:
            position_pct *= 0.5
        elif margin_rate > 50:
            position_pct *= 0.7

        position_pct = min(position_pct, max_pct)

        # Минимальная позиция
        if score >= 0 and position_pct < 0.03:
            position_pct = 0.03
        if score < 0 and position_pct < 0.015:
            position_pct = 0.015

        max_position_value = total_available * position_pct

        # ========== 3. ОГРАНИЧЕНИЕ МАКСИМАЛЬНОЙ СУММЫ LONG ==========
        MAX_LONG_AMOUNT = 20000  # максимум 20,000₽ на один LONG
        # Для маленького капитала снижаем лимит
        if total < 30000:
            MAX_LONG_AMOUNT = 15000
        if total < 15000:
            MAX_LONG_AMOUNT = 10000

        if max_position_value > MAX_LONG_AMOUNT:
            old_value = max_position_value
            max_position_value = MAX_LONG_AMOUNT
            info(f"   ⚠️ LONG ограничен суммой {MAX_LONG_AMOUNT}₽: {old_value:.0f}₽ → {max_position_value:.0f}₽")

        quantity = int(max_position_value / price)
        original_quantity = quantity

        # ========== 4. КОРРЕКТИРОВКА ПО ЛОТНОСТИ ==========
        if lot > 1:
            quantity = (quantity // lot) * lot

        if quantity < lot:
            lot_cost = lot * price
            if lot_cost <= total * 0.5:
                quantity = lot
                info(f"   ⚠️ Увеличено до минимального лота: {quantity} шт")
            else:
                warning(f"   ❌ Минимальный лот {lot} шт стоит {lot_cost:.0f}₽ > 50% капитала")
                return 0

        total_cost = quantity * price

        # Проверка, что позиция не слишком большая
        if total_cost > total * 0.4:
            warning(f"   ⚠️ LONG позиция {total_cost:.0f}₽ > 40% капитала")
            # Пробуем уменьшить
            new_quantity = int(total * 0.3 / price)
            if lot > 1:
                new_quantity = (new_quantity // lot) * lot
            if new_quantity >= lot:
                quantity = new_quantity
                total_cost = quantity * price
                info(f"   🔧 Уменьшено до {quantity} шт (30% капитала)")
            else:
                return 0

        take_profit_pct = settings_manager.get('take_profit_pct', 1.0)
        stop_loss_pct = settings_manager.get('stop_loss_pct', 0.5)
        stop_loss_price = price * (1 - stop_loss_pct / 100)

        # ========== 5. ПРОВЕРКА БЕЗОПАСНОСТИ ==========
        required_for_close = quantity * stop_loss_price
        if required_for_close > available * 0.8:
            max_safe_quantity = int(available * 0.8 / stop_loss_price)
            if lot > 1:
                max_safe_quantity = (max_safe_quantity // lot) * lot
            if max_safe_quantity >= lot:
                quantity = max_safe_quantity
                total_cost = quantity * price
                info(f"   🔧 Уменьшено для безопасности: {quantity} шт")
            else:
                warning(f"   ❌ Даже минимальный лот не безопасен для закрытия")
                return 0

        # ========== 6. ФИНАЛЬНАЯ ПРОВЕРКА ==========
        # Если после округления количество сильно уменьшилось - предупреждаем
        if quantity < original_quantity * 0.5 and original_quantity > 0:
            warning(f"   ⚠️ LONG: количество уменьшилось с {original_quantity} до {quantity} (>50%)")

        position_percent = (total_cost / total * 100) if total > 0 else 0
        info(f"   📊 Итог: {quantity} шт ({position_pct * 100:.1f}% от капитала, {position_percent:.1f}% от портфеля)")

        return quantity if quantity >= lot else 0

    # ========== SHORT ПОЗИЦИИ ==========

    def _calculate_short(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
        """Расчёт размера SHORT позиции с автоматической блокировкой недоступных инструментов"""
        from trading_bot.core.settings_manager import settings_manager
        import time

        # Константы с возможностью переопределения через settings_manager
        MAX_SHORT_AMOUNT = settings_manager.get('max_short_amount', 10000)  # максимум 10,000₽ на один SHORT
        MIN_CAPITAL_FOR_SHORT = settings_manager.get('min_capital_for_short',
                                                     getattr(config, 'min_capital_for_short', 5000))
        CASH_RESERVE_PCT = settings_manager.get('cash_reserve_pct', getattr(config, 'cash_reserve_pct', 0.20))

        price = stock.price
        lot = stock.lot
        ticker = stock.ticker

        available = available_funds
        _, total, _ = _get_tbank().get_available_funds()
        margin_info = _get_tbank().get_margin_info()
        available_margin = margin_info.get('available_margin', 0)
        margin_rate = margin_info.get('margin_rate', 0)
        used_margin = margin_info.get('used_margin', 0)

        info(f"\n{'═' * 50}")
        info(f"🔧 РАСЧЁТ SHORT ПОЗИЦИИ: {ticker}")
        info(f"   💰 Свободно: {available_funds:.0f}₽")
        info(f"   💰 Общий капитал: {total:.0f}₽")
        info(f"{'═' * 50}")

        # ========== 1. ПРОВЕРКА ДОСТУПНОСТИ SHORT (С БЛОКИРОВКОЙ) ==========
        # Инициализируем кэш для заблокированных тикеров
        if not hasattr(self, '_short_blocked_until'):
            self._short_blocked_until = {}

        # Проверяем, не в чёрном ли списке
        if ticker in self._short_blocked_until:
            if time.time() < self._short_blocked_until[ticker]:
                remaining = int(self._short_blocked_until[ticker] - time.time())
                warning(f"   🔻 SHORT {ticker} временно заблокирован (ещё {remaining // 60} мин)")
                return 0
            else:
                del self._short_blocked_until[ticker]

        # Проверяем через API
        try:
            # Статус торгов
            trading_status = _get_tbank().get_trading_status(stock.figi)

            # Если нет рыночных И нет лимитных — нельзя торговать
            if not trading_status.get('market_order_available', False) and not trading_status.get(
                    'limit_order_available', False):
                warning(f"   🔻 SHORT {ticker} недоступен: нет доступных типов заявок")
                self._short_blocked_until[ticker] = time.time() + 3600
                return 0

            # OTC проверка
            if _get_tbank().is_confirmation_required(stock.figi):
                warning(f"   🔻 SHORT {ticker} недоступен: OTC инструмент требует подтверждения")
                self._short_blocked_until[ticker] = time.time() + 3600
                return 0

        except Exception as e:
            debug(f"   ⚠️ Ошибка проверки SHORT для {ticker}: {e}")
            return 0

        # ========== 2. ПРОВЕРКА КАПИТАЛА ==========
        if total < MIN_CAPITAL_FOR_SHORT:
            info(f"   🔻 SHORT недоступен: капитал {total:.0f}₽ < {MIN_CAPITAL_FOR_SHORT}₽")
            return 0

        max_margin_rate = settings_manager.get('max_margin_rate_for_short', 70)
        if margin_rate > max_margin_rate:
            warning(f"   🔻 SHORT недоступен: маржа {margin_rate:.1f}% > {max_margin_rate}%")
            return 0

        min_required = total * 0.1
        if available < min_required:
            warning(f"   🔻 Недостаточно средств: нужно ~{min_required:.0f}₽, есть {available:.0f}₽")
            return 0

        # ========== 2.5. ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА ЛИМИТА ==========
        min_lot_cost = lot * price
        if min_lot_cost > MAX_SHORT_AMOUNT:
            warning(f"   🔻 SHORT {ticker}: минимальный лот {lot} шт стоит {min_lot_cost:.0f}₽ > {MAX_SHORT_AMOUNT}₽")
            return 0

        # ========== 3. РАСЧЁТ РАЗМЕРА ==========
        # Резерв (можно настраивать)
        cash_reserve_pct = settings_manager.get('cash_reserve_pct', CASH_RESERVE_PCT)
        reserved_amount = max(total * cash_reserve_pct, 500.0)
        available_for_trading = max(0, total - reserved_amount)
        total_available = available_for_trading + available_margin

        volatility = self._get_volatility(ticker)

        # Базовый процент (можно настраивать через settings)
        short_base_pct = settings_manager.get('short_base_pct', {
            'thresholds': [(5000, 0.03, 0.08), (10000, 0.05, 0.10), (20000, 0.07, 0.12), (float('inf'), 0.09, 0.15)]
        })

        # Определяем базовый процент по капиталу
        base_pct = 0.09
        max_pct = 0.15
        for threshold, bp, mp in short_base_pct['thresholds']:
            if total < threshold:
                base_pct = bp
                max_pct = mp
                break

        abs_score = abs(score)

        # Множитель сигнала (можно настраивать)
        score_multipliers = settings_manager.get('short_score_multipliers', {
            8: 1.4, 6: 1.2, 4: 1.0, 2: 0.8, 1: 0.6, 0: 0.5
        })

        score_multiplier = 0.5
        for s, m in score_multipliers.items():
            if abs_score >= s:
                score_multiplier = m
                break

        short_pct = base_pct * score_multiplier

        # Корректировка по волатильности (можно настраивать)
        volatility_factors = settings_manager.get('short_volatility_factors', [(0.02, 0.5), (0.015, 0.7)])
        for threshold, factor in volatility_factors:
            if volatility > threshold:
                short_pct *= factor
                break

        # Корректировка по марже (можно настраивать)
        margin_factors = settings_manager.get('short_margin_factors', [(60, 0.5), (40, 0.7)])
        for threshold, factor in margin_factors:
            if margin_rate > threshold:
                short_pct *= factor
                break

        short_pct = min(short_pct, max_pct)

        # Минимальные пороги (можно настраивать)
        min_short_pct_high = settings_manager.get('min_short_pct_high', 0.02)
        min_short_pct_low = settings_manager.get('min_short_pct_low', 0.01)

        if abs_score >= 2 and short_pct < min_short_pct_high:
            short_pct = min_short_pct_high
        elif abs_score < 2 and short_pct < min_short_pct_low:
            short_pct = min_short_pct_low

        max_position_value = min(available_margin - used_margin, total_available * 0.3, total * short_pct)

        if max_position_value <= 0:
            warning(f"   🔻 Нет доступной маржи для SHORT")
            return 0

        quantity = int(max_position_value / price)
        original_quantity = quantity

        # ========== 4. КОРРЕКТИРОВКА ПО ЛОТНОСТИ ==========
        if lot > 1:
            quantity = (quantity // lot) * lot

        if quantity < lot:
            lot_cost = lot * price
            required_margin = lot_cost * 0.5
            if required_margin <= available_for_trading:
                quantity = lot
            else:
                warning(
                    f"   🔻 SHORT: минимальный лот {lot} шт стоит {lot_cost:.0f}₽, требуется маржа {required_margin:.0f}₽, доступно {available_for_trading:.0f}₽")
                return 0

        # ========== 5. ОГРАНИЧЕНИЕ МАКСИМАЛЬНОЙ СУММЫ SHORT ==========
        current_short_value = quantity * price
        if current_short_value > MAX_SHORT_AMOUNT:
            old_quantity = quantity
            old_value = current_short_value

            max_quantity = int(MAX_SHORT_AMOUNT / price)

            if lot > 1:
                max_quantity = (max_quantity // lot) * lot

            if max_quantity >= lot:
                quantity = max_quantity
                new_value = quantity * price
                info(
                    f"   ⚠️ SHORT ограничен суммой {MAX_SHORT_AMOUNT}₽: {old_quantity} шт ({old_value:.0f}₽) → {quantity} шт ({new_value:.0f}₽)")
            else:
                warning(
                    f"   🔻 SHORT {ticker}: даже минимальный лот {lot} шт стоит {lot * price:.0f}₽ > {MAX_SHORT_AMOUNT}₽")
                return 0

        if quantity <= 0:
            warning(f"   🔻 SHORT {ticker}: расчёт дал нулевое количество")
            return 0

        # ========== 6. ПРОВЕРКА ЗАКРЫТИЯ ==========
        worst_case_price = price * 1.10
        buy_back_cost = quantity * worst_case_price * 1.05

        if buy_back_cost > available * 0.9:
            max_safe_qty = int(available * 0.8 / worst_case_price)
            if max_safe_qty >= lot:
                new_qty = (max_safe_qty // lot) * lot
                if new_qty > quantity:
                    quantity = new_qty
                buy_back_cost = quantity * worst_case_price * 1.05
            else:
                warning(
                    f"   🔻 SHORT: даже минимальный лот {lot} шт требует {lot * worst_case_price * 1.05:.0f}₽ для закрытия")
                return 0

        remaining_after = available - buy_back_cost
        if remaining_after < total * 0.05:
            warning(f"   🔻 SHORT: после закрытия останется {remaining_after:.0f}₽ < {total * 0.05:.0f}₽ (5% капитала)")
            return 0

        # ========== 7. ФИНАЛЬНАЯ ПРОВЕРКА ==========
        if quantity < original_quantity * 0.7:
            warning(f"   ⚠️ SHORT: количество уменьшилось с {original_quantity} до {quantity} (>30%)")

        info(f"   📊 Итог: SHORT {quantity} шт ({short_pct * 100:.1f}% от капитала, сумма {quantity * price:.0f}₽)")
        return quantity if quantity >= lot else 0

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def record_closed_trade(self, ticker: str, entry_price: float, exit_price: float,
                            quantity: int, pnl: float, pnl_pct: float,
                            entry_time, exit_time, holding_minutes: float, side: str = 'LONG'):
        """Запись закрытой сделки в advanced_risk_manager"""
        try:
            trade_record = TradeRecord(
                ticker=ticker, side=side,
                entry_price=entry_price, exit_price=exit_price,
                quantity=quantity, pnl=pnl, pnl_pct=pnl_pct,
                entry_time=entry_time, exit_time=exit_time,
                holding_minutes=holding_minutes
            )
            advanced_risk_manager.add_trade(trade_record)
            info(f"   ✅ Сделка по {ticker} записана в историю")
        except Exception as e:
            error(f"   ❌ Ошибка записи сделки {ticker}: {e}")

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
            debug(f"Ошибка расчёта волатильности: {e}")
        return 0.01

    def _get_minutes_to_end(self) -> Tuple[float, str]:
        """Получение минут до конца сессии"""
        now = get_moscow_time()
        current_time = now.time()
        MAIN_END = dt_time(18, 59)
        EVENING_END = dt_time(23, 49, 59)

        if current_time <= MAIN_END:
            end_time = now.replace(hour=18, minute=59, second=0, microsecond=0)
            minutes_left = max(0, (end_time - now).total_seconds() / 60)
            return minutes_left, "main"
        else:
            end_time = now.replace(hour=23, minute=49, second=59, microsecond=0)
            minutes_left = max(0, (end_time - now).total_seconds() / 60)
            return minutes_left, "evening"


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
position_sizer = PositionSizer(None)