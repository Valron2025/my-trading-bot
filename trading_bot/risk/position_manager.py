# trading_bot/risk/position_manager.py (исправленный полностью)

"""Модуль управления позициями - ПОЛНАЯ ВЕРСИЯ с Iceberg и Trailing Stop"""

import time
import asyncio
import uuid
import random
from typing import Dict, Optional, List, Any, Tuple
from datetime import datetime, timedelta, timezone

from ..config import config
from ..models import Position, OrderSide
from ..logger import info, success, error, warning, debug


# ========== ЧАСОВОЙ ПОЯС (МСК) ==========
MOSCOW_TZ = timezone(timedelta(hours=3))


def _get_tbank():
    """Отложенный импорт для избежания циклических зависимостей"""
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    """Отложенный импорт telegram_notifier"""
    from trading_bot.telegram.telegram_notifier import telegram
    return telegram


# ========== ICEBERG ORDER MANAGER ==========

class IcebergOrderManager:
    """
    Управление айсберг-заявками (видимая часть меньше реального объёма)
    """

    def __init__(self, bot):
        self.bot = bot
        self._iceberg_orders: Dict[str, Dict] = {}

    async def place_iceberg_order(
            self,
            ticker: str,
            figi: str,
            direction: str,
            total_quantity: int,
            visible_quantity: int = None,
            price: float = None,
            order_type: str = "LIMIT"
    ) -> Dict[str, Any]:
        """
        Размещение айсберг-заявки

        Args:
            ticker: Тикер
            figi: FIGI инструмента
            direction: BUY или SELL
            total_quantity: Общий объём
            visible_quantity: Видимая часть (по умолчанию 10% от общего)
            price: Цена (если не указана - рыночная)
            order_type: Тип ордера
        """
        try:
            from trading_bot.api.tbank_client import tbank

            if visible_quantity is None:
                visible_quantity = max(1, total_quantity // 10)

            if price is None or price <= 0:
                price = tbank.get_current_price(figi)
                if not price:
                    return {"success": False, "error": "Не удалось получить цену"}

            min_lot = await self._get_min_lot(figi)

            # Корректировка по лоту
            if min_lot > 1:
                visible_quantity = ((visible_quantity + min_lot - 1) // min_lot) * min_lot
                if visible_quantity > total_quantity:
                    visible_quantity = total_quantity

            iceberg_id = str(uuid.uuid4())[:8]

            iceberg_data = {
                "id": iceberg_id,
                "ticker": ticker,
                "figi": figi,
                "direction": direction,
                "total_quantity": total_quantity,
                "visible_quantity": visible_quantity,
                "remaining": total_quantity,
                "price": price,
                "min_lot": min_lot,
                "active": True,
                "created_at": datetime.now(),
                "parts_placed": 0,
                "order_ids": []
            }

            self._iceberg_orders[iceberg_id] = iceberg_data

            # Размещаем первую часть
            first_quantity = min(visible_quantity, total_quantity)

            # Случайное смещение цены для маскировки
            price_variation = random.uniform(-0.001, 0.001) * (price * 0.01)
            first_price = round(price + price_variation, 4)

            if direction == "BUY":
                success_flag = tbank.buy(figi, first_quantity)
            else:
                success_flag = tbank.sell(figi, first_quantity)

            if success_flag:
                iceberg_data["remaining"] = total_quantity - first_quantity
                iceberg_data["parts_placed"] = 1

                info(f"🧊 Айсберг-заявка {ticker}: {total_quantity} шт (видимо {visible_quantity})")
                info(f"   Часть 1: {first_quantity} шт по ~{first_price:.2f}₽")

                # Запускаем фоновое дозаполнение
                asyncio.create_task(self._refill_iceberg(iceberg_id))

                return {
                    "success": True,
                    "order_id": iceberg_id,
                    "type": "ICEBERG",
                    "visible_quantity": visible_quantity,
                    "total_quantity": total_quantity,
                    "ticker": ticker
                }

            return {"success": False, "error": "Не удалось разместить первую часть"}

        except Exception as e:
            error(f"❌ Ошибка айсберг-заявки {ticker}: {e}")
            return {"success": False, "error": str(e)}

    async def _refill_iceberg(self, iceberg_id: str):
        """Фоновое дозаполнение айсберг-заявки"""
        from trading_bot.api.tbank_client import tbank

        iceberg = self._iceberg_orders.get(iceberg_id)
        if not iceberg:
            return

        ticker = iceberg["ticker"]
        figi = iceberg["figi"]
        direction = iceberg["direction"]
        visible_quantity = iceberg["visible_quantity"]
        price = iceberg["price"]
        min_lot = iceberg.get("min_lot", 1)

        # Случайные интервалы для маскировки
        base_delay = random.uniform(3, 10)

        while iceberg.get("active", True) and iceberg.get("remaining", 0) > 0:
            try:
                remaining = iceberg.get("remaining", 0)
                if remaining <= 0:
                    break

                # Случайная задержка
                await asyncio.sleep(base_delay + random.uniform(-1, 2))

                # Размер следующей части (случайный)
                next_qty = random.randint(
                    max(1, int(visible_quantity * 0.5)),
                    min(visible_quantity, remaining)
                )

                if min_lot > 1:
                    next_qty = ((next_qty + min_lot - 1) // min_lot) * min_lot

                next_qty = min(next_qty, remaining)

                if next_qty < min_lot:
                    next_qty = min_lot

                # Случайное смещение цены
                price_variation = random.uniform(-0.002, 0.002) * (price * 0.01)
                next_price = round(price + price_variation, 4)

                info(f"🧊 Дозаполнение {ticker}: {next_qty} шт по ~{next_price:.2f}₽")

                if direction == "BUY":
                    success_flag = tbank.buy(figi, next_qty)
                else:
                    success_flag = tbank.sell(figi, next_qty)

                if success_flag:
                    iceberg["remaining"] = remaining - next_qty
                    iceberg["parts_placed"] += 1
                    info(f"   Часть {iceberg['parts_placed']}: осталось {iceberg['remaining']} шт")

                    # Уменьшаем задержку после успеха
                    base_delay = max(2, base_delay * 0.9)
                else:
                    warning(f"⚠️ Не удалось дозаполнить {ticker}, пауза...")
                    await asyncio.sleep(15)

            except Exception as e:
                error(f"❌ Ошибка дозаполнения {ticker}: {e}")
                await asyncio.sleep(30)

        iceberg["active"] = False
        info(f"✅ Айсберг-заявка {ticker} завершена ({iceberg.get('parts_placed', 0)} частей)")

    async def _get_min_lot(self, figi: str) -> int:
        """Получение минимального лота"""
        try:
            from trading_bot.api.tbank_client import tbank
            all_shares = tbank.get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('figi') == figi:
                    return stock.get('lot', 1)
            return 1
        except Exception:
            return 1

    def get_iceberg_status(self) -> Dict[str, Any]:
        """Статус всех айсберг-заявок"""
        return {
            "active_icebergs": len(self._iceberg_orders),
            "icebergs": [
                {
                    "ticker": o["ticker"],
                    "total": o["total_quantity"],
                    "remaining": o["remaining"],
                    "parts": o["parts_placed"]
                }
                for o in self._iceberg_orders.values() if o.get("active")
            ]
        }

    def cancel_iceberg(self, iceberg_id: str) -> bool:
        """Отмена айсберг-заявки"""
        if iceberg_id in self._iceberg_orders:
            self._iceberg_orders[iceberg_id]["active"] = False
            del self._iceberg_orders[iceberg_id]
            return True
        return False


# ========== TRAILING STOP MANAGER ==========

class TrailingStopManager:
    """Управление трейлинг-стопами"""

    def __init__(self, bot):
        self.bot = bot
        self._trailing_stops: Dict[str, Dict] = {}

    async def set_trailing_stop(
            self,
            ticker: str,
            figi: str,
            quantity: int,
            trailing_percent: float = 2.0,
            current_price: float = None
    ) -> Dict[str, Any]:
        """
        Установка трейлинг-стопа

        Args:
            ticker: Тикер
            figi: FIGI инструмента
            quantity: Количество акций
            trailing_percent: Процент отступа (например 2.0 = 2%)
            current_price: Текущая цена (если не указана - получим из API)
        """
        try:
            from trading_bot.api.tbank_client import tbank

            if current_price is None:
                current_price = tbank.get_current_price(figi)
                if not current_price:
                    return {"success": False, "error": "Не удалось получить цену"}

            trailing_id = str(uuid.uuid4())[:8]
            stop_price = current_price * (1 - trailing_percent / 100)

            trailing_data = {
                "id": trailing_id,
                "ticker": ticker,
                "figi": figi,
                "quantity": quantity,
                "trailing_percent": trailing_percent,
                "highest_price": current_price,
                "current_stop": stop_price,
                "active": True,
                "created_at": datetime.now()
            }

            self._trailing_stops[trailing_id] = trailing_data

            info(f"📉 Трейлинг-стоп {ticker}: {trailing_percent}%, стоп {stop_price:.2f}₽")

            # Запускаем мониторинг
            asyncio.create_task(self._monitor_trailing_stop(trailing_id))

            return {
                "success": True,
                "trailing_id": trailing_id,
                "ticker": ticker,
                "trailing_percent": trailing_percent,
                "initial_stop": stop_price
            }

        except Exception as e:
            error(f"❌ Ошибка установки трейлинг-стопа {ticker}: {e}")
            return {"success": False, "error": str(e)}

    async def _monitor_trailing_stop(self, trailing_id: str):
        """Мониторинг трейлинг-стопа"""
        from trading_bot.api.tbank_client import tbank

        ts = self._trailing_stops.get(trailing_id)
        if not ts:
            return

        ticker = ts["ticker"]
        figi = ts["figi"]
        quantity = ts["quantity"]
        trailing_percent = ts["trailing_percent"]

        while ts.get("active", True):
            try:
                current_price = tbank.get_current_price(figi)

                if current_price:
                    # Обновляем максимум
                    if current_price > ts["highest_price"]:
                        ts["highest_price"] = current_price
                        new_stop = current_price * (1 - trailing_percent / 100)
                        ts["current_stop"] = new_stop
                        debug(f"📈 {ticker}: цена {current_price:.2f}, новый стоп {new_stop:.2f}")

                    # Проверяем срабатывание
                    if current_price <= ts["current_stop"]:
                        info(f"🔔 Трейлинг-стоп сработал для {ticker} при цене {current_price:.2f}")

                        # Закрываем позицию
                        success_flag = tbank.sell(figi, quantity)

                        if success_flag:
                            highest = ts["highest_price"]
                            drop_percent = (1 - current_price / highest) * 100 if highest > 0 else 0

                            info(f"✅ Позиция {ticker} закрыта по трейлинг-стопу")
                            info(f"   Максимум: {highest:.2f}, Закрытие: {current_price:.2f}, Падение: {drop_percent:.1f}%")

                            ts["active"] = False
                            ts["triggered_at"] = datetime.now()
                            ts["trigger_price"] = current_price
                            break
                        else:
                            error(f"❌ Не удалось закрыть {ticker} по трейлинг-стопу")

                await asyncio.sleep(5)  # Проверка каждые 5 секунд

            except Exception as e:
                error(f"❌ Ошибка мониторинга {ticker}: {e}")
                await asyncio.sleep(30)

        # Очистка
        if trailing_id in self._trailing_stops:
            del self._trailing_stops[trailing_id]

    def get_trailing_status(self) -> Dict[str, Any]:
        """Статус всех трейлинг-стопов"""
        return {
            "active_stops": len(self._trailing_stops),
            "stops": [
                {
                    "ticker": ts["ticker"],
                    "stop_price": round(ts["current_stop"], 2),
                    "highest": round(ts["highest_price"], 2),
                    "trailing": ts["trailing_percent"]
                }
                for ts in self._trailing_stops.values()
            ]
        }

    def cancel_trailing_stop(self, trailing_id: str) -> bool:
        """Отмена трейлинг-стопа"""
        if trailing_id in self._trailing_stops:
            self._trailing_stops[trailing_id]["active"] = False
            del self._trailing_stops[trailing_id]
            return True
        return False


# ========== ОСНОВНОЙ КЛАСС POSITION MANAGER ==========

class PositionManager:
    """Управление открытыми позициями"""

    def __init__(self):
        self._positions: Dict[str, Position] = {}
        self._temp_skip_until: Dict[str, datetime] = {}
        self._temp_blacklist: Dict[str, datetime] = {}
        self._otc_cache: Dict[str, bool] = {}
        self._otc_cache_time: Dict[str, datetime] = {}
        self._otc_cache_ttl = 3600
        self._trading_bot = None
        self._last_cleanup = datetime.now(MOSCOW_TZ)
        self._margin_closure_done = False
        self._figi_to_ticker_cache: Dict[str, str] = {}
        self._checking_critical_margin = False

        # Дополнительные менеджеры
        self.iceberg_manager = None
        self.trailing_manager = None

    def init_advanced_managers(self, bot):
        """Инициализация дополнительных менеджеров"""
        self.iceberg_manager = IcebergOrderManager(bot)
        self.trailing_manager = TrailingStopManager(bot)
        info("✅ Iceberg и Trailing Stop менеджеры инициализированы")

    def _round_price_for_order(self, price: float, figi: str = None) -> float:
        """Округление цены до 2 знаков после запятой"""
        if price <= 0:
            return 0.01
        rounded = round(price, 2)
        if rounded < 0.01:
            rounded = 0.01
        return rounded

    def set_trading_bot(self, trading_bot):
        """Устанавливает ссылку на TradingBot"""
        self._trading_bot = trading_bot
        success("✅ PositionManager связан с TradingBot")

    def is_temp_skipped(self, figi: str) -> bool:
        """Проверяет, не заблокирована ли акция временно"""
        now = datetime.now(MOSCOW_TZ)

        # Удаляем просроченные записи
        expired = [f for f, until in self._temp_skip_until.items() if now >= until]
        for f in expired:
            del self._temp_skip_until[f]

        return figi in self._temp_skip_until

    def is_temp_blacklisted(self, figi: str) -> bool:
        """Проверка в чёрном списке"""
        if figi in self._temp_blacklist:
            if datetime.now(MOSCOW_TZ) < self._temp_blacklist[figi]:
                return True
            del self._temp_blacklist[figi]
        return False

    def add_temp_skip(self, figi: str, minutes: int = 10):
        """Добавление во временную блокировку"""
        self._temp_skip_until[figi] = datetime.now(MOSCOW_TZ) + timedelta(minutes=minutes)
        info(f"🔒 Добавлен {figi} в временную блокировку на {minutes} минут")

    def add_to_blacklist(self, figi: str, minutes: int = 60):
        """Добавление в чёрный список"""
        self._temp_blacklist[figi] = datetime.now(MOSCOW_TZ) + timedelta(minutes=minutes)
        info(f"⛔ {figi} добавлен в чёрный список на {minutes} минут")

    def add_position(self, figi: str, quantity: int, price: float, side: OrderSide,
                     take_profit_pct: float = None, stop_loss_pct: float = None,
                     trailing_stop_pct: float = None, auto_set_stop: bool = True,
                     ticker: str = None) -> Position:
        """Добавление позиции с автоматической установкой стоп-приказов"""

        from trading_bot.api.tbank_client import tbank

        # Если ticker не передан, пробуем получить
        if ticker is None:
            ticker = self._get_ticker_by_figi(figi)

        position = Position(
            figi=figi,
            ticker=ticker or figi[:12],
            quantity=quantity,
            avg_price=price,
            side=side,
            entry_time=datetime.now(MOSCOW_TZ)
        )

        position.take_profit_pct = take_profit_pct if take_profit_pct is not None else config.take_profit_pct
        position.stop_loss_pct = stop_loss_pct if stop_loss_pct is not None else config.stop_loss_pct
        position.trailing_stop_pct = trailing_stop_pct if trailing_stop_pct is not None else config.trailing_stop_pct

        if side == OrderSide.LONG:
            position.highest_price = price
            stop_price = price * (1 - position.stop_loss_pct / 100)
            take_profit_price = price * (1 + position.take_profit_pct / 100)
        else:
            position.lowest_price = price
            stop_price = price * (1 + position.stop_loss_pct / 100)
            take_profit_price = price * (1 - position.take_profit_pct / 100)

        self._positions[figi] = position

        info(f"📊 ДОБАВЛЕНА ПОЗИЦИЯ {ticker}: {side.value} {quantity}шт по {price:.2f}₽")
        info(
            f"   🎯 TP: +{position.take_profit_pct}% | 🛑 SL: -{position.stop_loss_pct}% | 🔻 TS: {position.trailing_stop_pct}%")

        if auto_set_stop:
            try:
                # ========== 1. ПРОВЕРКА: требует ли инструмент подтверждения ==========
                if tbank.is_confirmation_required(figi):
                    info(f"📋 {ticker} требует подтверждения сделок → используем эмуляцию стоп-приказов")
                    position.stop_order_placed = False
                    return position

                # ========== 2. ПРОВЕРКА: доступен ли рынок ==========
                is_available, reason = tbank.is_market_available(figi)
                if not is_available:
                    info(f"⏸️ Рынок недоступен для {ticker}: {reason} → эмуляция стоп-приказов")
                    position.stop_order_placed = False
                    return position

                # ========== 3. ПРОВЕРКА: поддерживает ли инструмент стоп-приказы ==========
                supports_stops = tbank.supports_stop_orders(figi)
                if not supports_stops:
                    info(f"📋 {ticker} не поддерживает стоп-приказы → используем эмуляцию")
                    position.stop_order_placed = False
                    return position

                # ========== 4. УСТАНОВКА РЕАЛЬНЫХ СТОП-ПРИКАЗОВ ==========
                side_str = "LONG" if side == OrderSide.LONG else "SHORT"

                # Устанавливаем стоп-лосс
                stop_success = self.set_stop_loss(figi, side_str, stop_price)
                if stop_success:
                    info(f"   ✅ Стоп-лосс установлен на {stop_price:.2f}₽")
                    position.stop_order_placed = True
                    position.stop_order_price = stop_price
                else:
                    info(f"   ⚠️ Стоп-лосс не установлен, используем эмуляцию")
                    position.stop_order_placed = False

                # Устанавливаем тейк-профит
                tp_success = self.set_take_profit(figi, side_str, take_profit_price)
                if tp_success:
                    info(f"   ✅ Тейк-профит установлен на {take_profit_price:.2f}₽")
                    position.take_profit_price = take_profit_price
                else:
                    info(f"   ⚠️ Тейк-профит не установлен")

            except Exception as e:
                error_msg = str(e)
                if "30240" in error_msg:
                    info(f"📋 {ticker} требует подтверждения сделок → используем эмуляцию")
                elif "30042" in error_msg:
                    warning(f"⚠️ {ticker}: недостаточно маржи для установки стоп-приказов")
                else:
                    warning(f"⚠️ Ошибка установки стоп-приказов для {ticker}: {error_msg[:100]}")
                position.stop_order_placed = False

        return position

    def remove_position(self, figi: str) -> Optional[Position]:
        """Удаление позиции"""
        if figi in self._positions:
            pos = self._positions.pop(figi)
            info(f"🗑️ Удалена позиция {figi}")
            return pos
        return None

    def get_position(self, figi: str) -> Optional[Position]:
        """Получение позиции"""
        return self._positions.get(figi)

    def get_all_positions(self) -> Dict[str, Position]:
        """Получение всех позиций"""
        return self._positions.copy()

    def sync_with_broker(self):
        """Синхронизация с брокером"""
        try:
            broker_positions = _get_tbank().get_positions()
            broker_figis = {p['figi'] for p in broker_positions}

            # Удаляем позиции, которых нет у брокера
            for figi in list(self._positions.keys()):
                if figi not in broker_figis:
                    info(f"Позиция {figi} закрыта у брокера, удаляем из менеджера")
                    del self._positions[figi]

            # Добавляем новые позиции
            for pos in broker_positions:
                figi = pos['figi']
                quantity_raw = pos['quantity']

                if quantity_raw < 0:
                    side = OrderSide.SHORT
                    quantity = abs(quantity_raw)
                else:
                    side = OrderSide.LONG
                    quantity = quantity_raw

                if quantity == 0:
                    if figi in self._positions:
                        del self._positions[figi]
                    continue

                current_price = _get_tbank().get_current_price(figi)
                if not current_price:
                    continue

                # Получаем тикер
                ticker = self._get_ticker_by_figi(figi) or figi[:12]

                if figi in self._positions:
                    existing = self._positions[figi]
                    if existing.side != side:
                        warning(f"🔄 Сторона позиции {figi} изменилась, удаляем старую")
                        del self._positions[figi]
                    else:
                        if existing.quantity != quantity:
                            existing.quantity = quantity
                        existing.avg_price = pos['avg_price']
                        if side == OrderSide.LONG and current_price > existing.highest_price:
                            existing.highest_price = current_price
                        elif side == OrderSide.SHORT and current_price < existing.lowest_price:
                            existing.lowest_price = current_price
                        continue

                # ✅ ИСПРАВЛЕНО: добавляем ticker
                position = Position(
                    figi=figi,
                    ticker=self._get_ticker_by_figi(figi) or figi[:12],
                    quantity=quantity,
                    avg_price=pos['avg_price'],
                    side=side,
                    entry_time=datetime.now(MOSCOW_TZ)
                )

                if side == OrderSide.LONG:
                    position.highest_price = current_price
                else:
                    position.lowest_price = current_price

                self._positions[figi] = position
                info(f"📌 Синхронизирована {side.value} позиция: {quantity}шт по {pos['avg_price']:.2f}₽")

        except Exception as e:
            error(f"Ошибка синхронизации с брокером: {e}")

    def _get_positions_with_pnl(self) -> list:
        """Получение списка позиций с расчётом P&L"""
        positions_data = []
        for figi, position in self._positions.items():
            current_price = _get_tbank().get_current_price(figi)
            if current_price:
                profit_pct = position.current_profit_pct(current_price)
                profit_amount = position.current_profit_amount(current_price)
                positions_data.append({
                    'figi': figi,
                    'position': position,
                    'profit_pct': profit_pct,
                    'profit_amount': profit_amount,
                    'quantity': position.quantity,
                    'side': position.side
                })
        return positions_data

    def manage_long_position(self, position: Position, current_price: float) -> bool:
        """
        Управление LONG позицией - С ЗАЩИТОЙ ОТ МГНОВЕННОГО ЗАКРЫТИЯ
        """
        # ========== 1. ПРОВЕРКА ОСТАНОВКИ БОТА ==========
        if self._trading_bot and hasattr(self._trading_bot, '_shutting_down'):
            if self._trading_bot._shutting_down:
                self._close_position(position, current_price, "остановка бота", 0)
                return True

        # ========== 2. ЗАЩИТА ОТ ДУБЛИРОВАНИЯ ==========
        if hasattr(position, '_in_manage') and position._in_manage:
            return False
        position._in_manage = True

        try:
            # ========== 3. БАЗОВЫЕ РАСЧЁТЫ ==========
            now = datetime.now(MOSCOW_TZ)
            entry_time = position.entry_time
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=MOSCOW_TZ)

            hold_seconds = int((now - entry_time).total_seconds())
            hold_minutes = hold_seconds / 60

            profit_pct = (current_price - position.avg_price) / position.avg_price * 100
            position.update_high_low(current_price)

            take_profit_pct = config.take_profit_pct
            stop_loss_pct = config.stop_loss_pct
            trailing_stop_pct = config.trailing_stop_pct

            take_profit_price = position.avg_price * (1 + take_profit_pct / 100)
            stop_loss_price = position.avg_price * (1 - stop_loss_pct / 100)

            # Буфер для эмуляции (чтобы не срабатывало на копейках)
            BUFFER = 0.2
            take_profit_trigger = take_profit_pct - BUFFER
            stop_loss_trigger = -stop_loss_pct + BUFFER

            # ========== 4. ПРОВЕРКА ВОЗМОЖНОСТИ АВТОТОРГОВЛИ ==========
            is_tradable, reason = _get_tbank().is_tradable_automatically(position.figi)
            if not is_tradable:
                success(f"   🚫 {position.ticker} нельзя торговать автоматически: {reason}")
                self._close_position(position, current_price, f"автоторговля невозможна: {reason}", profit_pct)
                return True

            # ========== 5. КРАСИВОЕ ЛОГИРОВАНИЕ СОСТОЯНИЯ (раз в минуту) ==========
            if hold_seconds % 60 == 0:
                icon = "🟢" if profit_pct > 0 else "🔴" if profit_pct < 0 else "⚪"
                info(f"   {icon} [{position.ticker}] {hold_minutes:.1f}мин | {profit_pct:+.2f}% | {current_price:.2f}₽")

            # ========== 6. ЗАЩИТА ОТ МГНОВЕННОГО ЗАКРЫТИЯ ==========
            if hold_seconds < 30:
                info(
                    f"   🕐 [{position.ticker}] Адаптация: {hold_seconds}/30с, {profit_pct:+.2f}% | {current_price:.2f}₽")
                return False

            # ========== 7. ПРОВЕРКА OTC РЕЖИМА ==========
            try:
                from trading_bot.core.market_checker import MarketChecker
                market_checker = MarketChecker()
                is_otc_mode = market_checker.is_otc_mode()
            except ImportError:
                is_otc_mode = False

            # ========== 8. УСТАНОВКА РЕАЛЬНЫХ СТОП-ПРИКАЗОВ ==========
            if not hasattr(position, 'stop_order_placed') or not position.stop_order_placed:
                if _get_tbank().is_confirmation_required(position.figi):
                    success(f"📋 {position.ticker} требует подтверждения сделок → эмуляция")
                    position.stop_order_placed = False
                elif is_otc_mode:
                    success(f"📋 OTC режим → эмуляция стоп-приказов для {position.ticker}")
                    position.stop_order_placed = False
                else:
                    supports_stops = _get_tbank().supports_stop_orders(position.figi)
                    if not supports_stops:
                        success(f"📋 {position.ticker} не поддерживает стоп-приказы → эмуляция")
                        position.stop_order_placed = False
                    else:
                        info(f"📋 Устанавливаем РЕАЛЬНЫЕ стоп-приказы для {position.ticker}")
                        info(f"   🛑 Стоп-лосс: {stop_loss_price:.2f}₽ (-{stop_loss_pct:.1f}%)")
                        info(f"   🎯 Тейк-профит: {take_profit_price:.2f}₽ (+{take_profit_pct:.1f}%)")

                        stop_success = False
                        tp_success = False

                        try:
                            stop_success = _get_tbank().place_stop_loss_order(
                                figi=position.figi,
                                quantity=position.quantity,
                                stop_price=stop_loss_price,
                                side="LONG"
                            )
                            if stop_success:
                                success(f"   ✅ Реальный стоп-лосс установлен!")
                        except Exception as e:
                            error_msg = str(e)
                            if "30240" in error_msg:
                                warning(f"⚠️ {position.ticker} требует подтверждения сделок")
                            else:
                                warning(f"⚠️ Ошибка установки стоп-лосса: {error_msg[:100]}")

                        try:
                            tp_success = _get_tbank().place_take_profit_order(
                                figi=position.figi,
                                quantity=position.quantity,
                                take_profit_price=take_profit_price,
                                side="LONG"
                            )
                            if tp_success:
                                success(f"   ✅ Реальный тейк-профит установлен!")
                        except Exception as e:
                            error_msg = str(e)
                            if "30240" in error_msg:
                                warning(f"⚠️ {position.ticker} требует подтверждения сделок")
                            else:
                                warning(f"⚠️ Ошибка установки тейк-профита: {error_msg[:100]}")

                        if stop_success or tp_success:
                            position.stop_order_placed = True
                            position.stop_order_price = stop_loss_price
                            position.take_profit_price = take_profit_price
                            success(f"✅ Реальные стоп-приказы установлены!")
                        else:
                            success(f"⚠️ Используем эмуляцию стоп-приказов")
                            position.stop_order_placed = False

            # ========== 9. ЭМУЛЯЦИЯ СТОП-ЛОССА ==========
            if not getattr(position, 'stop_order_placed', False):
                # Стоп-лосс (с подтверждением из 2х проверок)
                if profit_pct <= stop_loss_trigger:
                    if hasattr(position, '_stop_check_count'):
                        position._stop_check_count += 1
                    else:
                        position._stop_check_count = 1

                    if position._stop_check_count >= 2:
                        info(f"\n{'🔴' * 40}")
                        info(f"🛑 СТОП-ЛОСС LONG (эмуляция)!")
                        info(f"   Тикер: {position.ticker}")
                        info(f"   Убыток: {profit_pct:.2f}%")
                        info(f"   Цена входа: {position.avg_price:.2f}₽")
                        info(f"   Цена стопа: {stop_loss_price:.2f}₽")
                        info(f"   Текущая цена: {current_price:.2f}₽")
                        info(f"   Время: {hold_minutes:.1f} мин")
                        info(f"{'🔴' * 40}")
                        self._close_position(position, current_price, "стоп-лосс", profit_pct)
                        return True
                    else:
                        warning(f"   ⚠️ [{position.ticker}] Стоп-лосс кандидат, ждём подтверждения...")
                        return False

                if hasattr(position, '_stop_check_count'):
                    delattr(position, '_stop_check_count')

                # Тейк-профит
                if profit_pct >= take_profit_trigger:
                    info(f"\n{'🟢' * 40}")
                    info(f"🎯 ТЕЙК-ПРОФИТ LONG (эмуляция)!")
                    info(f"   Тикер: {position.ticker}")
                    info(f"   Прибыль: {profit_pct:.2f}%")
                    info(f"   Цена входа: {position.avg_price:.2f}₽")
                    info(f"   Цена тейка: {take_profit_price:.2f}₽")
                    info(f"   Текущая цена: {current_price:.2f}₽")
                    info(f"   Время: {hold_minutes:.1f} мин")
                    info(f"{'🟢' * 40}")
                    self._close_position(position, current_price, "тейк-профит", profit_pct)
                    return True

                # Трейлинг-стоп
                activation_threshold = take_profit_pct * 0.3
                if profit_pct > activation_threshold:
                    highest_price = position.highest_price
                    distance_to_target = (take_profit_pct - profit_pct) / take_profit_pct if take_profit_pct > 0 else 1
                    dynamic_trailing = trailing_stop_pct * (1 + distance_to_target * 0.5)
                    dynamic_trailing = min(0.8, dynamic_trailing)

                    trailing_stop_price = highest_price * (1 - dynamic_trailing / 100)
                    if current_price <= trailing_stop_price:
                        info(f"\n{'🔻' * 40}")
                        info(f"🔻 ТРЕЙЛИНГ-СТОП LONG (эмуляция)!")
                        info(f"   Тикер: {position.ticker}")
                        info(f"   Прибыль: {profit_pct:.2f}%")
                        info(f"   Максимум: {highest_price:.2f}₽")
                        info(f"   Стоп: {trailing_stop_price:.2f}₽ (-{dynamic_trailing:.1f}%)")
                        info(f"   Текущая цена: {current_price:.2f}₽")
                        info(f"{'🔻' * 40}")
                        self._close_position(position, current_price, "трейлинг-стоп", profit_pct)
                        return True

            # ========== 10. ТАЙМАУТ ПОЗИЦИИ (ИСПРАВЛЕНО!) ==========
            if profit_pct < 0:
                if profit_pct < -0.5:
                    max_hold = 2
                    warning(f"   🔴 СИЛЬНЫЙ УБЫТОК {position.ticker} ({profit_pct:.2f}%)! Таймаут {max_hold} мин")
                else:
                    max_hold = 5
                    warning(f"   🟡 УБЫТОК {position.ticker} ({profit_pct:.2f}%). Таймаут {max_hold} мин")
            else:
                max_hold = self._get_timeout_for_position(position, profit_pct)

            # ✅ ИСПРАВЛЕНО: строгое сравнение (>, а не >=)
            if hold_minutes > max_hold:
                info(f"\n{'⏰' * 40}")
                info(f"⏰ ТАЙМАУТ LONG ПОЗИЦИИ!")
                info(f"   Тикер: {position.ticker}")
                info(f"   Прибыль: {profit_pct:.2f}%")
                info(f"   Время: {hold_minutes:.1f} мин (лимит {max_hold:.0f} мин)")
                info(f"   Цена: {current_price:.2f}₽")
                info(f"{'⏰' * 40}")
                self._cancel_stop_orders(position)
                self._close_position(position, current_price, "таймаут", profit_pct)
                return True

            return False

        finally:
            if hasattr(position, '_in_manage'):
                delattr(position, '_in_manage')

    def _get_timeout_for_position(self, position: Position, profit_pct: float) -> int:
        """Рассчитывает таймаут для позиции в зависимости от прибыли и типа"""
        max_hold = config.adaptive_timeout_minutes

        # Для SHORT позиций увеличиваем таймаут
        if position.side == OrderSide.SHORT:
            max_hold = max_hold * 2

        # Если позиция в убытке - уменьшаем таймаут
        if profit_pct < 0:
            max_hold = max_hold * 0.5

        # Для OTC инструментов увеличиваем таймаут
        if self._is_otc_instrument(position.figi):
            max_hold = int(max_hold * 2.5)

        return max(5, min(60, max_hold))

    def _cancel_stop_orders(self, position: Position):
        """Отмена стоп-приказов перед закрытием позиции"""
        try:
            stop_orders = _get_tbank().get_stop_orders()
            if not stop_orders:
                return

            cancelled = 0
            for order in stop_orders:
                if order.get('figi') == position.figi:
                    try:
                        _get_tbank().cancel_stop_order(order['stop_order_id'])
                        cancelled += 1
                        success(f"   ✅ Отменён стоп-приказ для {position.figi}")
                    except Exception as e:
                        if "30240" in str(e) or "не найден" in str(e):
                            debug(f"   Стоп-приказ для {position.figi} уже неактивен")
                        else:
                            warning(f"   ⚠️ Ошибка отмены стоп-приказа: {e}")

            if cancelled > 0:
                success(f"   ✅ Отменено {cancelled} стоп-приказов")

        except Exception as e:
            warning(f"   ⚠️ Ошибка получения списка стоп-приказов: {e}")

    def _is_otc_instrument(self, figi: str) -> bool:
        """Проверка OTC инструмента (внебиржевой)"""
        try:
            all_shares = _get_tbank().get_all_shares(limit=1000)
            for stock in all_shares:
                if stock.get('figi') == figi:
                    if stock.get('exchange') == 'INSTRUMENT_EXCHANGE_DEALER':
                        return True
                    if stock.get('for_qual_investor_flag', False):
                        return True
            return False
        except Exception:
            return False

    def manage_short_position(self, position: Position, current_price: float) -> bool:
        """Управление SHORT позицией с частичным закрытием при таймауте"""
        if hasattr(position, '_in_manage') and position._in_manage:
            return False
        position._in_manage = True

        try:
            profit_pct = (position.avg_price - current_price) / position.avg_price * 100
            position.update_high_low(current_price)

            take_profit_pct = position.take_profit_pct if position.take_profit_pct > 0 else config.take_profit_pct
            stop_loss_pct = position.stop_loss_pct if position.stop_loss_pct > 0 else config.stop_loss_pct

            # Стоп-лосс
            if profit_pct <= -stop_loss_pct:
                success(f"🛑 СТОП-ЛОСС SHORT! {profit_pct:.2f}%")
                self._close_position(position, current_price, "стоп-лосс SHORT", profit_pct)
                return True

            # Тейк-профит
            if profit_pct >= take_profit_pct:
                success(f"🎯 ТЕЙК-ПРОФИТ SHORT! +{profit_pct:.2f}%")
                self._close_position(position, current_price, "тейк-профит SHORT", profit_pct)
                return True

            # ========== НОВАЯ ЛОГИКА ТАЙМАУТА С ЧАСТИЧНЫМ ЗАКРЫТИЕМ ==========
            hold_minutes = position.hold_minutes()
            max_hold = config.adaptive_timeout_minutes * 2

            if profit_pct < 0:
                # В убытке - уменьшаем таймаут
                max_hold = max_hold * 0.5
                max_hold = max(3, max_hold)  # Минимум 3 минуты

            if hold_minutes >= max_hold:
                # ТАЙМАУТ - пробуем закрыть частично, если не хватает средств
                info(f"⏰ ТАЙМАУТ SHORT! {hold_minutes:.0f} мин")

                # Проверяем достаточно ли средств для полного закрытия
                buy_back_cost = position.quantity * current_price
                available, total, _ = _get_tbank().get_available_funds()

                if available < buy_back_cost * 1.05:
                    # Не хватает - пробуем частичное закрытие
                    max_affordable = int(available * 0.9 / current_price)
                    if max_affordable >= 1:
                        # Округляем до лота (берём из позиции)
                        lot = 1
                        try:
                            all_shares = _get_tbank().get_all_shares(limit=500)
                            for stock in all_shares:
                                if stock.get('figi') == position.figi:
                                    lot = stock.get('lot', 1)
                                    break
                        except:
                            pass

                        partial_qty = (max_affordable // lot) * lot
                        if partial_qty >= lot and partial_qty < position.quantity:
                            warning(
                                f"🔄 Частичное закрытие SHORT {position.ticker}: {partial_qty} из {position.quantity} шт")
                            if _get_tbank().buy(position.figi, partial_qty):
                                position.quantity -= partial_qty
                                success(f"✅ Частично закрыто {partial_qty} шт, осталось {position.quantity} шт")
                                return False  # Не закрываем полностью
                else:
                    # Достаточно средств - закрываем полностью
                    self._close_position(position, current_price, "таймаут SHORT", profit_pct)
                    return True

            return False

        finally:
            if hasattr(position, '_in_manage'):
                delattr(position, '_in_manage')

    def _close_position(self, position: Position, current_price: float, reason: str, profit_pct: float):
        """Закрытие позиции с проверкой достаточности средств и обработкой ошибок"""
        from trading_bot.api.tbank_client import tbank

        ticker = getattr(position, 'ticker', None)
        if not ticker:
            ticker = self._get_ticker_by_figi(position.figi) or position.figi[:8]

        # ========== 1. ПОЛУЧАЕМ ЛОТ ДЛЯ ИНСТРУМЕНТА ==========
        lot = 1
        try:
            all_shares = tbank.get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('figi') == position.figi:
                    lot = stock.get('lot', 1)
                    break
        except Exception:
            pass

        # ========== 2. ПРОВЕРКА ДОСТАТОЧНОСТИ СРЕДСТВ ДЛЯ SHORT ==========
        if position.side == OrderSide.SHORT:
            buy_back_cost = position.quantity * current_price * 1.05  # +5% запас

            available, total, _ = tbank.get_available_funds()

            if available < buy_back_cost:
                error(f"🚫 НЕДОСТАТОЧНО СРЕДСТВ ДЛЯ ЗАКРЫТИЯ SHORT {ticker}!")
                error(f"   Нужно: {buy_back_cost:.0f}₽, Доступно: {available:.0f}₽")

                # Пробуем закрыть частично
                max_affordable_qty = int(available * 0.9 / current_price)
                if max_affordable_qty >= lot:
                    new_quantity = (max_affordable_qty // lot) * lot
                    if new_quantity < position.quantity and new_quantity >= lot:
                        warning(f"🔄 Пробуем частичное закрытие: {new_quantity} из {position.quantity} шт")
                        if tbank.buy(position.figi, new_quantity):
                            position.quantity -= new_quantity
                            success(f"✅ Частично закрыто {new_quantity} шт, осталось {position.quantity} шт")
                            return
                error(f"❌ Невозможно закрыть SHORT {ticker}")
                return

        # ========== 3. РАСЧЁТ ПРИБЫЛИ ==========
        profit_amount = position.current_profit_amount(current_price)
        hold_minutes = position.hold_minutes()

        # ========== 4. КРАСИВЫЙ ВЫВОД ==========
        icon = "✅" if profit_amount > 0 else "❌"
        side_icon = "🟢" if position.side == OrderSide.LONG else "🔴"

        info(f"\n{'═' * 55}")
        info(f"{icon} ЗАКРЫТИЕ ПОЗИЦИИ")
        info(f"{'═' * 55}")
        info(f"   📊 Тикер:     {ticker}")
        info(f"   🔄 Сторона:   {side_icon} {position.side.value}")
        info(f"   🎯 Количество: {position.quantity} шт")
        info(f"   💰 Вход:      {position.avg_price:.2f}₽")
        info(f"   💰 Выход:     {current_price:.2f}₽")
        info(f"   📉 Изменение:  {profit_pct:+.2f}%")
        info(f"   💵 P&L:       {profit_amount:+.2f}₽")
        info(f"   ⏱️ Время:      {hold_minutes:.1f} мин")
        info(f"   📋 Причина:   {reason}")
        info(f"{'═' * 55}")

        # ========== 5. ИСПОЛНЕНИЕ ЗАЯВКИ ==========
        order_success = False

        try:
            if position.side == OrderSide.SHORT:
                info(f"🔴 Исполнение: покупка {position.quantity} шт {ticker}")
                if lot > 1 and position.quantity % lot != 0:
                    error(f"❌ Ошибка: количество {position.quantity} шт не кратно лоту {lot}")
                    return
                order_success = tbank.buy(position.figi, position.quantity)
            else:
                info(f"🟢 Исполнение: продажа {position.quantity} шт {ticker}")
                if lot > 1 and position.quantity % lot != 0:
                    error(f"❌ Ошибка: количество {position.quantity} шт не кратно лоту {lot}")
                    return
                order_success = tbank.sell(position.figi, position.quantity)

        except Exception as e:
            error_msg = str(e)
            if "30240" in error_msg:
                error(f"❌ {ticker} требует подтверждения сделок! Закройте позицию вручную в приложении")
            elif "30042" in error_msg:
                error(f"❌ {ticker}: недостаточно средств для закрытия SHORT")
            else:
                error(f"❌ Ошибка при закрытии {ticker}: {error_msg[:100]}")
            return

        # ========== 6. ОБРАБОТКА РЕЗУЛЬТАТА ==========
        if order_success:
            success(f"✅ ПОЗИЦИЯ ЗАКРЫТА: {ticker} | {profit_amount:+.2f}₽ ({profit_pct:+.2f}%)")

            # Telegram уведомление
            telegram = _get_telegram()
            if telegram:
                telegram.send_trade_closed(
                    side=position.side.value,
                    reason=reason,
                    profit_pct=profit_pct,
                    profit_amount=profit_amount,
                    ticker=ticker,
                    quantity=position.quantity
                )

            # Удаляем из менеджера
            if position.figi in self._positions:
                self.remove_position(position.figi)
        else:
            error(f"❌ НЕ УДАЛОСЬ ЗАКРЫТЬ ПОЗИЦИЮ: {ticker}")
            available, total, _ = tbank.get_available_funds()
            error(f"   💰 Доступно средств: {available:.2f}₽")
            error(f"   📊 Общий капитал: {total:.2f}₽")

            # Проверка статуса рынка
            is_available, status_msg = tbank.is_market_available(position.figi)
            if not is_available:
                error(f"   ⚠️ Рынок недоступен: {status_msg}")

    def check_all_positions(self):
        """Проверка всех открытых позиций"""
        if self._trading_bot and hasattr(self._trading_bot, '_shutting_down'):
            if self._trading_bot._shutting_down:
                return

        # Сначала синхронизируем с брокером
        self.sync_with_broker()

        for figi, position in list(self._positions.items()):
            current_price = _get_tbank().get_current_price(figi)
            if not current_price:
                continue

            if position.side == OrderSide.LONG:
                self.manage_long_position(position, current_price)
            else:
                self.manage_short_position(position, current_price)

    def emergency_close_worst_positions(self, max_to_close: int = 2) -> int:
        """Аварийное закрытие самых убыточных позиций"""
        if not self._positions:
            return 0

        positions_with_pnl = self._get_positions_with_pnl()
        positions_with_pnl.sort(key=lambda x: x['profit_pct'])

        closed = 0
        for item in positions_with_pnl[:max_to_close]:
            figi = item['figi']
            position = item['position']
            profit_pct = item['profit_pct']
            current_price = _get_tbank().get_current_price(figi)

            if current_price:
                warning(f"🚨 АВАРИЙНОЕ закрытие {position.side.value} {figi} (P&L: {profit_pct:+.2f}%)")

                if position.side == OrderSide.SHORT:
                    order_success = _get_tbank().buy(figi, position.quantity)
                else:
                    order_success = _get_tbank().sell(figi, position.quantity)

                if order_success:
                    self.remove_position(figi)
                    closed += 1
                    warning(f"   ✅ Позиция {figi} закрыта")

        return closed

    def emergency_close_all_positions(self) -> int:
        """Аварийное закрытие всех позиций"""
        warning("🚨 АВАРИЙНОЕ ЗАКРЫТИЕ ВСЕХ ПОЗИЦИЙ!")

        closed = 0
        positions_dict = self.get_all_positions().copy()

        if not positions_dict:
            return 0

        for figi, position in positions_dict.items():
            current_price = _get_tbank().get_current_price(figi)
            if not current_price:
                continue

            if position.side == OrderSide.SHORT:
                if _get_tbank().buy(figi, position.quantity):
                    closed += 1
                    self.remove_position(figi)
            else:
                if _get_tbank().sell(figi, position.quantity):
                    closed += 1
                    self.remove_position(figi)

        info(f"✅ Аварийно закрыто {closed} позиций")
        return closed

    def check_critical_margin(self) -> bool:
        """Проверка критической маржи"""
        if self._checking_critical_margin:
            return False
        self._checking_critical_margin = True

        try:
            margin_info = _get_tbank().get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)

            if margin_rate > 85:
                error(f"\n🔥 КРИТИЧЕСКАЯ МАРЖА: {margin_rate:.1f}%!")
                closed = self.emergency_close_worst_positions(max_to_close=3)
                if closed > 0:
                    success(f"✅ Закрыто {closed} убыточных позиций")
                return True

            return False

        except Exception as e:
            error(f"Ошибка проверки маржи: {e}")
            return False
        finally:
            self._checking_critical_margin = False

    def cleanup_expired_skips(self) -> int:
        """Очистка просроченных временных блокировок"""
        now = datetime.now(MOSCOW_TZ)
        expired = []

        for figi, until in self._temp_skip_until.items():
            if now >= until:
                expired.append(figi)

        for figi in expired:
            del self._temp_skip_until[figi]

        return len(expired)

    def get_skip_remaining_seconds(self, figi: str) -> int:
        """Сколько секунд осталось до разблокировки"""
        if figi in self._temp_skip_until:
            remaining = (self._temp_skip_until[figi] - datetime.now(MOSCOW_TZ)).total_seconds()
            return max(0, int(remaining))
        return 0

    def get_all_blocked(self) -> Dict[str, int]:
        """Получить все заблокированные инструменты"""
        result = {}
        now = datetime.now(MOSCOW_TZ)
        for figi, until in self._temp_skip_until.items():
            remaining = max(0, int((until - now).total_seconds() / 60))
            result[figi] = remaining
        return result

    def clear_all_skips(self) -> int:
        """Очистить ВСЕ блокировки"""
        count = len(self._temp_skip_until)
        self._temp_skip_until.clear()
        info(f"🧹 Очищено {count} блокировок")
        return count

    def _get_ticker_by_figi(self, figi: str) -> str:
        """Получение тикера по FIGI"""
        if figi in self._figi_to_ticker_cache:
            return self._figi_to_ticker_cache[figi]

        try:
            all_shares = _get_tbank().get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('figi') == figi:
                    ticker = stock.get('ticker', figi[:8])
                    self._figi_to_ticker_cache[figi] = ticker
                    return ticker
        except Exception:
            pass

        return figi[:8]

    def close_position(self, figi: str, quantity: Optional[int] = None) -> bool:
        """Публичный метод для закрытия конкретной позиции"""
        try:
            position = self.get_position(figi)
            if not position:
                warning(f"Позиция по FIGI {figi} не найдена")
                return False

            if quantity is None:
                quantity = position.quantity
            elif quantity > position.quantity:
                warning(f"Невозможно закрыть {quantity} шт, доступно только {position.quantity} шт")
                return False

            current_price = _get_tbank().get_current_price(figi)
            if not current_price:
                error(f"Не удалось получить цену для {figi}")
                return False

            if position.side == OrderSide.SHORT:
                order_success = _get_tbank().buy(figi, quantity)
            else:
                order_success = _get_tbank().sell(figi, quantity)

            if order_success:
                if quantity >= position.quantity:
                    self.remove_position(figi)
                    success(f"✅ Позиция {figi} полностью закрыта")
                else:
                    position.quantity -= quantity
                    success(f"✅ Позиция {figi} частично закрыта: осталось {position.quantity} шт")
                return True
            else:
                error(f"❌ Не удалось закрыть позицию {figi}")
                return False

        except Exception as e:
            error(f"Ошибка при закрытии позиции {figi}: {e}")
            return False

    def set_stop_loss(self, figi: str, side: str, stop_price: float) -> bool:
        """
        Установка стоп-лосса через API брокера

        Args:
            figi: FIGI инструмента
            side: "LONG" или "SHORT"
            stop_price: Цена активации стоп-лосса
        """
        try:
            from trading_bot.api.tbank_client import tbank

            position = self.get_position(figi)
            if not position:
                warning(f"Позиция {figi} не найдена для установки стоп-лосса")
                return False

            quantity = position.quantity

            # Проверяем доступность рынка
            is_available, reason = tbank.is_market_available(figi)
            if not is_available:
                warning(f"Рынок недоступен для установки стоп-лосса {figi}: {reason}")
                return False

            success = tbank.place_stop_loss_order(figi, quantity, stop_price, side)

            if success:
                # Сохраняем информацию о стоп-приказе в позиции
                position.stop_order_placed = True
                position.stop_order_price = stop_price
                info(f"✅ Стоп-лосс установлен для {figi} на {stop_price:.2f}₽")
            else:
                warning(f"⚠️ Не удалось установить стоп-лосс для {figi}")

            return success

        except Exception as e:
            error(f"Ошибка установки стоп-лосса для {figi}: {e}")
            return False

    def set_take_profit(self, figi: str, side: str, take_profit_price: float) -> bool:
        """
        Установка тейк-профита через API брокера

        Args:
            figi: FIGI инструмента
            side: "LONG" или "SHORT"
            take_profit_price: Цена фиксации прибыли
        """
        try:
            from trading_bot.api.tbank_client import tbank

            position = self.get_position(figi)
            if not position:
                warning(f"Позиция {figi} не найдена для установки тейк-профита")
                return False

            quantity = position.quantity

            # Проверяем доступность рынка
            is_available, reason = tbank.is_market_available(figi)
            if not is_available:
                warning(f"Рынок недоступен для установки тейк-профита {figi}: {reason}")
                return False

            success = tbank.place_take_profit_order(figi, quantity, take_profit_price, side)

            if success:
                position.take_profit_order_id = "placed"
                position.take_profit_price = take_profit_price
                info(f"✅ Тейк-профит установлен для {figi} на {take_profit_price:.2f}₽")
            else:
                warning(f"⚠️ Не удалось установить тейк-профит для {figi}")

            return success

        except Exception as e:
            error(f"Ошибка установки тейк-профита для {figi}: {e}")
            return False

    def is_trading_allowed(self, figi: str) -> Tuple[bool, str]:
        """
        Проверка, можно ли торговать инструментом прямо сейчас

        Returns:
            (can_trade, reason)
        """
        try:
            from trading_bot.api.tbank_client import tbank
            return tbank.is_market_available(figi)
        except Exception as e:
            debug(f"Ошибка проверки доступности торгов для {figi}: {e}")
            return True, "Ошибка проверки"

    def is_margin_trading_allowed(self) -> Tuple[bool, str]:
        """
        Проверка, включена ли маржинальная торговля

        Returns:
            (is_allowed, reason)
        """
        try:
            from trading_bot.api.tbank_client import tbank
            return tbank.check_margin_trading_allowed()
        except Exception as e:
            error(f"Ошибка проверки маржинальной торговли: {e}")
            return False, f"Ошибка: {e}"

    def add_temp_skip_adaptive(self, figi: str, error_code: str = "", minutes: int = 10):
        """
        Добавление временной блокировки с анализом ошибки
        НЕ блокирует при ошибках: 30079 (рынок закрыт), 30049 (торги приостановлены)
        """
        # Ошибки, при которых НЕ БЛОКИРУЕМ
        NO_BLOCK_CODES = ["30079", "30049", "30014"]

        for code in NO_BLOCK_CODES:
            if code in error_code:
                warning(f"⏸️ {figi}: {error_code}, повторная попытка в следующем цикле")
                return

        # Остальные ошибки - блокируем
        self.add_temp_skip(figi, minutes)
        warning(f"🔒 {figi} заблокирован на {minutes} мин (ошибка: {error_code})")


# Глобальный экземпляр
position_manager = PositionManager()