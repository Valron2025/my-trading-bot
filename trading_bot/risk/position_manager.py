# trading_bot/risk/position_manager.py
"""Модуль управления позициями - с ATR-based SL для реальной торговли"""

import time
import asyncio
import uuid
import random
from typing import Dict, Optional, List, Any, Tuple
from datetime import datetime, timedelta, timezone

from ..config import config
from ..models import Position, OrderSide
from ..logger import info, success, error, warning, debug

MOSCOW_TZ = timezone(timedelta(hours=3))


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import telegram
    return telegram

# ========== ATR-BASED SL КАЛЬКУЛЯТОР ==========

class ATRStopLossCalculator:
    """
    Калькулятор динамического стоп-лосса на основе ATR
    Используется в РЕАЛЬНОЙ торговле
    """

    @staticmethod
    def calculate_atr(
            figi: str,
            period: int = 14,
            days: int = 5
    ) -> Tuple[float, float]:
        """
        Расчёт ATR для инструмента

        Args:
            figi: FIGI инструмента
            period: Период ATR (14)
            days: Количество дней данных

        Returns:
            Tuple[float, float]: (atr, atr_pct)
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.analysis.advanced_indicators import advanced_indicators

        try:
            candles = tbank.get_candles(figi, days=days, interval_minutes=15)

            if not candles or len(candles) < period + 1:
                debug(f"⚠️ Недостаточно свечей для расчёта ATR: {len(candles) if candles else 0}")
                return 0.0, 1.0

            # Извлекаем данные из свечей
            highs = []
            lows = []
            closes = []

            for c in candles[-period * 3:]:  # Берём достаточно свечей
                if isinstance(c, (list, tuple)) and len(c) >= 2:
                    closes.append(c[0])
                    highs.append(c[0] * 1.005)  # Аппроксимация
                    lows.append(c[0] * 0.995)
                elif hasattr(c, 'close'):
                    closes.append(c.close)
                    highs.append(getattr(c, 'high', c.close))
                    lows.append(getattr(c, 'low', c.close))
                elif isinstance(c, dict):
                    closes.append(c.get('close', 0))
                    highs.append(c.get('high', closes[-1]))
                    lows.append(c.get('low', closes[-1]))
                else:
                    continue

            if len(closes) < period + 1:
                return 0.0, 1.0

            # Расчёт ATR
            atr_array = advanced_indicators._calculate_atr(highs, lows, closes, period=period)
            atr = atr_array[-1] if len(atr_array) > 0 else 0
            current_price = closes[-1] if closes else 0

            if current_price > 0:
                atr_pct = (atr / current_price) * 100
            else:
                atr_pct = 1.0

            return atr, atr_pct

        except Exception as e:
            debug(f"Ошибка расчёта ATR: {e}")
            return 0.0, 1.0

    @staticmethod
    def get_dynamic_sl_pct(
            figi: str,
            side: str,
            atr_pct: float = None,
            use_cache: bool = True
    ) -> float:
        """
        Получение динамического стоп-лосса в процентах

        Args:
            figi: FIGI инструмента
            side: "LONG" или "SHORT"
            atr_pct: ATR в процентах (если уже рассчитан)
            use_cache: Использовать кэш

        Returns:
            float: Рекомендуемый стоп-лосс в процентах
        """
        from trading_bot.cache import TTLCache

        # Кэш для ATR (TTL 1 час)
        if use_cache:
            cache = TTLCache(default_ttl=3600, max_size=500, name="atr_cache")
            cache_key = f"atr_{figi}"
            cached_atr = cache.get(cache_key)
            if cached_atr and not atr_pct:
                atr_pct = cached_atr

        # Если ATR не передан, рассчитываем
        if atr_pct is None or atr_pct == 0:
            _, atr_pct = ATRStopLossCalculator.calculate_atr(figi)
            if use_cache and atr_pct > 0:
                cache.set(cache_key, atr_pct, ttl=3600)

        # Базовый SL на основе ATR
        if atr_pct > 0:
            if side == "SHORT":
                # Для SHORT: больший запас из-за "выносов" вверх
                dynamic_sl = max(0.6, min(2.0, atr_pct * 1.8))
            else:
                # Для LONG: стандартный
                dynamic_sl = max(0.4, min(1.5, atr_pct * 1.3))
        else:
            # Fallback на настройки из конфига
            dynamic_sl = config.stop_loss_pct

        # Логируем
        if atr_pct > 0:
            info(f"   📊 ATR-based SL: ATR={atr_pct:.2f}% → SL={dynamic_sl:.2f}% ({side})")

        return dynamic_sl

    @staticmethod
    def get_dynamic_tp_pct(figi: str, atr_pct: float = None) -> float:
        """
        Получение динамического тейк-профита на основе ATR

        Args:
            figi: FIGI инструмента
            atr_pct: ATR в процентах (если уже рассчитан)

        Returns:
            float: Рекомендуемый тейк-профит в процентах
        """
        if atr_pct is None or atr_pct == 0:
            _, atr_pct = ATRStopLossCalculator.calculate_atr(figi)

        if atr_pct > 0:
            # TP = 2.5 × ATR (но не менее 0.8% и не более 3%)
            dynamic_tp = max(0.8, min(3.0, atr_pct * 2.5))
        else:
            dynamic_tp = config.take_profit_pct

        if atr_pct > 0:
            info(f"   📊 ATR-based TP: ATR={atr_pct:.2f}% → TP={dynamic_tp:.2f}%")

        return dynamic_tp



# ========== ICEBERG ORDER MANAGER ==========

class IcebergOrderManager:
    """Управление айсберг-заявками (видимая часть меньше реального объёма)"""

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
        try:
            from trading_bot.api.tbank_client import tbank

            if visible_quantity is None:
                visible_quantity = max(1, total_quantity // 10)

            if price is None or price <= 0:
                price = tbank.get_current_price(figi)
                if not price:
                    return {"success": False, "error": "Не удалось получить цену"}

            min_lot = await self._get_min_lot(figi)

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

            first_quantity = min(visible_quantity, total_quantity)
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
                asyncio.create_task(self._refill_iceberg(iceberg_id))
                return {"success": True, "order_id": iceberg_id, "type": "ICEBERG"}

            return {"success": False, "error": "Не удалось разместить первую часть"}

        except Exception as e:
            error(f"❌ Ошибка айсберг-заявки {ticker}: {e}")
            return {"success": False, "error": str(e)}

    async def _refill_iceberg(self, iceberg_id: str):
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
        base_delay = random.uniform(3, 10)

        while iceberg.get("active", True) and iceberg.get("remaining", 0) > 0:
            try:
                remaining = iceberg.get("remaining", 0)
                if remaining <= 0:
                    break

                await asyncio.sleep(base_delay + random.uniform(-1, 2))

                next_qty = random.randint(
                    max(1, int(visible_quantity * 0.5)),
                    min(visible_quantity, remaining)
                )

                if min_lot > 1:
                    next_qty = ((next_qty + min_lot - 1) // min_lot) * min_lot
                next_qty = min(next_qty, remaining)
                if next_qty < min_lot:
                    next_qty = min_lot

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
        return {
            "active_icebergs": len(self._iceberg_orders),
            "icebergs": [{"ticker": o["ticker"], "total": o["total_quantity"],
                          "remaining": o["remaining"], "parts": o["parts_placed"]}
                         for o in self._iceberg_orders.values() if o.get("active")]
        }

    def cancel_iceberg(self, iceberg_id: str) -> bool:
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
            asyncio.create_task(self._monitor_trailing_stop(trailing_id))

            return {"success": True, "trailing_id": trailing_id, "ticker": ticker}

        except Exception as e:
            error(f"❌ Ошибка установки трейлинг-стопа {ticker}: {e}")
            return {"success": False, "error": str(e)}

    async def _monitor_trailing_stop(self, trailing_id: str):
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
                    if current_price > ts["highest_price"]:
                        ts["highest_price"] = current_price
                        new_stop = current_price * (1 - trailing_percent / 100)
                        ts["current_stop"] = new_stop
                        debug(f"📈 {ticker}: цена {current_price:.2f}, новый стоп {new_stop:.2f}")

                    if current_price <= ts["current_stop"]:
                        info(f"🔔 Трейлинг-стоп сработал для {ticker} при цене {current_price:.2f}")
                        success_flag = tbank.sell(figi, quantity)
                        if success_flag:
                            highest = ts["highest_price"]
                            drop_percent = (1 - current_price / highest) * 100 if highest > 0 else 0
                            info(f"✅ Позиция {ticker} закрыта по трейлинг-стопу")
                            info(f"   Максимум: {highest:.2f}, Закрытие: {current_price:.2f}, Падение: {drop_percent:.1f}%")
                            ts["active"] = False
                            break
                        else:
                            error(f"❌ Не удалось закрыть {ticker} по трейлинг-стопу")

                await asyncio.sleep(5)

            except Exception as e:
                error(f"❌ Ошибка мониторинга {ticker}: {e}")
                await asyncio.sleep(30)

        if trailing_id in self._trailing_stops:
            del self._trailing_stops[trailing_id]

    def get_trailing_status(self) -> Dict[str, Any]:
        return {
            "active_stops": len(self._trailing_stops),
            "stops": [{"ticker": ts["ticker"], "stop_price": round(ts["current_stop"], 2),
                       "highest": round(ts["highest_price"], 2), "trailing": ts["trailing_percent"]}
                      for ts in self._trailing_stops.values()]
        }

    def cancel_trailing_stop(self, trailing_id: str) -> bool:
        if trailing_id in self._trailing_stops:
            self._trailing_stops[trailing_id]["active"] = False
            del self._trailing_stops[trailing_id]
            return True
        return False


# ========== ОСНОВНОЙ КЛАСС POSITION MANAGER ==========
class PositionManager:
    """Управление открытыми позициями - СИНГЛТОН с ATR-based SL"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if PositionManager._initialized:
            return
        PositionManager._initialized = True

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

        self.db = None

        self.iceberg_manager = None
        self.trailing_manager = None

        self._close_attempts: Dict[str, int] = {}
        self._last_close_attempt: Dict[str, datetime] = {}

        info("✅ PositionManager (СИНГЛТОН) инициализирован с ATR-based SL")

    def set_database(self, db):
        """Установка менеджера базы данных"""
        self.db = db
        info("✅ PositionManager подключён к DatabaseManager")

    def init_advanced_managers(self, bot):
        self.iceberg_manager = IcebergOrderManager(bot)
        self.trailing_manager = TrailingStopManager(bot)
        info("✅ Iceberg и Trailing Stop менеджеры инициализированы")

    # ========== ATR-BASED SL ДЛЯ ПОЗИЦИЙ ==========

    def _get_atr_based_sltp(self, figi: str, side: str, current_price: float) -> Tuple[float, float, float]:
        """
        Получение ATR-based SL и TP для позиции

        Returns:
            Tuple[float, float, float]: (sl_pct, tp_pct, atr_pct)
        """
        # Рассчитываем ATR
        _, atr_pct = ATRStopLossCalculator.calculate_atr(figi)

        if atr_pct > 0:
            sl_pct = ATRStopLossCalculator.get_dynamic_sl_pct(figi, side, atr_pct)
            tp_pct = ATRStopLossCalculator.get_dynamic_tp_pct(figi, atr_pct)
        else:
            sl_pct = config.stop_loss_pct
            tp_pct = config.take_profit_pct

        return sl_pct, tp_pct, atr_pct



    def _set_protective_orders(self, position: Position, stop_price: float, take_profit_price: float):
        """Установка защитных ордеров с ATR-based уровнями"""
        from trading_bot.api.tbank_client import tbank

        figi = position.figi
        ticker = position.ticker
        quantity = position.quantity

        info(f"\n   🛡️ УСТАНОВКА ЗАЩИТНЫХ ОРДЕРОВ:")

        # Округляем цены
        stop_price_rounded = tbank._round_to_min_increment(figi, stop_price)
        tp_price_rounded = tbank._round_to_min_increment(figi, take_profit_price)

        # Стоп-лосс
        try:
            if position.stop_loss_pct > 0:
                if position.side == OrderSide.LONG:
                    stop_side = "LONG"
                else:
                    stop_side = "SHORT"

                info(f"      📊 СТОП-ЛОСС: {stop_side} {quantity} шт {ticker} при {stop_price_rounded:.2f}₽")

                if tbank.place_stop_loss_order(
                        figi=position.figi,
                        quantity=quantity,
                        stop_price=stop_price_rounded,
                        side=stop_side
                ):
                    info(f"      ✅ Стоп-лосс установлен на {stop_price_rounded:.2f}₽")
                else:
                    warning(f"      ⚠️ Не удалось установить стоп-лосс для {ticker}")
        except Exception as e:
            warning(f"      ⚠️ Ошибка установки стоп-лосса: {e}")

        # Тейк-профит
        try:
            if position.take_profit_pct > 0:
                if position.side == OrderSide.LONG:
                    tp_side = "LONG"
                else:
                    tp_side = "SHORT"

                info(f"      📊 ТЕЙК-ПРОФИТ: {tp_side} {quantity} шт {ticker} при {tp_price_rounded:.2f}₽")

                if hasattr(tbank, 'place_take_profit_order'):
                    success = tbank.place_take_profit_order(figi, quantity, tp_price_rounded, tp_side)
                else:
                    success = tbank.place_limit_order(figi, quantity, tp_side, tp_price_rounded)

                if success:
                    info(f"      ✅ Тейк-профит установлен на {tp_price_rounded:.2f}₽")
                else:
                    warning(f"      ⚠️ Не удалось установить тейк-профит для {ticker}")
        except Exception as e:
            warning(f"      ⚠️ Ошибка установки тейк-профита: {e}")

    def _get_trading_bot(self):
        from trading_bot import get_trading_bot
        return get_trading_bot()

    def _round_price_for_order(self, price: float, figi: str = None) -> float:
        if price <= 0:
            return 0.01
        rounded = round(price, 2)
        if rounded < 0.01:
            rounded = 0.01
        return rounded

    def set_trading_bot(self, trading_bot):
        self._trading_bot = trading_bot
        success("✅ PositionManager связан с TradingBot")

    def is_temp_skipped(self, figi: str) -> bool:
        now = datetime.now(MOSCOW_TZ)
        expired = [f for f, until in self._temp_skip_until.items() if now >= until]
        for f in expired:
            del self._temp_skip_until[f]
        return figi in self._temp_skip_until

    def is_temp_blacklisted(self, figi: str) -> bool:
        if figi in self._temp_blacklist:
            if datetime.now(MOSCOW_TZ) < self._temp_blacklist[figi]:
                return True
            del self._temp_blacklist[figi]
        return False

    def add_temp_skip(self, figi: str, minutes: int = 10):
        self._temp_skip_until[figi] = datetime.now(MOSCOW_TZ) + timedelta(minutes=minutes)
        info(f"🔒 Добавлен {figi} в временную блокировку на {minutes} минут")

    def add_to_blacklist(self, figi: str, minutes: int = 60):
        self._temp_blacklist[figi] = datetime.now(MOSCOW_TZ) + timedelta(minutes=minutes)
        info(f"⛔ {figi} добавлен в чёрный список на {minutes} минут")

    # ========== ОСНОВНЫЕ МЕТОДЫ УПРАВЛЕНИЯ ПОЗИЦИЯМИ ==========

    def add_position(self, figi: str, quantity: int, price: float, side: OrderSide,
                     take_profit_pct: float = None, stop_loss_pct: float = None,
                     trailing_stop_pct: float = None, auto_set_stop: bool = True,
                     ticker: str = None) -> Optional[Position]:
        """
        Добавление позиции с ATR-based SL/TP
        """
        from trading_bot.api.tbank_client import tbank

        # ✅ ЕСЛИ TP/SL НЕ ПЕРЕДАНЫ - РАССЧИТЫВАЕМ ДИНАМИЧЕСКИ
        if take_profit_pct is None or stop_loss_pct is None:
            sl_pct, tp_pct, atr_pct = self._get_atr_based_sltp(figi, side.value, price)
            if take_profit_pct is None:
                take_profit_pct = tp_pct
            if stop_loss_pct is None:
                stop_loss_pct = sl_pct

        # ✅ ИСПРАВЛЕНО: используем прямой вызов tbank
        if ticker is None:
            ticker = tbank._get_ticker_by_figi(figi) or figi[:8]

        info(f"\n{'═' * 60}")
        info(f"📊 ДОБАВЛЕНИЕ ПОЗИЦИИ {ticker}")
        info(f"{'═' * 60}")

        # Проверка OTC
        if tbank.is_confirmation_required(figi):
            warning(f"⚠️ {ticker} требует подтверждения сделок! Позиция НЕ будет открыта.")
            return None

        # Проверка доступности рынка
        is_available, reason = tbank.is_market_available(figi)
        if not is_available:
            warning(f"⚠️ Рынок недоступен для {ticker}: {reason}")
            return None

        # ========== ATR-BASED SL/TP ==========
        if stop_loss_pct is None or take_profit_pct is None:
            sl_pct, tp_pct, atr_pct = self._get_atr_based_sltp(figi, side.value, price)
            stop_loss_pct = stop_loss_pct or sl_pct
            take_profit_pct = take_profit_pct or tp_pct
            info(f"   📊 ATR={atr_pct:.2f}% → SL={stop_loss_pct:.2f}%, TP={take_profit_pct:.2f}%")
        else:
            atr_pct = 0

        trailing_stop_pct = trailing_stop_pct if trailing_stop_pct is not None else config.trailing_stop_pct

        if side == OrderSide.LONG:
            stop_price = price * (1 - stop_loss_pct / 100)
            take_profit_price = price * (1 + take_profit_pct / 100)
            side_text = "LONG"
        else:
            stop_price = price * (1 + stop_loss_pct / 100)
            take_profit_price = price * (1 - take_profit_pct / 100)
            side_text = "SHORT"

        info(f"   📊 ПАРАМЕТРЫ ПОЗИЦИИ:")
        info(f"      📈 Сторона: {side_text}")
        info(f"      🔢 Количество: {quantity} шт")
        info(f"      💰 Цена входа: {price:.2f}₽")
        info(f"      🎯 Тейк-профит: +{take_profit_pct}% ({take_profit_price:.2f}₽)")
        info(f"      🛑 Стоп-лосс: -{stop_loss_pct}% ({stop_price:.2f}₽)")
        if atr_pct > 0:
            info(f"      📊 ATR: {atr_pct:.2f}%")

        # Создаём позицию
        position = Position(
            figi=figi,
            ticker=ticker,
            quantity=quantity,
            avg_price=price,
            side=side,
            entry_time=datetime.now(MOSCOW_TZ)
        )

        position.take_profit_pct = take_profit_pct
        position.stop_loss_pct = stop_loss_pct
        position.trailing_stop_pct = trailing_stop_pct
        position.atr_entry = atr_pct

        if side == OrderSide.LONG:
            position.highest_price = price
        else:
            position.lowest_price = price

        self._positions[figi] = position
        info(f"   ✅ Позиция сохранена в менеджере")

        # Устанавливаем защитные ордера
        if auto_set_stop:
            self._set_protective_orders(position, stop_price, take_profit_price)

        from trading_bot.logger import success as log_success
        log_success(f"\n{'🎉' * 40}")
        log_success(f"✅ ПОЗИЦИЯ {ticker} УСПЕШНО ОТКРЫТА!")
        log_success(f"   📊 Сторона: {side_text}")
        log_success(f"   🔢 Количество: {quantity} шт")
        log_success(f"   💰 Цена входа: {price:.2f}₽")
        log_success(f"   🎯 Тейк-профит: {take_profit_price:.2f}₽ (+{take_profit_pct}%)")
        log_success(f"   🛑 Стоп-лосс: {stop_price:.2f}₽ (-{stop_loss_pct}%)")
        if atr_pct > 0:
            log_success(f"   📊 ATR при входе: {atr_pct:.2f}%")
        log_success(f"{'🎉' * 40}")

        # Telegram уведомление
        telegram = _get_telegram()
        if telegram:
            try:
                telegram.send_trade_opened(
                    side=side.value,
                    ticker=ticker,
                    quantity=quantity,
                    price=price
                )
            except Exception as e:
                debug(f"   ⚠️ Ошибка отправки уведомления: {e}")

        return position

    def _place_stop_order_with_retry(self, figi: str, quantity: int, stop_price: float, side: str,
                                     retries: int = 2) -> bool:
        """Размещение стоп-ордера с повторными попытками"""
        from trading_bot.api.tbank_client import tbank

        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    info(f"   🔄 Повторная попытка {attempt} установки стоп-лосса...")
                    time.sleep(1)

                success = tbank.place_stop_loss_order(figi, quantity, stop_price, side)
                if success:
                    return True

            except Exception as e:
                error_msg = str(e)
                if "30240" in error_msg:
                    return False
                if attempt == retries:
                    raise

        return False

    def _place_take_profit_order_with_retry(self, figi: str, quantity: int, take_profit_price: float, side: str,
                                            retries: int = 2) -> bool:
        """Размещение тейк-профита с повторными попытками"""
        from trading_bot.api.tbank_client import tbank

        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    info(f"   🔄 Повторная попытка {attempt} установки тейк-профита...")
                    time.sleep(1)

                success = tbank.place_take_profit_order(figi, quantity, take_profit_price, side)
                if success:
                    return True

            except Exception as e:
                error_msg = str(e)
                if "30240" in error_msg:
                    return False
                if attempt == retries:
                    raise

        return False

    def _cancel_stop_orders_by_figi(self, figi: str):
        """Отмена всех стоп-ордеров по FIGI"""
        try:
            stop_orders = _get_tbank().get_stop_orders()
            if not stop_orders:
                return

            cancelled = 0
            for order in stop_orders:
                if order.get('figi') == figi:
                    try:
                        _get_tbank().cancel_stop_order(order['stop_order_id'])
                        cancelled += 1
                    except Exception:
                        pass
            if cancelled > 0:
                info(f"   🔄 Отменено {cancelled} стоп-приказов для {figi}")
        except Exception as e:
            debug(f"Ошибка отмены стоп-ордеров для {figi}: {e}")

    def remove_position(self, figi: str) -> Optional[Position]:
        """
        Удаление позиции из менеджера
        ВНИМАНИЕ: НЕ закрывает позицию у брокера!
        """
        if figi in self._positions:
            pos = self._positions.pop(figi)
            info(f"🗑️ Удалена позиция {pos.ticker or figi[:8]} из менеджера")
            return pos
        return None

    def get_position(self, figi: str) -> Optional[Position]:
        return self._positions.get(figi)

    def get_all_positions(self) -> Dict[str, Position]:
        return self._positions.copy()

    def sync_with_broker(self):
        """Синхронизация с брокером с учётом статуса блокировки"""
        try:
            from trading_bot.api.tbank_client import tbank  # ✅ ДОБАВИТЬ

            broker_positions = _get_tbank().get_positions()
            broker_figis = {p['figi'] for p in broker_positions}

            # Удаляем позиции, которых нет у брокера
            for figi in list(self._positions.keys()):
                if figi not in broker_figis:
                    info(f"Позиция {figi} закрыта у брокера, удаляем из менеджера")
                    del self._positions[figi]

            for pos in broker_positions:
                figi = pos['figi']
                quantity_raw = pos['quantity']
                side = OrderSide.SHORT if quantity_raw < 0 else OrderSide.LONG
                quantity = abs(quantity_raw)
                is_blocked = pos.get('blocked', False)

                if quantity == 0:
                    if figi in self._positions:
                        del self._positions[figi]
                    continue

                current_price = _get_tbank().get_current_price(figi)
                if not current_price:
                    continue

                # ✅ ИСПРАВЛЕНО: используем прямой вызов tbank
                ticker = tbank._get_ticker_by_figi(figi) or figi[:12]

                if figi in self._positions:
                    existing = self._positions[figi]
                    if existing.side != side:
                        warning(f"🔄 Сторона позиции {figi} изменилась, удаляем старую")
                        del self._positions[figi]
                    else:
                        existing.quantity = quantity
                        existing.avg_price = pos['avg_price']
                        existing.blocked = is_blocked
                        if side == OrderSide.LONG and current_price > existing.highest_price:
                            existing.highest_price = current_price
                        elif side == OrderSide.SHORT and current_price < existing.lowest_price:
                            existing.lowest_price = current_price
                        continue

                position = Position(
                    figi=figi,
                    ticker=ticker,
                    quantity=quantity,
                    avg_price=pos['avg_price'],
                    side=side,
                    entry_time=datetime.now(MOSCOW_TZ)
                )
                position.blocked = is_blocked

                if side == OrderSide.LONG:
                    position.highest_price = current_price
                else:
                    position.lowest_price = current_price
                self._positions[figi] = position

                if is_blocked:
                    warning(f"🔒 Позиция {ticker} ЗАБЛОКИРОВАНА! Нельзя закрыть через API")

        except Exception as e:
            error(f"Ошибка синхронизации с брокером: {e}")

    def sync_and_cleanup(self) -> int:
        """
        ПОЛНАЯ СИНХРОНИЗАЦИЯ И ОЧИСТКА МЁРТВЫХ ПОЗИЦИЙ

        Удаляет из менеджера все позиции, которых нет у брокера.
        Это решает проблему "застрявших" позиций.

        Returns:
            int: Количество удалённых позиций
        """
        from trading_bot.api.tbank_client import tbank

        try:
            # Получаем реальные позиции от брокера
            real_positions = tbank.get_positions()
            real_figis = {p['figi'] for p in real_positions if abs(p.get('quantity', 0)) > 0}

            # Удаляем из менеджера всё, чего нет у брокера
            removed = 0
            for figi in list(self._positions.keys()):
                if figi not in real_figis:
                    ticker = tbank._get_ticker_by_figi(figi)
                    info(f"🧹 Синхронизация: удалена мёртвая позиция {ticker} ({figi})")
                    del self._positions[figi]
                    removed += 1

            # Также очищаем чёрный список от старых записей
            now = datetime.now(MOSCOW_TZ)

            # Очищаем временные блокировки
            expired_skips = [f for f, until in self._temp_skip_until.items() if now >= until]
            for figi in expired_skips:
                del self._temp_skip_until[figi]

            # Очищаем чёрный список
            expired_black = [f for f, until in self._temp_blacklist.items() if now >= until]
            for figi in expired_black:
                del self._temp_blacklist[figi]

            if removed > 0:
                info(f"🧹 Синхронизация завершена: удалено {removed} мёртвых позиций")
            else:
                debug(f"🧹 Синхронизация: всё чисто, позиций в менеджере: {len(self._positions)}")

            return removed

        except Exception as e:
            error(f"Ошибка синхронизации: {e}")
            return 0

    def _get_positions_with_pnl(self) -> list:
        positions_data = []
        for figi, position in self._positions.items():
            current_price = _get_tbank().get_current_price(figi)
            if current_price:
                positions_data.append({
                    'figi': figi,
                    'position': position,
                    'profit_pct': position.current_profit_pct(current_price),
                    'profit_amount': position.current_profit_amount(current_price),
                    'quantity': position.quantity,
                    'side': position.side
                })
        return positions_data

    # ========== УПРАВЛЕНИЕ ПОЗИЦИЯМИ ==========

    def check_all_positions(self):
        """Проверка всех открытых позиций"""
        if self._trading_bot and hasattr(self._trading_bot, '_shutting_down'):
            if self._trading_bot._shutting_down:
                return

        self.sync_with_broker()

        for figi, position in list(self._positions.items()):
            current_price = _get_tbank().get_current_price(figi)
            if not current_price:
                continue

            if position.side == OrderSide.LONG:
                self.manage_long_position(position, current_price)
            else:
                self.manage_short_position(position, current_price)

    def manage_long_position(self, position: Position, current_price: float) -> bool:
        """
        Управление LONG позицией
        """
        from trading_bot.trading.position_closer import position_closer
        from trading_bot.api.tbank_client import tbank

        if self._trading_bot and hasattr(self._trading_bot, '_shutting_down'):
            if self._trading_bot._shutting_down:
                info(f"🔒 Закрытие {position.ticker} по сигналу остановки бота")
                return self._close_long_position_safe(position)

        if hasattr(position, '_in_manage') and position._in_manage:
            return False
        position._in_manage = True

        try:
            now = datetime.now(MOSCOW_TZ)
            entry_time = position.entry_time
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=MOSCOW_TZ)

            hold_seconds = int((now - entry_time).total_seconds())
            hold_minutes = hold_seconds / 60

            profit_pct = (current_price - position.avg_price) / position.avg_price * 100

            if hold_seconds < 60:
                info(f"   🕐 [{position.ticker}] ЗАЩИТА: {hold_seconds}/60с, {profit_pct:+.2f}% - НЕ ЗАКРЫВАЕМ")
                return False

            position.update_high_low(current_price)

            take_profit_pct = config.take_profit_pct
            stop_loss_pct = config.stop_loss_pct

            # Стоп-лосс
            stop_loss_price = position.avg_price * (1 - stop_loss_pct / 100)
            if current_price <= stop_loss_price:
                info(f"\n{'🛑' * 40}")
                info(f"🛑 СТОП-ЛОСС LONG! {position.ticker} | Убыток: {profit_pct:.2f}%")
                info(f"{'🛑' * 40}")
                return self._close_long_position_safe(position)

            # Тейк-профит
            take_profit_price = position.avg_price * (1 + take_profit_pct / 100)
            if current_price >= take_profit_price:
                info(f"\n{'🎯' * 40}")
                info(f"🎯 ТЕЙК-ПРОФИТ LONG! {position.ticker} | Прибыль: {profit_pct:.2f}%")
                info(f"{'🎯' * 40}")
                return self._close_long_position_safe(position)

            # Трейлинг-стоп
            TRAILING_ACTIVATION_PCT = 2.0
            TRAILING_STOP_PCT = 0.5

            if profit_pct > TRAILING_ACTIVATION_PCT and not hasattr(position, 'trailing_activated'):
                position.trailing_activated = True
                position.highest_price = current_price
                position.trailing_stop = current_price * (1 - TRAILING_STOP_PCT / 100)
                info(f"\n🔻 ТРЕЙЛИНГ-СТОП LONG АКТИВИРОВАН! {position.ticker}")

            if hasattr(position, 'trailing_activated') and position.trailing_activated:
                if current_price > position.highest_price:
                    position.highest_price = current_price
                    position.trailing_stop = current_price * (1 - TRAILING_STOP_PCT / 100)
                    info(f"   📈 [{position.ticker}] Новый максимум: {position.highest_price:.2f}₽")

                if current_price <= position.trailing_stop:
                    info(f"\n🔔 ТРЕЙЛИНГ-СТОП LONG СРАБОТАЛ! {position.ticker}")
                    return self._close_long_position_safe(position)

            return False

        finally:
            if hasattr(position, '_in_manage'):
                delattr(position, '_in_manage')

    def _close_long_position_safe(self, position) -> bool:
        """
        БЕЗОПАСНОЕ ЗАКРЫТИЕ LONG ПОЗИЦИИ
        """
        from trading_bot.api.tbank_client import tbank
        import time

        ticker = position.ticker or position.figi[:8]
        figi = position.figi
        quantity = position.quantity
        current_price = tbank.get_current_price(figi)

        if not current_price:
            error(f"❌ Не удалось получить цену для {ticker}")
            return False

        print(f"\n🔒 БЕЗОПАСНОЕ ЗАКРЫТИЕ LONG: {ticker}")
        print(f"   Количество: {quantity} шт")
        print(f"   Текущая цена: {current_price:.2f}₽")

        # Проверяем, что позиция существует
        broker_positions = tbank.get_positions(force_refresh=True)
        position_exists = False
        for pos in broker_positions:
            if pos.get('figi') == figi and abs(pos.get('quantity', 0)) > 0:
                position_exists = True
                break

        if not position_exists:
            info(f"   ℹ️ Позиция {ticker} уже закрыта")
            self.remove_position(figi)
            return True

        # Отправляем заявку на продажу
        try:
            success_flag = tbank.sell(figi, quantity, use_market=True)
            if not success_flag:
                error(f"   ❌ ЗАЯВКА НЕ ОТПРАВЛЕНА!")
                return False
        except Exception as e:
            error(f"   ❌ ОШИБКА: {e}")
            return False

        # Ждём исполнения
        for attempt in range(10):
            time.sleep(1)
            broker_positions = tbank.get_positions(force_refresh=True)
            position_still_exists = False
            for pos in broker_positions:
                if pos.get('figi') == figi and abs(pos.get('quantity', 0)) > 0:
                    position_still_exists = True
                    break

            if not position_still_exists:
                success(f"\n✅ ПОЗИЦИЯ {ticker} УСПЕШНО ЗАКРЫТА!")
                self.remove_position(figi)
                return True

        warning(f"\n⚠️ ПОЗИЦИЯ {ticker} ВСЁ ЕЩЁ СУЩЕСТВУЕТ!")
        return False

    def manage_short_position(self, position: Position, current_price: float) -> bool:
        """
        Управление SHORT позицией с улучшенной безопасностью
        SHORT: зарабатываем на падении цены, теряем на росте

        Returns:
            bool: True если позиция закрыта, False если продолжает торговаться
        """
        from trading_bot.trading.position_closer import position_closer
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, success, error, warning, debug
        from datetime import datetime, timedelta, timezone
        import time

        # ========== 1. ЗАЩИТА ОТ ПОВТОРНОГО ВХОДА ==========
        if hasattr(position, '_closing'):
            info(f"   🔒 [{position.ticker}] SHORT: уже в процессе закрытия, пропускаем")
            return False

        if hasattr(position, '_in_manage') and position._in_manage:
            return False

        # ========== 2. ПРОВЕРКА АВАРИЙНОГО ЗАКРЫТИЯ ==========
        if self._trading_bot and hasattr(self._trading_bot, '_shutting_down'):
            if self._trading_bot._shutting_down:
                info(f"\n{'🔒' * 40}")
                info(f"🔒 АВАРИЙНОЕ ЗАКРЫТИЕ SHORT: {position.ticker}")
                info(f"   Причина: остановка бота")
                info(f"{'🔒' * 40}")
                return self._close_short_position_safe(position)

        position._in_manage = True

        try:
            # ========== 3. РАСЧЁТ ВРЕМЕНИ УДЕРЖАНИЯ ==========
            now = datetime.now(MOSCOW_TZ)
            entry_time = position.entry_time
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=MOSCOW_TZ)

            hold_seconds = int((now - entry_time).total_seconds())
            hold_minutes = hold_seconds / 60

            # ========== 4. РАСЧЁТ ПРИБЫЛИ ==========
            # Для SHORT: прибыль когда цена падает
            profit_pct = (position.avg_price - current_price) / position.avg_price * 100
            profit_amount = (position.avg_price - current_price) * position.quantity

            # ========== 5. ЗАЩИТА ОТ МГНОВЕННОГО ЗАКРЫТИЯ (60 секунд) ==========
            if hold_seconds < 60:
                info(f"\n   🕐 ЗАЩИТА SHORT [{position.ticker}]")
                info(f"      Время удержания: {hold_seconds}/60с")
                info(f"      Текущая прибыль: {profit_pct:+.2f}% ({profit_amount:+.2f}₽)")
                info(f"      Цена: {current_price:.2f}₽ (вход: {position.avg_price:.2f}₽)")
                info(f"      ⏸️ ПОЗИЦИЯ НЕ ЗАКРЫТА (защитный период)")
                return False

            # ========== 6. ОБНОВЛЕНИЕ ЭКСТРЕМУМОВ ==========
            position.update_high_low(current_price)

            # ========== 7. ПОЛУЧЕНИЕ ПАРАМЕТРОВ ==========
            take_profit_pct = position.take_profit_pct if position.take_profit_pct > 0 else config.take_profit_pct
            stop_loss_pct = position.stop_loss_pct if position.stop_loss_pct > 0 else config.stop_loss_pct

            # ========== 8. ПРОВЕРКА СТОП-ЛОССА ==========
            stop_loss_price = position.avg_price * (1 + stop_loss_pct / 100)

            if current_price >= stop_loss_price:
                info(f"\n{'🛑' * 60}")
                info(f"🛑 СТОП-ЛОСС SHORT СРАБОТАЛ!")
                info(f"{'🛑' * 60}")
                info(f"   📊 Тикер: {position.ticker}")
                info(f"   📉 Убыток: {profit_pct:.2f}% ({profit_amount:+.2f}₽)")
                info(f"   💰 Цена входа: {position.avg_price:.2f}₽")
                info(f"   💰 Текущая цена: {current_price:.2f}₽")
                info(f"   🛑 Цена стоп-лосса: {stop_loss_price:.2f}₽")
                info(f"   ⏱️ Время удержания: {hold_minutes:.1f} мин")

                # Отменяем стоп-приказы
                self._cancel_stop_orders(position)

                # Пытаемся закрыть позицию
                closed = self._close_short_position_safe(position)

                if closed:
                    # ✅ ТОЛЬКО если успешно закрыли
                    info(f"\n   ✅ ПОЗИЦИЯ {position.ticker} УСПЕШНО ЗАКРЫТА ПО СТОП-ЛОССУ")
                    self.remove_position(position.figi)
                    return True
                else:
                    # ❌ НЕ УДАЛЯЕМ ПОЗИЦИЮ!
                    error(f"\n   ❌ КРИТИЧЕСКАЯ ОШИБКА: ПОЗИЦИЯ {position.ticker} НЕ ЗАКРЫТА!")
                    error(f"   💡 Причина: недостаточно средств или ошибка API")
                    error(f"   💡 Рекомендация: пополните счёт или закройте вручную в приложении Т-Банк")
                    error(f"   ⚠️ ПОЗИЦИЯ ОСТАЁТСЯ В МЕНЕДЖЕРЕ ДЛЯ ПОВТОРНЫХ ПОПЫТОК")

                    # Отправляем Telegram уведомление о критической ошибке
                    try:
                        telegram = _get_telegram()
                        if telegram:
                            telegram.send_error(
                                f"🚨 **КРИТИЧЕСКАЯ ОШИБКА!**\n\n"
                                f"❌ НЕ УДАЛОСЬ ЗАКРЫТЬ SHORT ПО СТОП-ЛОССУ!\n\n"
                                f"📊 {position.ticker}\n"
                                f"💰 Цена входа: {position.avg_price:.2f}₽\n"
                                f"💰 Текущая цена: {current_price:.2f}₽\n"
                                f"🛑 Стоп-лосс: {stop_loss_price:.2f}₽\n"
                                f"📉 Убыток: {profit_pct:.2f}%\n\n"
                                f"⚠️ **ПОЗИЦИЯ НЕ БЫЛА УДАЛЕНА ИЗ МЕНЕДЖЕРА!**\n"
                                f"⚠️ Требуется ручное вмешательство!"
                            )
                    except Exception as e:
                        debug(f"   ⚠️ Ошибка отправки уведомления: {e}")

                    # ⚠️ НЕТ remove_position()!
                    return False

            # ========== 9. ПРОВЕРКА ТЕЙК-ПРОФИТА ==========
            take_profit_price = position.avg_price * (1 - take_profit_pct / 100)

            if current_price <= take_profit_price:
                info(f"\n{'🎯' * 60}")
                info(f"🎯 ТЕЙК-ПРОФИТ SHORT СРАБОТАЛ!")
                info(f"{'🎯' * 60}")
                info(f"   📊 Тикер: {position.ticker}")
                info(f"   📈 Прибыль: {profit_pct:.2f}% ({profit_amount:+.2f}₽)")
                info(f"   💰 Цена входа: {position.avg_price:.2f}₽")
                info(f"   💰 Текущая цена: {current_price:.2f}₽")
                info(f"   🎯 Цена тейк-профита: {take_profit_price:.2f}₽")
                info(f"   ⏱️ Время удержания: {hold_minutes:.1f} мин")
                info(f"   📉 Разница: {position.avg_price - current_price:+.2f}₽")
                info(f"{'🎯' * 60}")

                # Отменяем все стоп-приказы
                info(f"\n   🔄 Отмена стоп-приказов для {position.ticker}...")
                self._cancel_stop_orders(position)

                # Пытаемся закрыть позицию
                info(f"\n   🔒 Попытка закрытия SHORT позиции {position.ticker}...")
                closed = self._close_short_position_safe(position)

                if closed:
                    info(f"\n   ✅ ПОЗИЦИЯ {position.ticker} УСПЕШНО ЗАКРЫТА ПО ТЕЙК-ПРОФИТУ")
                    self.remove_position(position.figi)
                    return True
                else:
                    error(f"\n   ❌ КРИТИЧЕСКАЯ ОШИБКА: ПОЗИЦИЯ {position.ticker} НЕ ЗАКРЫТА!")
                    error(f"   💡 Причина: недостаточно средств или ошибка API")
                    error(f"   ⚠️ ПОЗИЦИЯ ОСТАЁТСЯ В МЕНЕДЖЕРЕ")
                    return False

            # ========== 10. ТРЕЙЛИНГ-СТОП ДЛЯ SHORT ==========
            TRAILING_ACTIVATION_PCT = 2.0  # Активация при прибыли 2%
            TRAILING_STOP_PCT = 0.5  # Отступ от максимума 0.5%

            # Активация трейлинг-стопа
            if profit_pct > TRAILING_ACTIVATION_PCT and not hasattr(position, 'trailing_activated'):
                position.trailing_activated = True
                position.lowest_price = current_price
                position.trailing_stop = current_price * (1 + TRAILING_STOP_PCT / 100)

                info(f"\n{'🔻' * 60}")
                info(f"🔻 ТРЕЙЛИНГ-СТОП SHORT АКТИВИРОВАН!")
                info(f"{'🔻' * 60}")
                info(f"   📊 Тикер: {position.ticker}")
                info(f"   📈 Прибыль: {profit_pct:.2f}% ({profit_amount:+.2f}₽)")
                info(f"   💰 Текущая цена: {current_price:.2f}₽")
                info(f"   📉 Минимум: {position.lowest_price:.2f}₽")
                info(f"   🛡️ Стоп-цена: {position.trailing_stop:.2f}₽ (+{TRAILING_STOP_PCT}%)")
                info(f"{'🔻' * 60}")

            # Обновление трейлинг-стопа
            if hasattr(position, 'trailing_activated') and position.trailing_activated:
                # Обновляем минимум при падении цены
                if current_price < position.lowest_price:
                    old_stop = position.trailing_stop
                    position.lowest_price = current_price
                    position.trailing_stop = current_price * (1 + TRAILING_STOP_PCT / 100)

                    info(f"\n   📉 [{position.ticker}] ОБНОВЛЕНИЕ ТРЕЙЛИНГ-СТОПА:")
                    info(
                        f"      Новый минимум: {position.lowest_price:.2f}₽ (был {position.lowest_price if hasattr(position, 'lowest_price') else current_price:.2f}₽)")
                    info(f"      Новый стоп: {position.trailing_stop:.2f}₽ (был {old_stop:.2f}₽)")
                    info(f"      Прибыль: {profit_pct:.2f}%")

                # Проверка срабатывания трейлинг-стопа
                if current_price >= position.trailing_stop:
                    info(f"\n{'🔔' * 60}")
                    info(f"🔔 ТРЕЙЛИНГ-СТОП SHORT СРАБОТАЛ!")
                    info(f"{'🔔' * 60}")
                    info(f"   📊 Тикер: {position.ticker}")
                    info(f"   📈 Прибыль: {profit_pct:.2f}% ({profit_amount:+.2f}₽)")
                    info(f"   📉 Минимум за время удержания: {position.lowest_price:.2f}₽")
                    info(f"   💰 Цена закрытия: {current_price:.2f}₽")
                    info(f"   ⏱️ Время удержания: {hold_minutes:.1f} мин")
                    info(
                        f"   📊 Откат от минимума: {((current_price - position.lowest_price) / position.lowest_price * 100):.2f}%")
                    info(f"{'🔔' * 60}")

                    # Отменяем стоп-приказы
                    self._cancel_stop_orders(position)

                    # Закрываем позицию
                    closed = self._close_short_position_safe(position)

                    if closed:
                        info(f"\n   ✅ ПОЗИЦИЯ {position.ticker} УСПЕШНО ЗАКРЫТА ПО ТРЕЙЛИНГ-СТОПУ")
                        self.remove_position(position.figi)
                        return True
                    else:
                        error(f"\n   ❌ НЕ УДАЛОСЬ ЗАКРЫТЬ {position.ticker} ПО ТРЕЙЛИНГ-СТОПУ!")
                        return False

            # ========== 11. ТАЙМАУТ (только если в убытке) ==========
            max_hold = config.adaptive_timeout_minutes * 2

            if profit_pct < 0:
                max_hold = max(5, max_hold * 0.5)  # Уменьшаем время для убыточных позиций

                if hold_minutes >= max_hold:
                    info(f"\n{'⏰' * 60}")
                    info(f"⏰ ТАЙМАУТ SHORT ПОЗИЦИИ!")
                    info(f"{'⏰' * 60}")
                    info(f"   📊 Тикер: {position.ticker}")
                    info(f"   📉 Прибыль: {profit_pct:.2f}% ({profit_amount:+.2f}₽)")
                    info(f"   ⏱️ Время удержания: {hold_minutes:.0f} мин > {max_hold} мин (лимит)")
                    info(f"   💰 Текущая цена: {current_price:.2f}₽")
                    info(f"   💡 Причина: превышен таймаут для убыточной позиции")
                    info(f"{'⏰' * 60}")

                    closed = self._close_short_position_safe(position)
                    if closed:
                        info(f"\n   ✅ ПОЗИЦИЯ {position.ticker} ЗАКРЫТА ПО ТАЙМАУТУ")
                        self.remove_position(position.figi)
                        return True
                    else:
                        warning(f"\n   ⚠️ ПОЗИЦИЯ {position.ticker} НЕ ЗАКРЫТА ПО ТАЙМАУТУ! Оставляем в менеджере")
                        return False

            # ========== 12. ЛОГИРОВАНИЕ СОСТОЯНИЯ (каждую минуту) ==========
            if hold_seconds % 60 == 0:
                icon = "🟢" if profit_pct > 0 else "🔴" if profit_pct < 0 else "⚪"
                info(f"\n   {icon} [{position.ticker}] SHORT СТАТУС:")
                info(f"      ⏱️ Время: {hold_minutes:.1f} мин")
                info(f"      📊 P&L: {profit_pct:+.2f}% ({profit_amount:+.2f}₽)")
                info(f"      💰 Цена: {current_price:.2f}₽ (вход: {position.avg_price:.2f}₽)")
                info(f"      🛑 Стоп-лосс: {stop_loss_price:.2f}₽ (+{stop_loss_pct}%)")
                info(f"      🎯 Тейк-профит: {take_profit_price:.2f}₽ (-{take_profit_pct}%)")

                if hasattr(position, 'trailing_activated'):
                    info(
                        f"      🔻 Трейлинг активен: стоп={position.trailing_stop:.2f}₽, минимум={position.lowest_price:.2f}₽")

            return False  # Позиция продолжает торговаться

        except Exception as e:
            error(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА В manage_short_position для {getattr(position, 'ticker', 'unknown')}:")
            error(f"   {type(e).__name__}: {e}")
            import traceback
            error(f"   {traceback.format_exc()}")
            return False

        finally:
            if hasattr(position, '_in_manage'):
                delattr(position, '_in_manage')

    def retry_stuck_short_positions(self):
        """Повторные попытки закрытия зависших SHORT позиций"""
        from trading_bot.api.tbank_client import tbank

        for figi, position in list(self._positions.items()):
            if position.side != OrderSide.SHORT:
                continue

            # Проверяем, есть ли позиция у брокера
            broker_positions = tbank.get_positions(force_refresh=True)
            exists = any(p.get('figi') == figi and abs(p.get('quantity', 0)) > 0
                         for p in broker_positions)

            if not exists:
                # Позиция уже закрыта, просто удаляем из менеджера
                info(f"🧹 Очистка: позиция {position.ticker} уже закрыта у брокера")
                self.remove_position(figi)
                continue

            # Проверяем, не пора ли повторить попытку
            last_attempt = self._last_close_attempt.get(figi)
            if last_attempt:
                minutes_since_attempt = (datetime.now(MOSCOW_TZ) - last_attempt).total_seconds() / 60
                if minutes_since_attempt < 5:  # Не чаще 1 раза в 5 минут
                    continue

            # Повторяем попытку закрытия
            attempts = self._close_attempts.get(figi, 0)
            if attempts < 3:  # Максимум 3 попытки
                info(f"🔄 Повторная попытка {attempts + 1}/3 закрытия SHORT {position.ticker}")
                self._close_attempts[figi] = attempts + 1
                self._last_close_attempt[figi] = datetime.now(MOSCOW_TZ)

                closed = self._close_short_position_safe(position)
                if closed:
                    self.remove_position(figi)

    def _close_short_position_safe(self, position) -> bool:
        """
        БЕЗОПАСНОЕ ЗАКРЫТИЕ SHORT ПОЗИЦИИ
        Возвращает True ТОЛЬКО если позиция реально закрыта у брокера!
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, success, error, warning, debug
        import time
        from datetime import datetime

        if hasattr(position, '_closing'):
            warning(f"   🔒 SHORT {position.ticker}: уже в процессе закрытия, пропускаем")
            return False

        position._closing = True

        try:
            ticker = position.ticker or position.figi[:8]
            figi = position.figi
            quantity = position.quantity

            info(f"\n{'🔒' * 60}")
            info(f"🔒 НАЧАЛО БЕЗОПАСНОГО ЗАКРЫТИЯ SHORT ПОЗИЦИИ")
            info(f"{'🔒' * 60}")
            info(f"   📊 Тикер: {ticker}")
            info(f"   🔢 Количество: {quantity} шт")

            # ========== 1. ПРОВЕРКА OTC ==========
            if tbank.is_confirmation_required(figi):
                warning(f"\n🔐 {ticker} - OTC ИНСТРУМЕНТ! НЕВОЗМОЖНО ЗАКРЫТЬ АВТОМАТИЧЕСКИ!")
                warning(f"   📱 Закройте позицию ВРУЧНУЮ в приложении Т-Банк!")

                # Отправляем Telegram уведомление
                try:
                    telegram = _get_telegram()
                    if telegram:
                        telegram.send_error(
                            f"🚨 **OTC ИНСТРУМЕНТ!**\n\n"
                            f"Инструмент {ticker} требует РУЧНОГО закрытия!\n"
                            f"📊 SHORT {quantity} шт\n\n"
                            f"**Закройте вручную в приложении Т-Банк!**"
                        )
                except Exception:
                    pass

                # ✅ НЕ УДАЛЯЕМ ПОЗИЦИЮ ИЗ МЕНЕДЖЕРА!
                return False

            # ========== 2. ПОЛУЧАЕМ ТЕКУЩУЮ ЦЕНУ ==========
            current_price = tbank.get_current_price(figi)
            if not current_price or current_price <= 0:
                error(f"   ❌ НЕ УДАЛОСЬ ПОЛУЧИТЬ ЦЕНУ для {ticker}!")
                return False

            info(f"   ✅ Текущая цена: {current_price:.4f}₽")

            # ========== 3. ПРОВЕРКА СУЩЕСТВОВАНИЯ ПОЗИЦИИ ==========
            broker_positions = tbank.get_positions(force_refresh=True)
            position_exists = False
            actual_qty = 0

            for pos in broker_positions:
                if pos.get('figi') == figi:
                    actual_qty = abs(pos.get('quantity', 0))
                    if actual_qty > 0:
                        position_exists = True
                    break

            if not position_exists:
                info(f"   ℹ️ Позиция {ticker} уже закрыта у брокера")
                return True

            # ========== 4. ОТПРАВКА ЗАЯВКИ ==========
            info(f"\n📡 ОТПРАВКА ЗАЯВКИ НА ПОКУПКУ {quantity} шт {ticker}...")

            try:
                success_flag = tbank.buy(figi, quantity, use_market=True)
                if not success_flag:
                    warning(f"   ⚠️ Рыночная заявка не удалась, пробуем лимитную...")
                    limit_price = current_price * 1.02
                    success_flag = tbank.place_limit_order(figi, quantity, "BUY", limit_price)

                if not success_flag:
                    error(f"   ❌ НЕ УДАЛОСЬ ЗАКРЫТЬ {ticker}!")
                    return False

            except Exception as e:
                error_msg = str(e)
                if "30240" in error_msg:
                    warning(f"   🔐 {ticker}: ОШИБКА 30240 - ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ!")
                    warning(f"   📱 Закройте позицию ВРУЧНУЮ в приложении Т-Банк!")
                    return False
                else:
                    error(f"   ❌ ОШИБКА: {e}")
                    return False

            # ========== 5. ОЖИДАНИЕ ИСПОЛНЕНИЯ ==========
            for attempt in range(15):
                time.sleep(1)
                broker_positions = tbank.get_positions(force_refresh=True)
                still_exists = False
                for pos in broker_positions:
                    if pos.get('figi') == figi:
                        if abs(pos.get('quantity', 0)) > 0:
                            still_exists = True
                        break

                if not still_exists:
                    success(f"\n✅ ПОЗИЦИЯ {ticker} УСПЕШНО ЗАКРЫТА!")
                    return True

            warning(f"\n⚠️ ЗАЯВКА ОТПРАВЛЕНА, НО ПОЗИЦИЯ {ticker} ВСЁ ЕЩЁ СУЩЕСТВУЕТ!")
            return False

        except Exception as e:
            error(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА В _close_short_position_safe: {e}")
            return False

        finally:
            if hasattr(position, '_closing'):
                delattr(position, '_closing')

    def _get_timeout_for_position(self, position: Position, profit_pct: float) -> int:
        """Рассчитывает таймаут для позиции в зависимости от прибыли и типа"""
        max_hold = config.adaptive_timeout_minutes

        # Для SHORT позиций увеличиваем таймаут
        if position.side == OrderSide.SHORT:
            max_hold = max_hold * 2

        # Если позиция в убытке - уменьшаем таймаут, но НЕ МЕНЬШЕ 5 минут
        if profit_pct < 0:
            max_hold = max(5, max_hold * 0.5)

        # Для OTC инструментов увеличиваем таймаут
        if self._is_otc_instrument(position.figi):
            max_hold = int(max_hold * 2.5)

        return max(5, min(60, max_hold))

    def _is_otc_instrument(self, figi: str) -> bool:
        """Проверка OTC инструмента (внебиржевой)"""
        try:
            from trading_bot.api.tbank_client import tbank
            # Используем правильный метод проверки
            return tbank.is_confirmation_required(figi)
        except Exception:
            return False

    def _send_close_notification(self, position: Position, profit_pct: float, profit_amount: float, ticker: str,
                                 reason: str):
        """
        Отправка уведомления о закрытии позиции в Telegram
        """
        info(f"📱 Отправка уведомления в Telegram...")
        telegram = _get_telegram()
        if telegram:
            try:
                telegram.send_trade_closed(
                    side=position.side.value,
                    reason=reason,
                    profit_pct=profit_pct,
                    profit_amount=profit_amount,
                    ticker=ticker,
                    quantity=position.quantity
                )
                info(f"   ✅ Уведомление отправлено")
            except Exception as e:
                warning(f"   ⚠️ Ошибка отправки уведомления: {e}")
        else:
            debug(f"   ℹ️ Telegram не доступен")

    def _cancel_stop_orders(self, position: Position):
        """
        Отмена всех стоп-приказов по позиции
        Детальное логирование каждого шага
        """
        from trading_bot.api.tbank_client import tbank

        ticker = getattr(position, 'ticker', None)
        if not ticker:
            ticker = self._get_trading_bot()._get_ticker_by_figi(position.figi) or position.figi[:8]

        info(f"   🔄 Отмена стоп-приказов для {ticker}...")

        try:
            stop_orders = tbank.get_stop_orders()
            if not stop_orders:
                debug(f"   ℹ️ Нет активных стоп-приказов для {ticker}")
                return

            cancelled = 0
            failed = 0

            for order in stop_orders:
                if order.get('figi') == position.figi:
                    order_id = order.get('stop_order_id', 'unknown')
                    try:
                        tbank.cancel_stop_order(order_id)
                        cancelled += 1
                        success(f"   ✅ Отменён стоп-приказ {order_id[:8]}... для {ticker}")
                    except Exception as e:
                        error_msg = str(e)
                        if "30240" in error_msg:
                            debug(f"   ℹ️ Стоп-приказ {order_id[:8]}... для {ticker} требует подтверждения")
                        elif "не найден" in error_msg or "not found" in error_msg.lower():
                            debug(f"   ℹ️ Стоп-приказ {order_id[:8]}... для {ticker} уже неактивен")
                        else:
                            failed += 1
                            warning(f"   ⚠️ Ошибка отмены стоп-приказа {order_id[:8]}...: {error_msg[:100]}")

            if cancelled > 0:
                success(f"   ✅ Отменено {cancelled} стоп-приказов для {ticker}")
            if failed > 0:
                warning(f"   ⚠️ Не удалось отменить {failed} стоп-приказов для {ticker}")

        except Exception as e:
            error(f"   ❌ Ошибка получения списка стоп-приказов для {ticker}: {e}")

    # ========== СТОП-ПРИКАЗЫ ==========

    def set_stop_loss(self, figi: str, side: str, stop_price: float) -> bool:
        try:
            from trading_bot.api.tbank_client import tbank
            position = self.get_position(figi)
            if not position:
                warning(f"Позиция {figi} не найдена для установки стоп-лосса")
                return False

            is_available, reason = tbank.is_market_available(figi)
            if not is_available:
                warning(f"Рынок недоступен для установки стоп-лосса {figi}: {reason}")
                return False

            success = tbank.place_stop_loss_order(figi, position.quantity, stop_price, side)
            if success:
                position.stop_order_placed = True
                position.stop_order_price = stop_price
                info(f"✅ Стоп-лосс установлен для {figi} на {stop_price:.2f}₽")
            return success
        except Exception as e:
            error(f"Ошибка установки стоп-лосса для {figi}: {e}")
            return False

    def set_take_profit(self, figi: str, side: str, take_profit_price: float) -> bool:
        try:
            from trading_bot.api.tbank_client import tbank
            position = self.get_position(figi)
            if not position:
                warning(f"Позиция {figi} не найдена для установки тейк-профита")
                return False

            is_available, reason = tbank.is_market_available(figi)
            if not is_available:
                warning(f"Рынок недоступен для установки тейк-профита {figi}: {reason}")
                return False

            success = tbank.place_take_profit_order(figi, position.quantity, take_profit_price, side)
            if success:
                position.take_profit_order_id = "placed"
                position.take_profit_price = take_profit_price
                info(f"✅ Тейк-профит установлен для {figi} на {take_profit_price:.2f}₽")
            return success
        except Exception as e:
            error(f"Ошибка установки тейк-профита для {figi}: {e}")
            return False

    # ========== АВАРИЙНЫЕ МЕТОДЫ ==========

    def get_otc_positions(self) -> List[Dict]:
        """Возвращает список OTC позиций, требующих ручного закрытия"""
        from trading_bot.api.tbank_client import tbank

        otc_positions = []
        for figi, position in self._positions.items():
            try:
                if tbank.is_confirmation_required(figi):
                    otc_positions.append({
                        'ticker': position.ticker,
                        'figi': figi,
                        'quantity': position.quantity,
                        'side': position.side.value,
                        'avg_price': position.avg_price,
                        'current_pnl': position.current_profit_amount(
                            tbank.get_current_price(figi) or position.avg_price
                        )
                    })
            except Exception:
                continue

        if otc_positions:
            warning(f"⚠️ Найдено {len(otc_positions)} OTC позиций, требующих ручного закрытия")
            for pos in otc_positions:
                warning(f"   {pos['ticker']}: {pos['side']} {pos['quantity']} шт")

        return otc_positions

    def _close_worst_positions(self, max_to_close: int = 1) -> int:
        """
        Закрытие самых убыточных позиций

        Args:
            max_to_close: Максимальное количество позиций для закрытия

        Returns:
            int: Количество успешно закрытых позиций
        """
        from trading_bot.logger import info, success, error, warning
        from trading_bot.api.tbank_client import tbank

        try:
            # Получаем все позиции с P&L
            positions = tbank.get_positions()
            if not positions:
                info("   📭 Нет открытых позиций для анализа")
                return 0

            # Рассчитываем P&L для каждой позиции
            positions_with_pnl = []
            for pos in positions:
                figi = pos.get('figi')
                quantity = pos.get('quantity', 0)
                avg_price = pos.get('avg_price', 0)
                ticker = pos.get('ticker', figi[:8])

                if quantity == 0 or avg_price == 0:
                    continue

                # Получаем текущую цену
                current_price = tbank.get_current_price(figi)
                if not current_price:
                    current_price = avg_price

                # Рассчитываем P&L
                if quantity > 0:  # LONG
                    pnl = (current_price - avg_price) * quantity
                    pnl_pct = (current_price - avg_price) / avg_price * 100 if avg_price > 0 else 0
                else:  # SHORT
                    pnl = (avg_price - current_price) * abs(quantity)
                    pnl_pct = (avg_price - current_price) / avg_price * 100 if avg_price > 0 else 0

                positions_with_pnl.append({
                    'figi': figi,
                    'ticker': ticker,
                    'quantity': abs(quantity),
                    'side': 'LONG' if quantity > 0 else 'SHORT',
                    'avg_price': avg_price,
                    'current_price': current_price,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'is_profitable': pnl > 0
                })

            if not positions_with_pnl:
                info("   📭 Нет позиций для закрытия")
                return 0

            # Сортируем по убытку (самые убыточные первые)
            positions_with_pnl.sort(key=lambda x: x['pnl'])

            info(f"\n   📊 АНАЛИЗ ПОЗИЦИЙ ДЛЯ ЗАКРЫТИЯ:")
            for p in positions_with_pnl[:max_to_close + 2]:
                status = "🔴 УБЫТОК" if p['pnl'] < 0 else "🟢 ПРИБЫЛЬ"
                info(f"      {p['ticker']} ({p['side']}): {status} {p['pnl']:+.2f}₽ ({p['pnl_pct']:+.2f}%)")

            # Закрываем самые убыточные
            closed = 0
            to_close = positions_with_pnl[:max_to_close]

            for pos in to_close:
                # Пропускаем прибыльные позиции при экстренном закрытии
                if pos['pnl'] > 0 and max_to_close <= 2:
                    info(f"   ⏸️ {pos['ticker']}: пропускаем (прибыльная {pos['pnl']:+.2f}₽)")
                    continue

                info(f"\n   🔒 ЗАКРЫТИЕ {pos['ticker']} ({pos['side']})...")
                info(f"      Убыток: {pos['pnl']:.2f}₽ ({pos['pnl_pct']:.2f}%)")
                info(f"      Количество: {pos['quantity']} шт")

                try:
                    if pos['side'] == 'LONG':
                        result = tbank.sell(pos['figi'], pos['quantity'], use_market=True)
                    else:
                        result = tbank.buy(pos['figi'], pos['quantity'], use_market=True)

                    if result:
                        success(f"   ✅ {pos['ticker']} успешно закрыт")
                        closed += 1
                    else:
                        error(f"   ❌ Не удалось закрыть {pos['ticker']}")

                        # Пробуем альтернативный метод
                        info(f"      🔄 Пробуем close_position_with_retry...")
                        result2 = tbank.close_position_with_retry(
                            pos['figi'],
                            pos['quantity'],
                            "SELL" if pos['side'] == 'LONG' else "BUY"
                        )
                        if result2.get('success'):
                            success(f"   ✅ {pos['ticker']} закрыт через retry")
                            closed += 1
                        else:
                            error(f"   ❌ {pos['ticker']} НЕ ЗАКРЫТ: {result2.get('reason', 'unknown')}")

                except Exception as e:
                    error(f"   ❌ Ошибка при закрытии {pos['ticker']}: {e}")

            if closed > 0:
                info(f"\n   📊 ИТОГ: закрыто {closed} из {max_to_close} позиций")

            return closed

        except Exception as e:
            error(f"❌ Ошибка в _close_worst_positions: {e}")
            return 0

    def emergency_close_by_symbol(self, ticker: str) -> bool:
        """
        Экстренное закрытие позиции по тикеру (ручной вызов)
        Использует умное закрытие с прогрессивным проскальзыванием
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.core.blacklist_manager import blacklist_manager

        self.sync_and_cleanup()

        try:
            # Находим FIGI по тикеру
            info(f"🔍 Поиск FIGI для тикера {ticker}...")
            all_shares = tbank.get_all_shares(limit=500)
            figi = None
            found_ticker = None

            for share in all_shares:
                if share.get('ticker') == ticker.upper():
                    figi = share.get('figi')
                    found_ticker = share.get('ticker')
                    break

            if not figi:
                error(f"❌ Не найден FIGI для {ticker}")
                return False

            info(f"   ✅ Найден FIGI: {figi} для {found_ticker}")

            # Находим позицию
            position = self.get_position(figi)
            if not position:
                error(f"❌ Позиция {ticker} не найдена в менеджере")
                return False

            # Проверка блокировки
            if hasattr(position, 'blocked') and position.blocked:
                error(f"❌ НЕВОЗМОЖНО ЗАКРЫТЬ {ticker}: позиция ЗАБЛОКИРОВАНА брокером!")
                error(f"   Закройте позицию вручную через приложение Т-Банк")
                return False

            # Получаем текущую цену
            current_price = tbank.get_current_price(figi)
            if not current_price:
                error(f"❌ Не удалось получить текущую цену для {ticker}")
                return False

            # Рассчитываем P&L
            profit_pct = position.current_profit_pct(current_price)
            profit_amount = position.current_profit_amount(current_price)

            info(f"\n{'🚨' * 40}")
            info(f"🚨 ЭКСТРЕННОЕ ЗАКРЫТИЕ ПОЗИЦИИ {ticker}")
            info(f"{'🚨' * 40}")
            info(f"   📊 Сторона: {position.side.value}")
            info(f"   🔢 Количество: {position.quantity} шт")
            info(f"   💰 Цена входа: {position.avg_price:.2f}₽")
            info(f"   💹 Текущая цена: {current_price:.2f}₽")
            info(f"   📈 P&L: {profit_amount:+.2f}₽ ({profit_pct:+.2f}%)")

            # Отменяем стоп-приказы
            info(f"\n📋 Отмена стоп-приказов...")
            self._cancel_stop_orders(position)

            # Определяем направление
            if position.side == OrderSide.LONG:
                direction = "SELL"
            else:
                direction = "BUY"

            info(f"\n📋 Исполнение умного закрытия...")
            result = tbank.close_position_with_retry(
                figi=figi,
                quantity=position.quantity,
                direction=direction,
                max_attempts=10,
                emergency_slippage=0.10
            )

            if result.get('success'):
                success(f"\n{'✅' * 30}")
                success(f"✅ ПОЗИЦИЯ {ticker} ЭКСТРЕННО ЗАКРЫТА!")
                success(f"   💰 Цена закрытия: {result.get('price'):.2f}₽")
                success(f"   📉 Проскальзывание: {result.get('slippage_pct'):.2f}%")
                success(f"   📊 P&L: {profit_amount:+.2f}₽ ({profit_pct:+.2f}%)")
                success(f"{'✅' * 30}")

                # Отправляем уведомление
                telegram = _get_telegram()
                if telegram:
                    try:
                        telegram.send_trade_closed(
                            side=position.side.value,
                            reason="ЭКСТРЕННОЕ (ручной вызов)",
                            profit_pct=profit_pct,
                            profit_amount=profit_amount,
                            ticker=ticker,
                            quantity=position.quantity
                        )
                        info(f"   📱 Уведомление отправлено в Telegram")
                    except Exception as e:
                        debug(f"   ⚠️ Ошибка отправки уведомления: {e}")

                # Удаляем позицию из менеджера
                self.remove_position(figi)
                return True
            else:
                error(f"❌ Не удалось экстренно закрыть {ticker}")
                # Добавляем в чёрный список
                blacklist_manager.add_temporary(ticker, ttl_minutes=60)
                return False

        except Exception as e:
            error(f"❌ Ошибка экстренного закрытия {ticker}: {e}")
            return False

    def check_critical_margin(self) -> bool:
        """Проверка критической маржи с аварийным закрытием"""

        self.sync_and_cleanup()

        if self._checking_critical_margin:
            return False
        self._checking_critical_margin = True

        try:
            from trading_bot.api.tbank_client import tbank
            from trading_bot.core.blacklist_manager import blacklist_manager

            margin_info = tbank.get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)

            # Критическая маржа > 85%
            if margin_rate > 85:
                error(f"\n🔥 КРИТИЧЕСКАЯ МАРЖА: {margin_rate:.1f}%!")

                # Закрываем ВСЕ позиции, а не только убыточные
                closed = 0
                for figi, position in list(self._positions.items()):
                    ticker = position.ticker or figi[:8]
                    warning(f"🚨 АВАРИЙНОЕ ЗАКРЫТИЕ {ticker}")

                    if position.side == OrderSide.LONG:
                        direction = "SELL"
                    else:
                        direction = "BUY"

                    result = tbank.close_position_with_retry(
                        figi=figi,
                        quantity=position.quantity,
                        direction=direction,
                        max_attempts=10,
                        emergency_slippage=0.10
                    )

                    if result.get('success'):
                        self.remove_position(figi)
                        closed += 1
                        success(f"   ✅ {ticker} закрыт")
                    else:
                        error(f"   ❌ Не удалось закрыть {ticker}")
                        blacklist_manager.add_temporary(ticker, ttl_minutes=60)

                if closed > 0:
                    success(f"✅ Аварийно закрыто {closed} позиций")
                return True

            return False

        except Exception as e:
            error(f"Ошибка проверки маржи: {e}")
            return False
        finally:
            self._checking_critical_margin = False

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

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

    def is_trading_allowed(self, figi: str) -> Tuple[bool, str]:
        """Проверка, можно ли торговать инструментом прямо сейчас"""
        try:
            from trading_bot.api.tbank_client import tbank
            return tbank.is_market_available(figi)
        except Exception as e:
            debug(f"Ошибка проверки доступности торгов для {figi}: {e}")
            return True, "Ошибка проверки"

    def is_margin_trading_allowed(self) -> Tuple[bool, str]:
        """Проверка, включена ли маржинальная торговля"""
        try:
            from trading_bot.api.tbank_client import tbank
            return tbank.check_margin_trading_allowed()
        except Exception as e:
            error(f"Ошибка проверки маржинальной торговли: {e}")
            return False, f"Ошибка: {e}"

    def add_temp_skip_adaptive(self, figi: str, error_code: str = "", minutes: int = 10):
        """Добавление временной блокировки с анализом ошибки"""
        NO_BLOCK_CODES = ["30079", "30049", "30014"]

        for code in NO_BLOCK_CODES:
            if code in error_code:
                warning(f"⏸️ {figi}: {error_code}, повторная попытка в следующем цикле")
                return

        self.add_temp_skip(figi, minutes)
        warning(f"🔒 {figi} заблокирован на {minutes} мин (ошибка: {error_code})")

    def sync_and_recover_positions(self) -> int:
        """
        СИНХРОНИЗАЦИЯ И ВОССТАНОВЛЕНИЕ ПОЗИЦИЙ ИЗ БРОКЕРА
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.models import Position, OrderSide
        from trading_bot.logger import info, success, error, warning, debug

        print("\n" + "=" * 80)
        print("🔄 СИНХРОНИЗАЦИЯ И ВОССТАНОВЛЕНИЕ ПОЗИЦИЙ")
        print("=" * 80)
        info("🔄 СИНХРОНИЗАЦИЯ ПОЗИЦИЙ С БРОКЕРОМ...")

        try:
            # ========== 1. ПОЛУЧАЕМ ПОЗИЦИИ ОТ БРОКЕРА ==========
            print("\n📡 [1/6] ПОЛУЧЕНИЕ ПОЗИЦИЙ ОТ БРОКЕРА...")
            broker_positions = tbank.get_positions(force_refresh=True)

            if not broker_positions:
                print("   📭 Нет открытых позиций у брокера")
                info("   📭 Нет открытых позиций для синхронизации")
                return 0

            print(f"   ✅ Найдено {len(broker_positions)} позиций у брокера")
            info(f"📊 Получено {len(broker_positions)} позиций от брокера")

            # ========== 2. ЛОГИРУЕМ ТЕКУЩИЕ ПОЗИЦИИ В МЕНЕДЖЕРЕ ==========
            print(f"\n📊 [2/6] ТЕКУЩИЕ ПОЗИЦИИ В МЕНЕДЖЕРЕ: {len(self._positions)}")
            for figi, existing_pos in self._positions.items():
                print(
                    f"   - {existing_pos.ticker}: {existing_pos.side.value} {existing_pos.quantity}шт по {existing_pos.avg_price:.2f}₽")

            restored = 0
            recovered_orders = 0
            updated_count = 0

            # ✅ СПИСОК OTC ПОЗИЦИЙ ДЛЯ ОЧИСТКИ
            otc_figis_to_cleanup = []

            # ========== 3. ВОССТАНАВЛИВАЕМ ПОЗИЦИИ ==========
            print("\n🔄 [3/6] ВОССТАНОВЛЕНИЕ ПОЗИЦИЙ...")

            for idx, pos in enumerate(broker_positions, 1):
                figi = pos['figi']
                quantity = abs(pos['quantity'])

                if quantity == 0:
                    continue

                # Определяем avg_price (внутри цикла, для каждой позиции)
                avg_price = pos['avg_price']

                print(f"\n   [{idx}/{len(broker_positions)}] Обработка FIGI: {figi[:12]}...")

                # ✅ ПРОВЕРКА OTC ПРИ ВОССТАНОВЛЕНИИ
                if tbank.is_confirmation_required(figi):
                    ticker = tbank._get_ticker_by_figi(figi) or figi[:8]
                    warning(f"⚠️ {ticker} - OTC ИНСТРУМЕНТ! НЕВОЗМОЖНО УПРАВЛЯТЬ ЧЕРЕЗ API!")
                    warning(f"   Пропускаем восстановление")
                    otc_figis_to_cleanup.append(figi)
                    continue  # Переходим к следующей позиции

                # Если позиции нет в менеджере, но есть у брокера - восстанавливаем
                if figi not in self._positions:
                    ticker = tbank._get_ticker_by_figi(figi) or figi[:8]
                    side = OrderSide.SHORT if pos['quantity'] < 0 else OrderSide.LONG

                    print(f"   🆕 НОВАЯ ПОЗИЦИЯ: {ticker}")
                    print(f"      Сторона: {side.value}")
                    print(f"      Количество: {quantity} шт")
                    print(f"      Средняя цена: {avg_price:.4f}₽")
                    print(f"      Текущая позиция у брокера: {pos['quantity']} шт")

                    info(f"🔄 Восстановление позиции {ticker}: {side.value} {quantity} шт по {avg_price:.2f}₽")

                    # Получаем текущую цену для установки максимума/минимума
                    current_price = tbank.get_current_price(figi)
                    print(
                        f"      Текущая цена: {current_price:.4f}₽" if current_price else "      ⚠️ Не удалось получить текущую цену")

                    # Создаём объект позиции
                    position = Position(
                        figi=figi,
                        ticker=ticker,
                        quantity=quantity,
                        avg_price=avg_price,
                        side=side,
                        entry_time=datetime.now(MOSCOW_TZ)
                    )

                    # Устанавливаем начальные значения для трейлинг-стопа
                    if current_price:
                        if side == OrderSide.LONG:
                            position.highest_price = current_price
                            position.lowest_price = current_price
                            print(f"      📈 LONG: установлен максимум = {current_price:.4f}₽")
                        else:
                            position.lowest_price = current_price
                            position.highest_price = current_price
                            print(f"      📉 SHORT: установлен минимум = {current_price:.4f}₽")

                    # Сохраняем в менеджер
                    self._positions[figi] = position
                    restored += 1
                    print(f"   ✅ Позиция {ticker} добавлена в менеджер")

                    # Восстанавливаем защитные ордера
                    print(f"\n   🛡️ [4/6] ВОССТАНОВЛЕНИЕ ЗАЩИТНЫХ ОРДЕРОВ ДЛЯ {ticker}...")
                    orders_recovered = self._restore_protective_orders_for_position(position)
                    recovered_orders += orders_recovered
                    print(f"   ✅ Восстановлено ордеров: {orders_recovered}")

                else:
                    # Позиция уже есть в менеджере - обновляем данные
                    existing = self._positions[figi]
                    print(f"   📍 Существующая позиция: {existing.ticker}")
                    print(f"      Текущее количество в менеджере: {existing.quantity} шт")
                    print(f"      Количество у брокера: {quantity} шт")

                    if existing.quantity != quantity:
                        print(f"      🔄 Обновляем количество: {existing.quantity} → {quantity}")
                        existing.quantity = quantity
                        updated_count += 1

                    if abs(existing.avg_price - avg_price) > 0.01:
                        print(f"      🔄 Обновляем среднюю цену: {existing.avg_price:.4f} → {avg_price:.4f}₽")
                        existing.avg_price = avg_price
                        updated_count += 1

            # ========== 4. УДАЛЯЕМ МЁРТВЫЕ ПОЗИЦИИ ==========
            print("\n🗑️ [5/6] УДАЛЕНИЕ МЁРТВЫХ ПОЗИЦИЙ...")
            broker_figis = {p['figi'] for p in broker_positions}
            removed = 0

            for figi in list(self._positions.keys()):
                if figi not in broker_figis:
                    ticker = self._positions[figi].ticker
                    print(f"   🗑️ Удаляем мёртвую позицию: {ticker}")
                    info(f"🧹 Удалена мёртвая позиция {ticker}")
                    del self._positions[figi]
                    removed += 1

            if removed > 0:
                print(f"   ✅ Удалено мёртвых позиций: {removed}")
            else:
                print("   ✅ Нет мёртвых позиций для удаления")

            # ✅ ОЧИСТКА OTC-ПОЗИЦИЙ ИЗ БД
            if otc_figis_to_cleanup and self.db:
                try:
                    print(f"\n🗑️ [6/6] ОЧИСТКА OTC ПОЗИЦИЙ ИЗ БД...")
                    for figi in otc_figis_to_cleanup:
                        ticker = tbank._get_ticker_by_figi(figi) or figi[:8]
                        print(f"   🧹 Удаляем OTC позицию {ticker} из БД")
                        warning(f"🧹 Удаление OTC позиции {ticker} из базы данных")

                    cleaned = self.db.cleanup_otc_positions(otc_figis_to_cleanup)
                    print(f"   ✅ Очищено OTC позиций из БД: {cleaned}")
                    info(f"✅ Очищено {cleaned} OTC позиций из базы данных")
                except Exception as e:
                    error(f"   ❌ Ошибка очистки OTC позиций из БД: {e}")

            # ========== 5. ИТОГИ ==========
            print("\n" + "=" * 80)
            print("📊 ИТОГИ СИНХРОНИЗАЦИИ")
            print("=" * 80)
            print(f"   🔄 Восстановлено позиций: {restored}")
            print(f"   🛡️ Восстановлено защитных ордеров: {recovered_orders}")
            print(f"   📝 Обновлено позиций: {updated_count}")
            print(f"   🗑️ Удалено мёртвых позиций: {removed}")
            print(f"   🚫 OTC позиций пропущено: {len(otc_figis_to_cleanup)}")
            print(f"   📊 Всего позиций в менеджере: {len(self._positions)}")
            print("=" * 80)

            if restored > 0:
                success(f"✅ Восстановлено {restored} позиций из брокера")
                success(f"✅ Восстановлено {recovered_orders} защитных ордеров")
            else:
                info("   ✅ Все позиции синхронизированы")

            return restored

        except Exception as e:
            error(f"❌ Ошибка синхронизации: {e}")
            import traceback
            error(f"   {traceback.format_exc()}")
            return 0

    def _restore_protective_orders_for_position(self, position) -> int:
        """
        ВОССТАНОВЛЕНИЕ ЗАЩИТНЫХ ОРДЕРОВ (TP/SL) ДЛЯ ПОЗИЦИИ
        Проверяет активные заявки у брокера и восстанавливает их в менеджере

        Args:
            position: Объект позиции

        Returns:
            int: Количество восстановленных ордеров
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, warning, debug, success

        ticker = position.ticker or position.figi[:8]
        recovered = 0

        print(f"\n   🔍 ПРОВЕРКА АКТИВНЫХ ЗАЯВОК ДЛЯ {ticker}:")

        try:
            # Получаем активные заявки от брокера
            active_orders = tbank.get_active_orders()

            if not active_orders:
                print(f"      📭 Нет активных заявок у брокера для {ticker}")
                debug(f"   📭 Нет активных заявок для {ticker}")
                return 0

            # Фильтруем заявки по FIGI
            position_orders = [o for o in active_orders if o.get('figi') == position.figi]

            if not position_orders:
                print(f"      📭 Нет активных заявок для {ticker} (другие инструменты есть)")
                debug(f"   📭 Нет активных заявок для {ticker}")
                return 0

            print(f"      📋 Найдено {len(position_orders)} активных заявок для {ticker}")
            info(f"   📋 Найдено {len(position_orders)} активных заявок для {ticker}")

            for order in position_orders:
                direction = order.get('direction')
                price = order.get('price', 0)
                quantity = order.get('quantity', 0)
                order_id = order.get('order_id', 'unknown')[:8]

                print(f"\n      📍 ЗАЯВКА {order_id}: {direction} {quantity}шт по {price:.2f}₽")

                # Определяем тип заявки (TP или SL) в зависимости от стороны позиции
                if position.side == OrderSide.LONG:
                    if direction == "SELL":
                        if price > position.avg_price:
                            # Это тейк-профит (цена выше входа)
                            position.take_profit_price = price
                            position.take_profit_order_id = order.get('order_id')
                            recovered += 1
                            print(
                                f"         ✅ ВОССТАНОВЛЕН TP: {price:.2f}₽ (выше входа на {((price - position.avg_price) / position.avg_price * 100):.2f}%)")
                            success(f"   ✅ Восстановлен TP для {ticker}: {price:.2f}₽")
                        elif price < position.avg_price:
                            # Это стоп-лосс (цена ниже входа)
                            position.stop_order_price = price
                            position.stop_order_placed = True
                            recovered += 1
                            print(
                                f"         ✅ ВОССТАНОВЛЕН SL: {price:.2f}₽ (ниже входа на {((position.avg_price - price) / position.avg_price * 100):.2f}%)")
                            success(f"   ✅ Восстановлен SL для {ticker}: {price:.2f}₽")
                        else:
                            print(f"         ⚠️ НЕИЗВЕСТНЫЙ ТИП: цена равна цене входа")
                    else:
                        print(f"         ⚠️ НЕИЗВЕСТНОЕ НАПРАВЛЕНИЕ: {direction} для LONG позиции")

                elif position.side == OrderSide.SHORT:
                    if direction == "BUY":
                        if price < position.avg_price:
                            # Для SHORT: тейк-профит (цена ниже входа)
                            position.take_profit_price = price
                            position.take_profit_order_id = order.get('order_id')
                            recovered += 1
                            print(
                                f"         ✅ ВОССТАНОВЛЕН TP: {price:.2f}₽ (ниже входа на {((position.avg_price - price) / position.avg_price * 100):.2f}%)")
                            success(f"   ✅ Восстановлен TP для {ticker}: {price:.2f}₽")
                        elif price > position.avg_price:
                            # Для SHORT: стоп-лосс (цена выше входа)
                            position.stop_order_price = price
                            position.stop_order_placed = True
                            recovered += 1
                            print(
                                f"         ✅ ВОССТАНОВЛЕН SL: {price:.2f}₽ (выше входа на {((price - position.avg_price) / position.avg_price * 100):.2f}%)")
                            success(f"   ✅ Восстановлен SL для {ticker}: {price:.2f}₽")
                        else:
                            print(f"         ⚠️ НЕИЗВЕСТНЫЙ ТИП: цена равна цене входа")
                    else:
                        print(f"         ⚠️ НЕИЗВЕСТНОЕ НАПРАВЛЕНИЕ: {direction} для SHORT позиции")

            # Если не нашли защитных ордеров, создаём новые
            if recovered == 0:
                print(f"\n      ⚠️ Не найдено защитных ордеров для {ticker}")
                print(f"      🔄 СОЗДАЁМ НОВЫЕ ЗАЩИТНЫЕ ОРДЕРА...")

                # Рассчитываем TP/SL на основе конфига
                from trading_bot.config import config
                current_price = tbank.get_current_price(position.figi) or position.avg_price

                if position.side == OrderSide.LONG:
                    take_profit_price = position.avg_price * (1 + config.take_profit_pct / 100)
                    stop_loss_price = position.avg_price * (1 - config.stop_loss_pct / 100)

                    print(f"         📈 LONG: TP={take_profit_price:.2f}₽ (+{config.take_profit_pct}%)")
                    print(f"         📉 SL={stop_loss_price:.2f}₽ (-{config.stop_loss_pct}%)")

                    # Создаём тейк-профит
                    if tbank.place_limit_order(position.figi, position.quantity, "SELL", take_profit_price):
                        position.take_profit_price = take_profit_price
                        recovered += 1
                        success(f"   ✅ Создан новый TP для {ticker}: {take_profit_price:.2f}₽")

                    # Создаём стоп-лосс (если поддерживается)
                    if tbank.supports_stop_orders(position.figi):
                        if tbank.place_stop_loss_order(position.figi, position.quantity, stop_loss_price, "LONG"):
                            position.stop_order_price = stop_loss_price
                            position.stop_order_placed = True
                            recovered += 1
                            success(f"   ✅ Создан новый SL для {ticker}: {stop_loss_price:.2f}₽")
                    else:
                        print(f"         ⚠️ Стоп-ордера не поддерживаются, используем программный трейлинг")
                        warning(f"   ⚠️ Стоп-ордера не поддерживаются для {ticker}")

                else:  # SHORT
                    take_profit_price = position.avg_price * (1 - config.take_profit_pct / 100)
                    stop_loss_price = position.avg_price * (1 + config.stop_loss_pct / 100)

                    print(f"         📉 SHORT: TP={take_profit_price:.2f}₽ (-{config.take_profit_pct}%)")
                    print(f"         📈 SL={stop_loss_price:.2f}₽ (+{config.stop_loss_pct}%)")

                    # Создаём тейк-профит
                    if tbank.place_limit_order(position.figi, position.quantity, "BUY", take_profit_price):
                        position.take_profit_price = take_profit_price
                        recovered += 1
                        success(f"   ✅ Создан новый TP для {ticker}: {take_profit_price:.2f}₽")

                    # Создаём стоп-лосс (если поддерживается)
                    if tbank.supports_stop_orders(position.figi):
                        if tbank.place_stop_loss_order(position.figi, position.quantity, stop_loss_price, "SHORT"):
                            position.stop_order_price = stop_loss_price
                            position.stop_order_placed = True
                            recovered += 1
                            success(f"   ✅ Создан новый SL для {ticker}: {stop_loss_price:.2f}₽")
                    else:
                        print(f"         ⚠️ Стоп-ордера не поддерживаются, используем программный трейлинг")
                        warning(f"   ⚠️ Стоп-ордера не поддерживаются для {ticker}")

            # Настраиваем трейлинг-стоп (всегда программный)
            if hasattr(self, '_setup_trailing_stop'):
                self._setup_trailing_stop(position)
                print(f"      🔻 Настроен программный трейлинг-стоп")

            return recovered

        except Exception as e:
            error(f"   ❌ Ошибка восстановления ордеров для {ticker}: {e}")
            import traceback
            debug(f"      {traceback.format_exc()}")
            return 0

    # ========== МЕТОД: УСТАНОВКА ЗАЩИТНЫХ ОРДЕРОВ ==========

    def _place_protective_orders_for_position(self, position: Position):
        """
        УСТАНОВКА ЗАЩИТНЫХ ОРДЕРОВ (TP/SL) ДЛЯ СУЩЕСТВУЮЩЕЙ ПОЗИЦИИ
        ВНИМАНИЕ: БОЛЬШИНСТВО ИНСТРУМЕНТОВ НЕ ПОДДЕРЖИВАЮТ СТОП-ОРДЕРА!
        ИСПОЛЬЗУЕТСЯ ТОЛЬКО ПРОГРАММНЫЙ ТРЕЙЛИНГ-СТОП
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, warning, debug, success
        from trading_bot.config import config

        figi = position.figi
        ticker = position.ticker or figi[:8]
        price = position.avg_price
        quantity = position.quantity

        info(f"\n{'═' * 70}")
        info(f"🛡️ УСТАНОВКА ЗАЩИТЫ ДЛЯ ПОЗИЦИИ {ticker}")
        info(f"{'═' * 70}")
        info(f"   📊 Параметры позиции:")
        info(f"      FIGI: {figi}")
        info(f"      Сторона: {position.side.value}")
        info(f"      Количество: {quantity} шт")
        info(f"      Цена входа: {price:.4f}₽")

        # ========== 1. ВСЕГДА ИСПОЛЬЗУЕМ ТОЛЬКО ПРОГРАММНЫЙ ТРЕЙЛИНГ-СТОП ==========
        info(f"\n   📋 ВЫБОР ТИПА ЗАЩИТЫ:")
        info(f"      🔧 Тип: ПРОГРАММНЫЙ ТРЕЙЛИНГ-СТОП")
        info(f"      ❌ Биржевые стоп-ордера: НЕ ИСПОЛЬЗУЮТСЯ (API ограничения)")

        # ========== 2. НАСТРАИВАЕМ ПАРАМЕТРЫ ТРЕЙЛИНГ-СТОПА ==========
        trailing_stop_pct = getattr(config, 'trailing_stop_pct', 0.5)
        info(f"\n   📊 НАСТРОЙКИ ТРЕЙЛИНГ-СТОПА:")
        info(f"      📐 Процент трейлинга: {trailing_stop_pct}%")

        if position.side == OrderSide.LONG:
            position.highest_price = price
            position.trailing_stop_price = price * (1 - trailing_stop_pct / 100)
            info(f"      📈 LONG позиция:")
            info(f"         📊 Текущий максимум: {price:.4f}₽")
            info(f"         🔻 Стоп-цена: {position.trailing_stop_price:.4f}₽")
            info(f"         📉 Падение для срабатывания: {trailing_stop_pct}%")
        else:
            position.lowest_price = price
            position.trailing_stop_price = price * (1 + trailing_stop_pct / 100)
            info(f"      📉 SHORT позиция:")
            info(f"         📊 Текущий минимум: {price:.4f}₽")
            info(f"         🔺 Стоп-цена: {position.trailing_stop_price:.4f}₽")
            info(f"         📈 Рост для срабатывания: {trailing_stop_pct}%")

        position.trailing_stop_pct = trailing_stop_pct
        position.trailing_activated = False

        # ========== 3. ДОПОЛНИТЕЛЬНАЯ ДИАГНОСТИКА API ==========
        info(f"\n   🔍 ДИАГНОСТИКА API (информационно):")

        try:
            # Получаем статус торгов
            status = tbank.get_trading_status(figi)

            api_available = status.get('api_trade_available', False)
            market_available = status.get('market_order_available', False)
            limit_available = status.get('limit_order_available', False)
            trading_status = status.get('trading_status_description', 'unknown')

            info(f"      📊 СТАТУС ТОРГОВ:")
            info(f"         📈 Режим: {trading_status}")
            info(f"         🔌 API торговля: {'✅ ДОСТУПНА' if api_available else '❌ НЕ ДОСТУПНА'}")
            info(f"         🏷️ Рыночные заявки: {'✅ ДОСТУПНЫ' if market_available else '❌ НЕ ДОСТУПНЫ'}")
            info(f"         📋 Лимитные заявки: {'✅ ДОСТУПНЫ' if limit_available else '❌ НЕ ДОСТУПНЫ'}")

            # Проверяем поддержку стоп-ордеров (только для информации)
            try:
                supports_stops = tbank.supports_stop_orders(figi)
                info(f"         🛑 Биржевые стопы: {'✅ ПОДДЕРЖИВАЮТСЯ' if supports_stops else '❌ НЕ ПОДДЕРЖИВАЮТСЯ'}")
                if not supports_stops:
                    info(f"            → Используем программный трейлинг-стоп")
            except Exception as e:
                debug(f"         ⚠️ Ошибка проверки стопов: {e}")

        except Exception as e:
            debug(f"      ⚠️ Ошибка получения статуса торгов: {e}")

        # ========== 4. ПРОВЕРКА OTC СТАТУСА ==========
        try:
            is_otc = tbank.is_confirmation_required(figi)
            if is_otc:
                info(f"\n   🔐 OTC СТАТУС:")
                info(f"      ⚠️ {ticker} требует подтверждения сделок (OTC)")
                info(f"      📋 Будут использованы ТОЛЬКО лимитные заявки")
                position.is_otc = True
            else:
                info(f"\n   ✅ OTC СТАТУС: обычный инструмент")
                position.is_otc = False
        except Exception as e:
            debug(f"      ⚠️ Ошибка проверки OTC статуса: {e}")

        # ========== 5. ОПРЕДЕЛЯЕМ ДОСТУПНЫЕ ТИПЫ ЗАЯВОК ==========
        try:
            market_available = False
            status = tbank.get_trading_status(figi)
            market_available = status.get('market_order_available', False)

            info(f"\n   📋 ДОСТУПНЫЕ ТИПЫ ЗАЯВОК:")

            if not market_available:
                info(f"      ⚠️ Рыночные заявки: НЕ ДОСТУПНЫ")
                info(f"      📋 Будут использованы ТОЛЬКО лимитные заявки")
                position.market_orders_disabled = True
            else:
                info(f"      ✅ Рыночные заявки: ДОСТУПНЫ")
                info(f"      📋 Лимитные заявки: ДОСТУПНЫ")
                position.market_orders_disabled = False

        except Exception as e:
            debug(f"      ⚠️ Ошибка определения типов заявок: {e}")

        # ========== 6. ИТОГОВЫЙ СТАТУС ЗАЩИТЫ ==========
        info(f"\n{'─' * 70}")
        info(f"📊 ИТОГОВЫЙ СТАТУС ЗАЩИТЫ ДЛЯ {ticker}:")
        info(f"   🛡️ Тип: ПРОГРАММНЫЙ ТРЕЙЛИНГ-СТОП")

        if position.side == OrderSide.LONG:
            info(f"   📈 LONG: максимум={position.highest_price:.4f}₽, стоп={position.trailing_stop_price:.4f}₽")
        else:
            info(f"   📉 SHORT: минимум={position.lowest_price:.4f}₽, стоп={position.trailing_stop_price:.4f}₽")

        info(f"   📐 Параметры: трейлинг={position.trailing_stop_pct}%, активирован={position.trailing_activated}")
        info(f"{'─' * 70}")

        success(f"✅ Защита для {ticker} настроена (программный трейлинг-стоп)")
        info(f"{'═' * 70}\n")

    # ========== ПРОВЕРКА МАРЖИ ПЕРЕД ОТКРЫТИЕМ ==========

    def can_open_new_position(self, required_margin: float = 0, total_cost: float = 0) -> Tuple[bool, str]:
        """
        Проверка, можно ли открыть новую позицию с учётом текущей маржи и средств

        Args:
            required_margin: Требуемая маржа для новой позиции (в рублях)
            total_cost: Полная стоимость позиции (для расчёта маржи)

        Returns:
            Tuple[bool, str]: (можно_открывать, причина)
        """
        from trading_bot.api.tbank_client import tbank

        try:
            # Получаем информацию о средствах
            available, total_capital, _ = tbank.get_available_funds()

            # Получаем информацию о марже
            margin_info = tbank.get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)

            # ========== 1. ПРОВЕРКА МИНИМАЛЬНОГО КАПИТАЛА ==========
            if total_capital < 500:
                return False, f"Капитал {total_capital:.0f}₽ < 500₽"

            # ========== 2. ПРОВЕРКА СВОБОДНЫХ СРЕДСТВ ==========
            MIN_FREE_FUNDS = 300
            if available < MIN_FREE_FUNDS:
                return False, f"Свободных средств {available:.0f}₽ < {MIN_FREE_FUNDS}₽"

            # ========== 3. ПРОВЕРКА РАЗМЕРА ПОЗИЦИИ ОТНОСИТЕЛЬНО КАПИТАЛА ==========
            if total_cost > 0:
                MAX_POSITION_PCT = 0.7
                if total_cost > total_capital * MAX_POSITION_PCT:
                    return False, f"Сумма сделки {total_cost:.0f}₽ > {MAX_POSITION_PCT * 100:.0f}% капитала ({total_capital:.0f}₽)"

                # Проверка, что свободных средств хватит хотя бы на 30% позиции
                MIN_COVERAGE_PCT = 0.3
                if available < total_cost * MIN_COVERAGE_PCT:
                    return False, f"Свободных средств {available:.0f}₽ < {MIN_COVERAGE_PCT * 100:.0f}% от суммы сделки"

            # ========== 4. ПРОВЕРКА МАРЖИ ==========
            if margin_rate > 85:
                return False, f"Маржа {margin_rate:.1f}% > 85% (критический уровень)"

            # Если маржа > 75% - предупреждение, но не блокируем
            if margin_rate > 75:
                warning(f"⚠️ Высокая маржа {margin_rate:.1f}%, новые позиции ограничены")

            # ========== 5. ПРОВЕРКА ПОСЛЕ ОТКРЫТИЯ ==========
            if required_margin > 0 and total_cost > 0:
                liquid_portfolio = margin_info.get('liquid_portfolio', 1)
                if liquid_portfolio > 0:
                    # Расчётная маржа после открытия
                    estimated_margin_rate = margin_rate + (total_cost / liquid_portfolio * 100)
                    if estimated_margin_rate > 85:
                        return False, f"После открытия маржа станет {estimated_margin_rate:.1f}% > 85%"

            return True, "OK"

        except Exception as e:
            debug(f"Ошибка проверки: {e}")
            return True, f"Ошибка проверки: {e}"

    def cleanup_stuck_positions(self, max_stuck_minutes: int = 10) -> int:
        """Периодическая очистка "застрявших" позиций"""
        now = datetime.now(MOSCOW_TZ)
        cleaned = 0

        for figi, last_attempt in list(self._last_close_attempt.items()):
            if (now - last_attempt).total_seconds() > max_stuck_minutes * 60:
                ticker = self._get_trading_bot()._get_ticker_by_figi(figi) or figi[:8]

                # ⚠️ ПРОВЕРЯЕМ, ДЕЙСТВИТЕЛЬНО ЛИ ПОЗИЦИЯ ЗАКРЫТА У БРОКЕРА!
                from trading_bot.api.tbank_client import tbank
                broker_positions = tbank.get_positions(force_refresh=True)
                still_exists = False
                for pos in broker_positions:
                    if pos.get('figi') == figi and abs(pos.get('quantity', 0)) > 0:
                        still_exists = True
                        break

                if not still_exists:
                    # Позиции нет у брокера - можно удалять
                    warning(
                        f"🧹 Очистка застрявшей позиции {ticker} (не закрывается >{max_stuck_minutes} мин, но у брокера уже нет)")
                    self.remove_position(figi)
                    cleaned += 1
                else:
                    # Позиция ВСЁ ЕЩЁ существует у брокера - НЕ УДАЛЯЕМ!
                    warning(f"⚠️ Позиция {ticker} застряла, но ВСЁ ЕЩЁ существует у брокера! НЕ УДАЛЯЕМ из менеджера")
                    # Сбрасываем счётчик попыток, чтобы попробовать снова
                    self._close_attempts[figi] = 0
                    self._last_close_attempt[figi] = now

        if cleaned > 0:
            info(f"🧹 Очищено {cleaned} застрявших позиций")

        return cleaned

    def check_eternal_positions(self, max_hold_minutes: int = 120):
        """Проверка позиций, которые висят слишком долго"""
        from trading_bot.trading.position_closer import position_closer

        now = datetime.now(MOSCOW_TZ)

        for figi, position in list(self._positions.items()):
            hold_minutes = position.hold_minutes()

            # 2 часа - максимальное время удержания
            if hold_minutes > max_hold_minutes:
                ticker = position.ticker or figi[:8]
                warning(f"⏰ Позиция {ticker} висит {hold_minutes:.0f} мин > {max_hold_minutes} мин")
                warning(f"   Принудительное закрытие по таймауту")

                # ✅ ИСПРАВЛЕНО: используем position_closer
                success = position_closer.close_position_smart(figi, ticker)
                if success:
                    info(f"   ✅ Позиция {ticker} закрыта по таймауту")
                else:
                    error(f"   ❌ Не удалось закрыть {ticker} по таймауту")

    def calculate_overnight_fee(self, uncovered_amount: float, days_open: int = 1) -> float:
        """
        Расчёт платы за перенос непокрытой позиции (овернайт)

        Args:
            uncovered_amount: Сумма непокрытой позиции (заёмные средства)
            days_open: Количество дней переноса

        Returns:
            float: Комиссия за перенос в рублях
        """
        if uncovered_amount <= 0:
            return 0.0

        # Тарифы Т-Банка 2026 (для тарифа "Инвестор")
        if uncovered_amount <= 5000:
            daily_fee = 0
        elif uncovered_amount <= 50000:
            daily_fee = 40
        elif uncovered_amount <= 100000:
            daily_fee = 80
        elif uncovered_amount <= 250000:
            daily_fee = 190
        elif uncovered_amount <= 500000:
            daily_fee = 375
        elif uncovered_amount <= 1000000:
            daily_fee = 750
        elif uncovered_amount <= 2500000:
            daily_fee = 1850
        else:
            daily_fee = uncovered_amount * 0.00070

        return daily_fee * days_open

    def get_uncovered_amount(self) -> Tuple[float, float]:
        """
        Получение суммы непокрытых позиций (заёмных средств)

        Returns:
            Tuple[float, float]: (общая сумма непокрытых позиций, использованная маржа)
        """
        try:
            margin_info = _get_tbank().get_margin_info()
            if not margin_info:
                return 0.0, 0.0

            uncovered = margin_info.get('starting_margin', 0)
            liquid = margin_info.get('liquid_portfolio', 0)

            return uncovered, liquid

        except Exception as e:
            debug(f"Ошибка получения непокрытых позиций: {e}")
            return 0.0, 0.0

    def check_uncovered_positions_before_clearing(self) -> bool:
        """
        Проверка непокрытых позиций перед клирингом (18:45 МСК)

        Returns:
            bool: True если есть непокрытые позиции, требующие внимания
        """
        from trading_bot.utils.time_utils import get_moscow_time
        from datetime import time as dt_time

        now = get_moscow_time()
        current_time = now.time()
        clearing_time = dt_time(18, 45)

        # Проверяем только если до клиринга меньше 30 минут
        if current_time < clearing_time:
            minutes_left = (clearing_time.hour * 60 + clearing_time.minute) - (
                    current_time.hour * 60 + current_time.minute)
        else:
            return False

        if 0 < minutes_left <= 30:
            uncovered, liquid = self.get_uncovered_amount()

            if uncovered > 5000:  # Только если есть комиссия
                daily_fee = self.calculate_overnight_fee(uncovered, 1)

                warning(f"⚠️ До клиринга {minutes_left:.0f} мин!")
                warning(f"   Непокрытая позиция: {uncovered:.0f}₽")
                warning(f"   Ликвидный портфель: {liquid:.0f}₽")
                warning(f"   Плата за перенос: {daily_fee:.0f}₽/день")

                # Отправляем в Telegram
                telegram = _get_telegram()
                if telegram:
                    if uncovered <= 5000:
                        message = f"ℹ️ До клиринга {minutes_left:.0f} мин.\nНепокрытая позиция: {uncovered:.0f}₽ (бесплатно)"
                    else:
                        message = (
                            f"⚠️ **ВНИМАНИЕ!**\n"
                            f"До клиринга {minutes_left:.0f} минут!\n\n"
                            f"📊 Непокрытая позиция: {uncovered:.0f}₽\n"
                            f"💰 Комиссия за перенос: {daily_fee:.0f}₽/день\n\n"
                            f"Рекомендуем закрыть позиции до 18:45 МСК!"
                        )
                    telegram.send_message(message)

                return True

        return False

    def close_position_smart(self, position, max_attempts: int = 3) -> bool:
        """
        УМНОЕ ЗАКРЫТИЕ ПОЗИЦИИ с анализом стакана и ПРОВЕРКОЙ СРЕДСТВ
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, success, error, warning, debug
        import time

        self.sync_and_cleanup()

        # ========== 1. ПОЛУЧАЕМ ДАННЫЕ О ПОЗИЦИИ ==========
        if hasattr(position, 'figi'):
            figi = position.figi
            quantity = position.quantity
            ticker = getattr(position, 'ticker', figi[:8])
            side = position.side.value  # LONG или SHORT
            direction = "SELL" if side == "LONG" else "BUY"
            avg_price = position.avg_price

            if hasattr(position, 'blocked') and position.blocked:
                error(f"❌ НЕВОЗМОЖНО ЗАКРЫТЬ {ticker}: позиция ЗАБЛОКИРОВАНА!")
                return False
        else:
            figi = position.get('figi')
            quantity = abs(position.get('quantity', 0))
            ticker = position.get('ticker', figi[:8])
            side = "LONG" if position.get('quantity', 0) > 0 else "SHORT"
            direction = "SELL" if side == "LONG" else "BUY"
            avg_price = position.get('avg_price', 0)

        if quantity == 0:
            warning(f"⚠️ Нулевое количество для {ticker}, пропускаем")
            return False

        print(f"\n{'🔍' * 40}")
        print(f"🔍 УМНОЕ ЗАКРЫТИЕ ПОЗИЦИИ: {ticker}")
        print(f"   Сторона: {side}")
        print(f"   Направление: {direction}")
        print(f"   Количество: {quantity} шт")
        print(f"   Средняя цена: {avg_price:.2f}₽" if avg_price else "")
        print(f"{'🔍' * 40}")

        # ========== 2. ПОЛУЧАЕМ ТЕКУЩУЮ ЦЕНУ ==========
        current_price = tbank.get_current_price(figi)
        if not current_price:
            error(f"❌ Не удалось получить текущую цену для {ticker}")
            return False

        print(f"\n💰 Текущая цена: {current_price:.2f}₽")

        # ========== 3. ПРОВЕРКА СРЕДСТВ ДЛЯ SHORT ПОЗИЦИИ ==========
        if side == "SHORT":
            needed_funds = quantity * current_price * 1.05  # +5% запас
            available, total_capital, _ = tbank.get_available_funds()

            print(f"\n💰 ПРОВЕРКА СРЕДСТВ ДЛЯ ЗАКРЫТИЯ SHORT:")
            print(f"   Нужно для выкупа: {needed_funds:.2f}₽")
            print(f"   Доступно средств: {available:.2f}₽")
            print(f"   Капитал: {total_capital:.2f}₽")

            if available < needed_funds:
                error(f"\n❌ НЕДОСТАТОЧНО СРЕДСТВ ДЛЯ ЗАКРЫТИЯ SHORT {ticker}!")
                error(f"   Дефицит: {needed_funds - available:.2f}₽")

                # Пытаемся освободить средства, закрыв убыточные LONG
                print(f"\n🔄 ПОПЫТКА ОСВОБОДИТЬ СРЕДСТВА...")
                freed = self._try_free_funds_for_short(needed_funds - available, ticker)

                if freed > 0:
                    time.sleep(2)
                    available, _, _ = tbank.get_available_funds()
                    print(f"   💰 После освобождения доступно: {available:.2f}₽")

                    if available >= needed_funds:
                        success(f"   ✅ Средств достаточно после освобождения!")
                    else:
                        error(f"   ❌ Всё ещё недостаточно средств!")
                        return False
                else:
                    error(f"   ❌ Не удалось освободить средства!")

                    # Отправляем Telegram уведомление
                    try:
                        telegram = _get_telegram()
                        if telegram:
                            telegram.send_error(
                                f"🚨 **НЕДОСТАТОЧНО СРЕДСТВ!**\n\n"
                                f"Тикер: {ticker} (SHORT)\n"
                                f"Нужно для выкупа: {needed_funds:.0f}₽\n"
                                f"Доступно: {available:.0f}₽\n"
                                f"Дефицит: {needed_funds - available:.0f}₽\n\n"
                                f"⚠️ Позиция НЕ МОЖЕТ БЫТЬ ЗАКРЫТА!\n"
                                f"💡 Пополните счёт или закройте вручную!"
                            )
                    except Exception as e:
                        debug(f"Ошибка отправки уведомления: {e}")

                    return False

        # ========== 4. ПРОВЕРКА, ЧТО ПОЗИЦИЯ ВООБЩЕ СУЩЕСТВУЕТ ==========
        print(f"\n📋 ПРОВЕРКА СУЩЕСТВОВАНИЯ ПОЗИЦИИ У БРОКЕРА...")
        broker_positions = tbank.get_positions(force_refresh=True)
        position_exists = False
        actual_quantity = 0
        for pos in broker_positions:
            if pos.get('figi') == figi and abs(pos.get('quantity', 0)) > 0:
                position_exists = True
                actual_quantity = abs(pos.get('quantity', 0))
                print(f"   ✅ Позиция существует: {actual_quantity} шт")
                break

        if not position_exists:
            info(f"   ℹ️ Позиция {ticker} уже закрыта у брокера")
            self.remove_position(figi)
            return True

        # Если количество изменилось, используем актуальное
        if actual_quantity != quantity:
            warning(f"   ⚠️ Количество изменилось: {quantity} → {actual_quantity} шт")
            quantity = actual_quantity

        # ========== 5. СТРАТЕГИЯ 1: Анализ стакана (умная заявка) ==========
        print(f"\n📡 СТРАТЕГИЯ 1: Анализ стакана")
        for attempt in range(max_attempts):
            try:
                print(f"   📍 Попытка {attempt + 1}/{max_attempts} (стакан)")
                result = tbank.place_smart_order(figi, quantity, direction)

                if result.get('success'):
                    success(f"\n✅ {ticker} закрыт через анализ стакана!")
                    # Проверяем, что позиция действительно закрыта
                    time.sleep(2)
                    broker_positions = tbank.get_positions(force_refresh=True)
                    still_exists = any(
                        p.get('figi') == figi and abs(p.get('quantity', 0)) > 0 for p in broker_positions)
                    if not still_exists:
                        self.remove_position(figi)
                        return True
                    else:
                        warning(f"   ⚠️ Заявка отправлена, но позиция всё ещё существует")
                else:
                    reason = result.get('reason', 'неизвестная причина')
                    warning(f"   ❌ Не удалось: {reason}")
                    time.sleep(1)
            except Exception as e:
                warning(f"   ❌ Ошибка: {e}")
                time.sleep(1)

        # ========== 6. СТРАТЕГИЯ 2: Основной метод с прогрессивным проскальзыванием ==========
        print(f"\n📡 СТРАТЕГИЯ 2: close_position_with_retry")
        result = tbank.close_position_with_retry(
            figi=figi,
            quantity=quantity,
            direction=direction,
            max_attempts=5,
            emergency_slippage=0.05
        )

        if result.get('success'):
            success(f"\n✅ {ticker} закрыт основным методом!")
            # Проверяем закрытие
            time.sleep(2)
            broker_positions = tbank.get_positions(force_refresh=True)
            still_exists = any(p.get('figi') == figi and abs(p.get('quantity', 0)) > 0 for p in broker_positions)
            if not still_exists:
                self.remove_position(figi)
                return True
            else:
                warning(f"   ⚠️ Заявка отправлена, но позиция всё ещё существует")

        # ========== 7. ВСЁ НЕ УДАЛОСЬ ==========
        error(f"\n❌ НЕ УДАЛОСЬ ЗАКРЫТЬ {ticker} после всех попыток!")
        error(f"   💡 Рекомендация: закройте позицию вручную в приложении Т-Банк")

        # Отправляем Telegram уведомление
        try:
            telegram = _get_telegram()
            if telegram:
                telegram.send_error(
                    f"🚨 **НЕ УДАЛОСЬ ЗАКРЫТЬ ПОЗИЦИЮ!**\n\n"
                    f"📊 {ticker} ({side})\n"
                    f"🔢 Количество: {quantity} шт\n"
                    f"💰 Цена: {current_price:.2f}₽\n\n"
                    f"⚠️ Закройте позицию ВРУЧНУЮ в приложении Т-Банк!"
                )
        except Exception as e:
            debug(f"Ошибка отправки уведомления: {e}")

        return False

    def _try_free_funds_for_short(self, needed_deficit: float, current_ticker: str) -> float:
        """
        ПОПЫТКА ОСВОБОДИТЬ СРЕДСТВА ДЛЯ ЗАКРЫТИЯ SHORT
        Закрывает самые убыточные LONG позиции

        Args:
            needed_deficit: Необходимая сумма (дефицит)
            current_ticker: Тикер текущей позиции (чтобы не закрывать её)

        Returns:
            float: Сумма освобождённых средств
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, success, error, warning
        import time

        info(f"\n🔄 ПОПЫТКА ОСВОБОДИТЬ {needed_deficit:.0f}₽ для закрытия SHORT {current_ticker}")

        freed_funds = 0
        closed_positions = []

        try:
            # Получаем все позиции
            broker_positions = tbank.get_positions(force_refresh=True)

            if not broker_positions:
                info("   📭 Нет позиций для закрытия")
                return 0

            # Находим убыточные LONG позиции
            long_positions = []
            for pos in broker_positions:
                pos_ticker = tbank._get_ticker_by_figi(pos.get('figi')) or pos.get('figi', '')[:8]

                if pos_ticker == current_ticker:
                    continue  # Пропускаем текущий тикер

                if pos.get('quantity', 0) > 0:  # LONG
                    pos_figi = pos.get('figi')
                    pos_qty = abs(pos.get('quantity', 0))
                    pos_avg = pos.get('avg_price', 0)
                    pos_current = tbank.get_current_price(pos_figi)

                    if pos_current:
                        pos_pnl = (pos_current - pos_avg) * pos_qty
                        long_positions.append({
                            'figi': pos_figi,
                            'ticker': pos_ticker,
                            'qty': pos_qty,
                            'pnl': pos_pnl,
                            'pnl_pct': (pos_current - pos_avg) / pos_avg * 100 if pos_avg > 0 else 0,
                            'avg_price': pos_avg,
                            'current_price': pos_current,
                            'value': pos_qty * pos_current
                        })

            if not long_positions:
                info("   📭 Нет LONG позиций для закрытия")
                return 0

            # Сортируем по убытку (самые убыточные первые)
            long_positions.sort(key=lambda x: x['pnl'])

            losing_positions = [p for p in long_positions if p['pnl'] < 0]
            info(f"   📊 Найдено убыточных LONG позиций: {len(losing_positions)}")

            if not losing_positions:
                info("   ✅ Нет убыточных LONG позиций")
                return 0

            # Закрываем убыточные позиции, пока не наберём нужную сумму
            for pos in losing_positions:
                if freed_funds >= needed_deficit:
                    break

                info(f"\n   📍 ЗАКРЫТИЕ УБЫТОЧНОЙ LONG: {pos['ticker']}")
                info(f"      Текущий P&L: {pos['pnl']:+.2f}₽ ({pos['pnl_pct']:+.2f}%)")
                info(f"      Количество: {pos['qty']} шт")
                info(f"      Цена входа: {pos['avg_price']:.2f}₽")
                info(f"      Текущая цена: {pos['current_price']:.2f}₽")
                info(f"      Стоимость позиции: {pos['value']:.2f}₽")

                try:
                    # Отправляем рыночную заявку на продажу
                    result = tbank.sell(pos['figi'], pos['qty'], use_market=True)

                    if result:
                        freed_funds += pos['value']
                        closed_positions.append(pos['ticker'])
                        success(f"      ✅ {pos['ticker']} закрыт, освобождено {pos['value']:.0f}₽")
                        time.sleep(1)  # Пауза между закрытиями
                    else:
                        error(f"      ❌ Не удалось закрыть {pos['ticker']}")

                        # Пробуем лимитную заявку
                        info(f"      🔄 Пробуем лимитную заявку...")
                        limit_price = pos['current_price'] * 0.98  # -2% для быстрого исполнения
                        result2 = tbank.sell(pos['figi'], pos['qty'], price=limit_price)

                        if result2:
                            freed_funds += pos['value']
                            closed_positions.append(pos['ticker'])
                            success(f"      ✅ {pos['ticker']} закрыт лимитной заявкой, освобождено {pos['value']:.0f}₽")
                            time.sleep(1)
                        else:
                            error(f"      ❌ Не удалось закрыть {pos['ticker']} ни одним способом")

                except Exception as e:
                    error(f"      ❌ Ошибка при закрытии {pos['ticker']}: {e}")

            # Итоги освобождения средств
            if closed_positions:
                info(f"\n   {'=' * 50}")
                info(f"   ✅ ОСВОБОЖДЕНИЕ СРЕДСТВ ЗАВЕРШЕНО")
                info(f"   {'=' * 50}")
                info(f"   📊 Освобождено средств: {freed_funds:.0f}₽")
                info(f"   📋 Закрытые позиции: {', '.join(closed_positions)}")
                info(f"   🎯 Требовалось: {needed_deficit:.0f}₽")

                if freed_funds >= needed_deficit:
                    info(f"   ✅ ДЕФИЦИТ ПОКРЫТ!")
                else:
                    info(f"   ⚠️ ПОКРЫТО ТОЛЬКО {freed_funds / needed_deficit * 100:.0f}% дефицита")
            else:
                info(f"\n   ⚠️ НЕ УДАЛОСЬ ОСВОБОДИТЬ СРЕДСТВА")

            return freed_funds

        except Exception as e:
            error(f"   ❌ Ошибка при освобождении средств: {e}")
            import traceback
            error(f"   {traceback.format_exc()}")
            return 0

    def _check_reversal_profit(self, position: Position, current_price: float) -> bool:
        """
        ПРОВЕРКА РАЗВОРОТНЫХ ПАТТЕРНОВ ДЛЯ ФИКСАЦИИ ПРИБЫЛИ

        Возвращает True если нужно закрыть позицию (разворот от поддержки/сопротивления)
        """
        ticker = position.ticker
        profit_pct = position.current_profit_pct(current_price)

        # Разворот срабатывает только при прибыли > 1%
        if profit_pct < 1.0:
            return False

        # Получаем последние 30 свечей
        from trading_bot.api.tbank_client import tbank
        figi = position.figi
        candles = tbank.get_candles(figi, days=3, interval_minutes=5)

        if not candles or len(candles) < 20:
            return False

        # Берём цены закрытия
        prices = [c[0] for c in candles[-30:]]

        # Находим минимум за последние 20 свечей
        recent_min = min(prices[-20:])
        recent_max = max(prices[-20:])

        current_idx = len(prices) - 1

        # Паттерн 1: Отскок от минимума (разворот вверх)
        is_reversal_up = False
        for i in range(max(0, current_idx - 5), current_idx + 1):
            if prices[i] <= recent_min * 1.01:  # цена у минимума
                if current_price > recent_min * 1.02:  # отскочила на 2%+
                    is_reversal_up = True
                    break

        # Паттерн 2: Пик и падение (разворот вниз)
        is_reversal_down = False
        for i in range(max(0, current_idx - 5), current_idx + 1):
            if prices[i] >= recent_max * 0.99:  # цена у максимума
                if current_price < recent_max * 0.98:  # упала на 2%+
                    is_reversal_down = True
                    break

        # Для LONG позиции: при развороте ВНИЗ - фиксируем прибыль
        if position.side == OrderSide.LONG and is_reversal_down:
            info(f"\n🔄 РАЗВОРОТ ВНИЗ ДЛЯ {ticker} (прибыль {profit_pct:.1f}%) — ФИКСИРУЕМ ПРИБЫЛЬ")
            return True

        # Для SHORT позиции: при развороте ВВЕРХ - фиксируем прибыль
        if position.side == OrderSide.SHORT and is_reversal_up:
            info(f"\n🔄 РАЗВОРОТ ВВЕРХ ДЛЯ {ticker} (прибыль {profit_pct:.1f}%) — ФИКСИРУЕМ ПРИБЫЛЬ")
            return True

        # Дополнительная проверка: закрывающая свеча (пин-бар)
        if len(candles) >= 3:
            last = candles[-1]
            prev = candles[-2]

            # Пин-бар вверх (длинная нижняя тень)
            body = abs(last[0] - last[5] if len(last) > 5 else 0)
            lower_shadow = min(last[5] if len(last) > 5 else last[0], last[0]) - (last[4] if len(last) > 4 else last[0])

            if position.side == OrderSide.SHORT and profit_pct > 1.0:
                if lower_shadow > body * 2:
                    info(f"\n🔄 ПИН-БАР ВВЕРХ ДЛЯ {ticker} (прибыль {profit_pct:.1f}%) — ФИКСИРУЕМ ПРИБЫЛЬ")
                    return True

        return False

    def _get_dynamic_take_profit(self, position: Position, current_price: float, atr_pct: float) -> float:
        """
        ДИНАМИЧЕСКИЙ ТЕЙК-ПРОФИТ НА ОСНОВЕ ATR И ВОЛАТИЛЬНОСТИ
        """
        profit_pct = position.current_profit_pct(current_price)

        # Базовая цель
        base_target = position.take_profit_pct

        # Если прибыль уже > 2%, подтягиваем тейк-профит
        if profit_pct > 2.0:
            new_target = profit_pct - 0.5  # фиксируем 0.5% от максимума
            info(f"   📈 {position.ticker}: подтягиваем TP с {base_target}% до {new_target:.1f}%")
            return new_target

        # Корректировка по волатильности
        if atr_pct > 1.5:
            return min(base_target * 1.5, 5.0)  # увеличиваем TP при высокой волатильности
        elif atr_pct < 0.3:
            return max(base_target * 0.5, 0.8)  # уменьшаем TP при низкой волатильности

        return base_target


# ========== Глобальный экземпляр ==========
position_manager = PositionManager()
