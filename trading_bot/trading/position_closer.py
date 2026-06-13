"""Закрытие позиций"""

import time

from ..logger import info, success, error, warning, debug


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class PositionCloser:
    """Закрытие позиций (обычное и аварийное)"""

    def __init__(self, bot):
        self.bot = bot

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
                else:
                    from ..models import OrderSide
                    position = position_manager.add_position(figi, quantity, avg_price, OrderSide.LONG)
                    position.highest_price = current_price
                    info(f"📌 Обнаружена LONG позиция: {quantity} шт по {avg_price:.2f}₽")
            except ImportError:
                pass

    def close_all_positions_forced(self, session: str, minutes_left: float) -> int:
        """Принудительное закрытие всех позиций"""
        warning(f"\n{'=' * 60}")
        warning(f"🔒 ДО ОКОНЧАНИЯ {session.upper()} СЕССИИ {minutes_left:.0f} МИНУТ!")
        warning(f"   Закрываем все позиции")
        warning(f"{'=' * 60}")

        positions = _get_tbank().get_positions()
        if not positions:
            info("   📭 Нет открытых позиций")
            return 0

        closed_count = 0
        for pos in positions:
            figi = pos['figi']
            quantity = abs(pos['quantity'])
            ticker = self.bot._get_ticker_by_figi(figi) or figi[:8]

            if pos['quantity'] < 0:  # SHORT
                info(f"   Закрытие SHORT {ticker}: покупка {quantity} шт")
                if _get_tbank().buy(figi, quantity):
                    closed_count += 1
                    success(f"   ✅ SHORT {ticker} закрыт")
                    self._remove_position(figi)
                else:
                    error(f"   ❌ Не удалось закрыть SHORT {ticker}")
            else:  # LONG
                info(f"   Закрытие LONG {ticker}: продажа {quantity} шт")
                if _get_tbank().sell(figi, quantity):
                    closed_count += 1
                    success(f"   ✅ LONG {ticker} закрыт")
                    self._remove_position(figi)
                else:
                    error(f"   ❌ Не удалось закрыть LONG {ticker}")

        info(f"   ✅ Закрыто позиций: {closed_count}")
        return closed_count

    def emergency_close_all(self) -> int:
        """Аварийное закрытие всех позиций"""
        if hasattr(self.bot, '_emergency_closing') and self.bot._emergency_closing:
            warning("🚨 Уже выполняется аварийное закрытие, пропускаем...")
            return 0

        self.bot._emergency_closing = True

        try:
            warning("🚨 АВАРИЙНОЕ ЗАКРЫТИЕ ВСЕХ ПОЗИЦИЙ!")

            available, total, _ = _get_tbank().get_available_funds()
            if available < 500:
                error(f"🚨 КРИТИЧЕСКИ МАЛО СРЕДСТВ: {available:.0f}₽")
                return 0

            closed = 0
            positions = _get_tbank().get_positions()
            margin_info = _get_tbank().get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)
            is_critical = margin_rate > 85

            for pos in positions:
                figi = pos['figi']
                quantity = abs(pos['quantity'])
                ticker = self.bot._get_ticker_by_figi(figi) or figi[:8]

                if pos['quantity'] < 0:  # SHORT
                    success = self._emergency_close_short(figi, quantity, ticker, is_critical)
                    if success:
                        closed += 1
                else:  # LONG
                    success = self._emergency_close_long(figi, quantity, ticker)
                    if success:
                        closed += 1

            info(f"✅ Аварийно закрыто {closed} позиций")
            return closed

        except Exception as e:
            error(f"❌ Ошибка при аварийном закрытии: {e}")
            return 0
        finally:
            self.bot._emergency_closing = False

    def emergency_close_shorts(self) -> int:
        """Аварийное закрытие только SHORT позиций"""
        warning("🚨 АВАРИЙНОЕ ЗАКРЫТИЕ ВСЕХ SHORT ПОЗИЦИЙ!")

        try:
            closed = 0
            positions = _get_tbank().get_positions()
            shorts = [p for p in positions if p['quantity'] < 0]

            if not shorts:
                info("   Нет SHORT позиций для закрытия")
                return 0

            for pos in shorts:
                figi = pos['figi']
                quantity = abs(pos['quantity'])
                ticker = self.bot._get_ticker_by_figi(figi) or figi[:8]
                current_price = self.bot._get_current_price(figi)

                info(f"   Аварийное закрытие SHORT {ticker}: покупка {quantity} шт")

                success = False
                attempts = [
                    ("рыночная", None, "BUY", False),
                    ("лимитная +2%", current_price * 1.02 if current_price else None, "BUY", False),
                    ("лимитная +5%", current_price * 1.05 if current_price else None, "BUY", False),
                    ("лимитная +10%", current_price * 1.10 if current_price else None, "BUY", False),
                ]

                for attempt_name, limit_price, direction, use_emergency in attempts:
                    try:
                        if attempt_name == "рыночная":
                            success = _get_tbank().buy(figi, quantity)
                        elif limit_price and direction:
                            success = _get_tbank().place_pending_order(figi, quantity, direction, limit_price)

                        if success:
                            info(f"   ✅ {attempt_name} - успешно!")
                            closed += 1
                            self._remove_position(figi)
                            break
                        else:
                            time.sleep(0.5)
                    except Exception:
                        time.sleep(0.5)

                if not success and quantity > 1:
                    reduced_qty = max(1, quantity // 2)
                    warning(f"   🔄 Финальная попытка: уменьшенный размер {reduced_qty} шт")
                    if _get_tbank().buy(figi, reduced_qty):
                        closed += 1
                        self._update_position_quantity(figi, reduced_qty)

            return closed

        except Exception as e:
            error(f"❌ Ошибка: {e}")
            return 0

    def _emergency_close_short(self, figi: str, quantity: int, ticker: str, is_critical: bool) -> bool:
        """Аварийное закрытие одной SHORT позиции с проверкой средств"""
        from trading_bot.api.tbank_client import tbank

        # ========== 1. ПОЛУЧАЕМ ЛОТ ДЛЯ ИНСТРУМЕНТА ==========
        lot = 1
        try:
            all_shares = tbank.get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('figi') == figi:
                    lot = stock.get('lot', 1)
                    break
        except Exception as e:
            debug(f"Не удалось получить лот для {figi}: {e}")

        # ========== 2. ПОЛУЧАЕМ ТЕКУЩУЮ ЦЕНУ ==========
        current_price = self.bot._get_current_price(figi)
        if not current_price:
            error(f"❌ Не удалось получить текущую цену для {ticker}")
            return False

        # ========== 3. ПРОВЕРКА ДОСТАТОЧНОСТИ СРЕДСТВ ==========
        buy_back_cost = quantity * current_price
        available, total, _ = tbank.get_available_funds()

        # ========== 4. ЕСЛИ НЕ ХВАТАЕТ СРЕДСТВ - ПРОБУЕМ ЧАСТИЧНО ==========
        if available < buy_back_cost * 1.05:
            warning(f"⚠️ Недостаточно средств для закрытия SHORT {ticker}")
            warning(f"   Нужно: {buy_back_cost:.0f}₽, Доступно: {available:.0f}₽")

            # Пробуем закрыть частично - сколько можем позволить
            max_affordable_qty = int(available * 0.9 / current_price)

            if max_affordable_qty >= lot:
                # Округляем до лота
                partial_qty = (max_affordable_qty // lot) * lot
                if partial_qty < lot:
                    partial_qty = lot

                if partial_qty < quantity:
                    warning(f"🔄 Пробуем частичное закрытие SHORT {ticker}: {partial_qty} из {quantity} шт")

                    if tbank.buy(figi, partial_qty):
                        success(f"✅ Частично закрыт SHORT {ticker}: {partial_qty} шт")
                        # Обновляем позицию
                        self._update_position_quantity(figi, quantity - partial_qty)
                        return True
                    else:
                        warning(f"⚠️ Не удалось частично закрыть SHORT {ticker}")
                        return False
            else:
                # Используем lot, который уже определён выше
                error(
                    f"❌ Невозможно закрыть SHORT {ticker}: даже {lot} лот стоит {current_price * lot:.0f}₽ > {available:.0f}₽")
                return False

        # ========== 5. НОРМАЛЬНОЕ ЗАКРЫТИЕ (ДОСТАТОЧНО СРЕДСТВ) ==========
        # Критический режим - специальная экстренная заявка
        if is_critical and hasattr(tbank, '_place_market_order_emergency'):
            if tbank._place_market_order_emergency(figi, quantity, "BUY"):
                self._remove_position(figi)
                success(f"✅ Критический SHORT {ticker} закрыт (экстренная заявка)")
                return True

        # Рыночная заявка
        if tbank.buy(figi, quantity):
            self._remove_position(figi)
            success(f"✅ SHORT {ticker} закрыт (рыночная заявка)")
            return True

        # Лимитная заявка (+2% как запас)
        if current_price:
            limit_price = current_price * 1.02
            limit_price = tbank._round_to_min_increment(figi, limit_price)
            if hasattr(tbank, 'place_pending_order'):
                if tbank.place_pending_order(figi, quantity, "BUY", limit_price):
                    self._remove_position(figi)
                    success(f"✅ SHORT {ticker} закрыт (лимитная заявка +2%)")
                    return True

        # Лимитная заявка (+5%)
        if current_price:
            limit_price = current_price * 1.05
            limit_price = tbank._round_to_min_increment(figi, limit_price)
            if hasattr(tbank, 'place_pending_order'):
                if tbank.place_pending_order(figi, quantity, "BUY", limit_price):
                    self._remove_position(figi)
                    success(f"✅ SHORT {ticker} закрыт (лимитная заявка +5%)")
                    return True

        error(f"❌ НЕ УДАЛОСЬ ЗАКРЫТЬ SHORT {ticker} ни одним способом")
        return False

    def _emergency_close_long(self, figi: str, quantity: int, ticker: str) -> bool:
        """Аварийное закрытие одной LONG позиции"""
        if _get_tbank().sell(figi, quantity):
            self._remove_position(figi)
            return True

        current_price = self.bot._get_current_price(figi)
        if current_price and hasattr(_get_tbank(), 'place_pending_order'):
            limit_price = current_price * 0.98
            if _get_tbank().place_pending_order(figi, quantity, "SELL", limit_price):
                self._remove_position(figi)
                return True

        return False

    def _remove_position(self, figi: str):
        """Удаление позиции из менеджера"""
        try:
            from trading_bot.risk.position_manager import position_manager
            position_manager.remove_position(figi)
        except ImportError:
            pass

    def _update_position_quantity(self, figi: str, new_quantity: int):
        """Обновление количества позиции"""
        try:
            from trading_bot.risk.position_manager import position_manager
            pos = position_manager.get_position(figi)
            if pos:
                pos.quantity = new_quantity
                if pos.quantity <= 0:
                    position_manager.remove_position(figi)
        except ImportError:
            pass