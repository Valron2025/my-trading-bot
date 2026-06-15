"""Открытие позиций (LONG и SHORT) - ПОЛНАЯ ВЕРСИЯ"""

import time
from datetime import datetime
from typing import Optional

from ..config import config
from ..models import StockCandidate, OrderSide, StockAnalysis
from ..logger import info, success, error, warning, debug
from ..utils.time_utils import get_moscow_time


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


def _get_instrument_filter():
    """Ленивый импорт InstrumentFilter для избежания циклических импортов"""
    from trading_bot.analysis.instrument_filter import instrument_filter
    return instrument_filter


class PositionOpener:
    """Открытие позиций с поддержкой умных заявок и проверкой средств"""

    def __init__(self, bot):
        self.bot = bot
        self._long_pending = {}
        self._short_pending = {}
        self._temp_tp = None
        self._temp_sl = None
        self._temp_ts = None
        self._position_manager = None

    def _get_position_manager(self):
        """Ленивое получение PositionManager для избежания циклических импортов"""
        if self._position_manager is None:
            from trading_bot.risk.position_manager import position_manager
            self._position_manager = position_manager
        return self._position_manager

    def _get_order_type(self, figi: str = None, ticker: str = None) -> tuple:
        """
        УМНЫЙ ВЫБОР ТИПА ЗАЯВКИ с учётом:
        1. Доступности рыночных заявок для инструмента
        2. Текущей торговой сессии
        3. Настроек бота

        Returns:
            (use_market: bool, reason: str)
        """
        from trading_bot.utils.time_utils import (
            get_moscow_time, is_main_session_time,
            is_evening_session_time, is_pre_market_time
        )
        from trading_bot.api.tbank_client import tbank

        now = get_moscow_time()
        ticker_str = ticker or (figi[:8] if figi else "unknown")

        info(f"   🔍 ВЫБОР ТИПА ЗАЯВКИ ДЛЯ {ticker_str}:")

        # ========== 1. ПРОВЕРКА ДОСТУПНОСТИ РЫНОЧНЫХ ЗАЯВОК ДЛЯ ИНСТРУМЕНТА ==========
        market_available = True
        if figi:
            try:
                status = tbank.get_trading_status(figi)
                market_available = status.get('market_order_available', False)
                info(
                    f"      📊 Рыночные заявки для {ticker_str}: {'✅ ДОСТУПНЫ' if market_available else '❌ НЕ ДОСТУПНЫ'}")
            except Exception as e:
                debug(f"      ⚠️ Ошибка проверки: {e}")

        # ========== 2. ЕСЛИ РЫНОЧНЫЕ НЕ ДОСТУПНЫ - ТОЛЬКО ЛИМИТНЫЕ ==========
        if not market_available:
            info(f"      📋 ИСПОЛЬЗУЕМ: ЛИМИТНУЮ ЗАЯВКУ (рыночные недоступны)")
            return False, f"limit (market orders not available for {ticker_str})"

        # ========== 3. ПРИНУДИТЕЛЬНОЕ ИСПОЛЬЗОВАНИЕ ЛИМИТНЫХ ИЗ НАСТРОЕК ==========
        if getattr(config, 'use_limit_orders', False):
            if is_evening_session_time():
                warning(f"      ⚠️ Вечерняя сессия: лимитные заявки НЕ РАБОТАЮТ!")
                info(f"      🟢 ИСПОЛЬЗУЕМ: РЫНОЧНУЮ ЗАЯВКУ")
                return True, "market (evening session forced)"
            info(f"      📋 ИСПОЛЬЗУЕМ: ЛИМИТНУЮ ЗАЯВКУ (config forced)")
            return False, "limit (config forced)"

        # ========== 4. ПРИНУДИТЕЛЬНОЕ ИСПОЛЬЗОВАНИЕ РЫНОЧНЫХ ИЗ НАСТРОЕК ==========
        if getattr(config, 'use_market_orders', False):
            info(f"      🟢 ИСПОЛЬЗУЕМ: РЫНОЧНУЮ ЗАЯВКУ (config forced)")
            return True, "market (config forced)"

        # ========== 5. PRE-MARKET (06:50 - 09:50) - ТОЛЬКО ЛИМИТНЫЕ ==========
        if is_pre_market_time():
            info(f"      🌅 Pre-market: ИСПОЛЬЗУЕМ ЛИМИТНУЮ ЗАЯВКУ")
            return False, "limit (pre-market)"

        # ========== 6. ВЕЧЕРНЯЯ СЕССИЯ (19:00 - 23:50) - РЫНОЧНЫЕ ==========
        if is_evening_session_time():
            info(f"      🌙 Вечерняя сессия: ИСПОЛЬЗУЕМ РЫНОЧНУЮ ЗАЯВКУ")
            return True, "market (evening session)"

        # ========== 7. ОСНОВНАЯ СЕССИЯ (10:00 - 18:59) - ПРЕДПОЧИТАЕМ РЫНОЧНЫЕ ==========
        if is_main_session_time():
            if getattr(config, 'prefer_market_in_main', True):
                info(f"      🏛️ Основная сессия: ИСПОЛЬЗУЕМ РЫНОЧНУЮ ЗАЯВКУ")
                return True, "market (main session, fast execution)"
            info(f"      🏛️ Основная сессия: ИСПОЛЬЗУЕМ ЛИМИТНУЮ ЗАЯВКУ")
            return False, "limit (main session)"

        # ========== 8. ПО УМОЛЧАНИЮ - РЫНОЧНЫЕ ==========
        info(f"      🟢 ИСПОЛЬЗУЕМ: РЫНОЧНУЮ ЗАЯВКУ (default)")
        return True, "market (default)"

    # ========== LONG ПОЗИЦИИ ==========

    def open_long_market(self, stock: StockCandidate, quantity: int) -> bool:
        """Открытие LONG позиции с АВТОМАТИЧЕСКИМ ВЫБОРОМ типа заявки и детальным логированием"""
        from trading_bot.api.tbank_client import tbank

        ticker = stock.ticker

        info(f"\n{'=' * 60}")
        info(f"🟢 ОТКРЫТИЕ LONG ПОЗИЦИИ: {ticker}")
        info(f"   Количество: {quantity} шт")
        info(f"   Цена: {stock.price:.2f}₽")
        info(f"{'=' * 60}")

        # ========== ЗАЩИТА ОТ ДУБЛИРОВАНИЯ ==========
        pending_key = f"long_pending_{stock.figi}"
        if pending_key in self._long_pending:
            elapsed = (get_moscow_time() - self._long_pending[pending_key]).seconds
            if elapsed < 30:
                warning(f"⚠️ LONG заявка для {ticker} уже отправлена {elapsed} сек назад")
                info(f"   ❌ ОТКАЗ: дублирование (pending)")
                return False
            else:
                self._long_pending.pop(pending_key, None)

        # ========== ПРОВЕРКА OTC ==========
        try:
            if tbank.is_confirmation_required(stock.figi):
                warning(f"⛔ {ticker} - OTC инструмент (требует подтверждения)")
                self.bot._add_to_blacklist(ticker, minutes=60)
                info(f"   ❌ ОТКАЗ: OTC инструмент")
                return False

            is_otc, otc_reason = _get_instrument_filter().is_otc_instrument(stock.figi, ticker)
            if is_otc:
                error(f"❌ {ticker} - OTC ИНСТРУМЕНТ! НЕВОЗМОЖНО ЗАКРЫТЬ ЧЕРЕЗ API!")
                error(f"   Причина: {otc_reason}")
                self.bot._add_to_blacklist(ticker, minutes=3600)
                info(f"   ❌ ОТКАЗ: OTC (инструмент фильтр)")
                return False
        except Exception as e:
            warning(f"⚠️ Ошибка проверки OTC: {e}")
            info(f"   ❌ ОТКАЗ: ошибка OTC проверки")
            return False

        # ========== ПРОВЕРКА СПРЕДА ==========
        try:
            orderbook = tbank.get_orderbook(stock.figi, depth=1)
            if orderbook and orderbook.get('best_bid') and orderbook.get('best_ask'):
                best_bid = orderbook['best_bid']
                best_ask = orderbook['best_ask']
                spread_pct = (best_ask - best_bid) / best_bid * 100 if best_bid > 0 else 0
                MAX_SPREAD_PCT = 0.5
                if spread_pct > MAX_SPREAD_PCT:
                    warning(f"⚠️ {ticker}: слишком большой спред {spread_pct:.2f}%")
                    info(f"   ❌ ОТКАЗ: спред {spread_pct:.2f}% > {MAX_SPREAD_PCT}%")
                    return False
                info(f"   ✅ Спред OK: {spread_pct:.2f}%")
        except Exception as e:
            debug(f"   ⚠️ Не удалось проверить спред: {e}")

        # ========== ПРОВЕРКА МИНИМАЛЬНОГО ЛОТА ==========
        if quantity < stock.lot:
            error(f"❌ {ticker}: {quantity} < {stock.lot}")
            info(f"   ❌ ОТКАЗ: количество меньше лота")
            return False

        # ========== ПРОВЕРКА СРЕДСТВ ==========
        if not self._check_funds(stock.figi, quantity, stock.price, ticker):
            info(f"   ❌ ОТКАЗ: недостаточно средств (check_funds)")
            return False

        # ========== АВТОМАТИЧЕСКИЙ ВЫБОР ТИПА ЗАЯВКИ ==========
        use_market, order_reason = self._get_order_type(stock.figi, ticker)
        info(f"   📊 Тип заявки: {order_reason}")

        # Отметка времени отправки заявки
        self._long_pending[pending_key] = get_moscow_time()

        # ========== ОТПРАВКА ЗАЯВКИ ==========
        try:
            info(f"📡 ОТПРАВКА заявки: BUY {quantity} {ticker}")
            if use_market:
                success_flag = tbank.buy(stock.figi, quantity, use_market=True)
            else:
                limit_price = tbank._round_to_min_increment(stock.figi, stock.price * 1.01)
                info(f"   📊 Лимитная цена: {limit_price:.2f}₽ (рынок: {stock.price:.2f}₽)")
                success_flag = tbank.place_limit_order(stock.figi, quantity, "BUY", limit_price)

            if success_flag:
                success(f"✅ {ticker}: LONG позиция успешно открыта ({order_reason})")
                self._add_position_to_manager(stock, quantity, stock.price, OrderSide.LONG)
                self._long_pending.pop(pending_key, None)
                return True
            else:
                error(f"❌ {ticker}: не удалось открыть LONG позицию ({order_reason})")
                # Fallback: если лимитная не сработала, пробуем рыночную
                if not use_market:
                    warning(f"   🔄 Пробуем рыночную заявку как fallback...")
                    try:
                        success_flag = tbank.buy(stock.figi, quantity, use_market=True)
                        if success_flag:
                            success(f"✅ {ticker}: LONG позиция открыта (рыночная fallback)")
                            self._add_position_to_manager(stock, quantity, stock.price, OrderSide.LONG)
                            self._long_pending.pop(pending_key, None)
                            return True
                    except Exception as fallback_error:
                        error(f"   ❌ Fallback тоже не сработал: {fallback_error}")
                self._long_pending.pop(pending_key, None)
                return False
        except Exception as e:
            error(f"❌ Ошибка: {e}")
            self._long_pending.pop(pending_key, None)
            return False

    # ========== SHORT ПОЗИЦИИ ==========

    def open_short_market(self, stock: StockCandidate, quantity: int) -> bool:
        """Открытие SHORT позиции с АВТОМАТИЧЕСКИМ ВЫБОРОМ типа заявки и детальным логированием"""
        from trading_bot.api.tbank_client import tbank

        ticker = stock.ticker

        info(f"\n{'=' * 60}")
        info(f"🔴 ОТКРЫТИЕ SHORT ПОЗИЦИИ: {ticker}")
        info(f"   Количество: {quantity} шт")
        info(f"   Цена: {stock.price:.2f}₽")
        info(f"   FIGI: {stock.figi}")
        info(f"{'=' * 60}")

        # ========== ЗАЩИТА ОТ ДУБЛИРОВАНИЯ ==========
        pending_key = f"short_pending_{stock.figi}"
        if pending_key in self._short_pending:
            elapsed = (get_moscow_time() - self._short_pending[pending_key]).seconds
            if elapsed < 30:
                warning(f"⚠️ SHORT заявка для {ticker} уже отправлена {elapsed} сек назад")
                info(f"   ❌ ОТКАЗ: дублирование (pending)")
                return False
            else:
                self._short_pending.pop(pending_key, None)

        # ========== 1. ПРОВЕРКА OTC ==========
        try:
            if tbank.is_confirmation_required(stock.figi):
                warning(f"⛔ {ticker} - OTC инструмент (требует подтверждения)")
                self.bot._add_to_blacklist(ticker, minutes=60)
                info(f"   ❌ ОТКАЗ: OTC (confirmation_required)")
                return False

            is_otc, otc_reason = _get_instrument_filter().is_otc_instrument(stock.figi, ticker)
            if is_otc:
                error(f"❌ {ticker} - OTC ИНСТРУМЕНТ! НЕВОЗМОЖНО ЗАКРЫТЬ ЧЕРЕЗ API!")
                error(f"   Причина: {otc_reason}")
                self.bot._add_to_blacklist(ticker, minutes=3600)
                info(f"   ❌ ОТКАЗ: OTC (instrument_filter)")
                return False
        except Exception as e:
            warning(f"⚠️ Ошибка проверки OTC: {e}")
            info(f"   ❌ ОТКАЗ: ошибка OTC проверки")
            return False

        # ========== 2. ПРОВЕРКА МИНИМАЛЬНОГО ЛОТА ==========
        if quantity < stock.lot:
            error(f"❌ {ticker}: {quantity} < {stock.lot}")
            info(f"   ❌ ОТКАЗ: количество меньше лота")
            return False
        info(f"   ✅ Лот: {stock.lot}, количество {quantity} >= лота")

        # ========== 3. ПРОВЕРКА КАПИТАЛА ДЛЯ SHORT ==========
        available, total_capital, _ = tbank.get_available_funds()
        min_capital_for_short = getattr(config, 'min_capital_for_short', 7000)
        info(f"   Проверка капитала: total_capital={total_capital:.0f}₽, необходимо >= {min_capital_for_short}₽")
        if total_capital < min_capital_for_short:
            error(
                f"❌ {ticker}: недостаточно капитала для SHORT (нужно {min_capital_for_short}₽, есть {total_capital:.0f}₽)")
            info(f"   ❌ ОТКАЗ: капитал < {min_capital_for_short}")
            return False
        info(f"   ✅ Капитал достаточен")

        # ========== 4. ПРОВЕРКА МАРЖИ ==========
        from trading_bot.risk.position_manager import position_manager
        required_margin = quantity * stock.price * 0.2
        can_open, reason = position_manager.can_open_new_position(required_margin)
        info(f"   Проверка маржи: required_margin={required_margin:.2f}₽, can_open={can_open}, reason={reason}")
        if not can_open:
            warning(f"⚠️ Нельзя открыть SHORT {ticker}: {reason}")
            info(f"   ❌ ОТКАЗ: маржа не позволяет")
            return False
        info(f"   ✅ Маржа в порядке")

        # ========== 5. ПРОВЕРКА ВРЕМЕНИ ДО КЛИРИНГА ==========
        self._check_clearing_warning(quantity, stock.price, ticker)

        # ========== 6. ПРОВЕРКА ТОРГОВОГО ВРЕМЕНИ ==========
        from trading_bot.utils.time_utils import is_trading_time
        trading_time_ok = is_trading_time()
        info(f"   Торговое время разрешено: {trading_time_ok}")
        if not trading_time_ok:
            error(f"❌ {ticker}: Торги закрыты")
            info(f"   ❌ ОТКАЗ: вне торгового времени")
            return False
        info(f"   ✅ Торги открыты")

        # ========== 7. ПРОВЕРКА СТАТУСА ТОРГОВ ==========
        try:
            trading_status = tbank.get_trading_status(stock.figi)
            api_available = trading_status.get('api_trade_available', False)
            market_available = trading_status.get('market_order_available', False)
            limit_available = trading_status.get('limit_order_available', False)

            info(f"   API торговля доступна: {api_available}")
            info(f"   Рыночные заявки доступны: {market_available}")
            info(f"   Лимитные заявки доступны: {limit_available}")

            if not api_available:
                warning(f"⚠️ {ticker}: API торговля недоступна")
                info(f"   ❌ ОТКАЗ: API торговля недоступна")
                return False

            # ✅ НОВОЕ: если нет ни рыночных, ни лимитных заявок - нельзя торговать
            if not market_available and not limit_available:
                error(f"❌ {ticker}: нет доступных типов заявок для SHORT")
                info(f"   ❌ ОТКАЗ: нет доступных заявок")
                return False

        except Exception as e:
            debug(f"Ошибка проверки статуса: {e}")
            info(f"   ❌ ОТКАЗ: ошибка статуса торгов")
            return False

        # ========== 8. ПРОВЕРКА СУЩЕСТВУЮЩЕЙ ПОЗИЦИИ ==========
        existing = position_manager.get_position(stock.figi)
        info(f"   Существующая позиция: {existing is not None}")
        if existing:
            warning(f"⚠️ Уже есть позиция по {ticker}")
            info(f"   ❌ ОТКАЗ: позиция уже существует")
            return False

        # ========== 9. ПРОВЕРКА СТАТУСА БОТА ==========
        if self.bot._shutting_down:
            warning(f"🛑 Бот останавливается")
            info(f"   ❌ ОТКАЗ: бот останавливается")
            return False
        info(f"   ✅ Бот работает")

        # ========== 10. ПРОВЕРКА ВКЛЮЧЕНИЯ SHORT ==========
        use_short = config.use_short
        info(f"   config.use_short = {use_short}")
        if not use_short:
            error(f"🔻 SHORT отключён")
            info(f"   ❌ ОТКАЗ: SHORT отключён в конфиге")
            return False

        # ========== 11. ПРОВЕРКА МАРЖИНАЛЬНОЙ ТОРГОВЛИ ==========
        margin_allowed, margin_reason = tbank.check_margin_trading_allowed()
        info(f"   Маржинальная торговля разрешена: {margin_allowed}, причина: {margin_reason}")
        if not margin_allowed:
            error(f"❌ Маржинальная торговля недоступна: {margin_reason}")
            info(f"   ❌ ОТКАЗ: маржинальная торговля недоступна")
            return False

        # ========== 12. ПРОВЕРКА СРЕДСТВ ДЛЯ ЗАКРЫТИЯ ==========
        buy_back_cost = quantity * stock.price * 1.05
        available_funds, _, _ = tbank.get_available_funds()
        info(f"   Нужно для выкупа: {buy_back_cost:.2f}₽, доступно: {available_funds:.2f}₽")
        if available_funds < buy_back_cost:
            error(f"❌ Недостаточно средств для SHORT {ticker}!")
            error(f"   Нужно для выкупа: {buy_back_cost:.0f}₽, доступно: {available_funds:.0f}₽")
            info(f"   ❌ ОТКАЗ: недостаточно средств на выкуп")
            return False
        info(f"   ✅ Средств на выкуп достаточно")

        # ========== АВТОМАТИЧЕСКИЙ ВЫБОР ТИПА ЗАЯВКИ ==========
        use_market, order_reason = self._get_order_type(stock.figi, ticker)
        info(f"   📊 Тип заявки: {order_reason}")

        # Отметка времени отправки заявки
        self._short_pending[pending_key] = get_moscow_time()

        # ========== ОТПРАВКА ЗАЯВКИ ==========
        try:
            info(f"📡 ОТПРАВКА заявки: SHORT {quantity} шт {ticker}")

            # ✅ НОВОЕ: если рыночные недоступны, используем АГРЕССИВНУЮ лимитную
            if not market_available:
                info(f"   ⚠️ Рыночные заявки недоступны, используем АГРЕССИВНУЮ лимитную")
                # Агрессивная цена: 3-4% ниже рынка для гарантированного исполнения
                aggressive_price = stock.price * 0.96  # 4% ниже
                step = tbank._get_min_price_increment_advanced(stock.figi)
                if step > 0:
                    aggressive_price = round(aggressive_price / step) * step
                aggressive_price = max(aggressive_price, 0.01)

                info(f"   📊 Агрессивная лимитная цена: {aggressive_price:.2f}₽ (рынок: {stock.price:.2f}₽)")
                success_flag = tbank.place_limit_order(stock.figi, quantity, "SELL", aggressive_price)

            elif use_market:
                success_flag = tbank.sell(stock.figi, quantity)
                info(f"   (рыночная заявка)")
            else:
                limit_price = tbank._round_to_min_increment(stock.figi, stock.price * 0.99)
                info(f"   📊 Лимитная цена: {limit_price:.2f}₽ (рынок: {stock.price:.2f}₽)")
                success_flag = tbank.place_limit_order(stock.figi, quantity, "SELL", limit_price)

            if success_flag:
                # Ожидаем исполнения
                for attempt in range(5):
                    time.sleep(1)
                    positions = _get_tbank().get_positions()
                    if any(p.get('figi') == stock.figi for p in positions):
                        success(f"✅ SHORT {ticker} открыт! ({order_reason})")
                        break
                else:
                    warning(f"⚠️ SHORT {ticker} не подтверждён")
                    self._short_pending.pop(pending_key, None)
                    info(f"   ❌ ОТКАЗ: не подтверждён после отправки")
                    return False

                self._add_position_to_manager(stock, quantity, stock.price, OrderSide.SHORT)
                try:
                    _get_telegram().send_trade_opened("SHORT", ticker, quantity, stock.price)
                except Exception:
                    pass
                self._short_pending.pop(pending_key, None)
                return True

            error(f"❌ SHORT {ticker} не открыт ({order_reason})")

            # Fallback: если лимитная не сработала, пробуем АГРЕССИВНУЮ лимитную (ещё ниже)
            if not use_market or not market_available:
                warning(f"   🔄 Fallback: пробуем СУПЕР-АГРЕССИВНУЮ лимитную заявку...")
                try:
                    super_aggressive_price = stock.price * 0.93  # 7% ниже
                    step = tbank._get_min_price_increment_advanced(stock.figi)
                    if step > 0:
                        super_aggressive_price = round(super_aggressive_price / step) * step
                    super_aggressive_price = max(super_aggressive_price, 0.01)

                    info(f"   📊 Супер-агрессивная цена: {super_aggressive_price:.2f}₽")
                    success_flag = tbank.place_limit_order(stock.figi, quantity, "SELL", super_aggressive_price)

                    if success_flag:
                        success(f"✅ {ticker}: SHORT позиция открыта (супер-агрессивная лимитная fallback)")
                        self._add_position_to_manager(stock, quantity, stock.price, OrderSide.SHORT)
                        self._short_pending.pop(pending_key, None)
                        return True
                except Exception as fallback_error:
                    error(f"   ❌ Fallback тоже не сработал: {fallback_error}")

            self._short_pending.pop(pending_key, None)
            return False

        except Exception as e:
            error(f"❌ Ошибка SHORT {ticker}: {e}")
            import traceback
            debug(traceback.format_exc())
            self._short_pending.pop(pending_key, None)
            return False

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def _check_funds(self, figi: str, quantity: int, price: float, ticker: str) -> bool:
        """Проверка достаточности средств для LONG"""
        from trading_bot.api.tbank_client import tbank

        try:
            available, total_capital, _ = tbank.get_available_funds()
            total_cost = quantity * price

            MAX_POSITION_PCT = 0.7
            if total_cost > total_capital * MAX_POSITION_PCT:
                warning(f"⚠️ {ticker}: {total_cost:.0f}₽ > {MAX_POSITION_PCT * 100:.0f}% капитала")
                return False

            MIN_RESERVE = 300
            if available - total_cost < MIN_RESERVE:
                warning(f"⚠️ {ticker}: после сделки останется {available - total_cost:.0f}₽ < {MIN_RESERVE}₽")
                return False

            MIN_COVERAGE_PCT = 0.3
            if available < total_cost * MIN_COVERAGE_PCT:
                warning(f"⚠️ {ticker}: свободных средств {available:.0f}₽ < {MIN_COVERAGE_PCT * 100:.0f}% суммы")
                return False

            info(f"   💰 Проверка средств: OK (нужно {total_cost:.0f}₽, свободно {available:.0f}₽)")
            return True
        except Exception as e:
            warning(f"   ⚠️ Не удалось проверить средства: {e}")
            return True

    def _check_clearing_warning(self, quantity: int, price: float, ticker: str):
        """Проверка времени до клиринга"""
        from datetime import time as dt_time

        now = get_moscow_time()
        current_time = now.time()
        clearing_time = dt_time(18, 45)

        if current_time < clearing_time:
            minutes_to_clearing = (clearing_time.hour * 60 + clearing_time.minute) - (
                        current_time.hour * 60 + current_time.minute)
            if minutes_to_clearing < 60:
                warning(f"⚠️ До клиринга {minutes_to_clearing:.0f} мин!")
                estimated_uncovered = quantity * price
                if estimated_uncovered > 5000:
                    from trading_bot.risk.position_manager import position_manager
                    daily_fee = position_manager.calculate_overnight_fee(estimated_uncovered, 1)
                    warning(f"   Комиссия за перенос: {daily_fee:.0f}₽/день")

    def _add_position_to_manager(self, stock: StockCandidate, quantity: int, price: float, side: OrderSide):
        """Добавление позиции в менеджер с динамическими TP/SL"""
        try:
            position_manager = self._get_position_manager()

            # ✅ БЕРЁМ TP/SL ИЗ АНАЛИЗА (если есть)
            take_profit_pct = None
            stop_loss_pct = None

            if hasattr(stock, 'analysis') and stock.analysis:
                take_profit_pct = getattr(stock.analysis, 'take_profit_pct', None)
                stop_loss_pct = getattr(stock.analysis, 'stop_loss_pct', None)

            position_manager.add_position(
                figi=stock.figi,
                ticker=stock.ticker,
                quantity=quantity,
                price=price,
                side=side,
                take_profit_pct=take_profit_pct,  # ← ДИНАМИЧЕСКИЙ
                stop_loss_pct=stop_loss_pct,  # ← ДИНАМИЧЕСКИЙ
                trailing_stop_pct=config.trailing_stop_pct,
                auto_set_stop=True
            )
            info(f"   ✅ Позиция добавлена в менеджер")
        except Exception as e:
            error(f"   ❌ Ошибка добавления позиции: {e}")

    # ========== УМНЫЕ ЗАЯВКИ ==========

    async def open_position(self, stock: StockCandidate, quantity: int = None) -> bool:
        """Открытие позиции с поддержкой умных заявок"""
        if quantity is None:
            available_funds = self.bot.get_available_balance()
            score = getattr(stock.analysis, 'score', 0) if hasattr(stock, 'analysis') else 0
            quantity = self.bot.position_sizer.calculate(stock, available_funds, score)

        if quantity <= 0:
            warning(f"⚠️ {stock.ticker}: размер позиции = 0")
            return False

        price = stock.price
        if price <= 0:
            price = self.bot._get_current_price(stock.figi)
            if not price or price <= 0:
                error(f"❌ {stock.ticker}: не удалось получить цену")
                return False

        if not self._check_funds(stock.figi, quantity, price, stock.ticker):
            return False

        direction = "BUY" if stock.side == OrderSide.LONG else "SELL"
        LARGE_POSITION_THRESHOLD = getattr(config, 'large_position_threshold', 100)

        if quantity > LARGE_POSITION_THRESHOLD:
            return await self._open_iceberg_position(stock, quantity, direction, price)
        else:
            if stock.side == OrderSide.LONG:
                result = self.open_long_market(stock, quantity)
            else:
                result = self.open_short_market(stock, quantity)

            if result:
                self.bot._track_smart_order(None, stock.ticker, quantity,
                                            "market_long" if stock.side == OrderSide.LONG else "market_short")
            return result

    async def _open_iceberg_position(self, stock: StockCandidate, quantity: int, direction: str, price: float) -> bool:
        """Открытие позиции через айсберг-заявку"""
        if not self.bot.smart_orders_manager:
            error(f"❌ {stock.ticker}: SmartOrderManager не инициализирован")
            return False

        iceberg_ratio = getattr(config, 'iceberg_visible_ratio', 0.1)
        iceberg_size = max(10, int(quantity * iceberg_ratio))
        max_slippage = getattr(config, 'max_slippage_pct', 0.5) / 100

        limit_price = price * (1 + max_slippage) if direction == "BUY" else price * (1 - max_slippage)

        try:
            order_id = await self.bot.smart_orders_manager.place_iceberg_order(
                figi=stock.figi,
                ticker=stock.ticker,
                direction=direction,
                total_quantity=quantity,
                iceberg_size=iceberg_size,
                limit_price=limit_price,
                slippage_tolerance=max_slippage
            )

            if order_id:
                success(f"✅ {stock.ticker}: АЙСБЕРГ-ЗАЯВКА {order_id}")
                success(f"   📊 Всего: {quantity} шт, Видимая часть: {iceberg_size} шт")
                self.bot._track_smart_order(order_id, stock.ticker, quantity, "iceberg")
                return True
            else:
                error(f"❌ {stock.ticker}: не удалось разместить айсберг-заявку")
                return False
        except Exception as e:
            error(f"❌ {stock.ticker}: ошибка айсберг-заявки: {e}")
            return False

    # ========== АВТОМАТИЧЕСКОЕ ОТКРЫТИЕ ==========

    def open_position_auto(self, ticker: str, quantity: int, side: str,
                           price: float = None, use_market: bool = True) -> bool:
        """Автоматическое открытие позиции без анализа сигнала"""
        from trading_bot.api.tbank_client import tbank

        info(f"\n{'=' * 60}")
        info(f"🚀 АВТОМАТИЧЕСКОЕ ОТКРЫТИЕ ПОЗИЦИИ")
        info(f"   Тикер: {ticker}")
        info(f"   Сторона: {side}")
        info(f"   Количество: {quantity} шт")
        info(f"{'=' * 60}")

        try:
            figi = self.bot._get_figi_by_ticker(ticker)
            if not figi:
                error(f"❌ FIGI не найден для {ticker}")
                return False

            # ========== ПОЛУЧЕНИЕ ЦЕНЫ С ФАЛЛБЕКОМ ==========
            if price is None or price <= 0:
                price = tbank.get_current_price(figi)
                if not price or price <= 0:
                    # Фаллбек: получаем из стакана
                    orderbook = tbank.get_orderbook(figi, depth=1)
                    if orderbook:
                        price = orderbook.get('best_ask', 0) or orderbook.get('best_bid', 0)
                    if not price or price <= 0:
                        error(f"❌ Не удалось получить цену для {ticker}")
                        return False
                info(f"   💰 Текущая цена: {price:.2f}₽")

            lot = self._get_lot(figi, ticker)

            if quantity % lot != 0:
                original_qty = quantity
                quantity = (quantity // lot) * lot
                if quantity == 0:
                    quantity = lot
                info(f"   📦 Корректировка под лот: {original_qty} → {quantity} шт")

            available, total_capital, _ = tbank.get_available_funds()
            total_cost = quantity * price

            # Для SHORT нужен запас на выкуп
            if side == "SHORT":
                total_cost = quantity * price * 1.05

            if total_cost > available:
                error(f"❌ Недостаточно средств: нужно {total_cost:.2f}₽, доступно {available:.2f}₽")
                return False

            stock = self._create_candidate(figi, ticker, price, lot, side)

            if side == "LONG":
                if use_market:
                    success_flag = self.open_long_market(stock, quantity)
                else:
                    limit_price = tbank._round_to_min_increment(figi, price * 1.01)
                    success_flag = tbank.place_limit_order(figi, quantity, "BUY", limit_price)
            else:  # SHORT
                # Дополнительная проверка капитала для SHORT
                min_capital_for_short = getattr(config, 'min_capital_for_short', 7000)
                if total_capital < min_capital_for_short:
                    error(f"❌ Недостаточно капитала для SHORT {ticker} (нужно {min_capital_for_short}₽)")
                    return False

                if use_market:
                    success_flag = self.open_short_market(stock, quantity)
                else:
                    limit_price = tbank._round_to_min_increment(figi, price * 0.99)
                    success_flag = tbank.place_limit_order(figi, quantity, "SELL", limit_price)

            if success_flag:
                success(f"✅ ПОЗИЦИЯ {ticker} УСПЕШНО ОТКРЫТА!")
                return True
            else:
                error(f"❌ НЕ УДАЛОСЬ открыть позицию {ticker}")
                return False
        except Exception as e:
            error(f"❌ Ошибка: {e}")
            return False

    def _get_lot(self, figi: str, ticker: str) -> int:
        """Получение лота инструмента"""
        from trading_bot.api.tbank_client import tbank
        all_shares = tbank.get_all_shares(limit=500)
        for stock in all_shares:
            if stock.get('figi') == figi:
                return stock.get('lot', 1)
        return 1

    def _create_candidate(self, figi: str, ticker: str, price: float, lot: int, side: str):
        """Создание объекта StockCandidate"""

        class SimpleAnalysis(StockAnalysis):
            def __init__(self):
                super().__init__(
                    figi=figi, name=ticker, score=10 if side == "LONG" else -10,
                    buy_signal=(side == "LONG"), sell_signal=(side == "SHORT"),
                    recommendation=f"MANUAL_{side}", signals=["Ручное открытие позиции"]
                )

        from ..models import StockCandidate as Candidate
        return Candidate(
            figi=figi, name=ticker, price=price, lot=lot, lot_price=price * lot,
            analysis=SimpleAnalysis(), side=OrderSide.LONG if side == "LONG" else OrderSide.SHORT,
            ticker=ticker, rank_score=10
        )

    def _add_temp_skip(self, figi: str, error_code: str = "", minutes: int = 10):
        """Добавление временной блокировки"""
        try:
            from trading_bot.risk.position_manager import position_manager

            NO_BLOCK_ERRORS = {"30079", "30049", "30014"}
            for code in NO_BLOCK_ERRORS:
                if code in error_code:
                    warning(f"⏸️ {figi}: ошибка {code}, повторная попытка")
                    return

            position_manager.add_temp_skip(figi, minutes)
            warning(f"🔒 {figi} заблокирован на {minutes} мин")
        except ImportError:
            pass
