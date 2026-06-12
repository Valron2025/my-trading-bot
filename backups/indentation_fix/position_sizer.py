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
    price = stock.price
    if price <= 0:
        error(f"❌ Некорректная цена для {stock.ticker}: {price}")
        return 0
        """Расчёт размера LONG позиции - ПОЛНОСТЬЮ АВТОМАТИЧЕСКИЙ С ПОДРОБНЫМ ЛОГИРОВАНИЕМ"""
        from trading_bot.core.settings_manager import settings_manager
        import time
        from trading_bot.utils.time_utils import is_trading_time, is_weekend_trading_time, is_otc_trading_time, \
            get_moscow_time

        price = stock.price
        lot = stock.lot
        ticker = stock.ticker

        info(f"\n{'═' * 70}")
        info(f"🔧 [LONG] РАСЧЁТ ПОЗИЦИИ: {ticker}")
        info(f"{'═' * 70}")
        info(f"   📊 Входные данные:")
        info(f"      💰 Цена: {price:.4f}₽")
        info(f"      🔢 Лот: {lot} шт")
        info(f"      📈 Score: {score}")
        info(f"      💵 Доступно средств: {available_funds:.2f}₽")
        info(f"{'─' * 70}")

        # ========== 0. ПРОВЕРКА ВРЕМЕНИ ТОРГОВ ==========
        info(f"\n   ⏰ [ШАГ 0/12] ПРОВЕРКА ВРЕМЕНИ ТОРГОВ:")
        now = get_moscow_time()
        is_trading = is_trading_time()
        is_weekend = is_weekend_trading_time()
        is_otc = is_otc_trading_time()

        info(f"      🕐 Текущее время: {now.strftime('%H:%M:%S')}")
        info(f"      🏛️ Основная сессия: {'✅ ДА' if is_trading else '❌ НЕТ'}")
        info(f"      🌙 Выходные (ДСВД): {'✅ ДА' if is_weekend else '❌ НЕТ'}")
        info(f"      📞 OTC режим: {'✅ ДА' if is_otc else '❌ НЕТ'}")

        can_trade = is_trading or is_weekend or is_otc

        if not can_trade:
            info(f"      ⏸️ РЕЗУЛЬТАТ: ТОРГИ ЗАКРЫТЫ → возвращаем 0")
            return 0
        info(f"      ✅ РЕЗУЛЬТАТ: ТОРГИ ОТКРЫТЫ → продолжаем")

        # ========== 1. ПРОВЕРКА ДОСТУПНОСТИ ИНСТРУМЕНТА ==========
        info(f"\n   🔍 [ШАГ 1/12] ПРОВЕРКА ДОСТУПНОСТИ ИНСТРУМЕНТА:")

        if not hasattr(self, '_long_blocked_until'):
            self._long_blocked_until = {}
            info(f"      📦 Инициализирован кэш блокировок LONG")

        # Проверяем, не в чёрном ли списке
        if ticker in self._long_blocked_until:
            if time.time() < self._long_blocked_until[ticker]:
                remaining = int(self._long_blocked_until[ticker] - time.time())
                info(f"      ⛔ {ticker} В ЧЁРНОМ СПИСКЕ (ещё {remaining // 60} мин)")
                return 0
            else:
                del self._long_blocked_until[ticker]
                info(f"      🔓 {ticker} ВЫШЕЛ из чёрного списка")
        else:
            info(f"      ✅ {ticker} НЕ в чёрном списке")

        try:
            trading_status = _get_tbank().get_trading_status(stock.figi)
            info(f"      📊 Статус торгов:")
            info(f"         🔌 API торговля: {'✅' if trading_status.get('api_trade_available', False) else '❌'}")
            info(f"         🏷️ Рыночные заявки: {'✅' if trading_status.get('market_order_available', False) else '❌'}")
            info(f"         📋 Лимитные заявки: {'✅' if trading_status.get('limit_order_available', False) else '❌'}")

            if not trading_status.get('api_trade_available', False):
                info(f"      ❌ API торговля НЕДОСТУПНА → блокируем на 1 час")
                self._long_blocked_until[ticker] = time.time() + 3600
                return 0

            # Проверяем OTC
            is_otc_instrument = _get_tbank().is_confirmation_required(stock.figi)
            if is_otc_instrument:
                info(f"      ⚠️ OTC ИНСТРУМЕНТ (требует подтверждения) → блокируем на 1 час")
                self._long_blocked_until[ticker] = time.time() + 3600
                return 0
            else:
                info(f"      ✅ НЕ OTC инструмент")

        except Exception as e:
            info(f"      ❌ Ошибка проверки: {e}")
            return 0

        # ========== 2. ПОЛУЧЕНИЕ КАПИТАЛА И МАРЖИ ==========
        info(f"\n   💰 [ШАГ 2/12] ПОЛУЧЕНИЕ КАПИТАЛА И МАРЖИ:")

        try:
            _, total, _ = _get_tbank().get_available_funds()
            info(f"      📊 Общий капитал: {total:.2f}₽")

            margin_info = _get_tbank().get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)
            available_margin = margin_info.get('available_margin', 0)
            info(f"      📈 Маржа: {margin_rate:.1f}%")
            info(f"      💰 Доступно маржи: {available_margin:.2f}₽")

            if self.capital_manager:
                self.capital_manager.update_margin_rate(margin_rate)
        except Exception as e:
            info(f"      ⚠️ Ошибка получения капитала: {e}")
            total = available_funds
            margin_rate = 0
            available_margin = 0

        # ========== 3. РАСЧЁТ РЕЗЕРВОВ ==========
        info(f"\n   💰 [ШАГ 3/12] РАСЧЁТ РЕЗЕРВОВ:")

        cash_reserve_pct = getattr(config, 'cash_reserve_pct', 0.20)
        min_cash_balance = getattr(config, 'min_cash_balance', 500.0)
        reserved_amount = max(total * cash_reserve_pct, min_cash_balance)
        available_for_trading = max(0, total - reserved_amount)

        info(f"      🔒 Резерв: {cash_reserve_pct * 100:.0f}% = {reserved_amount:.2f}₽")
        info(f"      💵 Доступно для торговли: {available_for_trading:.2f}₽")

        use_margin = settings_manager.get('use_margin', False)
        total_available = available_for_trading + (available_margin if use_margin else 0)

        info(f"      💳 Маржинальная торговля: {'✅ ВКЛЮЧЕНА' if use_margin else '❌ ВЫКЛЮЧЕНА'}")
        info(f"      💰 ИТОГО ДОСТУПНО: {total_available:.2f}₽")

        # ========== 4. РАСЧЁТ ВОЛАТИЛЬНОСТИ ==========
        info(f"\n   📊 [ШАГ 4/12] РАСЧЁТ ВОЛАТИЛЬНОСТИ:")
        volatility = self._get_volatility(ticker)
        info(f"      📈 Волатильность: {volatility:.2%}")

        if volatility > 0.02:
            info(f"      ⚠️ Высокая волатильность → уменьшим размер позиции")
        elif volatility < 0.005:
            info(f"      📉 Низкая волатильность → можно увеличить размер")

        # ========== 5. БАЗОВЫЙ ПРОЦЕНТ ПОД КАПИТАЛ ==========
        info(f"\n   📐 [ШАГ 5/12] БАЗОВЫЙ ПРОЦЕНТ ПОД КАПИТАЛ:")

        if total < 3000:
            base_pct = 0.05
            max_pct = 0.10
            mode = "микро"
        elif total < 7000:
            base_pct = 0.08
            max_pct = 0.15
            mode = "малый"
        elif total < 15000:
            base_pct = 0.10
            max_pct = 0.20
            mode = "средний"
        else:
            base_pct = 0.12
            max_pct = 0.25
            mode = "крупный"

        info(f"      📊 Режим капитала: {mode} ({total:.0f}₽)")
        info(f"      📐 Базовый процент: {base_pct * 100:.1f}%")
        info(f"      📈 Максимальный процент: {max_pct * 100:.1f}%")

        # ========== 6. МНОЖИТЕЛЬ СИГНАЛА ==========
        info(f"\n   🎯 [ШАГ 6/12] МНОЖИТЕЛЬ СИГНАЛА:")

        if score >= 6:
            score_multiplier = 1.5
            signal_strength = "ЭКСТРЕМАЛЬНО СИЛЬНЫЙ"
        elif score >= 4:
            score_multiplier = 1.3
            signal_strength = "ОЧЕНЬ СИЛЬНЫЙ"
        elif score >= 2:
            score_multiplier = 1.1
            signal_strength = "СИЛЬНЫЙ"
        elif score >= 1:
            score_multiplier = 1.0
            signal_strength = "НОРМАЛЬНЫЙ"
        elif score == 0:
            score_multiplier = 0.7
            signal_strength = "СЛАБЫЙ"
        else:
            score_multiplier = 0.5
            signal_strength = "ОЧЕНЬ СЛАБЫЙ"

        info(f"      📈 Score: {score}")
        info(f"      💪 Сила сигнала: {signal_strength}")
        info(f"      🔢 Множитель: {score_multiplier}")

        # ========== 7. ИТОГОВЫЙ ПРОЦЕНТ ПОЗИЦИИ ==========
        info(f"\n   📐 [ШАГ 7/12] ИТОГОВЫЙ ПРОЦЕНТ ПОЗИЦИИ:")

        position_pct = base_pct * score_multiplier
        info(f"      📊 Начальный процент: {position_pct * 100:.2f}%")

        # Корректировка по волатильности
        if volatility > 0.02:
            old_pct = position_pct
            position_pct *= 0.7
            info(f"      📉 Высокая волатильность → ×0.7: {old_pct * 100:.2f}% → {position_pct * 100:.2f}%")
        elif volatility > 0.015:
            old_pct = position_pct
            position_pct *= 0.85
            info(f"      📈 Средняя волатильность → ×0.85: {old_pct * 100:.2f}% → {position_pct * 100:.2f}%")
        elif volatility < 0.005:
            old_pct = position_pct
            position_pct *= 1.2
            info(f"      📉 Низкая волатильность → ×1.2: {old_pct * 100:.2f}% → {position_pct * 100:.2f}%")

        # Корректировка по марже
        if margin_rate > 70:
            old_pct = position_pct
            position_pct *= 0.5
            info(f"      🔴 Высокая маржа {margin_rate:.0f}% → ×0.5: {old_pct * 100:.2f}% → {position_pct * 100:.2f}%")
        elif margin_rate > 50:
            old_pct = position_pct
            position_pct *= 0.7
            info(f"      🟡 Средняя маржа {margin_rate:.0f}% → ×0.7: {old_pct * 100:.2f}% → {position_pct * 100:.2f}%")
        else:
            info(f"      🟢 Нормальная маржа {margin_rate:.0f}% → без изменений")

        position_pct = min(position_pct, max_pct)
        info(f"      🎯 ИТОГОВЫЙ ПРОЦЕНТ: {position_pct * 100:.2f}% (макс {max_pct * 100:.0f}%)")

        # ========== 8. РАСЧЁТ МАКСИМАЛЬНОЙ СУММЫ ==========
        info(f"\n   💰 [ШАГ 8/12] РАСЧЁТ МАКСИМАЛЬНОЙ СУММЫ ПОЗИЦИИ:")

        max_position_value = total_available * position_pct
        info(f"      📊 По проценту: {max_position_value:.2f}₽")

        # Ограничение максимальной суммы LONG
        if total < 15000:
            MAX_LONG_AMOUNT = 10000
        elif total < 30000:
            MAX_LONG_AMOUNT = 15000
        else:
            MAX_LONG_AMOUNT = 20000

        if max_position_value > MAX_LONG_AMOUNT:
            info(f"      ⚠️ Ограничение MAX_LONG_AMOUNT: {MAX_LONG_AMOUNT}₽")
            max_position_value = MAX_LONG_AMOUNT

        info(f"      💰 ИТОГО МАКСИМУМ: {max_position_value:.2f}₽")

        # ========== 9. РАСЧЁТ КОЛИЧЕСТВА ==========
        info(f"\n   🔢 [ШАГ 9/12] РАСЧЁТ КОЛИЧЕСТВА:")

        quantity = int(max_position_value / price)
        original_quantity = quantity
        info(f"      🔢 Расчётное количество: {quantity} шт")

        # Корректировка по лотности
        if lot > 1:
            old_qty = quantity
            quantity = (quantity // lot) * lot
            info(f"      🔄 Корректировка по лоту {lot}: {old_qty} → {quantity} шт")

        if quantity < lot:
            lot_cost = lot * price
            if lot_cost <= total * 0.5:
                quantity = lot
                info(f"      ⚠️ Увеличено до минимального лота: {quantity} шт (стоимость {lot_cost:.2f}₽)")
            else:
                info(f"      ❌ Минимальный лот {lot} шт стоит {lot_cost:.2f}₽ > 50% капитала")
                return 0
        else:
            info(f"      ✅ Количество корректно: {quantity} шт")

        total_cost = quantity * price
        info(f"      💰 Стоимость позиции: {total_cost:.2f}₽")

        # ========== 10. ПРОВЕРКА БЕЗОПАСНОСТИ ==========
        info(f"\n   🛡️ [ШАГ 10/12] ПРОВЕРКА БЕЗОПАСНОСТИ:")

        stop_loss_pct = settings_manager.get('stop_loss_pct', 0.5)
        stop_loss_price = price * (1 - stop_loss_pct / 100)
        required_for_close = quantity * stop_loss_price

        info(f"      🛑 Стоп-лосс: {stop_loss_pct}% = {stop_loss_price:.2f}₽")
        info(f"      💰 Требуется для закрытия: {required_for_close:.2f}₽")
        info(f"      💵 Доступно средств: {available_funds:.2f}₽")  # ← ИСПРАВЛЕНО

        if required_for_close > available_funds * 0.8:  # ← ИСПРАВЛЕНО
            info(f"      ⚠️ Недостаточно средств для закрытия → уменьшаем позицию")
            max_safe_quantity = int(available_funds * 0.8 / stop_loss_price)  # ← ИСПРАВЛЕНО
            if lot > 1:
                max_safe_quantity = (max_safe_quantity // lot) * lot
            if max_safe_quantity >= lot:
                quantity = max_safe_quantity
                total_cost = quantity * price
                info(f"      🔧 Уменьшено до {quantity} шт (безопасный размер)")
            else:
                info(f"      ❌ Даже минимальный лот не безопасен")
                return 0
        else:
            info(f"      ✅ Безопасно")

        # ========== 11. ФИНАЛЬНАЯ ПРОВЕРКА ==========
        info(f"\n   ✅ [ШАГ 11/12] ФИНАЛЬНАЯ ПРОВЕРКА:")

        if quantity < original_quantity * 0.5 and original_quantity > 0:
            info(f"      ⚠️ Количество уменьшилось на {(1 - quantity / original_quantity) * 100:.0f}%")

        position_percent = (total_cost / total * 100) if total > 0 else 0
        info(f"      📊 Итоговый процент портфеля: {position_percent:.1f}%")
        info(f"      🎯 Итоговое количество: {quantity} шт")

        final_result = quantity if quantity >= lot else 0
        info(f"      {'✅' if final_result > 0 else '❌'} РЕЗУЛЬТАТ: {final_result}")

        # ========== 12. ИТОГОВЫЙ ОТЧЁТ ==========
        info(f"\n{'═' * 70}")
        if final_result > 0:
            info(f"✅ [LONG] {ticker}: РАЗМЕР ПОЗИЦИИ = {final_result} шт")
            info(f"   💰 Сумма: {total_cost:.2f}₽")
            info(f"   📊 Процент капитала: {position_pct * 100:.1f}%")
            info(f"   🛡️ Стоп-лосс: {stop_loss_price:.2f}₽ ({stop_loss_pct}%)")
        else:
            info(f"❌ [LONG] {ticker}: НЕЛЬЗЯ ОТКРЫТЬ ПОЗИЦИЮ")
        info(f"{'═' * 70}")

        return final_result

    # ========== SHORT ПОЗИЦИИ ==========

    def _calculate_short(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
    price = stock.price
    if price <= 0:
        error(f"❌ Некорректная цена для {stock.ticker}: {price}")
        return 0
        """Расчёт размера SHORT позиции с автоматической блокировкой недоступных инструментов"""
        from trading_bot.core.settings_manager import settings_manager
        import time
        from trading_bot.utils.time_utils import is_trading_time, is_weekend_trading_time, is_otc_trading_time, \
            get_moscow_time

        price = stock.price
        lot = stock.lot
        ticker = stock.ticker

        info(f"\n{'═' * 70}")
        info(f"🔧 [SHORT] РАСЧЁТ ПОЗИЦИИ: {ticker}")
        info(f"{'═' * 70}")
        info(f"   📊 Входные данные:")
        info(f"      💰 Цена: {price:.4f}₽")
        info(f"      🔢 Лот: {lot} шт")
        info(f"      📈 Score: {score}")
        info(f"      💵 Доступно средств: {available_funds:.2f}₽")
        info(f"{'─' * 70}")

        # ========== 0. ПРОВЕРКА ВРЕМЕНИ ТОРГОВ ==========
        info(f"\n   ⏰ [ШАГ 0/12] ПРОВЕРКА ВРЕМЕНИ ТОРГОВ:")
        now = get_moscow_time()
        is_trading = is_trading_time()
        is_weekend = is_weekend_trading_time()
        is_otc = is_otc_trading_time()

        info(f"      🕐 Текущее время: {now.strftime('%H:%M:%S')}")
        info(f"      🏛️ Основная сессия: {'✅ ДА' if is_trading else '❌ НЕТ'}")
        info(f"      🌙 Выходные (ДСВД): {'✅ ДА' if is_weekend else '❌ НЕТ'}")
        info(f"      📞 OTC режим: {'✅ ДА' if is_otc else '❌ НЕТ'}")

        can_trade = is_trading or is_weekend or is_otc

        if not can_trade:
            info(f"      ⏸️ РЕЗУЛЬТАТ: ТОРГИ ЗАКРЫТЫ → возвращаем 0")
            return 0
        info(f"      ✅ РЕЗУЛЬТАТ: ТОРГИ ОТКРЫТЫ → продолжаем")

        # ========== 1. ПРОВЕРКА ДОСТУПНОСТИ SHORT ==========
        info(f"\n   🔍 [ШАГ 1/12] ПРОВЕРКА ДОСТУПНОСТИ SHORT:")

        MAX_SHORT_AMOUNT = settings_manager.get('max_short_amount', 10000)
        MIN_CAPITAL_FOR_SHORT = settings_manager.get('min_capital_for_short', 5000)
        CASH_RESERVE_PCT = settings_manager.get('cash_reserve_pct', 0.20)

        if not hasattr(self, '_short_blocked_until'):
            self._short_blocked_until = {}
            info(f"      📦 Инициализирован кэш блокировок SHORT")

        # Проверяем, не в чёрном ли списке
        if ticker in self._short_blocked_until:
            if time.time() < self._short_blocked_until[ticker]:
                remaining = int(self._short_blocked_until[ticker] - time.time())
                info(f"      ⛔ {ticker} В ЧЁРНОМ СПИСКЕ (ещё {remaining // 60} мин)")
                return 0
            else:
                del self._short_blocked_until[ticker]
                info(f"      🔓 {ticker} ВЫШЕЛ из чёрного списка")
        else:
            info(f"      ✅ {ticker} НЕ в чёрном списке")

        try:
            trading_status = _get_tbank().get_trading_status(stock.figi)
            info(f"      📊 Статус торгов:")
            info(f"         🔌 API торговля: {'✅' if trading_status.get('api_trade_available', False) else '❌'}")
            info(f"         🏷️ Рыночные заявки: {'✅' if trading_status.get('market_order_available', False) else '❌'}")
            info(f"         📋 Лимитные заявки: {'✅' if trading_status.get('limit_order_available', False) else '❌'}")

            # Если нет рыночных И нет лимитных — нельзя торговать
            if not trading_status.get('market_order_available', False) and not trading_status.get(
                    'limit_order_available', False):
                info(f"      ❌ НЕТ доступных типов заявок → блокируем на 1 час")
                self._short_blocked_until[ticker] = time.time() + 3600
                return 0

            # OTC проверка
            if _get_tbank().is_confirmation_required(stock.figi):
                info(f"      ⚠️ OTC ИНСТРУМЕНТ (требует подтверждения) → блокируем на 1 час")
                self._short_blocked_until[ticker] = time.time() + 3600
                return 0
            else:
                info(f"      ✅ НЕ OTC инструмент")

        except Exception as e:
            info(f"      ❌ Ошибка проверки: {e}")
            return 0

        # ========== 2. ПОЛУЧЕНИЕ КАПИТАЛА И МАРЖИ ==========
        info(f"\n   💰 [ШАГ 2/12] ПОЛУЧЕНИЕ КАПИТАЛА И МАРЖИ:")

        try:
            _, total, _ = _get_tbank().get_available_funds()
            info(f"      📊 Общий капитал: {total:.2f}₽")

            margin_info = _get_tbank().get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)
            available_margin = margin_info.get('available_margin', 0)
            used_margin = margin_info.get('used_margin', 0)
            info(f"      📈 Маржа: {margin_rate:.1f}%")
            info(f"      💰 Доступно маржи: {available_margin:.2f}₽")
            info(f"      🔒 Использовано маржи: {used_margin:.2f}₽")
        except Exception as e:
            info(f"      ⚠️ Ошибка получения капитала: {e}")
            total = available_funds
            margin_rate = 0
            available_margin = 0
            used_margin = 0

        # ========== 3. ПРОВЕРКА КАПИТАЛА ДЛЯ SHORT ==========
        info(f"\n   💰 [ШАГ 3/12] ПРОВЕРКА КАПИТАЛА ДЛЯ SHORT:")

        if total < MIN_CAPITAL_FOR_SHORT:
            info(f"      ❌ Капитал {total:.0f}₽ < {MIN_CAPITAL_FOR_SHORT}₽ → SHORT недоступен")
            return 0
        else:
            info(f"      ✅ Капитал достаточен: {total:.0f}₽ >= {MIN_CAPITAL_FOR_SHORT}₽")

        max_margin_rate = settings_manager.get('max_margin_rate_for_short', 70)
        if margin_rate > max_margin_rate:
            info(f"      ❌ Маржа {margin_rate:.1f}% > {max_margin_rate}% → SHORT недоступен")
            return 0
        else:
            info(f"      ✅ Маржа в норме: {margin_rate:.1f}% <= {max_margin_rate}%")

        min_required = total * 0.1
        if available_funds < min_required:
            info(f"      ❌ Недостаточно средств: нужно ~{min_required:.0f}₽, есть {available_funds:.0f}₽")
            return 0
        else:
            info(f"      ✅ Средств достаточно: {available_funds:.0f}₽ >= {min_required:.0f}₽")

        # ========== 4. РАСЧЁТ ВОЛАТИЛЬНОСТИ ==========
        info(f"\n   📊 [ШАГ 4/12] РАСЧЁТ ВОЛАТИЛЬНОСТИ:")
        volatility = self._get_volatility(ticker)
        info(f"      📈 Волатильность: {volatility:.2%}")

        # ========== 5. БАЗОВЫЙ ПРОЦЕНТ ==========
        info(f"\n   📐 [ШАГ 5/12] БАЗОВЫЙ ПРОЦЕНТ:")

        short_base_pct = settings_manager.get('short_base_pct', {
            'thresholds': [(5000, 0.03, 0.08), (10000, 0.05, 0.10), (20000, 0.07, 0.12), (float('inf'), 0.09, 0.15)]
        })

        base_pct = 0.09
        max_pct = 0.15
        for threshold, bp, mp in short_base_pct['thresholds']:
            if total < threshold:
                base_pct = bp
                max_pct = mp
                break

        info(f"      📊 Базовый процент: {base_pct * 100:.1f}%")
        info(f"      📈 Максимальный процент: {max_pct * 100:.1f}%")

        # ========== 6. МНОЖИТЕЛЬ СИГНАЛА ==========
        info(f"\n   🎯 [ШАГ 6/12] МНОЖИТЕЛЬ СИГНАЛА:")

        abs_score = abs(score)
        score_multipliers = settings_manager.get('short_score_multipliers', {
            8: 1.4, 6: 1.2, 4: 1.0, 2: 0.8, 1: 0.6, 0: 0.5
        })

        score_multiplier = 0.5
        for s, m in score_multipliers.items():
            if abs_score >= s:
                score_multiplier = m
                break

        info(f"      📈 Score: {score} (|{abs_score}|)")
        info(f"      🔢 Множитель сигнала: {score_multiplier}")

        # ========== 7. ИТОГОВЫЙ ПРОЦЕНТ SHORT ==========
        info(f"\n   📐 [ШАГ 7/12] ИТОГОВЫЙ ПРОЦЕНТ SHORT:")

        short_pct = base_pct * score_multiplier
        info(f"      📊 Начальный процент: {short_pct * 100:.2f}%")

        # Корректировка по волатильности
        volatility_factors = settings_manager.get('short_volatility_factors', [(0.02, 0.5), (0.015, 0.7)])
        for threshold, factor in volatility_factors:
            if volatility > threshold:
                old_pct = short_pct
                short_pct *= factor
                info(
                    f"      📉 Волатильность {volatility:.2%} > {threshold:.1%} → ×{factor}: {old_pct * 100:.2f}% → {short_pct * 100:.2f}%")
                break

        # Корректировка по марже
        margin_factors = settings_manager.get('short_margin_factors', [(60, 0.5), (40, 0.7)])
        for threshold, factor in margin_factors:
            if margin_rate > threshold:
                old_pct = short_pct
                short_pct *= factor
                info(
                    f"      🔴 Маржа {margin_rate:.0f}% > {threshold}% → ×{factor}: {old_pct * 100:.2f}% → {short_pct * 100:.2f}%")
                break

        short_pct = min(short_pct, max_pct)
        info(f"      🎯 ИТОГОВЫЙ ПРОЦЕНТ: {short_pct * 100:.2f}% (макс {max_pct * 100:.0f}%)")

        # ========== 8. РАСЧЁТ ДОСТУПНОЙ МАРЖИ ==========
        info(f"\n   💰 [ШАГ 8/12] РАСЧЁТ ДОСТУПНОЙ МАРЖИ:")

        cash_reserve_pct = settings_manager.get('cash_reserve_pct', CASH_RESERVE_PCT)
        reserved_amount = max(total * cash_reserve_pct, 500.0)
        available_for_trading = max(0, total - reserved_amount)
        total_available = available_for_trading + available_margin

        info(f"      🔒 Резерв: {cash_reserve_pct * 100:.0f}% = {reserved_amount:.2f}₽")
        info(f"      💵 Доступно: {available_for_trading:.2f}₽")
        info(f"      💳 Маржа: {available_margin:.2f}₽")
        info(f"      💰 ИТОГО ДОСТУПНО: {total_available:.2f}₽")

        max_position_value = min(available_margin - used_margin, total_available * 0.3, total * short_pct)

        if max_position_value <= 0:
            info(f"      ❌ Нет доступной маржи для SHORT")
            return 0
        info(f"      💰 Максимальная сумма позиции: {max_position_value:.2f}₽")

        # ========== 9. РАСЧЁТ КОЛИЧЕСТВА ==========
        info(f"\n   🔢 [ШАГ 9/12] РАСЧЁТ КОЛИЧЕСТВА:")

        quantity = int(max_position_value / price)
        original_quantity = quantity
        info(f"      🔢 Расчётное количество: {quantity} шт")

        # Корректировка по лотности
        if lot > 1:
            old_qty = quantity
            quantity = (quantity // lot) * lot
            info(f"      🔄 Корректировка по лоту {lot}: {old_qty} → {quantity} шт")

        if quantity < lot:
            lot_cost = lot * price
            required_margin = lot_cost * 0.5
            if required_margin <= available_for_trading:
                quantity = lot
                info(f"      ⚠️ Увеличено до минимального лота: {quantity} шт (стоимость {lot_cost:.2f}₽)")
            else:
                info(
                    f"      ❌ Минимальный лот {lot} шт требует {required_margin:.0f}₽ маржи, доступно {available_for_trading:.0f}₽")
                return 0
        else:
            info(f"      ✅ Количество корректно: {quantity} шт")

        # ========== 10. ОГРАНИЧЕНИЕ МАКСИМАЛЬНОЙ СУММЫ ==========
        info(f"\n   ⚠️ [ШАГ 10/12] ОГРАНИЧЕНИЕ МАКСИМАЛЬНОЙ СУММЫ:")

        current_short_value = quantity * price
        info(f"      💰 Текущая сумма: {current_short_value:.2f}₽")
        info(f"      📊 Лимит SHORT: {MAX_SHORT_AMOUNT}₽")

        if current_short_value > MAX_SHORT_AMOUNT:
            old_quantity = quantity
            max_quantity = int(MAX_SHORT_AMOUNT / price)
            if lot > 1:
                max_quantity = (max_quantity // lot) * lot
            if max_quantity >= lot:
                quantity = max_quantity
                new_value = quantity * price
                info(
                    f"      ⚠️ Уменьшено: {old_quantity} шт ({old_quantity * price:.0f}₽) → {quantity} шт ({new_value:.0f}₽)")
            else:
                info(f"      ❌ Даже минимальный лот превышает лимит")
                return 0
        else:
            info(f"      ✅ В пределах лимита")

        if quantity <= 0:
            info(f"      ❌ Расчёт дал нулевое количество")
            return 0

        # ========== 11. ПРОВЕРКА ЗАКРЫТИЯ ==========
        info(f"\n   🛡️ [ШАГ 11/12] ПРОВЕРКА ЗАКРЫТИЯ SHORT:")

        worst_case_price = price * 1.10
        buy_back_cost = quantity * worst_case_price * 1.05

        info(f"      📈 Худшая цена для закрытия: {worst_case_price:.2f}₽ (+10%)")
        info(f"      💰 Требуется для закрытия: {buy_back_cost:.2f}₽")
        info(f"      💵 Доступно средств: {available_funds:.2f}₽")

        if buy_back_cost > available_funds * 0.9:
            info(f"      ⚠️ Недостаточно средств для закрытия → уменьшаем")
            max_safe_qty = int(available_funds * 0.8 / worst_case_price)
            if max_safe_qty >= lot:
                new_qty = (max_safe_qty // lot) * lot
                if new_qty > quantity:
                    quantity = new_qty
                buy_back_cost = quantity * worst_case_price * 1.05
                info(f"      🔧 Уменьшено до {quantity} шт")
            else:
                info(f"      ❌ Даже минимальный лот не безопасен")
                return 0
        else:
            info(f"      ✅ Безопасно")

        remaining_after = available_funds - buy_back_cost
        info(f"      💰 Останется после закрытия: {remaining_after:.2f}₽")

        if remaining_after < total * 0.05:
            info(f"      ❌ После закрытия останется {remaining_after:.0f}₽ < 5% капитала")
            return 0
        else:
            info(f"      ✅ Достаточный запас")

        # ========== 12. ФИНАЛЬНАЯ ПРОВЕРКА ==========
        info(f"\n   ✅ [ШАГ 12/12] ФИНАЛЬНАЯ ПРОВЕРКА:")

        if quantity < original_quantity * 0.7:
            info(f"      ⚠️ Количество уменьшилось на {(1 - quantity / original_quantity) * 100:.0f}%")

        final_short_value = quantity * price
        info(f"      💰 Итоговая сумма: {final_short_value:.2f}₽")
        info(f"      📊 Процент капитала: {short_pct * 100:.1f}%")

        final_result = quantity if quantity >= lot else 0
        info(f"      {'✅' if final_result > 0 else '❌'} РЕЗУЛЬТАТ: {final_result}")

        # ========== 13. ИТОГОВЫЙ ОТЧЁТ ==========
        info(f"\n{'═' * 70}")
        if final_result > 0:
            info(f"✅ [SHORT] {ticker}: РАЗМЕР ПОЗИЦИИ = {final_result} шт")
            info(f"   💰 Сумма: {final_short_value:.2f}₽")
            info(f"   📊 Процент капитала: {short_pct * 100:.1f}%")
        else:
            info(f"❌ [SHORT] {ticker}: НЕЛЬЗЯ ОТКРЫТЬ ПОЗИЦИЮ")
        info(f"{'═' * 70}")

        return final_result

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
                    result = sum(abs(r) for r in returns) / len(returns)
            return max(0.005, min(0.05, result))
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