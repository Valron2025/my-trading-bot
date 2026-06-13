"""Открытие позиций (LONG и SHORT)"""

import time
from datetime import datetime

from ..config import config
from ..models import StockCandidate, OrderSide
from ..logger import info, success, error, warning, debug
from ..utils.time_utils import get_moscow_time


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class PositionOpener:
    """Открытие Long и Short позиций"""

    def __init__(self, bot):
        self.bot = bot
        self._long_pending = {}
        self._short_pending = {}
        self._temp_tp = None
        self._temp_sl = None
        self._temp_ts = None

    def open_long_market(self, stock: StockCandidate, quantity: int) -> bool:
        """Открытие LONG позиции с проверкой дублирования"""

        # ========== 0. ПРОВЕРКА: НЕТ ЛИ УЖЕ ПОЗИЦИИ ПО ЭТОМУ ТИКЕРУ ==========
        try:
            from trading_bot.risk.position_manager import position_manager
            existing = position_manager.get_position(stock.figi)
            if existing:
                warning(f"⚠️ УЖЕ ЕСТЬ ПОЗИЦИЯ по {stock.ticker} ({existing.side.value})")
                warning(f"   Нельзя открыть вторую позицию по тому же инструменту!")
                return False
        except Exception as e:
            debug(f"Ошибка проверки существующей позиции: {e}")

        # ========== 1. ПРОВЕРКА ФЛАГА ОСТАНОВКИ ==========
        if self.bot._shutting_down:
            warning(f"🛑 Бот останавливается, пропускаем покупку {stock.name}")
            return False

        # ========== 2. ПРОВЕРКА РЫНКА ==========
        if not self.bot._is_trading_allowed(stock.ticker):
            error(f"❌ Рынок закрыт! Покупка {stock.name} невозможна")
            from trading_bot.risk.position_manager import position_manager
            position_manager.add_temp_skip_adaptive(stock.figi, error_code="MARKET_CLOSED", minutes=5)
            return False

        # ========== 3. ПОЛУЧАЕМ АКТУАЛЬНЫЕ ДАННЫЕ ==========
        try:
            available, total, _ = _get_tbank().get_available_funds()
            margin_info = _get_tbank().get_margin_info()
            available_margin = margin_info.get('available_margin', 0)
            margin_rate = margin_info.get('margin_rate', 0)

            info(f"\n📊 ДАННЫЕ ДЛЯ ПОКУПКИ {stock.name}:")
            info(f"   💰 Цена: {stock.price:.2f}₽")
            info(f"   📦 Лот: {stock.lot} шт")
            info(f"   💵 Свободно средств: {available:.2f}₽")
            info(f"   📈 Доступно маржи: {available_margin:.2f}₽")
            info(f"   📊 Текущая маржа: {margin_rate:.1f}%")
            info(f"   🎯 Score сигнала: {stock.analysis.score}")
        except Exception as e:
            debug(f"Не удалось получить данные для проверки: {e}")
            available = 0
            available_margin = 0
            margin_rate = 0

        # ========== 4. АВТОМАТИЧЕСКОЕ УМЕНЬШЕНИЕ РАЗМЕРА ==========
        total_cost = quantity * stock.price
        stop_loss_price = stock.price * (1 - config.stop_loss_pct / 100)
        required_for_close = quantity * stop_loss_price * 1.05

        # Проверка: достаточно ли средств
        if available > 0 and total_cost > available * 0.95:
            original_qty = quantity
            max_qty = int((available * 0.9) / stock.price / stock.lot) * stock.lot
            if max_qty >= stock.lot:
                quantity = max_qty
                total_cost = quantity * stock.price
                required_for_close = quantity * stop_loss_price * 1.05
                warning(f"   ⚠️ Уменьшаем позицию: {original_qty} → {quantity} шт из-за недостатка средств")
            else:
                error(f"❌ Недостаточно средств даже для минимального лота!")
                return False

        # Проверка: хватит ли средств для закрытия при стопе
        total_available = available + available_margin
        if total_available > 0 and required_for_close > total_available * 0.9:
            warning(f"   ⚠️ Недостаточно средств для закрытия при стопе, уменьшаем позицию...")
            original_qty = quantity
            max_safe_position = total_available * 0.7
            max_qty = int(max_safe_position / stop_loss_price / stock.lot) * stock.lot
            if max_qty >= stock.lot:
                quantity = max_qty
                total_cost = quantity * stock.price
                required_for_close = quantity * stop_loss_price * 1.05
                warning(f"   ⚠️ Уменьшаем позицию: {original_qty} → {quantity} шт")
            else:
                error(f"❌ Невозможно открыть позицию: даже минимальный лот требует {required_for_close:.0f}₽ для стопа")
                return False

        # Проверка остатка после сделки
        if available > 0:
            remaining_after = available - total_cost
            MIN_RESERVE = 300
            if remaining_after < MIN_RESERVE:
                warning(f"   ⚠️ После сделки останется всего {remaining_after:.0f}₽ (минимум {MIN_RESERVE}₽)")
                if quantity > stock.lot:
                    original_qty = quantity
                    quantity -= stock.lot
                    total_cost = quantity * stock.price
                    warning(f"   ⚠️ Уменьшаем позицию: {original_qty} → {quantity} шт")

        if quantity <= 0:
            error(f"❌ Невозможно открыть позицию: расчётное количество = 0")
            return False

        if quantity % stock.lot != 0:
            original_qty = quantity
            quantity = (quantity // stock.lot) * stock.lot
            if quantity <= 0:
                quantity = stock.lot
            warning(f"   ⚠️ Корректируем количество для кратности лоту: {original_qty} → {quantity} шт")
            total_cost = quantity * stock.price

        info(f"\n📊 ИТОГОВЫЙ РАСЧЁТ:")
        info(f"   🔢 Количество: {quantity} шт (лот: {stock.lot} шт)")
        info(f"   💰 Сумма сделки: {total_cost:.2f}₽")
        info(f"   🛑 Стоп-лосс: {stop_loss_price:.2f}₽")

        # ========== 5. ЗАЩИТА ОТ ДУБЛИРОВАНИЯ ==========
        pending_key = f"long_pending_{stock.figi}"
        if hasattr(self, '_long_pending') and pending_key in self._long_pending:
            elapsed = (get_moscow_time() - self._long_pending[pending_key]).seconds
            if elapsed < 30:
                warning(f"⚠️ LONG заявка для {stock.name} уже отправлена {elapsed}с назад")
                return False

        if not hasattr(self, '_long_pending'):
            self._long_pending = {}
        self._long_pending[pending_key] = get_moscow_time()

        try:
            info(f"🟢 ОТПРАВКА ЗАЯВКИ: покупка {quantity} шт {stock.name}")

            if _get_tbank().buy(stock.figi, quantity):
                success(f"\n✅ КУПИЛИ {stock.name} ({quantity} шт)!")

                try:
                    from trading_bot.risk.position_manager import position_manager
                    position_manager.add_position(
                        figi=stock.figi,
                        ticker=stock.ticker,
                        quantity=quantity,
                        price=stock.price,
                        side=OrderSide.LONG,
                        take_profit_pct=config.take_profit_pct,
                        stop_loss_pct=config.stop_loss_pct,
                        trailing_stop_pct=config.trailing_stop_pct
                    )
                except ImportError:
                    pass

                _get_telegram().send_trade_opened("LONG", stock.name, quantity, stock.price)

                if hasattr(self, '_long_pending') and pending_key in self._long_pending:
                    del self._long_pending[pending_key]
                return True

            error(f"\n❌ НЕ УДАЛОСЬ открыть LONG позицию {stock.name}")
            if hasattr(self, '_long_pending') and pending_key in self._long_pending:
                del self._long_pending[pending_key]
            return False

        except Exception as e:
            error(f"❌ Исключение при открытии LONG {stock.name}: {e}")
            if hasattr(self, '_long_pending') and pending_key in self._long_pending:
                del self._long_pending[pending_key]
            return False

    def open_short_market(self, stock: StockCandidate, quantity: int) -> bool:
        """Открытие SHORT позиции с проверкой дублирования"""

        # ========== 0. ПРОВЕРКА: НЕТ ЛИ УЖЕ ПОЗИЦИИ ПО ЭТОМУ ТИКЕРУ ==========
        try:
            from trading_bot.risk.position_manager import position_manager
            existing = position_manager.get_position(stock.figi)
            if existing:
                warning(f"⚠️ УЖЕ ЕСТЬ ПОЗИЦИЯ по {stock.ticker} ({existing.side.value})")
                warning(f"   Нельзя открыть вторую позицию по тому же инструменту!")
                return False
        except Exception as e:
            debug(f"Ошибка проверки существующей позиции: {e}")

        if self.bot._shutting_down:
            warning(f"🛑 Бот останавливается, пропускаем продажу {stock.name}")
            return False

        if not config.use_short:
            error(f"🔻 SHORT отключён автоматически! {stock.name} не будет открыт")
            from trading_bot.risk.position_manager import position_manager
            position_manager.add_temp_skip_adaptive(stock.figi, error_code="SHORT_DISABLED", minutes=1440)
            return False

        # ========== 1. ПРОВЕРКА НАЛИЧИЯ СРЕДСТВ ДЛЯ ЗАКРЫТИЯ ==========
        # Рассчитываем, сколько понадобится денег для закрытия SHORT при росте цены на 10%
        worst_case_price = stock.price * 1.10
        required_for_close = quantity * worst_case_price * 1.05  # +5% запас

        available, total, _ = _get_tbank().get_available_funds()

        if required_for_close > available * 0.85:
            error(f"\n❌ НЕДОСТАТОЧНО СРЕДСТВ ДЛЯ ЗАКРЫТИЯ SHORT ПРИ РОСТЕ ЦЕНЫ!")
            error(f"   Требуется для закрытия в худшем случае: {required_for_close:.0f}₽")
            error(f"   Доступно средств: {available:.0f}₽")
            error(f"   Разница: {required_for_close - available:.0f}₽")

            # Пробуем уменьшить размер позиции
            max_safe_qty = int(available * 0.7 / worst_case_price)
            lot = stock.lot
            if max_safe_qty >= lot:
                new_quantity = (max_safe_qty // lot) * lot
                if new_quantity > 0:
                    warning(f"   Уменьшаем позицию: {quantity} → {new_quantity} шт")
                    quantity = new_quantity
                    required_for_close = quantity * worst_case_price * 1.05
                else:
                    error(
                        f"❌ Невозможно открыть SHORT: даже минимальный лот требует {worst_case_price * lot:.0f}₽ для закрытия")
                    return False
            else:
                error(f"❌ Невозможно открыть SHORT: нужно пополнить счёт на {required_for_close - available:.0f}₽")
                return False

        # ========== 2. ПРОВЕРКА МАРЖИНАЛЬНОЙ ТОРГОВЛИ ==========
        margin_allowed, margin_reason = _get_tbank().check_margin_trading_allowed()
        if not margin_allowed:
            error(f"❌ Маржинальная торговля недоступна: {margin_reason}")
            from trading_bot.risk.position_manager import position_manager
            position_manager.add_temp_skip_adaptive(stock.figi, error_code="MARGIN_NOT_ALLOWED", minutes=60)
            return False

        # ========== 3. ЗАЩИТА ОТ ДУБЛИРОВАНИЯ ==========
        pending_key = f"short_pending_{stock.figi}"
        if hasattr(self, '_short_pending') and pending_key in self._short_pending:
            elapsed = (get_moscow_time() - self._short_pending[pending_key]).seconds
            if elapsed < 30:
                warning(f"⚠️ SHORT заявка для {stock.name} уже отправлена {elapsed}с назад")
                return False

        if not hasattr(self, '_short_pending'):
            self._short_pending = {}
        self._short_pending[pending_key] = get_moscow_time()

        # ========== 4. СБОР ДАННЫХ ДЛЯ ПРОВЕРОК ==========
        entry_value = quantity * stock.price
        stop_loss_price = stock.price * (1 + config.stop_loss_pct / 100)
        required_for_close = quantity * stop_loss_price * 1.05

        available, total, _ = _get_tbank().get_available_funds()
        margin_info = _get_tbank().get_margin_info()
        available_margin = margin_info.get('available_margin', 0)
        margin_rate = margin_info.get('margin_rate', 0)

        # ========== 5. ПРОВЕРКА СРЕДСТВ ==========
        worst_case_price = stock.price * 1.05
        required_for_worst_case = quantity * worst_case_price * 1.10

        if required_for_worst_case > available * 0.85:
            error(f"\n❌ НЕДОСТАТОЧНО СРЕДСТВ ДЛЯ ЗАКРЫТИЯ SHORT ПРИ РОСТЕ ЦЕНЫ!")
            error(f"   Требуется для закрытия: {required_for_worst_case:.0f}₽, Доступно: {available:.0f}₽")
            from trading_bot.risk.position_manager import position_manager
            position_manager.add_temp_skip_adaptive(stock.figi, error_code="INSUFFICIENT_WORST_CASE", minutes=1440)
            if pending_key in self._short_pending:
                del self._short_pending[pending_key]
            return False

        # Проверка остатка после открытия
        margin_required = entry_value * 0.5
        remaining_after = available - margin_required

        if remaining_after < required_for_worst_case * 0.5:
            error(f"\n❌ ПОСЛЕ ОТКРЫТИЯ SHORT НЕ ХВАТИТ СРЕДСТВ ДЛЯ ЗАКРЫТИЯ!")
            error(f"   Потребуется залог: {margin_required:.0f}₽")
            error(f"   После открытия останется: {remaining_after:.0f}₽")
            from trading_bot.risk.position_manager import position_manager
            position_manager.add_temp_skip_adaptive(stock.figi, error_code="INSUFFICIENT_REMAINING", minutes=1440)
            if pending_key in self._short_pending:
                del self._short_pending[pending_key]
            return False

        # ========== 6. ПРОВЕРКА МАРЖИ ==========
        if margin_rate >= 70:
            error(f"\n❌ СЛИШКОМ ВЫСОКОЕ ИСПОЛЬЗОВАНИЕ МАРЖИ!")
            error(f"   Текущая маржа: {margin_rate:.1f}% > 70%")
            from trading_bot.risk.position_manager import position_manager
            position_manager.add_temp_skip_adaptive(stock.figi, error_code="MARGIN_TOO_HIGH", minutes=30)
            if pending_key in self._short_pending:
                del self._short_pending[pending_key]
            return False

        # ========== 7. ПРОВЕРКА РАЗМЕРА ПОЗИЦИИ ==========
        total_available = available + available_margin
        if entry_value > total_available * 0.3:
            error(f"\n❌ СЛИШКОМ БОЛЬШАЯ ПОЗИЦИЯ!")
            error(f"   Сумма сделки: {entry_value:.0f}₽ ({entry_value / total_available * 100:.0f}% капитала)")
            max_qty = int(total_available * 0.3 / stock.price / max(stock.lot, 1)) * stock.lot
            error(f"   Используйте позицию не более {max_qty} шт")
            from trading_bot.risk.position_manager import position_manager
            position_manager.add_temp_skip_adaptive(stock.figi, error_code="POSITION_TOO_LARGE", minutes=30)
            if pending_key in self._short_pending:
                del self._short_pending[pending_key]
            return False

        # ========== 8. ФИНАЛЬНОЕ ПРЕДУПРЕЖДЕНИЕ ==========
        take_profit_price = stock.price * (1 - config.take_profit_pct / 100)
        info(f"\n{'=' * 50}")
        warning(f"⚠️ РЫНОЧНАЯ ПРОДАЖА (SHORT) {quantity} шт {stock.name}")
        info(f"   💰 Сумма: {entry_value:.0f}₽")
        info(f"   🛑 Залог: {entry_value * 0.5:.0f}₽ (~50% от суммы)")
        info(f"   🎯 Тейк-профит: {take_profit_price:.2f}₽")
        info(f"   🛑 Стоп-лосс: {stop_loss_price:.2f}₽")
        info(f"{'=' * 50}")

        # ========== 9. ИСПОЛНЕНИЕ ЗАЯВКИ ==========
        try:
            info(f"🔴 ОТПРАВКА ЗАЯВКИ: продажа SHORT {quantity} шт {stock.name}")

            if _get_tbank().sell(stock.figi, quantity):
                success(f"\n✅ ПРОДАЛИ {stock.name} (SHORT)!")

                try:
                    from trading_bot.risk.position_manager import position_manager
                    position_manager.add_position(
                        figi=stock.figi,
                        ticker=stock.ticker,
                        quantity=quantity,
                        price=stock.price,
                        side=OrderSide.SHORT,
                        take_profit_pct=config.take_profit_pct,
                        stop_loss_pct=config.stop_loss_pct,
                        trailing_stop_pct=config.trailing_stop_pct
                    )
                except ImportError:
                    pass

                _get_telegram().send_trade_opened("SHORT", stock.name, quantity, stock.price)

                if pending_key in self._short_pending:
                    del self._short_pending[pending_key]
                return True

            error(f"\n❌ НЕ УДАЛОСЬ открыть SHORT позицию {stock.name}")
            if pending_key in self._short_pending:
                del self._short_pending[pending_key]
            return False

        except Exception as e:
            error(f"\n❌ Исключение при открытии SHORT {stock.name}: {e}")
            if pending_key in self._short_pending:
                del self._short_pending[pending_key]
            return False

    def _add_temp_skip(self, figi: str, error_code: str = "", minutes: int = 10):
        """
        Добавление временной блокировки
        НЕ блокирует при ошибках: 30079 (рынок закрыт), 30049 (торги приостановлены)
        """
        try:
            from trading_bot.risk.position_manager import position_manager

            # Ошибки, при которых НЕ БЛОКИРУЕМ
            NO_BLOCK_ERRORS = {
                "30079": "рынок закрыт или инструмент временно недоступен",
                "30049": "торги приостановлены (выходной день)",
                "30014": "инструмент не найден"
            }

            for code, msg in NO_BLOCK_ERRORS.items():
                if code in error_code:
                    warning(f"⏸️ {figi}: {msg}, повторная попытка в следующем цикле")
                    return

            # Остальные ошибки - блокируем
            position_manager.add_temp_skip(figi, minutes)
            warning(f"🔒 {figi} заблокирован на {minutes} мин (ошибка: {error_code[:50]})")

        except ImportError:
            pass