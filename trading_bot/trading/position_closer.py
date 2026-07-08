"""Закрытие позиций - ПОЛНАЯ ПРОДАКШН ВЕРСИЯ (ИСПРАВЛЕННАЯ)"""

import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from ..logger import info, success, error, warning, debug


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


def _get_blacklist_manager():
    """Получение экземпляра BlacklistManager"""
    try:
        from trading_bot.core.blacklist_manager import blacklist_manager
        return blacklist_manager
    except ImportError:
        return None


def _get_position_manager():
    """Получение PositionManager"""
    try:
        from trading_bot.risk.position_manager import position_manager
        return position_manager
    except ImportError:
        return None


class TradingConstants:
    """Константы для торговли"""
    WEEKEND_LIMIT_MARGIN = 85
    MIN_PARTIAL_LOT = 1
    MAX_RETRY_ATTEMPTS = 3
    API_RETRY_DELAY = 0.5

    # ✅ МАКСИМУМ ПОПЫТОК ЗАКРЫТИЯ
    MAX_CLOSE_ATTEMPTS = 5

    # ✅ ПРОСКАЛЬЗЫВАНИЕ ДЛЯ РАЗНЫХ РЕЖИМОВ (УВЕЛИЧЕНО)
    LIMIT_PRICE_OFFSETS = {
        'dsvd_short': [0.01, 0.02, 0.05, 0.10, 0.15],  # до 15%
        'dsvd_long': [0.005, 0.01, 0.02, 0.05, 0.10],   # до 10%
        'otc_short': [0.02, 0.05, 0.10, 0.20, 0.30],    # до 30% для OTC!
        'otc_long': [0.02, 0.05, 0.10, 0.20, 0.30],      # до 30% для OTC!
        'regular_short': [0.02, 0.05, 0.10, 0.15, 0.20], # до 20%
        'regular_long': [0.05, 0.10, 0.15, 0.20],        # до 20%
    }


class PositionCloser:
    """Закрытие позиций (обычное и аварийное)"""

    def __init__(self, bot):
        self.bot = bot
        self.constants = TradingConstants()

        # ✅ ОТСЛЕЖИВАНИЕ ПОПЫТОК ЗАКРЫТИЯ
        self._close_attempts: Dict[str, int] = {}  # figi -> количество попыток
        self._close_attempt_time: Dict[str, datetime] = {}  # figi -> время последней попытки
        
        self._price_cache = {}
        self._price_cache_ttl = 10  # 10 секунд
        
    def _get_price_cached(self, figi: str) -> Optional[float]:
        """Получение цены с кэшированием"""
        if figi in self._price_cache:
            cached_time, price = self._price_cache[figi]
            if (time.time() - cached_time) < self._price_cache_ttl:
                return price
        
        from trading_bot.api.tbank_client import tbank
        price = tbank.get_current_price(figi)
        if price:
            self._price_cache[figi] = (time.time(), price)
        return price

    # ========== СИНХРОНИЗАЦИЯ ==========

    def sync_existing_positions(self):
        """Синхронизация существующих позиций с менеджером"""
        positions = _get_tbank().get_positions()

        for pos in positions:
            figi = pos['figi']
            current_price = _get_tbank().get_current_price(figi)
            if not current_price:
                continue

            avg_price = pos['avg_price']
            quantity = abs(pos['quantity'])

            try:
                from trading_bot.risk.position_manager import position_manager
                if position_manager.get_position(figi):
                    continue

                if pos['quantity'] < 0:
                    from ..models import OrderSide
                    position = position_manager.add_position(figi, quantity, avg_price, OrderSide.SHORT)
                    position.lowest_price = current_price
                    info(f"📌 Обнаружена SHORT позиция: {quantity} шт по {avg_price:.2f}₽")
                elif pos['quantity'] > 0:
                    from ..models import OrderSide
                    position = position_manager.add_position(figi, quantity, avg_price, OrderSide.LONG)
                    position.highest_price = current_price
                    info(f"📌 Обнаружена LONG позиция: {quantity} шт по {avg_price:.2f}₽")
            except ImportError as e:
                debug(f"Ошибка импорта position_manager: {e}")

    # ========== ✅ НОВЫЙ МЕТОД: ЗАКРЫТИЕ ПО ТИКЕРУ ==========

    def emergency_close_by_ticker(self, ticker: str, max_attempts: int = 5) -> bool:
        """
        ЭКСТРЕННОЕ ЗАКРЫТИЕ ПОЗИЦИИ ПО ТИКЕРУ
        Использует прогрессивное проскальзывание до 30%
        """
        from trading_bot.api.tbank_client import tbank

        info(f"\n{'🚨' * 40}")
        info(f"🚨 ЭКСТРЕННОЕ ЗАКРЫТИЕ ПО ТИКЕРУ: {ticker}")
        info(f"{'🚨' * 40}")

        # Находим FIGI по тикеру
        all_shares = tbank.get_all_shares(limit=500)
        figi = None
        for share in all_shares:
            if share.get('ticker') == ticker.upper():
                figi = share.get('figi')
                break

        if not figi:
            error(f"❌ Не найден FIGI для тикера {ticker}")
            return False

        # Получаем позицию из PositionManager
        pm = _get_position_manager()
        if not pm:
            error(f"❌ PositionManager не доступен")
            return False

        position = pm.get_position(figi)
        if not position:
            error(f"❌ Позиция {ticker} не найдена в менеджере")
            return False

        quantity = position.quantity
        side = position.side.value

        # Получаем текущую цену
        current_price = tbank.get_current_price(figi)
        if not current_price:
            error(f"❌ Не удалось получить цену для {ticker}")
            return False

        # ========== 1. ПРОВЕРКА: ПОЗИЦИЯ УЖЕ ЗАКРЫТА? ==========
        try:
            broker_positions = tbank.get_positions()
            broker_figis = {p['figi'] for p in broker_positions if abs(p.get('quantity', 0)) > 0}
            if figi not in broker_figis:
                info(f"   ℹ️ Позиция {ticker} уже закрыта у брокера")
                pm.remove_position(figi)
                return True
        except Exception:
            pass

        # ========== 2. ПРОВЕРКА: OTC ИНСТРУМЕНТ ==========
        try:
            if tbank.is_confirmation_required(figi):
                warning(f"\n🔐 {ticker} - OTC ИНСТРУМЕНТ!")
                warning(f"   НЕВОЗМОЖНО ЗАКРЫТЬ АВТОМАТИЧЕСКИ!")
                warning(f"   📱 Закройте позицию ВРУЧНУЮ в приложении Т-Банк!")

                telegram = _get_telegram()
                if telegram:
                    telegram.send_error(f"""
        🚨 **OTC ИНСТРУМЕНТ!**

        Инструмент {ticker} требует РУЧНОГО подтверждения сделок!

        📊 Позиция: {side} {quantity} шт
        💰 Цена: {current_price:.2f}₽

        **Закройте позицию вручную через приложение Т-Банк!**
        """)

                # ✅ НЕ УДАЛЯЕМ ПОЗИЦИЮ! Она остаётся для ручного закрытия.
                return False
        except Exception as e:
            warning(f"   ⚠️ Ошибка проверки OTC: {e}")

        # ========== 3. ПЕРЕБОР СТРАТЕГИЙ С ПРОГРЕССИВНЫМ ПРОСКАЛЬЗЫВАНИЕМ ==========
        mode = self._get_trading_mode()
        is_otc = mode == "otc"

        for attempt in range(max_attempts):
            slippage = 0.02 + (attempt * 0.03)
            if is_otc:
                slippage = min(0.30, slippage * 1.5)

            if side == "SHORT":
                limit_price = current_price * (1 + slippage)
                direction = "BUY"
                action = "покупка"
            else:
                limit_price = current_price * (1 - slippage)
                direction = "SELL"
                action = "продажа"

            limit_price = tbank._round_to_min_increment_advanced(figi, limit_price)
            slippage_pct = abs(limit_price - current_price) / current_price * 100

            info(f"\n   📍 Попытка {attempt + 1}/{max_attempts}:")
            info(f"      {action} {quantity} шт {ticker} по {limit_price:.2f}₽")
            info(f"      Проскальзывание: {slippage_pct:.1f}%")

            try:
                success_flag = tbank.place_limit_order(figi, quantity, direction, limit_price)

                if success_flag:
                    success(f"\n✅ {ticker} УСПЕШНО ЗАКРЫТ!")
                    info(f"   Цена закрытия: {limit_price:.2f}₽")
                    info(f"   Проскальзывание: {slippage_pct:.1f}%")
                    pm.remove_position(figi)
                    return True
                else:
                    warning(f"   ❌ Лимитная заявка не прошла")

            except Exception as e:
                error_msg = str(e)
                warning(f"   ❌ Ошибка: {error_msg[:100]}")

                # ========== ОБРАБОТКА ОШИБКИ 30240 ==========
                if "30240" in error_msg:
                    warning(f"\n🔐 {ticker}: ОШИБКА 30240 - ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ СДЕЛОК!")
                    warning(f"   НЕВОЗМОЖНО ЗАКРЫТЬ АВТОМАТИЧЕСКИ!")
                    warning(f"   📱 Закройте позицию ВРУЧНУЮ в приложении Т-Банк!")

                    telegram = _get_telegram()
                    if telegram:
                        telegram.send_error(f"🚨 {ticker} требует ручного подтверждения! Закройте вручную!")

                    # ✅ НЕ УДАЛЯЕМ ПОЗИЦИЮ!
                    return False

                time.sleep(1)

        # Последний шанс - рыночная заявка (если не OTC)
        if not is_otc:
            info(f"\n   ⚡ Последний шанс: рыночная заявка")
            try:
                if side == "SHORT":
                    success_flag = tbank.buy(figi, quantity, use_market=True)
                else:
                    success_flag = tbank.sell(figi, quantity, use_market=True)

                if success_flag:
                    success(f"\n✅ {ticker} ЗАКРЫТ РЫНОЧНОЙ ЗАЯВКОЙ!")
                    pm.remove_position(figi)
                    return True
            except Exception as e:
                if "30240" in str(e):
                    warning(f"\n🔐 {ticker}: Рыночная заявка требует подтверждения!")
                    return False
                error(f"   ❌ Рыночная заявка не удалась: {e}")

        error(f"\n💀 НЕ УДАЛОСЬ ЗАКРЫТЬ {ticker} после {max_attempts} попыток!")
        return False

    # ========== ПРИНУДИТЕЛЬНОЕ ЗАКРЫТИЕ ==========

    def close_all_positions_forced(self, session: str, minutes_left: float) -> int:
        """Принудительное закрытие всех позиций (с учётом ДСВД и OTC)"""
        from trading_bot.utils.time_utils import is_dsvd_trading_time, is_otc_trading_time

        is_dsvd = is_dsvd_trading_time()
        is_otc = is_otc_trading_time()

        warning(f"\n{'=' * 60}")
        warning(f"🔒 ДО ОКОНЧАНИЯ {session.upper()} СЕССИИ {minutes_left:.0f} МИНУТ!")
        if is_dsvd:
            warning(f"   📊 ДСВД РЕЖИМ: рыночные + лимитные заявки")
        elif is_otc:
            warning(f"   🌙 OTC РЕЖИМ: ТОЛЬКО лимитные заявки (проскальзывание до 30%)")
        warning(f"   Закрываем все позиции")
        warning(f"{'=' * 60}")

        positions = _get_tbank().get_positions()
        if not positions:
            info("   📭 Нет открытых позиций")
            return 0

        available, total, _ = _get_tbank().get_available_funds()

        closed_count = 0
        for pos in positions:
            figi = pos['figi']
            quantity = abs(pos['quantity'])
            ticker = _get_tbank()._get_ticker_by_figi(figi) or figi[:8]

            # ✅ ПРОВЕРКА: сколько раз уже пытались закрыть
            attempts = self._close_attempts.get(figi, 0)

            if attempts >= self.constants.MAX_CLOSE_ATTEMPTS:
                error(f"   💀 {ticker}: {attempts} неудачных попыток! Удаляем из менеджера")
                pm = _get_position_manager()
                if pm:
                    pm.remove_position(figi)
                self._close_attempts.pop(figi, None)
                continue

            if quantity <= 0:
                warning(f"   ⚠️ Пропускаем {ticker}: количество <= 0")
                continue

            # ✅ УВЕЛИЧИВАЕМ СЧЁТЧИК ПОПЫТОК
            self._close_attempts[figi] = attempts + 1
            self._close_attempt_time[figi] = datetime.now()

            if pos['quantity'] < 0:
                buy_back_cost = quantity * self.bot._get_current_price(figi)

                if available < buy_back_cost * 1.05:
                    error(f"   ❌ НЕДОСТАТОЧНО СРЕДСТВ для закрытия SHORT {ticker}!")
                    error(f"      Нужно: {buy_back_cost:.0f}₽, Доступно: {available:.0f}₽")
                    continue

                info(f"   Закрытие SHORT {ticker}: покупка {quantity} шт")
                if self._emergency_close_short(figi, quantity, ticker, is_critical=False):
                    closed_count += 1
                    self._close_attempts.pop(figi, None)
                else:
                    error(f"   ❌ Не удалось закрыть SHORT {ticker} (попытка {attempts + 1})")
            else:
                info(f"   Закрытие LONG {ticker}: продажа {quantity} шт")
                if self._emergency_close_long(figi, quantity, ticker):
                    closed_count += 1
                    self._close_attempts.pop(figi, None)
                else:
                    error(f"   ❌ Не удалось закрыть LONG {ticker} (попытка {attempts + 1})")

        info(f"   ✅ Закрыто позиций: {closed_count}")

        if closed_count < len(positions):
            telegram = _get_telegram()
            if telegram:
                not_closed = len(positions) - closed_count
                telegram.send_error(f"⚠️ ВНИМАНИЕ! {not_closed} позиций не закрылись!")

        return closed_count

    # def _emergency_close_all_old(self) -> int:
    #     """⚠️ СТАРЫЙ МЕТОД - НЕ ИСПОЛЬЗОВАТЬ! Используйте _emergency_close_profitable_only
    #     Аварийное закрытие всех позиций
    #     """
    #     if hasattr(self.bot, '_emergency_closing') and self.bot._emergency_closing:
    #         warning("🚨 Уже выполняется аварийное закрытие")
    #         return 0
    #
    #     self.bot._emergency_closing = True
    #
    #     try:
    #         warning("🚨 АВАРИЙНОЕ ЗАКРЫТИЕ ВСЕХ ПОЗИЦИЙ!")
    #
    #         available, total, _ = _get_tbank().get_available_funds()
    #         if available < 500:
    #             error(f"🚨 КРИТИЧЕСКИ МАЛО СРЕДСТВ: {available:.0f}₽")
    #             return 0
    #
    #         closed = 0
    #         positions = _get_tbank().get_positions()
    #         margin_info = _get_tbank().get_margin_info()
    #         margin_rate = margin_info.get('margin_rate', 0)
    #         is_critical = margin_rate > 85
    #
    #         for pos in positions:
    #             figi = pos['figi']
    #             quantity = abs(pos['quantity'])
    #             ticker = _get_tbank()._get_ticker_by_figi(figi) or figi[:8]
    #
    #             if quantity <= 0:
    #                 continue
    #
    #             if pos['quantity'] < 0:
    #                 if self._emergency_close_short(figi, quantity, ticker, is_critical):
    #                     closed += 1
    #             else:
    #                 if self._emergency_close_long(figi, quantity, ticker):
    #                     closed += 1
    #
    #         info(f"✅ Аварийно закрыто {closed} позиций")
    #         return closed
    #
    #     except Exception as e:
    #         error(f"❌ Ошибка: {e}")
    #         return 0
    #     finally:
    #         self.bot._emergency_closing = False

    # def emergency_close_shorts(self) -> int:
    #     """Аварийное закрытие только SHORT позиций"""
    #     warning("🚨 АВАРИЙНОЕ ЗАКРЫТИЕ ВСЕХ SHORT ПОЗИЦИЙ!")
    #
    #     try:
    #         closed = 0
    #         positions = _get_tbank().get_positions()
    #         shorts = [p for p in positions if p['quantity'] < 0]
    #
    #         if not shorts:
    #             info("   Нет SHORT позиций для закрытия")
    #             return 0
    #
    #         for pos in shorts:
    #             figi = pos['figi']
    #             quantity = abs(pos['quantity'])
    #             ticker = _get_tbank()._get_ticker_by_figi(figi) or figi[:8]
    #             current_price = self.bot._get_current_price(figi)
    #
    #             if quantity <= 0:
    #                 continue
    #
    #             info(f"   Аварийное закрытие SHORT {ticker}: покупка {quantity} шт")
    #
    #             # ✅ УЛУЧШЕННЫЙ ПЕРЕБОР СТРАТЕГИЙ
    #             success = False
    #
    #             # Определяем режим
    #             mode = self._get_trading_mode()
    #             is_otc = mode == "otc"
    #
    #             # Для OTC - большие проскальзывания
    #             if is_otc:
    #                 offsets = [0.05, 0.10, 0.20, 0.30]
    #             else:
    #                 offsets = [0.02, 0.05, 0.10, 0.15, 0.20]
    #
    #             for offset in offsets:
    #                 try:
    #                     limit_price = current_price * (1 + offset)
    #                     limit_price = _get_tbank()._round_to_min_increment(figi, limit_price)
    #
    #                     if _get_tbank().place_limit_order(figi, quantity, "BUY", limit_price):
    #                         success = True
    #                         info(f"   ✅ лимитная +{offset*100:.0f}% - успешно!")
    #                         closed += 1
    #                         self._remove_position(figi)
    #                         break
    #                     time.sleep(self.constants.API_RETRY_DELAY)
    #                 except Exception:
    #                     time.sleep(self.constants.API_RETRY_DELAY)
    #
    #             if not success and not is_otc:
    #                 try:
    #                     if _get_tbank().buy(figi, quantity, use_market=True):
    #                         success = True
    #                         info(f"   ✅ рыночная - успешно!")
    #                         closed += 1
    #                         self._remove_position(figi)
    #                 except Exception:
    #                     pass
    #
    #             if not success and quantity > 1:
    #                 reduced_qty = max(1, quantity // 2)
    #                 warning(f"   🔄 Финальная попытка: уменьшенный размер {reduced_qty} шт")
    #                 if _get_tbank().buy(figi, reduced_qty):
    #                     closed += 1
    #                     self._update_position_quantity(figi, reduced_qty)
    #
    #         return closed
    #
    #     except Exception as e:
    #         error(f"❌ Ошибка: {e}")
    #         return 0

    # def force_close_stuck_positions(self) -> int:
    #     """ПРИНУДИТЕЛЬНОЕ ЗАКРЫТИЕ ЗАВИСШИХ ПОЗИЦИЙ"""
    #     from trading_bot.api.tbank_client import tbank
    #
    #     mode = self._get_trading_mode()
    #
    #     warning("🚨 ЗАПУСК ПРИНУДИТЕЛЬНОГО ЗАКРЫТИЯ ЗАВИСШИХ ПОЗИЦИЙ")
    #     if mode == "dsvd":
    #         warning("   📊 ДСВД РЕЖИМ: рыночные + лимитные заявки")
    #     elif mode == "otc":
    #         warning("   🌙 OTC РЕЖИМ: ТОЛЬКО лимитные заявки (проскальзывание до 30%)")
    #
    #     closed = 0
    #     positions = self.bot._get_positions(force_refresh=True)
    #
    #     for pos in positions:
    #         figi = pos['figi']
    #         quantity = abs(pos['quantity'])
    #         ticker = _get_tbank()._get_ticker_by_figi(figi) or figi[:8]
    #         side = "SHORT" if pos['quantity'] < 0 else "LONG"
    #
    #         if quantity <= 0:
    #             continue
    #
    #         # ✅ ПРОВЕРКА: если уже много попыток - вызываем emergency_close_by_ticker
    #         attempts = self._close_attempts.get(figi, 0)
    #         if attempts >= 3:
    #             warning(f"   🔥 {ticker}: {attempts} попыток! Вызываем экстренное закрытие")
    #             if self.emergency_close_by_ticker(ticker):
    #                 closed += 1
    #                 continue
    #
    #         info(f"🔧 Принудительное закрытие {side} {ticker}: {quantity} шт")
    #
    #         success = False
    #
    #         if mode in ("dsvd", "otc"):
    #             current_price = self.bot._get_current_price(figi)
    #             if current_price:
    #                 # ✅ УВЕЛИЧЕННОЕ ПРОСКАЛЬЗЫВАНИЕ ДЛЯ OTC
    #                 if mode == "otc":
    #                     slippage = 0.15  # 15% для OTC
    #                 else:
    #                     slippage = 0.05  # 5% для ДСВД
    #
    #                 if side == "SHORT":
    #                     limit_price = current_price * (1 + slippage)
    #                     limit_price = tbank._round_to_min_increment(figi, limit_price)
    #                     info(f"   📋 {mode.upper()}: лимитная BUY {ticker} по {limit_price:.2f}₽ (+{slippage*100:.0f}%)")
    #                     success = tbank.place_limit_order(figi, quantity, "BUY", limit_price)
    #                 else:
    #                     limit_price = current_price * (1 - slippage)
    #                     limit_price = tbank._round_to_min_increment(figi, limit_price)
    #                     info(f"   📋 {mode.upper()}: лимитная SELL {ticker} по {limit_price:.2f}₽ (-{slippage*100:.0f}%)")
    #                     success = tbank.place_limit_order(figi, quantity, "SELL", limit_price)
    #
    #                 if success:
    #                     success(f"✅ {ticker} закрыт ({mode.upper()} лимитная заявка)")
    #                     closed += 1
    #                     self._remove_position(figi)
    #                     self._close_attempts.pop(figi, None)
    #                     continue
    #         else:
    #             # Обычный режим - рыночная заявка
    #             try:
    #                 if side == "SHORT":
    #                     success = tbank.buy(figi, quantity, use_market=True)
    #                 else:
    #                     success = tbank.sell(figi, quantity, use_market=True)
    #
    #                 if success:
    #                     success(f"✅ {ticker} закрыт (рыночная заявка)")
    #                     closed += 1
    #                     self._remove_position(figi)
    #                     self._close_attempts.pop(figi, None)
    #                     continue
    #             except Exception as e:
    #                 warning(f"   Рыночная заявка не удалась: {e}")
    #
    #             # Лимитная заявка с большим запасом
    #             try:
    #                 current_price = self.bot._get_current_price(figi)
    #                 if current_price:
    #                     if side == "SHORT":
    #                         limit_price = current_price * 1.15
    #                         success = tbank.place_pending_order(figi, quantity, "BUY", limit_price)
    #                     else:
    #                         limit_price = current_price * 0.85
    #                         success = tbank.place_pending_order(figi, quantity, "SELL", limit_price)
    #
    #                     if success:
    #                         success(f"✅ {ticker} закрыт (лимитная заявка с запасом 15%)")
    #                         closed += 1
    #                         self._remove_position(figi)
    #                         self._close_attempts.pop(figi, None)
    #                         continue
    #             except Exception as e:
    #                 warning(f"   Лимитная заявка не удалась: {e}")
    #
    #         warning(f"⚠️ Не удалось закрыть {ticker}, удаляем из менеджера")
    #         self._remove_position(figi)
    #         self._close_attempts.pop(figi, None)
    #
    #     info(f"📊 Принудительно закрыто/удалено позиций: {closed}")
    #     return closed

    def close_uncovered_positions_before_clearing(self) -> int:
        """Закрытие ТОЛЬКО непокрытых позиций перед клирингом"""
        from trading_bot.risk.position_manager import position_manager

        warning(f"\n{'=' * 60}")
        warning(f"🔒 ЗАКРЫТИЕ НЕПОКРЫТЫХ ПОЗИЦИЙ ПЕРЕД КЛИРИНГОМ")
        warning(f"{'=' * 60}")

        positions = _get_tbank().get_positions()
        if not positions:
            info("   📭 Нет открытых позиций")
            return 0

        margin_info = _get_tbank().get_margin_info()
        starting_margin = margin_info.get('starting_margin', 0)
        liquid_portfolio = margin_info.get('liquid_portfolio', 0)

        if starting_margin <= 0:
            info("   ✅ Нет непокрытых позиций")
            return 0

        uncovered_ratio = (starting_margin / liquid_portfolio * 100) if liquid_portfolio > 0 else 0
        warning(f"   📊 Непокрытая позиция: {starting_margin:.0f}₽ ({uncovered_ratio:.1f}%)")

        daily_fee = position_manager.calculate_overnight_fee(starting_margin, 1)

        if daily_fee > 0:
            warning(f"   💰 Комиссия за перенос: {daily_fee:.0f}₽/день")

            if daily_fee > 10:
                warning(f"   🔒 Закрываем все позиции (комиссия {daily_fee:.0f}₽/день)")

                closed = 0
                for pos in positions:
                    figi = pos['figi']
                    quantity = abs(pos['quantity'])
                    ticker = _get_tbank()._get_ticker_by_figi(figi) or figi[:8]

                    if quantity <= 0:
                        continue

                    if pos['quantity'] < 0:
                        info(f"   Закрытие SHORT {ticker}: покупка {quantity} шт")
                        if self._emergency_close_short(figi, quantity, ticker, is_critical=False):
                            closed += 1
                    else:
                        if starting_margin > 0:
                            info(f"   Закрытие LONG {ticker}: продажа {quantity} шт")
                            if self._emergency_close_long(figi, quantity, ticker):
                                closed += 1

                info(f"   ✅ Закрыто позиций: {closed}")
                return closed
            else:
                info(f"   ⏸️ Комиссия {daily_fee:.0f}₽/день — оставляем позиции")
        else:
            info(f"   ✅ Комиссия отсутствует")

        return 0

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def _get_trading_mode(self) -> str:
        from trading_bot.utils.time_utils import is_dsvd_trading_time, is_otc_trading_time
        if is_dsvd_trading_time():
            return "dsvd"
        elif is_otc_trading_time():
            return "otc"
        return "regular"

    def _emergency_close_short(self, figi: str, quantity: int, ticker: str, is_critical: bool) -> bool:
        """Экстренное закрытие SHORT позиции с КЭШИРОВАНИЕМ"""
        from trading_bot.api.tbank_client import tbank

        if quantity <= 0:
            return False

        # ✅ ИСПОЛЬЗУЕМ КЭШ ДЛЯ ЦЕНЫ
        current_price = self._get_price_cached(figi)
        if not current_price:
            return False

        info(f"\n🚨 ЭКСТРЕННОЕ ЗАКРЫТИЕ SHORT {ticker}")
        info(f"   Количество: {quantity} шт")
        info(f"   Текущая цена: {current_price:.2f}₽")

        # ПРОВЕРКА OTC С КЭШИРОВАНИЕМ
        if tbank.is_confirmation_required(figi):
            warning(f"\n🔐 {ticker} - OTC ИНСТРУМЕНТ!")
            warning(f"   НЕВОЗМОЖНО ЗАКРЫТЬ АВТОМАТИЧЕСКИ!")
            from trading_bot.risk.position_manager import position_manager
            position_manager.remove_position(figi)
            return False

        # ПОПЫТКА 1: РЫНОЧНАЯ
        info(f"   📍 Попытка 1: рыночная заявка")
        try:
            result = tbank.buy(figi, quantity, use_market=True)
            if result:
                success(f"✅ SHORT {ticker} закрыт рыночной заявкой!")
                return True
        except Exception as e:
            error_msg = str(e)
            if "30240" in error_msg:
                warning(f"   ⚠️ OTC инструмент, требуется ручное закрытие")
                return False
            else:
                warning(f"   ⚠️ Ошибка: {error_msg[:100]}")

        # ПОПЫТКИ С ПРОСКАЛЬЗЫВАНИЕМ
        for offset in [0.02, 0.05, 0.10, 0.15, 0.20]:
            limit_price = current_price * (1 + offset)
            limit_price = tbank._round_to_min_increment_advanced(figi, limit_price)
            info(f"   📍 Попытка: лимитная +{offset * 100:.0f}% ({limit_price:.2f}₽)")
            try:
                if tbank.place_limit_order(figi, quantity, "BUY", limit_price):
                    success(f"✅ SHORT {ticker} закрыт лимитной заявкой!")
                    return True
            except Exception:
                continue

        error(f"❌ НЕ УДАЛОСЬ ЗАКРЫТЬ SHORT {ticker}!")
        return False

    def _emergency_close_long(self, figi: str, quantity: int, ticker: str) -> bool:
        from trading_bot.api.tbank_client import tbank

        if quantity <= 0:
            return False

        current_price = self.bot._get_current_price(figi)
        if not current_price:
            return False

        mode = self._get_trading_mode()

        # ✅ ОБНОВЛЁННЫЕ OFFSETS
        if mode == "dsvd":
            offsets = [0.005, 0.01, 0.02, 0.05, 0.10]
        elif mode == "otc":
            offsets = [0.02, 0.05, 0.10, 0.20, 0.30]  # до 30% для OTC
        else:
            offsets = [0.02, 0.05, 0.10, 0.15, 0.20]

        for offset in offsets:
            limit_price = current_price * (1 - offset)
            limit_price = tbank._round_to_min_increment(figi, limit_price)
            try:
                if tbank.place_limit_order(figi, quantity, "SELL", limit_price):
                    info(f"   ✅ Закрыт LONG {ticker} по {limit_price:.2f}₽ (-{offset*100:.0f}%)")
                    self._remove_position(figi)
                    return True
            except Exception:
                continue

        # Последний шанс - рыночная
        if mode != "otc":
            try:
                if tbank.sell(figi, quantity, use_market=True):
                    info(f"   ✅ Закрыт LONG {ticker} рыночной заявкой")
                    self._remove_position(figi)
                    return True
            except Exception:
                pass

        return False

    def _remove_position(self, figi: str):
        try:
            from trading_bot.risk.position_manager import position_manager
            position_manager.remove_position(figi)
        except ImportError:
            pass

    def _update_position_quantity(self, figi: str, new_quantity: int):
        if new_quantity <= 0:
            self._remove_position(figi)
            return
        try:
            from trading_bot.risk.position_manager import position_manager
            pos = position_manager.get_position(figi)
            if pos:
                pos.quantity = new_quantity
        except ImportError:
            pass

    def get_close_attempts_stats(self) -> Dict[str, int]:
        """Статистика попыток закрытия"""
        return dict(self._close_attempts)

    # ========== НОВЫЕ МЕТОДЫ ДЛЯ ЦЕНТРАЛИЗАЦИИ ==========

    def close_position_smart(self, figi: str, ticker: str = None,
                             max_attempts: int = 5) -> bool:
        """
        УМНОЕ ЗАКРЫТИЕ ПОЗИЦИИ - ОСНОВНОЙ МЕТОД!
        Использовать везде вместо прямых вызовов sell/buy.

        Args:
            figi: FIGI инструмента
            ticker: Тикер (опционально, для логов)
            max_attempts: Максимум попыток

        Returns:
            bool: Успех закрытия
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.risk.position_manager import position_manager

        if not ticker:
            ticker = tbank._get_ticker_by_figi(figi) or figi[:8]

        # Получаем позицию
        position = position_manager.get_position(figi)
        if not position:
            warning(f"⚠️ Позиция {ticker} не найдена в менеджере")
            return False

        quantity = position.quantity
        side = position.side.value

        info(f"\n🎯 УМНОЕ ЗАКРЫТИЕ {ticker} ({side})")
        info(f"   Количество: {quantity} шт")

        # Получаем текущую цену
        current_price = tbank.get_current_price(figi)
        if not current_price:
            error(f"❌ Не удалось получить цену для {ticker}")
            return False

        info(f"   Текущая цена: {current_price:.2f}₽")

        # Определяем режим
        mode = self._get_trading_mode()
        is_otc = mode == "otc"

        # Выбираем стратегию в зависимости от стороны
        if side == "SHORT":
            success = self._close_short_smart(figi, quantity, ticker, current_price, is_otc, max_attempts)
        else:
            success = self._close_long_smart(figi, quantity, ticker, current_price, is_otc, max_attempts)

        if success:
            position_manager.remove_position(figi)
            success(f"✅ {ticker} успешно закрыт!")
        else:
            error(f"❌ Не удалось закрыть {ticker} после {max_attempts} попыток")

        return success

    def _close_short_smart(self, figi: str, quantity: int, ticker: str,
                           current_price: float, is_otc: bool, max_attempts: int) -> bool:
        """Умное закрытие SHORT позиции"""
        from trading_bot.api.tbank_client import tbank

        # Прогрессивное проскальзывание
        for attempt in range(max_attempts):
            slippage = 0.02 + (attempt * 0.03)  # 2%, 5%, 8%, 11%, 14%
            if is_otc:
                slippage = min(0.30, slippage * 1.5)  # до 30% для OTC

            limit_price = current_price * (1 + slippage)
            limit_price = tbank._round_to_min_increment(figi, limit_price)

            info(f"   Попытка {attempt + 1}/{max_attempts}: лимитная +{slippage * 100:.0f}%")

            try:
                if tbank.place_limit_order(figi, quantity, "BUY", limit_price):
                    success(f"   ✅ SHORT {ticker} закрыт по {limit_price:.2f}₽")
                    return True
            except Exception as e:
                if "30042" in str(e):
                    warning(f"   ⚠️ Недостаточно средств для закрытия SHORT {ticker}")
                    info(f"   💡 Пополните счёт или закройте другие позиции")
                    return False
                continue

            time.sleep(0.5)

        # Последний шанс - рыночная (если не OTC)
        if not is_otc:
            info(f"   ⚡ Последний шанс: рыночная заявка")
            try:
                if tbank.buy(figi, quantity, use_market=True):
                    success(f"   ✅ SHORT {ticker} закрыт рыночной заявкой!")
                    return True
            except Exception as e:
                warning(f"   ❌ Рыночная заявка не удалась: {e}")

        return False

    def _close_long_smart(self, figi: str, quantity: int, ticker: str,
                          current_price: float, is_otc: bool, max_attempts: int) -> bool:
        """Умное закрытие LONG позиции"""
        from trading_bot.api.tbank_client import tbank

        for attempt in range(max_attempts):
            slippage = 0.02 + (attempt * 0.03)
            if is_otc:
                slippage = min(0.30, slippage * 1.5)

            limit_price = current_price * (1 - slippage)
            limit_price = tbank._round_to_min_increment(figi, limit_price)

            info(f"   Попытка {attempt + 1}/{max_attempts}: лимитная -{slippage * 100:.0f}%")

            try:
                if tbank.place_limit_order(figi, quantity, "SELL", limit_price):
                    success(f"   ✅ LONG {ticker} закрыт по {limit_price:.2f}₽")
                    return True
            except Exception:
                continue

            time.sleep(0.5)

        # Рыночная заявка
        if not is_otc:
            info(f"   ⚡ Последний шанс: рыночная заявка")
            try:
                if tbank.sell(figi, quantity, use_market=True):
                    success(f"   ✅ LONG {ticker} закрыт рыночной заявкой!")
                    return True
            except Exception as e:
                warning(f"   ❌ Рыночная заявка не удалась: {e}")

        return False

    def close_worst_positions(self, max_to_close: int = 2) -> int:
        """Закрыть самые убыточные позиции"""
        from trading_bot.risk.position_manager import position_manager
        from trading_bot.api.tbank_client import tbank

        positions = position_manager.get_all_positions()
        if not positions:
            return 0

        # Собираем данные по P&L
        positions_data = []
        for figi, pos in positions.items():
            current_price = tbank.get_current_price(figi)
            if current_price:
                profit_pct = pos.current_profit_pct(current_price)
                positions_data.append({
                    'figi': figi,
                    'position': pos,
                    'profit_pct': profit_pct,
                    'ticker': pos.ticker or figi[:8]
                })

        # Сортируем по убытку (самые убыточные первые)
        positions_data.sort(key=lambda x: x['profit_pct'])

        closed = 0
        for item in positions_data[:max_to_close]:
            if item['profit_pct'] >= 0:
                continue

            ticker = item['ticker']
            warning(f"🔄 Закрытие убыточной позиции {ticker} (P&L: {item['profit_pct']:.2f}%)")

            # ✅ ПРОВЕРКА OTC
            if tbank.is_confirmation_required(figi):
                warning(f"⚠️ {ticker} - OTC ИНСТРУМЕНТ! НЕВОЗМОЖНО ЗАКРЫТЬ АВТОМАТИЧЕСКИ!")
                warning(f"   📱 Закройте позицию вручную!")
                continue  # Пропускаем эту позицию

            if self.close_position_smart(item['figi'], ticker):
                closed += 1

        return closed


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
position_closer = PositionCloser(None)