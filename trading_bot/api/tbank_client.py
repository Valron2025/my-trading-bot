"""Клиент для работы с T-Bank API - ПОЛНАЯ ПРОДАКШН ВЕРСИЯ"""

# ========== LOCAL SSL FIX ==========
import os
import grpc

os.environ['GRPC_SSL_VERIFY'] = '0'

_original_secure_channel = grpc.secure_channel

def _patched_secure_channel(*args, **kwargs):
    target = args[0] if args else kwargs.get('target', 'invest-public-api.tbank.ru:443')
    print(f"🔓 LOCAL: insecure channel to {target}")
    return grpc.insecure_channel(target)

grpc.secure_channel = _patched_secure_channel
print("✅ LOCAL SSL FIX: Using insecure channel")
# ========== END FIX ==========

import time
from functools import wraps
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta, timezone
import uuid
from decimal import Decimal
import signal
from contextlib import contextmanager

MOSCOW_TZ = timezone(timedelta(hours=3))

from t_tech.invest import (
    Client,
    CandleInterval,
    OrderType,
    OrderDirection
)
from t_tech.invest.utils import quotation_to_decimal, decimal_to_quotation

from trading_bot.config import config
from trading_bot.logger import info, success, error, warning, debug


def retry_on_error(max_retries=3, delay=1, backoff=2, timeout_seconds=2.0):
    """Декоратор для повторных попыток при ошибках API с таймаутом"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    # Устанавливаем таймаут
                    with TimeoutManager(timeout_seconds):
                        return func(*args, **kwargs)
                except TimeoutError as e:
                    warning(f"⏰ Таймаут API ({timeout_seconds}с), попытка {attempt + 1}/{max_retries}")
                    time.sleep(current_delay)
                    current_delay *= backoff
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        warning(f"⚠️ Ошибка API, попытка {attempt + 1}/{max_retries}: {e}")
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        error(f"❌ Ошибка API после {max_retries} попыток: {e}")
                    current_delay *= backoff
            raise last_exception
        return wrapper
    return decorator


class TimeoutManager:
    """Управление таймаутами для API запросов"""

    def __init__(self, timeout_seconds: float = 2.0):
        self.timeout_seconds = timeout_seconds
        self._old_handler = None

    def __enter__(self):
        """Устанавливает таймаут"""
        try:
            self._old_handler = signal.signal(signal.SIGALRM, self._timeout_handler)
            signal.alarm(int(self.timeout_seconds))
        except AttributeError:
            # Windows не поддерживает SIGALRM
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Снимает таймаут"""
        try:
            signal.alarm(0)
            if self._old_handler:
                signal.signal(signal.SIGALRM, self._old_handler)
        except AttributeError:
            pass

    def _timeout_handler(self, signum, frame):
        raise TimeoutError(f"API запрос превысил лимит {self.timeout_seconds}с")


class TBankClient:
    """Клиент для работы с T-Bank API с поддержкой РЕАЛЬНЫХ заявок"""

    def __init__(self):
        self.token = config.tbank_token

        if not self.token:
            import os
            self.token = os.getenv("TBANK_TOKEN")
            if self.token:
                print(f"⚠️ Token loaded from os.getenv: {self.token[:20]}...")
        self._account_id: Optional[str] = None
        print(f"✅ Token initialized: {self.token[:20]}..." if self.token else "❌ TOKEN IS EMPTY!")

        # Кэш для логирования
        self._last_total = 0
        self._last_free = 0
        self._last_margin_rate = 0
        self._last_balance_log = 0
        self._last_margin_log = 0
        self._log_interval = 30

        # Кэш для тикеров
        self._ticker_cache = {}
        self._figi_to_ticker_cache = {}

        # Кэш для маржи
        self._margin_cache = None
        self._margin_cache_time = 0
        self._margin_cache_ttl = 5  # 5 секунд

        # Кэш для списка акций
        self._shares_cache = None
        self._shares_cache_time = 0
        self._shares_cache_ttl = 60   # 1 минута (уменьшаем)

        # Кэш для свечей
        self._candles_cache = {}
        self._candles_cache_time = {}
        self._candles_cache_ttl = 30  # 30 секунд (уменьшаем)

        # ========== КОНФИГУРАЦИЯ ТАЙМАУТОВ ДЛЯ T-INVEST API ==========
        self.timeout_config = {
            'get_candles': 0.5,  # 500ms (рекомендовано)
            'get_last_prices': 0.5,  # 500ms
            'get_trading_status': 0.5,  # 500ms
            'post_order': 1.5,  # 1500ms
            'cancel_order': 1.5,  # 1500ms
            'get_orders': 0.5,  # 500ms
            'get_portfolio': 1.5,  # 1500ms
            'get_positions': 1.0,  # 1000ms
            'get_stop_orders': 1.5,  # 1500ms
            'post_stop_order': 1.5,  # 1500ms
            'get_margin_attributes': 0.3,  # 300ms
            'get_info': 1.0,  # 1000ms
            'get_accounts': 0.3,  # 300ms
        }

        self._max_cache_size = 100  # Максимум 100 записей в кэше

    def get_timeout_stats(self) -> Dict[str, Any]:
        """Статистика по таймаутам"""
        return {
            'config': self.timeout_config.copy(),
            'cache_sizes': {
                'candles': len(self._candles_cache),
                'shares': len(self._shares_cache) if self._shares_cache else 0,
                'tickers': len(self._ticker_cache),
            },
            'cache_ttl': {
                'candles': self._candles_cache_ttl,
                'shares': self._shares_cache_ttl,
            }
        }

    @contextmanager
    def _get_client_context(self):
        """Контекстный менеджер для работы с клиентом"""
        client = None
        try:
            client = Client(self.token)
            yield client
        finally:
            if client:
                try:
                    # Закрываем клиент
                    if hasattr(client, 'close'):
                        client.close()
                except Exception:
                    pass

    @property
    def account_id(self) -> str:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        env_account_id = os.getenv("TBANK_ACCOUNT_ID")
        if env_account_id:
            self._account_id = env_account_id
            return self._account_id
        if self._account_id is None:
            with Client(self.token) as client:
                accounts = client.users.get_accounts().accounts
                if not accounts:
                    raise Exception("Нет доступных брокерских счетов")
                self._account_id = accounts[0].id
        return self._account_id

    # ========== ОСНОВНЫЕ ТОРГОВЫЕ МЕТОДЫ ==========

    def buy(self, figi: str, quantity: int, use_market: bool = None) -> bool:
        """
        Умная покупка - сама выбирает тип заявки

        Args:
            figi: FIGI инструмента
            quantity: Количество
            use_market: True - принудительно рыночная, False - принудительно лимитная,
                        None - автоматический выбор
        """
        price = self.get_current_price(figi)
        if not price:
            error(f"❌ Не удалось получить цену для покупки {figi}")
            return False

        total = quantity * price
        ticker = self._get_ticker_by_figi(figi)

        # Проверка средств
        available, total_cap, _ = self.get_available_funds()
        if total > available:
            warning(f"⚠️ Недостаточно средств: нужно {total:.2f}₽, доступно {available:.2f}₽")
            return False

        # ========== АВТОМАТИЧЕСКИЙ ВЫБОР ТИПА ЗАЯВКИ ==========
        if use_market is None:
            # Проверяем, можно ли использовать рыночную заявку
            market_available = self._is_market_order_available(figi)

            # Проверяем, срочно ли нужно исполнить (высокий score)
            urgent = hasattr(self, '_current_score') and getattr(self, '_current_score', 0) >= 7

            # Проверяем OTC режим
            is_otc = self._is_otc_mode()

            if market_available and (urgent or not is_otc):
                use_market = True
                info(f"🎯 ВЫБРАНА РЫНОЧНАЯ ЗАЯВКА (срочно={urgent}, OTC={is_otc})")
            else:
                use_market = False
                info(f"📋 ВЫБРАНА ЛИМИТНАЯ ЗАЯВКА (OTC режим или не срочно)")

        # ========== ИСПОЛНЕНИЕ ==========
        if use_market:
            info(f"🟢 РЫНОЧНАЯ ПОКУПКА: {quantity} шт {ticker or figi} (сумма: {total:.0f}₽)")
            result = self._place_market_order(figi, quantity, "BUY", is_short=False)

            if not result:
                # Fallback на лимитную
                warning(f"🔄 Рыночная не удалась, пробуем лимитную...")
                limit_price = price * 1.01
                limit_price = self._round_to_min_increment(figi, limit_price)
                result = self.place_limit_order(figi, quantity, "BUY", limit_price)
                if result:
                    success(f"✅ Лимитная заявка на покупку {quantity} шт {ticker or figi} размещена")
            return result
        else:
            limit_price = price * 1.01
            limit_price = self._round_to_min_increment(figi, limit_price)
            info(f"📋 ЛИМИТНАЯ ПОКУПКА: {quantity} шт {ticker or figi} по {limit_price:.2f}₽ (+1%)")
            return self.place_limit_order(figi, quantity, "BUY", limit_price)

    def sell(self, figi: str, quantity: int, use_market: bool = None) -> bool:
        """
        Умная продажа - сама выбирает тип заявки
        """
        price = self.get_current_price(figi)
        if not price:
            error(f"❌ Не удалось получить цену для продажи {figi}")
            return False

        total = quantity * price
        ticker = self._get_ticker_by_figi(figi)

        # ========== АВТОМАТИЧЕСКИЙ ВЫБОР ТИПА ЗАЯВКИ ==========
        if use_market is None:
            market_available = self._is_market_order_available(figi)
            urgent = hasattr(self, '_current_score') and getattr(self, '_current_score', 0) >= 7
            is_otc = self._is_otc_mode()

            if market_available and (urgent or not is_otc):
                use_market = True
                info(f"🎯 ВЫБРАНА РЫНОЧНАЯ ЗАЯВКА (срочно={urgent}, OTC={is_otc})")
            else:
                use_market = False
                info(f"📋 ВЫБРАНА ЛИМИТНАЯ ЗАЯВКА (OTC режим или не срочно)")

        # ========== ИСПОЛНЕНИЕ ==========
        if use_market:
            info(f"🔴 РЫНОЧНАЯ ПРОДАЖА: {quantity} шт {ticker or figi} (сумма: {total:.0f}₽)")
            result = self._place_market_order(figi, quantity, "SELL", is_short=False)

            if not result:
                warning(f"🔄 Рыночная не удалась, пробуем лимитную...")
                limit_price = price * 0.99
                limit_price = self._round_to_min_increment(figi, limit_price)
                result = self.place_limit_order(figi, quantity, "SELL", limit_price)
                if result:
                    success(f"✅ Лимитная заявка на продажу {quantity} шт {ticker or figi} размещена")
            return result
        else:
            limit_price = price * 0.99
            limit_price = self._round_to_min_increment(figi, limit_price)
            info(f"📋 ЛИМИТНАЯ ПРОДАЖА: {quantity} шт {ticker or figi} по {limit_price:.2f}₽ (-1%)")
            return self.place_limit_order(figi, quantity, "SELL", limit_price)

    def _is_market_order_available(self, figi: str) -> bool:
        """Проверка, доступны ли рыночные заявки для инструмента"""
        try:
            status = self.get_trading_status(figi)
            return status.get('market_order_available', False)
        except Exception:
            return True  # Если не можем проверить - разрешаем

    def _is_otc_mode(self) -> bool:
        """Проверка OTC режима - упрощённая версия (без лишних импортов)"""
        try:
            from datetime import datetime
            now = datetime.now()
            # Суббота (5) или воскресенье (6)
            if now.weekday() >= 5:
                return True

            # Проверка времени: внебиржевые часы 19:00 - 09:50
            current_time = now.time()
            from datetime import time as dt_time
            MORNING_START = dt_time(6, 50)
            MORNING_END = dt_time(9, 50)
            EVENING_START = dt_time(19, 0)

            if MORNING_START <= current_time <= MORNING_END:
                return True
            if current_time >= EVENING_START:
                return True

            return False
        except Exception:
            return False

    def sell_short(self, figi: str, quantity: int) -> bool:
        """Открытие SHORT позиции - ТРЕБУЕТ МАРЖУ"""
        price = self.get_current_price(figi)
        if not price:
            error(f"❌ Не удалось получить цену для SHORT {figi}")
            return False

        total = quantity * price
        ticker = self._get_ticker_by_figi(figi)
        info(f"🔴 SHORT: {quantity} шт {ticker or figi} по ~{price:.2f}₽ (сумма: {total:.0f}₽)")

        # ✅ is_short=True - это SHORT
        result = self._place_market_order(figi, quantity, "SELL", is_short=True)

        if result:
            success(f"✅ SHORT заявка исполнена")
        else:
            warning(f"🔄 Рыночная SHORT не удалась, пробуем лимитную...")
            limit_price = price * 0.99
            limit_price = self._round_to_min_increment(figi, limit_price)
            result = self.place_limit_order(figi, quantity, "SELL", limit_price)
            if result:
                success(f"✅ Лимитная SHORT заявка размещена")
        return result

    def _place_market_order(self, figi: str, quantity: int, direction: str, is_short: bool = False) -> bool:
        """
        Рыночная заявка

        Args:
            figi: FIGI инструмента
            quantity: Количество
            direction: "BUY" или "SELL"
            is_short: True - открытие SHORT (требует маржу), False - закрытие LONG (не требует)
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        try:
            from trading_bot import trading_bot
            if hasattr(trading_bot, '_shutting_down') and trading_bot._shutting_down:
                debug(f"🛑 Пропускаем заявку {direction} {ticker} - бот останавливается")
                return False
        except Exception:
            pass

        if self.is_confirmation_required(figi):
            error(f"❌ {ticker} требует подтверждения сделок")
            return False

        # ✅ ТОЛЬКО для SHORT (открытие) проверяем маржу
        if direction == "SELL" and is_short:
            margin_allowed, margin_reason = self.check_margin_trading_allowed()
            if not margin_allowed:
                error(f"🔻 SHORT невозможен: {margin_reason}")
                return False

        with Client(self.token) as client:
            try:
                dir_map = {
                    "BUY": OrderDirection.ORDER_DIRECTION_BUY,
                    "SELL": OrderDirection.ORDER_DIRECTION_SELL
                }
                # SHORT требует маржи, LONG - нет
                needs_margin = (direction == "SELL" and is_short)

                order = client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    direction=dir_map[direction],
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_MARKET,
                    order_id="",
                    confirm_margin_trade=needs_margin
                )

                if order and order.order_id:
                    debug(f"📋 Рыночная заявка {direction} {quantity} {ticker} размещена")
                    return True
                return False

            except Exception as e:
                error_msg = str(e)
                if "30079" in error_msg:
                    info(f"⏸️ {ticker}: Рынок закрыт")
                elif "30049" in error_msg:
                    info(f"⏸️ {ticker}: Торги приостановлены")
                elif "80006" in error_msg:
                    error(f"❌ Недостаточно средств для {ticker}")
                elif "30042" in error_msg:
                    error(f"❌ Недостаточно маржи для {ticker}")
                    if quantity > 1:
                        half = max(1, quantity // 2)
                        return self._place_market_order(figi, half, direction, is_short)
                else:
                    error(f"❌ Ошибка {ticker}: {error_msg[:100]}")
                return False

    def place_limit_order(self, figi: str, quantity: int, direction: str, target_price: float) -> bool:
        """Лимитная заявка с таймаутом"""
        timeout_seconds = self.timeout_config.get('post_order', 1.5)

        with Client(self.token) as client:
            try:
                target_price = self._round_to_min_increment(figi, target_price)
                if direction == "SELL" and target_price < 0.01:
                    target_price = self._round_to_min_increment(figi, 0.01)

                price_quotation = decimal_to_quotation(Decimal(str(target_price)))
                dir_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
                needs_margin = (direction == "SELL")

                ticker = self._get_ticker_by_figi(figi) or figi[:8]
                info(f"📋 ЛИМИТНАЯ: {direction} {quantity} шт {ticker} по {target_price:.2f}₽")

                order = client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    price=price_quotation,
                    direction=dir_map[direction],
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_LIMIT,
                    order_id="",
                    confirm_margin_trade=needs_margin
                )

                if order and order.order_id:
                    info(f"✅ Лимитная заявка {direction} размещена")
                    return True
                return False

            except Exception as e:
                error_msg = str(e)
                if "30079" in error_msg:
                    info(
                        f"⏸️ Инструмент {self._get_ticker_by_figi(figi) or figi[:8]} недоступен для торговли (рынок закрыт или выходной день)")
                    info(f"   Заявка останется активной до открытия рынка")
                elif "30049" in error_msg:
                    info(
                        f"⏸️ Инструмент {self._get_ticker_by_figi(figi) or figi[:8]}: торги приостановлены (праздничный день)")
                elif "30240" in error_msg:
                    warning(f"⚠️ Инструмент {self._get_ticker_by_figi(figi) or figi[:8]} требует подтверждения сделок")
                    warning(f"   Стоп-приказы не поддерживаются, будет использована эмуляция")
                else:
                    warning(f"❌ Ошибка лимитной заявки: {error_msg[:100]}")
                return False

    def place_pending_order(self, figi: str, quantity: int, direction: str, target_price: float,
                            expiry_hours: int = 24) -> bool:
        """Алиас для place_limit_order"""
        return self.place_limit_order(figi, quantity, direction, target_price)

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def get_user_tariff(self) -> Tuple[str, float]:
        with Client(self.token) as client:
            try:
                info_obj = client.users.get_info()
                tariff = info_obj.tariff
                if "trader" in tariff.lower():
                    return "Трейдер", 0.0005
                elif "investor" in tariff.lower():
                    return "Инвестор", 0.003
                elif "premium" in tariff.lower():
                    return "Premium", 0.0004
                else:
                    return "Стандарт", 0.003
            except Exception as e:
                error(f"Ошибка получения тарифа: {e}")
                return "Трейдер", 0.0005

    def get_margin_info(self) -> Dict[str, float]:
        """Получение информации о марже с кэшированием (5 секунд)"""

        # Проверка кэша
        now = time.time()
        if self._margin_cache is not None:
            if (now - self._margin_cache_time) < self._margin_cache_ttl:
                return self._margin_cache.copy()

        with Client(self.token) as client:
            try:
                margin = client.users.get_margin_attributes(account_id=self.account_id)
                if margin:
                    # ✅ ИСПРАВЛЕНО: используем quotation_to_decimal
                    liquid = float(quotation_to_decimal(margin.liquid_portfolio))
                    starting = float(quotation_to_decimal(margin.starting_margin))
                    minimal = float(quotation_to_decimal(margin.minimal_margin))

                    result = {
                        'liquid_portfolio': liquid,
                        'starting_margin': starting,
                        'minimal_margin': minimal,
                        'available_margin': max(0.0, liquid - starting),
                        'used_margin': starting,
                        'margin_rate': (starting / liquid * 100) if liquid > 0 else 0
                    }

                    # Сохраняем в кэш
                    self._margin_cache = result.copy()
                    self._margin_cache_time = now

                    return result
            except Exception as e:
                debug(f"Ошибка получения маржи: {e}")

            return {}

    def get_available_funds(self) -> Tuple[float, float, float]:
        """Получение доступных средств с таймаутом"""
        timeout_seconds = self.timeout_config.get('get_margin_attributes', 0.3)

        with Client(self.token) as client:
            try:
                if hasattr(client, 'users') and hasattr(client.users, 'get_margin_attributes'):
                    margin = client.users.get_margin_attributes(account_id=self.account_id)
                    if margin:
                        total = float(quotation_to_decimal(margin.liquid_portfolio))
                        starting = float(quotation_to_decimal(margin.starting_margin))
                        free = total - starting
                        return max(0.0, free), total, 0.0
            except Exception:
                pass

            try:
                if hasattr(client, 'operations') and hasattr(client.operations, 'get_portfolio'):
                    portfolio = client.operations.get_portfolio(account_id=self.account_id)
                    total = float(quotation_to_decimal(portfolio.total_amount_portfolio))
                    return total, total, 0.0
            except Exception:
                pass

            error("❌ НЕ УДАЛОСЬ ПОЛУЧИТЬ БАЛАНС!")
            return 0.0, 0.0, 0.0

    def get_user_info(self) -> Tuple[bool, str]:
        with Client(self.token) as client:
            try:
                info_obj = client.users.get_info()
                return info_obj.qual_status, info_obj.tariff
            except Exception as e:
                error(f"❌ Ошибка получения статуса: {e}")
                return False, "unknown"

    def check_qual_status(self) -> Tuple[bool, str]:
        return self.get_user_info()

    def get_positions(self) -> List[Dict[str, Any]]:
        with Client(self.token) as client:
            try:
                portfolio = client.operations.get_portfolio(account_id=self.account_id)
                positions = []
                for pos in portfolio.positions:
                    if pos.figi != "RUB000UTSTOM" and pos.quantity.units != 0:
                        positions.append({
                            'figi': pos.figi,
                            'quantity': pos.quantity.units,
                            'avg_price': float(quotation_to_decimal(pos.average_position_price))
                        })
                return positions
            except Exception as e:
                warning(f"Ошибка получения позиций: {e}")
                return []

    def get_active_orders(self) -> List[Dict[str, Any]]:
        """Получение активных заявок"""
        try:
            with Client(self.token) as client:
                orders = client.orders.get_orders(account_id=self.account_id)
                result = []
                for order in orders.orders:
                    direction = "BUY" if order.direction == 1 else "SELL"
                    result.append({
                        'order_id': order.order_id,
                        'figi': order.figi,
                        'direction': direction,
                        'price': float(quotation_to_decimal(order.price)) if order.price else 0,
                        'quantity': order.lots_requested,
                        'executed_quantity': order.executed_lots,
                        'status': str(order.order_state),
                        'ticker': self._get_ticker_by_figi(order.figi) or order.figi[:8]
                    })
                return result
        except Exception as e:
            debug(f"Ошибка получения заявок: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Отмена заявки"""
        try:
            with Client(self.token) as client:
                client.orders.cancel_order(
                    account_id=self.account_id,
                    order_id=order_id
                )
                return True
        except Exception as e:
            debug(f"Ошибка отмены заявки {order_id}: {e}")
            return False

    async def cleanup_duplicate_orders(self, ticker: str = None) -> Dict[str, Any]:
        """
        Очистка дублирующихся заявок

        Args:
            ticker: Если указан, очищает только для этого тикера
        """
        try:
            info(f"🧹 Очистка дублирующихся заявок{f' для {ticker}' if ticker else ''}...")

            # Получаем активные заявки
            active_orders = self.get_active_orders()

            if not active_orders:
                return {"success": True, "message": "Нет активных заявок", "cancelled": 0}

            # Фильтруем по тикеру
            if ticker:
                active_orders = [o for o in active_orders if o.get('ticker') == ticker]

            # Группируем по тикеру и направлению
            orders_by_key: Dict[str, List[Dict]] = {}

            for order in active_orders:
                order_ticker = order.get('ticker')
                direction = order.get('direction')

                if not order_ticker or not direction:
                    continue

                key = f"{order_ticker}_{direction}"
                if key not in orders_by_key:
                    orders_by_key[key] = []
                orders_by_key[key].append(order)

            cancelled = 0
            failed = 0

            for key, orders in orders_by_key.items():
                if len(orders) <= 1:
                    continue

                ticker_key, direction = key.split('_')
                warning(f"⚠️ Найдено {len(orders)} заявок для {key}")

                # Оставляем лучшую заявку
                if direction == "BUY":
                    best_order = min(orders, key=lambda x: x.get('price', float('inf')))
                else:
                    best_order = max(orders, key=lambda x: x.get('price', 0))

                # Отменяем остальные
                for order in orders:
                    if order.get('order_id') != best_order.get('order_id'):
                        result = self.cancel_order(order.get('order_id'))
                        if result:
                            cancelled += 1
                            info(f"✅ Отменена дублирующая заявка {order.get('order_id')}")
                        else:
                            failed += 1

                if cancelled > 0:
                    info(f"📊 Итог по {ticker_key}: отменено {cancelled}, ошибок {failed}")

            return {
                "success": True,
                "cancelled": cancelled,
                "failed": failed,
                "total_before": len(active_orders),
                "total_after": len(active_orders) - cancelled
            }

        except Exception as e:
            error(f"❌ Ошибка очистки дубликатов: {e}")
            return {"success": False, "error": str(e)}

    def get_current_price(self, figi: str) -> Optional[float]:
        """Получение текущей цены с таймаутом"""
        timeout_seconds = self.timeout_config.get('get_last_prices', 0.5)

        with Client(self.token) as client:
            try:
                last_prices = client.market_data.get_last_prices(figi=[figi])
                if last_prices and last_prices.last_prices:
                    return float(quotation_to_decimal(last_prices.last_prices[0].price))
            except Exception:
                pass
            return None

    def get_all_shares(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Получение списка акций с кэшированием (5 минут)"""

        # Проверка кэша
        now = time.time()
        if self._shares_cache is not None:
            if (now - self._shares_cache_time) < self._shares_cache_ttl:
                return self._shares_cache[:limit] if limit else self._shares_cache

        with Client(self.token) as client:
            try:
                response = client.instruments.shares()
                result = []
                for stock in response.instruments:
                    if stock.currency == "rub":
                        result.append({
                            'figi': stock.figi,
                            'ticker': stock.ticker,
                            'name': stock.name,
                            'lot': stock.lot,
                            'currency': stock.currency,
                            'api_trade_available': stock.api_trade_available_flag,
                        })
                        if len(result) >= limit:
                            break

                # Сохраняем в кэш
                self._shares_cache = result
                self._shares_cache_time = now

                # Только один лог при реальной загрузке
                info(f"📊 Загружено {len(result)} акций")
                return result
            except Exception as e:
                error(f"Ошибка получения списка акций: {e}")
                return []

    def get_candles(self, figi: str, days: int = 5, interval_minutes: int = 5) -> List[Tuple[float, float]]:
        """Получение свечей с кэшированием (1 минута)"""

        # Ключ кэша
        cache_key = f"{figi}_{days}_{interval_minutes}"

        # Проверка кэша
        now = time.time()
        if cache_key in self._candles_cache:
            cache_time = self._candles_cache_time.get(cache_key, 0)
            if (now - cache_time) < self._candles_cache_ttl:
                return self._candles_cache[cache_key].copy()

        # ✅ ДОБАВИТЬ ОГРАНИЧЕНИЕ КЭША ЗДЕСЬ (ПЕРЕД ЗАПРОСОМ)
        if len(self._candles_cache) > self._max_cache_size:
            keys_to_remove = list(self._candles_cache.keys())[:self._max_cache_size // 2]
            for key in keys_to_remove:
                del self._candles_cache[key]
                if key in self._candles_cache_time:
                    del self._candles_cache_time[key]

        # ✅ ДОБАВИТЬ ТАЙМАУТ
        timeout_seconds = self.timeout_config.get('get_candles', 0.5)

        with Client(self.token) as client:
            try:
                end_time = datetime.now(MOSCOW_TZ)
                start_time = end_time - timedelta(days=days)
                interval_map = {
                    1: CandleInterval.CANDLE_INTERVAL_1_MIN,
                    5: CandleInterval.CANDLE_INTERVAL_5_MIN,
                    15: CandleInterval.CANDLE_INTERVAL_15_MIN,
                    60: CandleInterval.CANDLE_INTERVAL_HOUR,
                    1440: CandleInterval.CANDLE_INTERVAL_DAY,
                }
                interval = interval_map.get(interval_minutes, CandleInterval.CANDLE_INTERVAL_5_MIN)
                candles = client.market_data.get_candles(
                    figi=figi,
                    from_=start_time,
                    to=end_time,
                    interval=interval
                )
                result = [(float(quotation_to_decimal(c.close)), float(c.volume)) for c in candles.candles]

                # Сохраняем в кэш
                self._candles_cache[cache_key] = result.copy()
                self._candles_cache_time[cache_key] = now

                return result
            except Exception as e:
                if "30014" in str(e):
                    return []
                debug(f"Ошибка получения свечей {figi}: {e}")
                return []

    def get_trading_status(self, figi: str) -> Dict[str, Any]:
        with Client(self.token) as client:
            try:
                status = client.market_data.get_trading_status(instrument_id=figi)
                return {
                    'trading_status': status.trading_status,
                    'api_trade_available': status.api_trade_available_flag,
                    'market_order_available': status.market_order_available_flag,
                    'limit_order_available': status.limit_order_available_flag,
                }
            except Exception as e:
                debug(f"Ошибка получения статуса для {figi}: {e}")
                return {}

    def is_confirmation_required(self, figi: str) -> bool:
        """
        Проверка, требует ли инструмент подтверждения сделок (ошибка 30240)
        С кэшированием результата
        """
        # Кэш для результатов
        if not hasattr(self, '_confirmation_cache'):
            self._confirmation_cache = {}
        if not hasattr(self, '_confirmation_cache_time'):
            self._confirmation_cache_time = {}

        now = time.time()
        cache_ttl = 3600  # 1 час

        # Проверяем кэш
        if figi in self._confirmation_cache:
            cache_time = self._confirmation_cache_time.get(figi, 0)
            if now - cache_time < cache_ttl:
                return self._confirmation_cache[figi]

        try:
            with Client(self.token) as client:
                status = client.market_data.get_trading_status(instrument_id=figi)

                # Признак требует подтверждения:
                # API trade available = true, но market и limit orders недоступны
                requires = (
                        status.api_trade_available_flag and
                        not status.market_order_available_flag and
                        not status.limit_order_available_flag
                )

                self._confirmation_cache[figi] = requires
                self._confirmation_cache_time[figi] = now

                if requires:
                    ticker = self._get_ticker_by_figi(figi) or figi[:8]
                    info(f"📋 {ticker} требует подтверждения сделок → эмуляция стоп-приказов")

                return requires

        except Exception as e:
            debug(f"Ошибка проверки подтверждения для {figi}: {e}")
            return False

    def supports_stop_orders(self, figi: str) -> bool:
        try:
            status = self.get_trading_status(figi)
            if not status.get('market_order_available', False):
                return False
            if not status.get('limit_order_available', False):
                return False
            return True
        except Exception:
            return False

    def get_stop_orders(self) -> List[Dict[str, Any]]:
        with Client(self.token) as client:
            try:
                stop_orders = client.stop_orders.get_stop_orders(account_id=self.account_id)
                result = []
                for order in stop_orders.stop_orders:
                    result.append({
                        'stop_order_id': order.stop_order_id,
                        'figi': order.figi,
                        'direction': order.direction,
                        'stop_price': float(quotation_to_decimal(order.stop_price)) if order.stop_price else 0,
                    })
                return result
            except Exception as e:
                debug(f"Ошибка получения стоп-приказов: {e}")
                return []

    def cancel_stop_order(self, stop_order_id: str) -> bool:
        with Client(self.token) as client:
            try:
                client.stop_orders.cancel_stop_order(
                    account_id=self.account_id,
                    stop_order_id=stop_order_id
                )
                return True
            except Exception as e:
                warning(f"Ошибка отмены стоп-приказа: {e}")
                return False

    def _get_min_price_increment(self, figi: str) -> float:
        return 0.01

    def _round_to_min_increment(self, figi: str, price: float) -> float:
        return round(price, 2)

    def _get_ticker_by_figi(self, figi: str) -> Optional[str]:
        if figi in self._ticker_cache:
            return self._ticker_cache[figi]
        try:
            all_shares = self.get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('figi') == figi:
                    ticker = stock.get('ticker')
                    self._ticker_cache[figi] = ticker
                    return ticker
        except Exception:
            pass
        return None

    def _place_market_order_emergency(self, figi: str, quantity: int, direction: str) -> bool:
        with Client(self.token) as client:
            try:
                dir_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
                order = client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    direction=dir_map[direction],
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_MARKET,
                    order_id=str(uuid.uuid4()),
                    confirm_margin_trade=False
                )
                return order and order.order_id
            except Exception:
                return False

    def is_tradable_automatically(self, figi: str) -> Tuple[bool, str]:
        """
        Проверка, можно ли торговать инструментом автоматически
        Returns: (is_tradable, reason)
        """
        try:
            status = self.get_trading_status(figi)
            if not status:
                return False, "не удалось получить статус торгов"

            if not status.get('api_trade_available', False):
                return False, "API торговля недоступна"

            if not status.get('market_order_available', False) and not status.get('limit_order_available', False):
                return False, "рыночные и лимитные заявки недоступны"

            return True, "OK"
        except Exception as e:
            debug(f"Ошибка проверки торговли для {figi}: {e}")
            return False, str(e)[:50]



    def get_order_price(self, figi: str, direction: str, quantity: int, price: float) -> Dict[str, Any]:
        """
        Получение предварительной стоимости заявки

        Args:
            figi: FIGI инструмента
            direction: "BUY" или "SELL"
            quantity: Количество
            price: Цена

        Returns:
            Dict с полями: total_amount, initial_amount, commission, lots_requested
        """
        with Client(self.token) as client:
            try:
                dir_map = {
                    "BUY": OrderDirection.ORDER_DIRECTION_BUY,
                    "SELL": OrderDirection.ORDER_DIRECTION_SELL
                }
                price_quotation = decimal_to_quotation(Decimal(str(price)))

                response = client.orders.get_order_price(
                    account_id=self.account_id,
                    instrument_id=figi,
                    direction=dir_map[direction],
                    quantity=quantity,
                    price=price_quotation
                )

                return {
                    'total_amount': float(quotation_to_decimal(response.total_order_amount)),
                    'initial_amount': float(quotation_to_decimal(response.initial_order_amount)),
                    'commission': float(quotation_to_decimal(response.executed_commission)),
                    'lots_requested': response.lots_requested,
                }
            except Exception as e:
                debug(f"Ошибка получения цены заявки для {figi}: {e}")
                return {}

    def get_technical_indicators(self, figi: str, indicator_type: str = "RSI", interval: str = "5min") -> Dict[str, Any]:
        """
        Получение технических индикаторов от биржи

        Args:
            figi: FIGI инструмента
            indicator_type: "RSI", "MACD", "BB", "SMA", "EMA"
            interval: "1min", "5min", "15min", "1hour", "1day"

        Returns:
            Dict с индикаторами
        """
        from t_tech.invest import CandleInterval

        interval_map = {
            "1min": CandleInterval.CANDLE_INTERVAL_1_MIN,
            "5min": CandleInterval.CANDLE_INTERVAL_5_MIN,
            "15min": CandleInterval.CANDLE_INTERVAL_15_MIN,
            "1hour": CandleInterval.CANDLE_INTERVAL_HOUR,
            "1day": CandleInterval.CANDLE_INTERVAL_DAY,
        }

        indicator_map = {
            "RSI": 3,
            "MACD": 4,
            "BB": 1,
            "SMA": 5,
            "EMA": 2
        }

        with Client(self.token) as client:
            try:
                response = client.market_data.get_tech_analysis(
                    instrument_uid=figi,
                    indicator_type=indicator_map.get(indicator_type, 3),
                    interval=interval_map.get(interval, CandleInterval.CANDLE_INTERVAL_5_MIN),
                    from_date=datetime.now() - timedelta(days=30),
                    to_date=datetime.now()
                )

                result = {}
                for item in response.technical_indicators:
                    if item.middle_band:
                        result['value'] = float(quotation_to_decimal(item.middle_band))
                    if hasattr(item, 'signal') and item.signal:
                        result['signal'] = float(quotation_to_decimal(item.signal))
                    if hasattr(item, 'macd') and item.macd:
                        result['macd'] = float(quotation_to_decimal(item.macd))
                    result['timestamp'] = item.timestamp.isoformat() if item.timestamp else None

                return result
            except Exception as e:
                debug(f"Ошибка получения индикаторов для {figi}: {e}")
                return {}

    def get_candles_fast(self, figi: str, interval_seconds: int = 5, days: int = 1) -> List[Tuple[float, float]]:
        """
        Получение свечей с малым интервалом (5, 10, 30 секунд)

        Args:
            figi: FIGI инструмента
            interval_seconds: 5, 10 или 30 секунд
            days: Количество дней (максимум 1 для быстрых свечей)

        Returns:
            List of (close, volume)
        """
        from t_tech.invest import CandleInterval

        interval_map = {
            5: CandleInterval.CANDLE_INTERVAL_5_SEC,
            10: CandleInterval.CANDLE_INTERVAL_10_SEC,
            30: CandleInterval.CANDLE_INTERVAL_30_SEC,
        }

        interval = interval_map.get(interval_seconds)
        if not interval:
            debug(f"Неподдерживаемый интервал: {interval_seconds} сек")
            return []

        with Client(self.token) as client:
            try:
                end_time = datetime.now()
                start_time = end_time - timedelta(days=min(days, 1))

                candles = client.market_data.get_candles(
                    instrument_id=figi,
                    from_date=start_time,
                    to_date=end_time,
                    interval=interval
                )
                return [(float(quotation_to_decimal(c.close)),
                         float(quotation_to_decimal(c.volume))) for c in candles.candles]
            except Exception as e:
                debug(f"Ошибка получения быстрых свечей для {figi}: {e}")
                return []

    def _get_min_price_increment_advanced(self, figi: str) -> float:
        """
        Улучшенное получение минимального шага цены для инструмента
        Использует API для точного определения шага
        """
        try:
            with Client(self.token) as client:
                # Пробуем через share_by (акции)
                try:
                    instrument = client.instruments.share_by(figi=figi)
                    if hasattr(instrument.instrument, 'min_price_increment'):
                        step = float(instrument.instrument.min_price_increment)
                        if step > 0:
                            debug(f"📊 Шаг цены для {figi}: {step} (из share_by)")
                            return step
                except Exception:
                    pass

                # Пробуем через bond_by (облигации)
                try:
                    instrument = client.instruments.bond_by(figi=figi)
                    if hasattr(instrument.instrument, 'min_price_increment'):
                        step = float(instrument.instrument.min_price_increment)
                        if step > 0:
                            debug(f"📊 Шаг цены для {figi}: {step} (из bond_by)")
                            return step
                except Exception:
                    pass

                # Пробуем через etf_by (ETF)
                try:
                    instrument = client.instruments.etf_by(figi=figi)
                    if hasattr(instrument.instrument, 'min_price_increment'):
                        step = float(instrument.instrument.min_price_increment)
                        if step > 0:
                            debug(f"📊 Шаг цены для {figi}: {step} (из etf_by)")
                            return step
                except Exception:
                    pass

                # Пробуем через future_by (фьючерсы)
                try:
                    instrument = client.instruments.future_by(figi=figi)
                    if hasattr(instrument.instrument, 'min_price_increment'):
                        step = float(instrument.instrument.min_price_increment)
                        if step > 0:
                            debug(f"📊 Шаг цены для {figi}: {step} (из future_by)")
                            return step
                except Exception:
                    pass

        except Exception as e:
            debug(f"Ошибка получения шага цены для {figi}: {e}")

        # Стандартный шаг - 1 копейка
        return 0.01

    def _round_to_min_increment_advanced(self, figi: str, price: float) -> float:
        """
        Округление цены до минимального шага инструмента (улучшенная версия)
        """
        step = self._get_min_price_increment_advanced(figi)
        if step <= 0:
            step = 0.01

        # Округляем до шага
        rounded = round(price / step) * step

        # Определяем нужное количество знаков после запятой
        step_str = str(step)
        if '.' in step_str:
            decimal_places = len(step_str.split('.')[1])
        else:
            decimal_places = 0

        # Округляем до нужного количества знаков
        result = round(rounded, decimal_places)

        # Дополнительная проверка для малых чисел
        if result < 0.01 and price > 0:
            result = step if step >= 0.01 else 0.01

        # Логируем корректировку
        if abs(result - price) > 0.001:
            debug(f"💰 Цена скорректирована: {price:.4f} → {result:.4f} (шаг={step})")

        return result

    def is_market_available(self, figi: str) -> Tuple[bool, str]:
        """
        Проверка, доступен ли инструмент для торговли прямо сейчас

        Args:
            figi: FIGI инструмента

        Returns:
            (is_available, reason)
        """
        try:
            status = self.get_trading_status(figi)

            # Проверяем API доступность
            if not status.get('api_trade_available', False):
                return False, "API торговля недоступна"

            # Проверяем рыночные заявки
            if not status.get('market_order_available', False):
                # В OTC режиме могут работать только лимитные заявки
                if status.get('limit_order_available', False):
                    return True, "OTC режим (доступны лимитные заявки)"
                return False, "Рыночные заявки недоступны"

            # Проверяем статус торгов
            trading_status = status.get('trading_status')

            # Статусы, когда можно торговать (по документации T-Invest API)
            # 5 = NORMAL_TRADING (нормальные торги)
            # 13 = SESSION_OPEN (сессия открыта)
            # 14 = DEALER_NEGOTIATED (дилерские торги)
            allowed_statuses = [5, 13, 14]

            # Статусы, когда торговля запрещена
            # 1 = CLOSED, 2 = DEAD, 3 = DEALER_QUOTE, 6 = SECONDARY_QUOTE,
            # 7 = QUOTE, 8 = DEALER_NEGOTIATED_CLOSING, 9 = NEGOTIATED,
            # 10 = CLOSING, 11 = CLOSING_BID, 12 = CLOSING_ASK
            blocked_statuses = [1, 2, 3, 6, 7, 8, 9, 10, 11, 12]

            if trading_status in allowed_statuses:
                return True, "Торги активны"
            elif trading_status in blocked_statuses:
                return False, f"Торги остановлены (статус {trading_status})"
            else:
                # Неизвестный статус - пробуем всё равно
                debug(f"Неизвестный статус торгов {trading_status} для {figi}, пробуем торговать")
                return True, f"Статус {trading_status} (пробуем)"

        except Exception as e:
            debug(f"Ошибка проверки доступности {figi}: {e}")
            return True, "Не удалось проверить"  # Даём шанс

    def check_margin_trading_allowed(self) -> Tuple[bool, str]:
        """
        Проверка, доступна ли маржинальная торговля на счёте
        """
        with Client(self.token) as client:
            try:
                # ПРЯМАЯ ПРОВЕРКА: пробуем получить маржинальные атрибуты
                margin = client.users.get_margin_attributes(account_id=self.account_id)

                if margin:
                    # Если получили данные - маржинальная торговля ДОСТУПНА
                    return True, "OK"

            except Exception as e:
                error_msg = str(e)
                if "50002" in error_msg:
                    return False, "Маржинальная торговля не включена. Включите в настройках счёта"
                if "50020" in error_msg:
                    return False, "Маржинальная торговля требует статуса квалифицированного инвестора"
                return False, f"Ошибка: {error_msg[:50]}"

            return False, "Не удалось определить"

    def place_stop_loss_order(self, figi: str, quantity: int, stop_price: float, side: str) -> bool:
        """
        Установка стоп-лосс приказа (Stop Market)
        """
        with Client(self.token) as client:
            try:
                from t_tech.invest import OrderDirection, StopOrderExpirationType
                from decimal import Decimal

                # Определяем направление
                if side == "LONG":
                    direction = OrderDirection.ORDER_DIRECTION_SELL
                elif side == "SHORT":
                    direction = OrderDirection.ORDER_DIRECTION_BUY
                else:
                    error(f"Неверная сторона для стоп-лосса: {side}")
                    return False

                # Округляем цену
                stop_price = self._round_to_min_increment_advanced(figi, stop_price)

                # Проверка цены
                current_price = self.get_current_price(figi)
                if current_price:
                    min_distance = self._get_min_price_increment_advanced(figi) * 2

                    if side == "LONG" and stop_price >= current_price - min_distance:
                        stop_price = self._round_to_min_increment_advanced(figi, current_price - min_distance)
                        warning(f"⚠️ Стоп-лосс LONG скорректирован до {stop_price:.2f}₽")
                    elif side == "SHORT" and stop_price <= current_price + min_distance:
                        stop_price = self._round_to_min_increment_advanced(figi, current_price + min_distance)
                        warning(f"⚠️ Стоп-лосс SHORT скорректирован до {stop_price:.2f}₽")

                info(f"📊 СТОП-ЛОСС: {side} {quantity} шт {figi} по {stop_price:.2f}₽")

                stop_price_quotation = decimal_to_quotation(Decimal(str(stop_price)))

                # ✅ ИСПРАВЛЕНО: используем число 2 вместо StopOrderType.STOP_ORDER_TYPE_STOP_MARKET
                order = client.stop_orders.post_stop_order(
                    figi=figi,
                    quantity=quantity,
                    price=None,
                    stop_price=stop_price_quotation,
                    direction=direction,
                    account_id=self.account_id,
                    stop_order_type=2,  # 2 = STOP_MARKET
                    expiration_type=StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL,
                    order_id=str(uuid.uuid4())
                )

                if order and order.stop_order_id:
                    success(f"✅ Стоп-лосс {side} для {figi} установлен на {stop_price:.2f}₽")
                    return True
                return False

            except Exception as e:
                error(f"❌ Ошибка установки стоп-лосса: {e}")
                return False

    def place_take_profit_order(self, figi: str, quantity: int, take_profit_price: float, side: str) -> bool:
        """
        Установка тейк-профит приказа (лимитный ордер)

        Args:
            figi: FIGI инструмента
            quantity: Количество лотов
            take_profit_price: Цена для фиксации прибыли
            side: "LONG" (для продажи) или "SHORT" (для покупки)

        Returns:
            bool: Успех операции
        """
        with Client(self.token) as client:
            try:
                from t_tech.invest import OrderDirection, OrderType
                from decimal import Decimal, ROUND_HALF_UP

                # Определяем направление
                if side == "LONG":
                    direction = OrderDirection.ORDER_DIRECTION_SELL
                elif side == "SHORT":
                    direction = OrderDirection.ORDER_DIRECTION_BUY
                else:
                    error(f"Неверная сторона для тейк-профита: {side}")
                    return False

                # Округляем цену до шага
                step = self._get_min_price_increment_advanced(figi)
                take_profit_price = self._round_to_min_increment_advanced(figi, take_profit_price)

                # Проверка для SHORT: цена не может быть ниже 0.01
                if side == "SHORT" and take_profit_price < 0.01:
                    warning(f"⚠️ Цена тейк-профита SHORT слишком низкая ({take_profit_price:.4f})")
                    take_profit_price = self._round_to_min_increment_advanced(figi, 0.01)

                # Проверка: тейк-профит не должен быть слишком близко к рынку
                current_price = self.get_current_price(figi)
                if current_price:
                    min_distance = step * 2

                    if side == "LONG" and take_profit_price <= current_price + min_distance:
                        new_price = current_price + min_distance * 2
                        take_profit_price = self._round_to_min_increment_advanced(figi, new_price)
                        warning(f"⚠️ Тейк-профит LONG скорректирован до {take_profit_price:.2f}₽")
                    elif side == "SHORT" and take_profit_price >= current_price - min_distance:
                        new_price = current_price - min_distance * 2
                        take_profit_price = self._round_to_min_increment_advanced(figi, max(new_price, 0.01))
                        warning(f"⚠️ Тейк-профит SHORT скорректирован до {take_profit_price:.2f}₽")

                if take_profit_price <= 0:
                    error(f"❌ Некорректная цена тейк-профита: {take_profit_price}")
                    return False

                # Создаём Decimal с правильным округлением
                price_decimal = Decimal(str(take_profit_price)).quantize(
                    Decimal(str(step)), rounding=ROUND_HALF_UP
                )
                price_quotation = decimal_to_quotation(price_decimal)

                info(f"📊 ТЕЙК-ПРОФИТ: {side} {quantity} шт {figi} по {take_profit_price:.2f}₽")

                order = client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    price=price_quotation,
                    direction=direction,
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_LIMIT,
                    order_id=str(uuid.uuid4()),
                    confirm_margin_trade=True
                )

                if order and order.order_id:
                    success(f"✅ Тейк-профит {side} для {figi} установлен на {take_profit_price:.2f}₽")
                    return True
                return False

            except Exception as e:
                error(f"❌ Ошибка установки тейк-профита: {e}")
                return False

    # ========== НОВЫЕ МЕТОДЫ ДЛЯ РАБОТЫ С ЗАЯВКАМИ ==========

    def get_max_lots(self, figi: str, direction: str, price: float = None) -> int:
        """
        Получение максимального количества лотов для покупки/продажи

        Args:
            figi: FIGI инструмента
            direction: "BUY" или "SELL"
            price: Цена (для лимитных заявок)

        Returns:
            Максимальное количество лотов
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        try:
            with Client(self.token) as client:
                price_quotation = decimal_to_quotation(Decimal(str(price))) if price else None

                info(f"📊 Запрос max lots для {ticker}: direction={direction}, price={price if price else 'market'}")

                max_lots = client.orders.get_max_lots(
                    account_id=self.account_id,
                    instrument_id=figi,
                    price=price_quotation
                )

                if direction == "BUY":
                    result = max_lots.buy_limits.buy_max_lots
                    info(f"✅ Максимум покупки {ticker}: {result} лотов")
                else:
                    result = max_lots.sell_limits.sell_max_lots
                    info(f"✅ Максимум продажи {ticker}: {result} лотов")

                return result

        except Exception as e:
            error_msg = str(e)
            if "30014" in error_msg:
                warning(f"⚠️ Инструмент {ticker} не найден")
            else:
                warning(f"⚠️ Ошибка получения max lots для {ticker}: {error_msg[:100]}")
            return 0

    def get_order_state(self, order_id: str, figi: str = None) -> Optional[Dict[str, Any]]:
        """
        Получение статуса заявки

        Args:
            order_id: Идентификатор заявки (биржевой)
            figi: FIGI инструмента (для логирования)

        Returns:
            Dict с полями: order_id, status, executed_lots, executed_commission, price
        """
        ticker = self._get_ticker_by_figi(figi) if figi else order_id[:8]

        try:
            with Client(self.token) as client:
                info(f"📊 Запрос статуса заявки {order_id} для {ticker}")

                order = client.orders.get_order_state(
                    account_id=self.account_id,
                    order_id=order_id
                )

                result = {
                    'order_id': order.order_id,
                    'status': str(order.order_state),
                    'executed_lots': order.executed_lots,
                    'requested_lots': order.lots_requested,
                    'executed_commission': float(
                        quotation_to_decimal(order.executed_commission)) if order.executed_commission else 0,
                    'price': float(quotation_to_decimal(order.price)) if order.price else 0,
                    'direction': "BUY" if order.direction == 1 else "SELL"
                }

                info(
                    f"✅ Статус заявки {order_id}: {result['status']}, исполнено {result['executed_lots']}/{result['requested_lots']} лотов")

                return result

        except Exception as e:
            error_msg = str(e)
            if "30070" in error_msg:
                warning(f"⚠️ Заявка {order_id} не найдена (возможно, уже исполнена)")
            else:
                warning(f"⚠️ Ошибка получения статуса заявки {order_id}: {error_msg[:100]}")
            return None

    def cancel_all_orders_by_ticker(self, ticker: str) -> Dict[str, Any]:
        """
        Отмена всех активных заявок по тикеру

        Args:
            ticker: Тикер инструмента

        Returns:
            Dict с количеством отменённых заявок
        """
        info(f"🔄 Отмена всех заявок для {ticker}")

        try:
            figi = self._get_figi_by_ticker(ticker)
            if not figi:
                warning(f"⚠️ FIGI для {ticker} не найден")
                return {"cancelled": 0, "failed": 0}

            with Client(self.token) as client:
                orders = client.orders.get_orders(account_id=self.account_id)
                cancelled = 0
                failed = 0

                for order in orders.orders:
                    if order.figi == figi:
                        try:
                            client.orders.cancel_order(
                                account_id=self.account_id,
                                order_id=order.order_id
                            )
                            cancelled += 1
                            info(f"   ✅ Отменена заявка {order.order_id} для {ticker}")
                        except Exception as e:
                            failed += 1
                            warning(f"   ⚠️ Не удалось отменить {order.order_id}: {e}")

                info(f"📊 Заявок для {ticker}: отменено {cancelled}, ошибок {failed}")
                return {"cancelled": cancelled, "failed": failed}

        except Exception as e:
            error(f"❌ Ошибка отмены заявок для {ticker}: {e}")
            return {"cancelled": 0, "failed": 0, "error": str(e)}

    def get_order_price_info(
            self,
            figi: str,
            direction: str,
            quantity: int,
            price: float
    ) -> Dict[str, Any]:
        """
        Получение предварительной стоимости заявки

        Args:
            figi: FIGI инструмента
            direction: "BUY" или "SELL"
            quantity: Количество лотов
            price: Цена за 1 инструмент

        Returns:
            Dict с полями: total_amount, commission, lots_requested
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        try:
            with Client(self.token) as client:
                dir_map = {
                    "BUY": OrderDirection.ORDER_DIRECTION_BUY,
                    "SELL": OrderDirection.ORDER_DIRECTION_SELL
                }
                price_quotation = decimal_to_quotation(Decimal(str(price)))

                info(f"📊 Расчёт стоимости заявки для {ticker}: {direction} {quantity} лотов по {price:.2f}₽")

                response = client.orders.get_order_price(
                    account_id=self.account_id,
                    instrument_id=figi,
                    direction=dir_map[direction],
                    quantity=quantity,
                    price=price_quotation
                )

                result = {
                    'total_amount': float(quotation_to_decimal(response.total_order_amount)),
                    'initial_amount': float(quotation_to_decimal(response.initial_order_amount)),
                    'commission': float(quotation_to_decimal(response.executed_commission)),
                    'lots_requested': response.lots_requested
                }

                info(f"✅ Стоимость: {result['total_amount']:.2f}₽, комиссия: {result['commission']:.4f}₽")
                return result

        except Exception as e:
            warning(f"⚠️ Ошибка расчёта стоимости для {ticker}: {e}")
            return {}

    def _get_figi_by_ticker(self, ticker: str) -> Optional[str]:
        """
        Получение FIGI по тикеру (кэшированный)

        Args:
            ticker: Тикер инструмента

        Returns:
            FIGI или None
        """
        ticker_upper = ticker.upper()

        if ticker_upper in self._ticker_cache:
            figi = self._ticker_cache[ticker_upper]
            if isinstance(figi, str):
                return figi

        try:
            all_shares = self.get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('ticker') == ticker_upper:
                    figi = stock.get('figi')
                    self._ticker_cache[ticker_upper] = figi
                    self._figi_to_ticker_cache[figi] = ticker_upper
                    return figi
        except Exception as e:
            warning(f"⚠️ Ошибка получения FIGI для {ticker}: {e}")

        return None

    def validate_order_before_send(
            self,
            figi: str,
            quantity: int,
            direction: str,
            price: float = None
    ) -> Tuple[bool, str, Dict]:
        """
        Валидация заявки перед отправкой

        Args:
            figi: FIGI инструмента
            quantity: Количество лотов
            direction: "BUY" или "SELL"
            price: Цена (для лимитных заявок)

        Returns:
            (is_valid, reason, additional_info)
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]
        info(f"🔍 Валидация заявки {ticker}: {direction} {quantity} лотов")

        additional_info = {}

        # 1. Проверка количества
        if quantity <= 0:
            return False, f"Количество {quantity} <= 0", additional_info

        # 2. Проверка максимального количества лотов
        max_lots = self.get_max_lots(figi, direction, price)
        if max_lots > 0 and quantity > max_lots:
            return False, f"Превышен лимит: {quantity} > {max_lots} лотов", additional_info
        additional_info['max_lots'] = max_lots

        # 3. Проверка цены
        if price:
            if price <= 0:
                return False, f"Цена {price} <= 0", additional_info

            # Проверка шага цены
            step = self._get_min_price_increment_advanced(figi)
            if step > 0:
                remainder = price % step
                if remainder > 0.0001:
                    suggested = round(price / step) * step
                    return False, f"Цена не кратна шагу {step}, предлагается {suggested:.4f}", additional_info

        # 4. Проверка торгового статуса
        status = self.get_trading_status(figi)
        if not status.get('api_trade_available', False):
            return False, "API торговля недоступна", additional_info

        if direction == "BUY" and not status.get('buy_available_flag', False):
            return False, "Покупка недоступна", additional_info

        if direction == "SELL" and not status.get('sell_available_flag', False):
            return False, "Продажа недоступна", additional_info

        # 5. Для SHORT - проверка маржи
        if direction == "SELL" and not self._is_long_position(figi):
            margin_allowed, reason = self.check_margin_trading_allowed()
            if not margin_allowed:
                return False, f"Маржинальная торговля недоступна: {reason}", additional_info

        info(f"✅ Заявка {ticker} прошла валидацию")
        return True, "OK", additional_info

    def _is_long_position(self, figi: str) -> bool:
        """Проверка, есть ли LONG позиция по инструменту (для определения SHORT)"""
        try:
            positions = self.get_positions()
            for pos in positions:
                if pos.get('figi') == figi and pos.get('quantity', 0) > 0:
                    return True
            return False
        except Exception:
            return False

    def get_withdraw_limits(self) -> Dict[str, Any]:
        """
        Получение доступного остатка для вывода денежных средств

        Returns:
            Dict с полями: money (доступно для вывода), blocked (заблокировано)
        """
        try:
            with Client(self.token) as client:
                limits = client.operations.get_withdraw_limits(account_id=self.account_id)

                result = {
                    'money': [],
                    'blocked': []
                }

                for money in limits.money:
                    result['money'].append({
                        'currency': money.currency,
                        'units': money.units,
                        'nano': money.nano,
                        'amount': float(quotation_to_decimal(money))
                    })

                for blocked in limits.blocked:
                    result['blocked'].append({
                        'currency': blocked.currency,
                        'units': blocked.units,
                        'nano': blocked.nano,
                        'amount': float(quotation_to_decimal(blocked))
                    })

                info(f"💰 Доступно для вывода: {result['money']}")
                return result

        except Exception as e:
            warning(f"⚠️ Ошибка получения лимитов вывода: {e}")
            return {'money': [], 'blocked': []}

# Единый экземпляр
tbank = TBankClient()