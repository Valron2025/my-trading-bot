"""Клиент для работы с T-Bank API - RENDER FIXED VERSION"""

import time
import os
import grpc
from functools import wraps
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta, timezone
import uuid
from decimal import Decimal
import signal
from contextlib import contextmanager
from threading import Lock

MOSCOW_TZ = timezone(timedelta(hours=3))

# ========== ПРИНУДИТЕЛЬНЫЙ ФИКС ПОРТА ==========
os.environ['GRPC_DNS_RESOLVER'] = 'native'

# Патчим соединение глобально
_original_secure_channel = grpc.secure_channel
grpc.secure_channel = lambda target, credentials, options=None: grpc.insecure_channel(target.replace(':443', ':80'))

print("🔓 Принудительно заменяем secure_channel на insecure (порт 80)")

# ========== ИМПОРТЫ ==========
from t_tech.invest import (
    Client as OriginalClient,
    CandleInterval,
    OrderType,
    OrderDirection
)
from t_tech.invest.utils import quotation_to_decimal, decimal_to_quotation

# Создаём класс-обёртку для клиента
class RenderCompatibleClient(OriginalClient):
    """Клиент, совместимый с Render - принудительно использует порт 80"""

    def __init__(self, token, *args, **kwargs):
        # Создаём insecure channel на порт 80
        self._custom_channel = grpc.insecure_channel('invest-public-api.tinkoff.ru:80')

        # Инициализируем родителя (он создаст стубы)
        super().__init__(token, *args, **kwargs)

        # Подменяем все стубы на наши с правильным каналом
        for attr_name in dir(self):
            if attr_name.endswith('_stub') and not attr_name.startswith('_'):
                stub = getattr(self, attr_name, None)
                if stub and hasattr(stub, '__init__'):
                    stub_class = stub.__class__
                    setattr(self, attr_name, stub_class(self._custom_channel))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, '_custom_channel'):
            self._custom_channel.close()

    def close(self):
        if hasattr(self, '_custom_channel'):
            self._custom_channel.close()

# Глобально заменяем Client
Client = RenderCompatibleClient

# Подменяем в модуле t_tech.invest
import t_tech.invest
t_tech.invest.Client = RenderCompatibleClient

print("✅ RenderCompatibleClient зарегистрирован (порт 80)")

# Остальные импорты
from trading_bot.utils.figi_resolver import get_figi_resolver
from trading_bot.order_validator import OrderValidator
from trading_bot.risk.position_manager import position_manager
from trading_bot.core.settings_manager import settings_manager
from trading_bot.telegram.telegram_notifier import get_telegram_notifier
from trading_bot.core.blacklist_manager import blacklist_manager
from trading_bot.config import config
from trading_bot.logger import info, success, error, warning, debug, logger
from trading_bot.cache import (
    price_cache, positions_cache, candles_cache,
    margin_cache, instruments_cache
)
from socket import timeout as SocketTimeoutError
from trading_bot.cache import TTLCache
from trading_bot.cache.unified_cache import USE_UNIFIED_CACHE, UnifiedCache


def retry_on_error(max_retries=3, delay=1, backoff=2, timeout_seconds=2.0):
    """Декоратор для повторных попыток при ошибках API с таймаутом"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    with TimeoutManager(timeout_seconds):
                        return func(*args, **kwargs)
                except SocketTimeoutError as e:
                    warning(f"⏰ Таймаут API ({timeout_seconds}с), попытка {attempt + 1}/{max_retries}")
                    current_delay = min(current_delay * 2, 60)
                    time.sleep(current_delay)
                    current_delay *= backoff
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        warning(f"⚠️ Ошибка API, попытка {attempt + 1}/{max_retries}: {e}")
                        current_delay = min(current_delay * 2, 60)
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        error(f"❌ Ошибка API после {max_retries} попыток: {e}")
            raise last_exception if last_exception else Exception("Unknown error")
        return wrapper
    return decorator


class TimeoutManager:
    """Управление таймаутами для API запросов"""

    def __init__(self, timeout_seconds: float = 2.0):
        self.timeout_seconds = timeout_seconds
        self._old_handler = None

    def __enter__(self):
        try:
            self._old_handler = signal.signal(signal.SIGALRM, self._timeout_handler)
            signal.alarm(int(self.timeout_seconds))
        except (AttributeError, ValueError) as e:
            debug(f"⚠️ Не удалось установить таймаут: {e}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            signal.alarm(0)
            if self._old_handler:
                signal.signal(signal.SIGALRM, self._old_handler)
        except (AttributeError, ValueError) as e:
            debug(f"⚠️ Не удалось сбросить таймаут: {e}")

    def _timeout_handler(self, signum, frame):
        raise TimeoutError(f"API запрос превысил лимит {self.timeout_seconds}с")


class TBankClient:
    """Клиент для работы с T-Bank API с поддержкой РЕАЛЬНЫХ заявок"""

    def __init__(self):
        self.token = config.tbank_token

        if not self.token:
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

        # Унифицированный кэш
        if USE_UNIFIED_CACHE:
            self._unified_cache = UnifiedCache(default_ttl=60, name="tbank_client")

        # ========== ЧЕРНЫЙ СПИСОК ТИКЕРОВ, ТРЕБУЮЩИХ ПОДТВЕРЖДЕНИЯ ==========
        self._confirmation_required_tickers = set()
        self._confirmation_blocklist_ttl = 3600
        self._confirmation_blocklist_time = {}

        # Кэш для результатов проверки подтверждения
        self._confirmation_cache = {}
        self._confirmation_cache_time = {}
        self._no_stop_orders = set()

        # ========== КОНФИГУРАЦИЯ ТАЙМАУТОВ ==========
        self.timeout_config = {
            'get_candles': 0.5,
            'get_last_prices': 0.5,
            'get_trading_status': 0.5,
            'post_order': 1.5,
            'cancel_order': 1.5,
            'get_orders': 0.5,
            'get_portfolio': 1.5,
            'get_positions': 1.0,
            'get_stop_orders': 1.5,
            'post_stop_order': 1.5,
            'get_margin_attributes': 0.3,
            'get_info': 1.0,
            'get_accounts': 0.3,
        }

        # ✅ ГЛОБАЛЬНАЯ ЗАЩИТА ОТ РЕЙТ-ЛИМИТА
        self._last_api_call = 0
        self._api_call_lock = Lock()
        self._min_interval = 1.0

        # ========== НОВОЕ: ИНИЦИАЛИЗАЦИЯ ВАЛИДАТОРА ==========
        self._validator = None

    # ========== НОВЫЙ МЕТОД ДЛЯ ИНИЦИАЛИЗАЦИИ ВАЛИДАТОРА ==========
    def _init_validator(self):
        """Инициализация валидатора заявок"""
        if self._validator is None:
            self._validator = OrderValidator(self.token, self.account_id)
        return self._validator

    def _wait_for_rate_limit(self):
        """Универсальная задержка перед любым API вызовом"""
        with self._api_call_lock:
            now = time.time()
            elapsed = now - self._last_api_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_api_call = time.time()

    def get_timeout_stats(self) -> Dict[str, Any]:
        """Статистика по таймаутам"""
        return {
            'config': self.timeout_config.copy(),
            'cache_stats': {
                'price_cache': price_cache.get_stats(),
                'candles_cache': candles_cache.get_stats() if hasattr(candles_cache, 'get_stats') else {},
                'margin_cache': margin_cache.get_stats() if hasattr(margin_cache, 'get_stats') else {},
                'instruments_cache': instruments_cache.get_stats() if hasattr(instruments_cache, 'get_stats') else {},
            },
            'ticker_cache_size': 0,
        }

    @contextmanager
    def _get_client_context(self):
        """Контекстный менеджер для работы с клиентом"""
        client = None
        try:
            client = Client(self.token)
            yield client
        except Exception as e:
            debug(f"Ошибка создания клиента: {e}")
            raise
        finally:
            if client:
                try:
                    if hasattr(client, 'close'):
                        client.close()
                except Exception as e:
                    debug(f"Ошибка закрытия клиента: {e}")

    @property
    def account_id(self) -> str:
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
        """Умная покупка - сама выбирает тип заявки с ПОДТВЕРЖДЕНИЕМ"""
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]
        validator = self._init_validator()

        # ✅ ПРЕДВАРИТЕЛЬНАЯ ВАЛИДАЦИЯ
        is_valid, reason, validation_info = validator.validate_before_send(
            figi=figi,
            quantity=quantity,
            direction="BUY",
            is_short=False
        )

        if not is_valid:
            error(f"❌ Валидация покупки {ticker} не пройдена: {reason}")
            return False

        # ОКРУГЛЕНИЕ ДО ЛОТА
        original_qty = quantity
        lot_size = self._get_lot_size(figi)

        if lot_size > 1:
            lots = quantity // lot_size
            if lots == 0:
                lots = 1
            quantity = lots * lot_size

            if quantity != original_qty:
                warning(f"🔄 BUY {ticker}: округление {original_qty} → {quantity} (лот={lot_size})")

        info(f"🔍 BUY {ticker}: начальная проверка...")
        info(f"   📊 Запрошено: {original_qty} шт, Исполняется: {quantity} шт, Лотность: {lot_size}")

        if self.is_confirmation_required(figi):
            error(f"❌ {ticker} в черном списке - покупка невозможна")
            return False

        price = self.get_current_price(figi)
        if not price:
            error(f"❌ Не удалось получить цену для покупки {figi}")
            return False

        # ========== ✅ ДОБАВИТЬ ОКРУГЛЕНИЕ ЦЕНЫ ДО ШАГА ==========
        step = self._get_min_price_increment_advanced(figi)
        if step > 0:
            original_price = price
            price = round(price / step) * step
            if abs(price - original_price) > 0.001:
                info(f"   💰 Цена скорректирована: {original_price:.4f} → {price:.4f} (шаг={step})")

        total = quantity * price
        info(f"📊 BUY {ticker}: {quantity} шт по {price:.2f}₽, сумма {total:.2f}₽")

        available, total_cap, _ = self.get_available_funds()
        if total > available:
            warning(f"⚠️ Недостаточно средств: нужно {total:.2f}₽, доступно {available:.2f}₽")
            return False

        result = validator.send_order_with_confirmation(
            figi=figi,
            quantity=quantity,
            direction="BUY",
            order_type="MARKET" if use_market else "LIMIT",
            price=price if not use_market else None,
            max_wait_seconds=10
        )

        if result.get('success') and result.get('found'):
            success(f"✅ Покупка {ticker}: {quantity} шт ПОДТВЕРЖДЕНА!")
            info(f"   Статус: {result.get('status')}, исполнено: {result.get('executed_lots')}/{result.get('requested_lots')}")

            position_entries[figi] = {
                'entry_time': datetime.now(),
                'entry_price': price,
                'highest_price': price,
                'quantity': quantity,
                'side': 'LONG'
            }
            return True
        else:
            error(f"❌ Покупка {ticker} НЕ ПОДТВЕРЖДЕНА: {result.get('error')}")
            return False

    def _get_lot_size(self, figi: str) -> int:
        """Получение размера лота для инструмента"""
        try:
            shares = self.get_all_shares(limit=1000)
            for share in shares:
                if share.get('figi') == figi:
                    return share.get('lot', 1)
        except Exception as e:
            debug(f"⚠️ Ошибка получения лотности: {e}")
        return 1

    def sell(self, figi: str, quantity: int, use_market: bool = None) -> bool:
        """Умная продажа - сама выбирает тип заявки"""
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        original_qty = quantity
        lot_size = self._get_lot_size(figi)

        if lot_size > 1:
            lots = quantity // lot_size
            if lots == 0:
                lots = 1
            quantity = lots * lot_size

            if quantity != original_qty:
                warning(f"🔄 SELL {ticker}: округление {original_qty} → {quantity} (лот={lot_size})")

        info(f"🔍 SELL {ticker}: начальная проверка...")
        info(f"   📊 Запрошено: {original_qty} шт, Исполняется: {quantity} шт, Лотность: {lot_size}")

        if self.is_confirmation_required(figi):
            error(f"❌ {ticker} в черном списке - продажа невозможна")
            return False

        price = self.get_current_price(figi)
        if not price:
            error(f"❌ Не удалось получить цену для продажи {figi}")
            return False

        step = self._get_min_price_increment_advanced(figi)
        if step > 0:
            original_price = price
            price = round(price / step) * step
            if abs(price - original_price) > 0.001:
                info(f"   💰 Цена скорректирована: {original_price:.4f} → {price:.4f} (шаг={step})")

        total = quantity * price
        info(f"📊 SELL {ticker}: {quantity} шт по {price:.2f}₽, сумма {total:.2f}₽")

        if use_market is None:
            market_available = self._is_market_order_available(figi)
            urgent = hasattr(self, '_current_score') and getattr(self, '_current_score', 0) >= 7
            is_otc = self._is_otc_mode()

            if market_available and (urgent or not is_otc):
                use_market = True
                info(f"🎯 ВЫБРАНА РЫНОЧНАЯ ЗАЯВКА (срочно={urgent}, OTC={is_otc})")
            else:
                use_market = False
                info(f"📋 ВЫБРАНА ЛИМИТНАЯ ЗАЯВКА")

        if use_market:
            info(f"🔴 РЫНОЧНАЯ ПРОДАЖА: {quantity} шт {ticker}")
            result = self._place_market_order_impl(figi, quantity, "SELL")

            if not result:
                warning(f"🔄 Рыночная не удалась, пробуем лимитную...")
                limit_price = self._round_to_min_increment_advanced(figi, price * 0.99)
                result = self.place_limit_order(figi, quantity, "SELL", limit_price)

                if not result:
                    warning(f"🔄 Лимитная не удалась, пробуем ПРИНУДИТЕЛЬНУЮ рыночную...")
                    result = self._place_market_order_impl(figi, quantity, "SELL")
            return result

        limit_price = self._round_to_min_increment_advanced(figi, price * 0.99)
        info(f"📋 ЛИМИТНАЯ ПРОДАЖА: по {limit_price:.2f}₽ (-1%)")

        try:
            result = self._place_limit_order_with_fallback(figi, quantity, "SELL", limit_price)

            if not result:
                warning(f"🔄 Лимитная с fallback не удалась, пробуем РЫНОЧНУЮ...")
                result = self._place_market_order_impl(figi, quantity, "SELL")

            return result
        except Exception as e:
            error_msg = str(e)
            if "30100" in error_msg:
                warning(f"⚠️ Ошибка 30100 при лимитной продаже {ticker} (некорректная цена)")
                warning(f"   Пробуем РЫНОЧНУЮ ЗАЯВКУ...")
                result = self._place_market_order_impl(figi, quantity, "SELL")
                if result:
                    success(f"✅ Рыночная продажа {ticker} успешна (после ошибки 30100)")
                    return True
                return False
            else:
                error(f"❌ Ошибка лимитной продажи {ticker}: {error_msg[:100]}")
                return False

    # ========== НОВЫЙ МЕТОД ДЛЯ ПРОВЕРКИ СТАТУСА ЗАЯВКИ ==========
    def check_order_status(self, order_id: str, figi: str = None) -> Optional[Dict[str, Any]]:
        validator = self._init_validator()
        return validator.get_order_status(order_id)

    def wait_for_order_completion(self, order_id: str, max_wait_seconds: int = 30) -> Dict[str, Any]:
        validator = self._init_validator()
        return validator.wait_for_completion(order_id, max_wait_seconds)

    def get_active_orders_detailed(self) -> List[Dict[str, Any]]:
        validator = self._init_validator()
        return validator.get_active_orders()

    def _place_limit_order_with_fallback(self, figi: str, quantity: int, direction: str, target_price: float) -> bool:
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        if self.is_confirmation_required(figi):
            error(f"❌ {ticker} требует подтверждения сделок (OTC)")
            error(f"   🔧 Закройте позицию вручную в приложении Т-Банк")
            return False

        with Client(self.token) as client:
            try:
                from decimal import Decimal, ROUND_HALF_UP

                step = self._get_min_price_increment_advanced(figi)
                if step <= 0:
                    step = 0.01

                target_price = round(target_price / step) * step
                target_price = max(target_price, step)

                if direction == "SELL" and target_price < 0.01:
                    target_price = self._round_to_min_increment_advanced(figi, 0.01)

                if target_price <= 0:
                    error(f"❌ Некорректная цена: {target_price}")
                    return False

                price_str = f"{target_price:.2f}"
                price_decimal = Decimal(price_str).quantize(Decimal(str(step)), rounding=ROUND_HALF_UP)
                price_quotation = decimal_to_quotation(price_decimal)

                dir_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
                confirm_margin = (direction == "SELL")

                info(f"📋 ЛИМИТНАЯ: {direction} {quantity} шт {ticker} по {target_price:.2f}₽")

                order = client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    price=price_quotation,
                    direction=dir_map[direction],
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_LIMIT,
                    order_id=str(uuid.uuid4()),
                    confirm_margin_trade=confirm_margin
                )

                if order and order.order_id:
                    info(f"✅ Лимитная заявка {direction} {quantity} {ticker} размещена, ID: {order.order_id[:8]}...")
                    return True
                else:
                    warning(f"⚠️ Лимитная заявка {direction} {quantity} {ticker} не размещена")
                    return False

            except Exception as e:
                error_msg = str(e)
                info(f"❌ Ошибка лимитной заявки {ticker}: {error_msg[:200]}")

                if "30100" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30100 - некорректная цена {target_price}")
                    info(f"   🔄 Fallback: {direction} {quantity} шт {ticker} через РЫНОК")
                    market_result = self._place_market_order_impl(figi, quantity, direction)
                    if market_result:
                        success(f"✅ Рыночная заявка {direction} {quantity} {ticker} УСПЕШНА (fallback после 30100)")
                        return True
                    return False

                elif "30099" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30099 - некорректная цена для лимитной заявки")
                    warning(f"   🔄 Пробуем рыночную заявку")
                    return self._place_market_order_impl(figi, quantity, direction)

                elif "30042" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30042 - недостаточно средств или маржи")

                    if self.is_confirmation_required(figi):
                        warning(f"   🔐 {ticker}: OTC инструмент, требуется ручное закрытие")
                        return False

                    try:
                        positions = self.get_positions()
                        real_figis = {p['figi'] for p in positions if abs(p.get('quantity', 0)) > 0}
                        if figi not in real_figis:
                            warning(f"   🧹 Позиции {ticker} нет у брокера! Удаляем из менеджера")
                            position_manager.remove_position(figi)
                            return True
                    except Exception as ex:
                        debug(f"   ⚠️ Ошибка проверки позиций: {ex}")

                    info(f"   💡 РЕКОМЕНДАЦИЯ: Пополните счёт или закройте часть позиций")
                    market_result = self._place_market_order_impl(figi, quantity, direction)
                    if market_result:
                        success(f"✅ Рыночная заявка {direction} {quantity} {ticker} УСПЕШНА (fallback после 30042)")
                        return True
                    return False

                elif "30068" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30068 - инструмент не торгуется или недоступен")
                    self.mark_as_confirmation_required(figi)
                    return False

                elif "90002" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 90002 - нарушено предусловие")
                    self.mark_as_confirmation_required(figi)
                    return False

                elif "30240" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30240 - стоп-ордера НЕ ПОДДЕРЖИВАЮТСЯ")
                    self._no_stop_orders.add(figi)
                    return False

                else:
                    error(f"❌ НЕИЗВЕСТНАЯ ошибка: {error_msg[:100]}")
                    return False

    def _place_market_order(self, figi: str, quantity: int, direction: str, is_short: bool = False) -> bool:
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        if direction == "BUY":
            info(f"🟢 РЫНОЧНАЯ ПОКУПКА: {quantity} шт {ticker}")
            return self._place_market_order_impl(figi, quantity, "BUY")
        else:
            info(f"🔴 РЫНОЧНАЯ ПРОДАЖА: {quantity} шт {ticker}")
            return self._place_market_order_impl(figi, quantity, "SELL")

    def _place_market_order_impl(self, figi: str, quantity: int, direction: str) -> bool:
        from t_tech.invest import OrderDirection, OrderType

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        original_qty = quantity
        lot_size = self._get_lot_size(figi)

        if lot_size > 1:
            lots = quantity // lot_size
            if lots == 0:
                lots = 1
            quantity = lots * lot_size

            if quantity != original_qty:
                debug(f"🔄 MARKET {direction} {ticker}: округление {original_qty} → {quantity} (лот={lot_size})")

        try:
            with Client(self.token) as client:
                dir_map = {
                    "BUY": OrderDirection.ORDER_DIRECTION_BUY,
                    "SELL": OrderDirection.ORDER_DIRECTION_SELL
                }

                info(f"   📡 ОТПРАВКА рыночной заявки: {direction} {quantity} шт {ticker}")

                confirm_margin = (direction == "SELL")

                order = client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    direction=dir_map[direction],
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_MARKET,
                    order_id=str(uuid.uuid4()),
                    confirm_margin_trade=confirm_margin
                )

                if order and order.order_id:
                    success(f"   ✅ Рыночный ордер {direction} {quantity} шт {ticker} ИСПОЛНЕН")
                    return True
                else:
                    warning(f"   ⚠️ Рыночный ордер {direction} {quantity} шт {ticker} НЕ ИСПОЛНЕН")
                    return False

        except Exception as e:
            error_msg = str(e)
            info(f"   ❌ Ошибка рыночного ордера {ticker}: {error_msg[:100]}")

            if "30068" in error_msg or "90002" in error_msg:
                warning(f"   ⚠️ {ticker}: ОШИБКА - инструмент недоступен")
                self.mark_as_confirmation_required(figi)
                return False

            elif "30042" in error_msg:
                warning(f"   ⚠️ {ticker}: ОШИБКА 30042 - рыночная заявка отклонена")
                current_price = self.get_current_price(figi)
                if current_price:
                    if direction == "SELL":
                        limit_price = self._round_to_min_increment_advanced(figi, current_price * 0.97)
                    else:
                        limit_price = self._round_to_min_increment_advanced(figi, current_price * 1.03)

                    info(f"   📋 АГРЕССИВНАЯ ЛИМИТНАЯ: {direction} {quantity} шт {ticker} по {limit_price:.2f}₽")
                    return self.place_limit_order(figi, quantity, direction, limit_price)
                else:
                    error(f"   ❌ Не удалось получить цену для лимитной заявки")
                    return False

            elif "30240" in error_msg:
                warning(f"   ⚠️ {ticker}: ОШИБКА 30240 - стоп-ордера НЕ ПОДДЕРЖИВАЮТСЯ")
                self._no_stop_orders.add(figi)
                return False

            else:
                warning(f"   ❌ Ошибка рыночного ордера {ticker}: {error_msg[:100]}")
                return False

    def place_limit_order(self, figi: str, quantity: int, direction: str, target_price: float) -> bool:
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        if self.is_confirmation_required(figi):
            error(f"❌ {ticker} требует подтверждения сделок")
            return False

        with Client(self.token) as client:
            try:
                from decimal import Decimal, ROUND_HALF_UP

                step = self._get_min_price_increment_advanced(figi)
                if step <= 0:
                    step = 0.01

                target_price = round(target_price / step) * step
                target_price = max(target_price, step)

                if direction == "SELL" and target_price < 0.01:
                    target_price = self._round_to_min_increment_advanced(figi, 0.01)

                if target_price <= 0:
                    error(f"❌ Некорректная цена для лимитной заявки: {target_price}")
                    return False

                price_str = f"{target_price:.2f}"
                price_decimal = Decimal(price_str).quantize(Decimal(str(step)), rounding=ROUND_HALF_UP)
                price_quotation = decimal_to_quotation(price_decimal)

                dir_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
                confirm_margin = (direction == "SELL")

                info(f"📋 ЛИМИТНАЯ: {direction} {quantity} шт {ticker} по {target_price:.2f}₽")

                order = client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    price=price_quotation,
                    direction=dir_map[direction],
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_LIMIT,
                    order_id=str(uuid.uuid4()),
                    confirm_margin_trade=confirm_margin
                )

                if order and order.order_id:
                    info(f"✅ Лимитная заявка размещена, ID: {order.order_id[:8]}...")
                    return True
                return False

            except Exception as e:
                error_msg = str(e)
                if "30240" in error_msg:
                    warning(f"⚠️ {ticker} требует подтверждения сделок")
                    self.mark_as_confirmation_required(figi)
                elif "30100" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30100 - некорректная цена {target_price}")
                    warning(f"   Пробуем рыночную заявку...")
                    return self._place_market_order_impl(figi, quantity, direction)
                else:
                    warning(f"❌ Ошибка лимитной заявки: {error_msg[:100]}")
                return False

    def place_pending_order(self, figi: str, quantity: int, direction: str, target_price: float,
                            expiry_hours: int = 24) -> bool:
        return self.place_limit_order(figi, quantity, direction, target_price)

    def _is_market_order_available(self, figi: str) -> bool:
        try:
            status = self.get_trading_status(figi)
            return status.get('market_order_available', False)
        except Exception as e:
            debug(f"Ошибка проверки доступности рыночных заявок для {figi}: {e}")
            return True

    def _is_otc_mode(self) -> bool:
        try:
            now = datetime.now()
            if now.weekday() >= 5:
                return True
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
        except Exception as e:
            debug(f"Ошибка проверки OTC режима: {e}")
            return False

    def mark_as_confirmation_required(self, figi: str):
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        self._confirmation_required_tickers.add(ticker)
        self._confirmation_blocklist_time[ticker] = time.time()

        if not hasattr(self, '_confirmation_cache'):
            self._confirmation_cache = {}
        if not hasattr(self, '_confirmation_cache_time'):
            self._confirmation_cache_time = {}

        self._confirmation_cache[figi] = True
        self._confirmation_cache_time[figi] = time.time()

        warning(f"⚠️ {ticker} добавлен в черный список на 1 час")

    def clear_confirmation_blocklist(self, ticker: str = None):
        if ticker:
            ticker_clean = ticker.upper()
            if ticker_clean in self._confirmation_required_tickers:
                self._confirmation_required_tickers.discard(ticker_clean)
                if ticker_clean in self._confirmation_blocklist_time:
                    del self._confirmation_blocklist_time[ticker_clean]
                info(f"✅ {ticker_clean} удален из черного списка")
        else:
            count = len(self._confirmation_required_tickers)
            self._confirmation_required_tickers.clear()
            self._confirmation_blocklist_time.clear()
            info(f"✅ Черный список очищен (удалено {count} тикеров)")

    def get_confirmation_blocklist(self) -> List[str]:
        now = time.time()
        to_remove = []
        for ticker, block_time in self._confirmation_blocklist_time.items():
            if now - block_time > self._confirmation_blocklist_ttl:
                to_remove.append(ticker)

        for ticker in to_remove:
            self._confirmation_required_tickers.discard(ticker)
            if ticker in self._confirmation_blocklist_time:
                del self._confirmation_blocklist_time[ticker]

        return list(self._confirmation_required_tickers)

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def get_user_tariff(self) -> Tuple[str, float]:
        self._wait_for_rate_limit()

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
        self._wait_for_rate_limit()

        cache_key = "margin_info"
        cached_result = margin_cache.get(cache_key)
        if cached_result is not None:
            return cached_result.copy()

        with Client(self.token) as client:
            try:
                margin = client.users.get_margin_attributes(account_id=self.account_id)
                if margin:
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

                    margin_cache.set(cache_key, result.copy(), ttl=30)
                    return result
            except Exception as e:
                debug(f"Ошибка получения маржи: {e}")

            return {}

    def get_available_funds(self) -> Tuple[float, float, float]:
        self._wait_for_rate_limit()

        with Client(self.token) as client:
            try:
                margin = client.users.get_margin_attributes(account_id=self.account_id)
                if margin:
                    total = float(quotation_to_decimal(margin.liquid_portfolio))
                    starting = float(quotation_to_decimal(margin.starting_margin))
                    free = total - starting
                    return max(0.0, free), total, 0.0
            except Exception as e:
                debug(f"Ошибка получения маржи для доступных средств: {e}")

            try:
                portfolio = client.operations.get_portfolio(account_id=self.account_id)
                total = float(quotation_to_decimal(portfolio.total_amount_portfolio))
                return total, total, 0.0
            except Exception as e:
                debug(f"Ошибка получения портфеля: {e}")

            error("❌ НЕ УДАЛОСЬ ПОЛУЧИТЬ БАЛАНС!")
            return 0.0, 0.0, 0.0

    def get_user_info(self) -> Tuple[bool, str]:
        self._wait_for_rate_limit()

        with Client(self.token) as client:
            try:
                info_obj = client.users.get_info()
                return info_obj.qual_status, info_obj.tariff
            except Exception as e:
                error(f"❌ Ошибка получения статуса: {e}")
                return False, "unknown"

    def check_qual_status(self) -> Tuple[bool, str]:
        return self.get_user_info()

    def get_positions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        self._wait_for_rate_limit()

        cache_key = "positions"

        if not force_refresh:
            cached_result = positions_cache.get(cache_key)
            if cached_result is not None:
                return cached_result.copy()

        with Client(self.token) as client:
            try:
                portfolio = client.operations.get_portfolio(account_id=self.account_id)
                positions = []
                for pos in portfolio.positions:
                    if pos.figi != "RUB000UTSTOM" and pos.quantity.units != 0:
                        is_blocked = getattr(pos, 'blocked', False)

                        positions.append({
                            'figi': pos.figi,
                            'quantity': pos.quantity.units,
                            'avg_price': float(quotation_to_decimal(pos.average_position_price)),
                            'blocked': is_blocked,
                            'ticker': self._get_ticker_by_figi(pos.figi) or pos.figi[:8]
                        })

                        if is_blocked:
                            warning(f"🔒 Позиция {positions[-1]['ticker']} ЗАБЛОКИРОВАНА брокером!")

                info(f"📊 Получено позиций от брокера: {len(positions)}")

                if len(positions) == 0:
                    positions_cache.delete(cache_key)
                    info(f"   📭 Позиций нет, кэш очищен")
                    return []

                positions_cache.set(cache_key, positions.copy(), ttl=5)
                return positions

            except Exception as e:
                warning(f"Ошибка получения позиций: {e}")
                return []

    def clear_positions_cache(self):
        cache_key = "positions"
        positions_cache.delete(cache_key)
        info(f"🧹 Кэш позиций очищен")

    def get_active_orders(self) -> List[Dict[str, Any]]:
        self._wait_for_rate_limit()

        try:
            with Client(self.token) as client:
                orders_response = client.orders.get_orders(account_id=self.account_id)
                result = []

                for order in orders_response.orders:
                    direction = "BUY" if order.direction == 1 else "SELL"

                    price = 0.0
                    if hasattr(order, 'price') and order.price:
                        price = float(quotation_to_decimal(order.price))

                    status = "ACTIVE"
                    if hasattr(order, 'state'):
                        status = str(order.state)
                    elif hasattr(order, 'order_state'):
                        status = str(order.order_state)

                    executed = 0
                    if hasattr(order, 'executed_lots'):
                        executed = order.executed_lots

                    result.append({
                        'order_id': order.order_id,
                        'figi': order.figi,
                        'direction': direction,
                        'price': price,
                        'quantity': order.lots_requested,
                        'executed_quantity': executed,
                        'status': status,
                        'ticker': self._get_ticker_by_figi(order.figi) or order.figi[:8],
                        'created_at': getattr(order, 'created_at', None)
                    })

                debug(f"📋 Получено активных заявок: {len(result)}")
                return result

        except Exception as e:
            debug(f"Ошибка получения заявок: {e}")
            return []

    def cancel_all_duplicate_orders(self, ticker: str = None) -> Dict[str, Any]:
        from collections import defaultdict

        try:
            info(f"🧹 Синхронная очистка дубликатов{f' для {ticker}' if ticker else ''}...")

            active_orders = self.get_active_orders()

            if not active_orders:
                debug("   📭 Нет активных заявок для очистки")
                return {"success": True, "cancelled": 0, "message": "Нет активных заявок"}

            if ticker:
                active_orders = [o for o in active_orders if o.get('ticker') == ticker]
                if not active_orders:
                    return {"success": True, "cancelled": 0, "message": f"Нет заявок для {ticker}"}

            groups = defaultdict(list)
            for order in active_orders:
                key = f"{order.get('ticker')}_{order.get('direction')}"
                groups[key].append(order)

            cancelled = 0
            failed = 0
            details = []

            for key, orders in groups.items():
                if len(orders) <= 1:
                    continue

                ticker_key, direction = key.split('_')

                market_orders = [o for o in orders if o.get('price', 0) == 0]
                limit_orders = [o for o in orders if o.get('price', 0) > 0]

                if limit_orders:
                    if direction == "BUY":
                        best_order = min(limit_orders, key=lambda x: x.get('price', float('inf')))
                        reason = "оставляем лучшую лимитную цену (минимальную)"
                    else:
                        best_order = max(limit_orders, key=lambda x: x.get('price', 0))
                        reason = "оставляем лучшую лимитную цену (максимальную)"
                    to_cancel = [o for o in orders if o.get('order_id') != best_order.get('order_id')]
                else:
                    orders.sort(key=lambda x: x.get('order_id', ''))
                    best_order = orders[-1]
                    to_cancel = orders[:-1]
                    reason = "оставляем последнюю рыночную заявку"

                info(f"   🔍 {key}: {len(orders)} заявок, оставляем {best_order.get('order_id', '')[:8]}")

                for order in to_cancel:
                    result = self.cancel_order(order.get('order_id'))
                    if result:
                        cancelled += 1
                        details.append({
                            'order_id': order.get('order_id', '')[:8],
                            'ticker': ticker_key,
                            'direction': direction,
                            'price': order.get('price', 0),
                            'reason': reason
                        })
                    else:
                        failed += 1

            if cancelled > 0:
                success(f"   ✅ Отменено дублирующихся заявок: {cancelled}")
                for detail in details[:5]:
                    info(f"      - {detail['ticker']} {detail['direction']}: {detail['reason']}")
            else:
                debug("   ✅ Дублирующихся заявок не найдено")

            return {
                "success": True,
                "cancelled": cancelled,
                "failed": failed,
                "details": details
            }

        except Exception as e:
            error(f"❌ Ошибка очистки дубликатов: {e}")
            return {"success": False, "error": str(e), "cancelled": 0}

    def cancel_order(self, order_id: str) -> bool:
        self._wait_for_rate_limit()

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

    def cancel_stale_limit_orders(self, max_age_seconds: int = 300) -> Dict[str, Any]:
        from datetime import datetime, timedelta
        from trading_bot.utils.time_utils import get_moscow_time

        try:
            now = get_moscow_time()
            cutoff_time = now - timedelta(seconds=max_age_seconds)

            active_orders = self.get_active_orders()
            if not active_orders:
                return {"success": True, "cancelled": 0, "message": "Нет активных заявок"}

            stale_orders = []
            for order in active_orders:
                created_at = order.get('created_at')
                if created_at:
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        except Exception as e:
                            debug(f"Ошибка парсинга даты: {e}")
                            continue

                    if created_at and created_at < cutoff_time:
                        stale_orders.append(order)

            if not stale_orders:
                return {"success": True, "cancelled": 0, "message": "Нет устаревших заявок"}

            info(f"🧹 Найдено {len(stale_orders)} устаревших заявок (старше {max_age_seconds}с)")

            cancelled = 0
            failed = 0

            for order in stale_orders:
                order_id = order.get('order_id')
                ticker = order.get('ticker', 'unknown')
                price = order.get('price', 0)
                direction = order.get('direction', 'UNKNOWN')

                info(f"   ⏰ Отмена устаревшей заявки: {ticker} {direction} по {price:.2f}₽")

                if self.cancel_order(order_id):
                    cancelled += 1
                else:
                    failed += 1
                    warning(f"      ❌ Не удалось отменить заявку {order_id[:8]}")

            info(f"   ✅ Отменено устаревших заявок: {cancelled}, ошибок: {failed}")

            return {
                "success": True,
                "cancelled": cancelled,
                "failed": failed,
                "stale_count": len(stale_orders)
            }

        except Exception as e:
            error(f"❌ Ошибка отмены устаревших заявок: {e}")
            return {"success": False, "error": str(e), "cancelled": 0}

    def get_current_price(self, figi: str) -> Optional[float]:
        self._wait_for_rate_limit()

        cache_key = figi
        cached_price = price_cache.get(cache_key)
        if cached_price is not None:
            return cached_price

        with Client(self.token) as client:
            try:
                last_prices = client.market_data.get_last_prices(figi=[figi])
                if last_prices and last_prices.last_prices:
                    price = float(quotation_to_decimal(last_prices.last_prices[0].price))
                    price_cache.set(cache_key, price, ttl=5)
                    return price
            except Exception as e:
                debug(f"Ошибка получения цены для {figi}: {e}")
            return None

    def get_all_shares(self, limit: int = 1000, retry: int = 3) -> List[Dict[str, Any]]:
        import time
        from grpc import RpcError, StatusCode

        cache_key = f"all_shares_{limit}"

        cached_result = instruments_cache.get(cache_key)
        if cached_result is not None:
            return cached_result[:limit] if limit else cached_result

        for attempt in range(retry):
            try:
                with Client(self.token) as client:
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
                                'asset_uid': stock.asset_uid if hasattr(stock, 'asset_uid') else None,
                            })
                            if len(result) >= limit:
                                break

                    instruments_cache.set(cache_key, result, ttl=300)
                    info(f"📊 Загружено {len(result)} акций")
                    return result

            except RpcError as e:
                if e.code() == StatusCode.UNAVAILABLE:
                    warning(f"⚠️ gRPC недоступен, попытка {attempt + 1}/{retry}")
                    time.sleep(2 ** attempt)
                    continue
                error(f"❌ gRPC ошибка: {e}")
                if attempt == retry - 1:
                    return []

            except Exception as e:
                error(f"❌ Ошибка получения списка акций: {e}")
                if attempt == retry - 1:
                    return []
                time.sleep(2 ** attempt)

        return []

    def get_candles(self, figi: str, days: int = 5, interval_minutes: int = 5) -> List[Tuple[float, float]]:
        self._wait_for_rate_limit()

        cache_key = f"{figi}_{days}_{interval_minutes}"

        cached_result = candles_cache.get(cache_key)
        if cached_result is not None:
            return cached_result.copy()

        lock_key = f"candle_lock_{figi}"
        if not hasattr(self, '_candle_locks'):
            self._candle_locks = {}

        if lock_key not in self._candle_locks:
            self._candle_locks[lock_key] = Lock()

        with self._candle_locks[lock_key]:
            cached_result = candles_cache.get(cache_key)
            if cached_result is not None:
                return cached_result.copy()

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

                    if result:
                        candles_cache.set(cache_key, result.copy(), ttl=30)

                    return result
                except Exception as e:
                    if "30014" in str(e):
                        return []
                    debug(f"Ошибка получения свечей {figi}: {e}")
                    return []

    def get_trading_status(self, figi: str) -> Dict[str, Any]:
        self._wait_for_rate_limit()

        cache_key = f"trading_status_{figi}"
        cached_result = instruments_cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        try:
            with Client(self.token) as client:
                status = client.market_data.get_trading_status(instrument_id=figi)

                result = {
                    'trading_status': status.trading_status,
                    'api_trade_available': status.api_trade_available_flag,
                    'market_order_available': status.market_order_available_flag,
                    'limit_order_available': status.limit_order_available_flag,
                    'trading_status_description': self._get_trading_status_description(status.trading_status),
                }

                result['order_types'] = []
                if result['market_order_available']:
                    result['order_types'].append('MARKET')
                if result['limit_order_available']:
                    result['order_types'].append('LIMIT')

                instruments_cache.set(cache_key, result.copy(), ttl=60)
                return result

        except Exception as e:
            debug(f"Ошибка получения статуса для {figi}: {e}")
            return {
                'trading_status': 0,
                'api_trade_available': False,
                'market_order_available': False,
                'limit_order_available': False,
                'order_types': [],
                'trading_status_description': 'Ошибка получения статуса'
            }

    def _get_trading_status_description(self, status_code) -> str:
        status_map = {
            0: 'Торговый статус не определён',
            1: 'Торги не начались (pre-market)',
            2: 'Торги активны (основная сессия)',
            3: 'Торги приостановлены',
            4: 'Торги завершены (основная сессия)',
            5: 'Торги активны (вечерняя сессия)',
            6: 'Торги не проводятся (выходной)',
            7: 'Торги активны (ДСВД)',
            8: 'Торги завершены (ДСВД)',
        }
        return status_map.get(status_code, f'Неизвестный статус ({status_code})')

    def check_instrument_type(self, ticker: str) -> Dict[str, Any]:
        result = {
            'ticker': ticker,
            'figi': None,
            'instrument_type': None,
            'exchange': None,
            'requires_confirmation': False,
            'trading_status': None,
            'api_trade_available': None,
            'market_order_available': None,
            'is_otc': False,
            'details': {}
        }

        figi = self._get_figi_by_ticker(ticker)
        if not figi:
            result['error'] = f"FIGI для {ticker} не найден"
            return result

        result['figi'] = figi

        instrument_info = self._get_instrument_by_figi(figi)
        if instrument_info:
            result['instrument_type'] = instrument_info.get('instrument_type')
            result['exchange'] = instrument_info.get('exchange')
            result['details'] = instrument_info

        trading_status = self.get_trading_status(figi)
        result['trading_status'] = trading_status
        result['api_trade_available'] = trading_status.get('api_trade_available')
        result['market_order_available'] = trading_status.get('market_order_available')

        result['requires_confirmation'] = self.is_confirmation_required(figi)
        result['is_otc'] = result['requires_confirmation']

        return result

    def is_confirmation_required(self, figi: str) -> bool:
        from trading_bot.logger import debug, warning, info
        import time

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        now = time.time()
        if figi in self._confirmation_cache:
            cache_time = self._confirmation_cache_time.get(figi, 0)
            if now - cache_time < 3600:
                result = self._confirmation_cache[figi]
                debug(f"   📦 Кэш OTC для {ticker}: {'✅ ДА (OTC)' if result else '❌ НЕТ'}")
                return result

        debug(f"   🔍 ПРОВЕРКА OTC ДЛЯ {ticker} (FIGI={figi[:12]}...)")

        try:
            with Client(self.token) as client:
                debug(f"   📡 ШАГ 1/5: get_trading_status()")
                status = client.market_data.get_trading_status(instrument_id=figi)

                api_available = getattr(status, 'api_trade_available_flag', False)
                market_available = getattr(status, 'market_order_available_flag', False)
                limit_available = getattr(status, 'limit_order_available_flag', False)

                debug(f"      📊 API доступна: {'✅' if api_available else '❌'}")
                debug(f"      📊 Рыночные заявки: {'✅' if market_available else '❌'}")
                debug(f"      📊 Лимитные заявки: {'✅' if limit_available else '❌'}")

                if not api_available:
                    warning(f"   🔐 {ticker}: API торговля НЕ ДОСТУПНА → OTC")
                    self._confirmation_cache[figi] = True
                    self._confirmation_cache_time[figi] = now
                    return True

                if not market_available and not limit_available:
                    warning(f"   🔐 {ticker}: НЕТ доступных типов заявок → OTC")
                    self._confirmation_cache[figi] = True
                    self._confirmation_cache_time[figi] = now
                    return True

                debug(f"   📡 ШАГ 2/5: instruments.shares()")
                try:
                    shares_response = client.instruments.shares()
                    instrument_info = None

                    for share in shares_response.instruments:
                        if share.figi == figi:
                            instrument_info = share
                            break

                    if instrument_info:
                        exchange = getattr(instrument_info, 'exchange', '')
                        for_qual = getattr(instrument_info, 'for_qual_investor_flag', False)

                        debug(f"      📊 Биржа: {exchange}")
                        debug(f"      📊 Квал. инвестор: {'✅' if for_qual else '❌'}")

                        if exchange == 'INSTRUMENT_EXCHANGE_DEALER' or 'DEALER' in str(exchange):
                            warning(f"   🔐 {ticker}: ВНЕБИРЖЕВОЙ инструмент (exchange={exchange}) → OTC")
                            self._confirmation_cache[figi] = True
                            self._confirmation_cache_time[figi] = now
                            return True

                        if for_qual:
                            warning(f"   🔐 {ticker}: ТРЕБУЕТ квалифицированного инвестора → OTC")
                            self._confirmation_cache[figi] = True
                            self._confirmation_cache_time[figi] = now
                            return True

                except Exception as e:
                    debug(f"      ⚠️ Не удалось получить информацию об инструменте: {e}")

                debug(f"   📡 ШАГ 3/5: Проверка стакана")
                try:
                    orderbook = client.market_data.get_order_book(figi=figi, depth=1)
                    if orderbook:
                        bid_exists = len(orderbook.bids) > 0 and orderbook.bids[0].quantity > 0
                        ask_exists = len(orderbook.asks) > 0 and orderbook.asks[0].quantity > 0

                        debug(f"      📊 Заявки на покупку: {'✅ есть' if bid_exists else '❌ нет'}")
                        debug(f"      📊 Заявки на продажу: {'✅ есть' if ask_exists else '❌ нет'}")

                except Exception as e:
                    debug(f"      ⚠️ Ошибка проверки стакана: {e}")

                debug(f"   📡 ШАГ 4/5: ИТОГОВЫЙ ВЕРДИКТ")
                info(f"   ✅ {ticker}: НЕ ТРЕБУЕТ подтверждения (можно торговать)")

                self._confirmation_cache[figi] = False
                self._confirmation_cache_time[figi] = now
                return False

        except Exception as e:
            error(f"   ❌ ОШИБКА проверки OTC для {ticker}: {e}")
            self._confirmation_cache[figi] = True
            self._confirmation_cache_time[figi] = now
            return True

    def check_instrument_tradability(self, figi: str) -> Dict[str, Any]:
        result = {
            'figi': figi,
            'ticker': self._get_ticker_by_figi(figi),
            'api_trade_available': False,
            'market_order_available': False,
            'limit_order_available': False,
            'exchange': 'UNKNOWN',
            'requires_confirmation': False,
            'is_tradable': False,
            'reason': None,
            'details': {}
        }

        try:
            with Client(self.token) as client:
                status = client.market_data.get_trading_status(instrument_id=figi)
                result['api_trade_available'] = getattr(status, 'api_trade_available_flag', False)
                result['market_order_available'] = getattr(status, 'market_order_available_flag', False)
                result['limit_order_available'] = getattr(status, 'limit_order_available_flag', False)

                try:
                    instrument = client.instruments.share_by(figi)
                    if hasattr(instrument, 'instrument'):
                        inst = instrument.instrument
                        result['exchange'] = getattr(inst, 'exchange', 'UNKNOWN')
                        result['details']['lot'] = getattr(inst, 'lot', 1)
                        result['details']['currency'] = getattr(inst, 'currency', 'UNKNOWN')
                        result['details']['for_qual_investor'] = getattr(inst, 'for_qual_investor_flag', False)
                except Exception as e:
                    debug(f"Не удалось получить информацию об инструменте {figi}: {e}")

                if not result['api_trade_available']:
                    result['requires_confirmation'] = True
                    result['reason'] = "API торговля недоступна"
                elif not result['market_order_available'] and not result['limit_order_available']:
                    result['requires_confirmation'] = True
                    result['reason'] = "Нет доступных типов заявок"
                elif 'DEALER' in result['exchange']:
                    result['requires_confirmation'] = True
                    result['reason'] = f"Внебиржевой инструмент (exchange={result['exchange']})"
                elif result['details'].get('for_qual_investor', False):
                    result['requires_confirmation'] = True
                    result['reason'] = "Требует квалифицированного инвестора"
                else:
                    result['requires_confirmation'] = False
                    result['reason'] = "OK"

                result['is_tradable'] = (
                        result['api_trade_available'] and
                        (result['market_order_available'] or result['limit_order_available']) and
                        not result['requires_confirmation']
                )

        except Exception as e:
            result['reason'] = f"Ошибка API: {str(e)[:100]}"
            result['requires_confirmation'] = True

        return result

    def _get_instrument_by_figi(self, figi: str) -> Optional[Dict]:
        cache_key = f"instrument_{figi}"
        cached_result = instruments_cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        try:
            with Client(self.token) as client:
                try:
                    share = client.instruments.share_by(id_type=1, id=figi)
                    if share and share.instrument:
                        result = {
                            'figi': share.instrument.figi,
                            'ticker': share.instrument.ticker,
                            'name': share.instrument.name,
                            'instrument_type': 'share',
                            'exchange': self._get_exchange_name(share.instrument),
                            'for_qual_investor_flag': getattr(share.instrument, 'for_qual_investor_flag', False),
                            'api_trade_available': getattr(share.instrument, 'api_trade_available_flag', True),
                            'lot': share.instrument.lot,
                            'currency': share.instrument.currency,
                        }
                        instruments_cache.set(cache_key, result, ttl=3600)
                        return result
                except Exception as e:
                    debug(f"Не удалось получить акцию по FIGI {figi}: {e}")

                try:
                    bond = client.instruments.bond_by(id_type=1, id=figi)
                    if bond and bond.instrument:
                        result = {
                            'figi': bond.instrument.figi,
                            'ticker': bond.instrument.ticker,
                            'name': bond.instrument.name,
                            'instrument_type': 'bond',
                            'exchange': self._get_exchange_name(bond.instrument),
                            'for_qual_investor_flag': getattr(bond.instrument, 'for_qual_investor_flag', False),
                            'api_trade_available': getattr(bond.instrument, 'api_trade_available_flag', True),
                            'lot': bond.instrument.lot,
                            'currency': bond.instrument.currency,
                        }
                        instruments_cache.set(cache_key, result, ttl=3600)
                        return result
                except Exception as e:
                    debug(f"Не удалось получить облигацию по FIGI {figi}: {e}")

        except Exception as e:
            debug(f"Ошибка получения информации об инструменте {figi}: {e}")

        return None

    def _get_exchange_name(self, instrument) -> str:
        try:
            if hasattr(instrument, 'exchange'):
                return instrument.exchange
            if hasattr(instrument, 'primary_exchange'):
                return instrument.primary_exchange
            if hasattr(instrument, 'exchange_name'):
                return instrument.exchange_name
        except Exception as e:
            debug(f"Ошибка получения названия биржи: {e}")
        return 'UNKNOWN'

    def get_stop_orders(self) -> List[Dict[str, Any]]:
        self._wait_for_rate_limit()

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

    def supports_stop_orders(self, figi: str) -> bool:
        try:
            status = self.get_trading_status(figi)

            if not (status.get('market_order_available', False) and status.get('limit_order_available', False)):
                return False

            try:
                from t_tech.invest import OrderDirection, StopOrderExpirationType
                from decimal import Decimal

                current_price = self.get_current_price(figi)
                if not current_price:
                    return False

                test_stop_price = current_price * 0.5 if current_price > 0 else 100
                test_stop_price = round(test_stop_price, 2)

                stop_price_quotation = decimal_to_quotation(Decimal(str(test_stop_price)))

                with Client(self.token) as client:
                    client.stop_orders.post_stop_order(
                        figi=figi,
                        quantity=1,
                        price=None,
                        stop_price=stop_price_quotation,
                        direction=OrderDirection.ORDER_DIRECTION_SELL,
                        account_id=self.account_id,
                        stop_order_type=2,
                        expiration_type=StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL,
                        order_id=str(uuid.uuid4())
                    )
                    return True

            except Exception as e:
                error_msg = str(e)
                if "30240" in error_msg:
                    debug(f"🔐 {figi}: стоп-ордера НЕ поддерживаются (ошибка 30240)")
                    return False
                return True

        except Exception as e:
            debug(f"Ошибка проверки стоп-ордеров для {figi}: {e}")
            return False

    def _get_min_price_increment(self, figi: str) -> float:
        return 0.01

    def _round_to_min_increment(self, figi: str, price: float) -> float:
        return round(price, 2)

    def _get_ticker_by_figi(self, figi: str) -> Optional[str]:
        resolver = get_figi_resolver()
        return resolver.get_ticker_by_figi(figi)

    def _get_figi_by_ticker(self, ticker: str) -> Optional[str]:
        resolver = get_figi_resolver()
        return resolver.get_figi_by_ticker(ticker)

    def _get_min_price_increment_advanced(self, figi: str) -> float:
        try:
            with Client(self.token) as client:
                try:
                    instrument = client.instruments.share_by(figi)
                    if hasattr(instrument.instrument, 'min_price_increment'):
                        step = float(instrument.instrument.min_price_increment)
                        if step > 0:
                            return step
                except Exception as e:
                    debug(f"Не удалось получить шаг цены акции: {e}")

                try:
                    instrument = client.instruments.bond_by(figi)
                    if hasattr(instrument.instrument, 'min_price_increment'):
                        step = float(instrument.instrument.min_price_increment)
                        if step > 0:
                            return step
                except Exception as e:
                    debug(f"Не удалось получить шаг цены облигации: {e}")

        except Exception as e:
            debug(f"Ошибка получения шага цены: {e}")
        return 0.01

    def _round_to_min_increment_advanced(self, figi: str, price: float) -> float:
        step = self._get_min_price_increment_advanced(figi)
        if step <= 0:
            step = 0.01

        rounded = round(price / step) * step

        step_str = str(step)
        if '.' in step_str:
            decimal_places = len(step_str.split('.')[1])
        else:
            decimal_places = 0

        result = round(rounded, decimal_places)

        if result < 0.01 and price > 0:
            result = step if step >= 0.01 else 0.01

        if abs(result - price) > 0.001:
            debug(f"💰 Цена скорректирована: {price:.4f} → {result:.4f} (шаг={step})")

        return result

    def is_market_available(self, figi: str) -> Tuple[bool, str]:
        self._wait_for_rate_limit()

        try:
            status = self.get_trading_status(figi)

            if not status.get('api_trade_available', False):
                return False, "API торговля недоступна"

            if not status.get('market_order_available', False):
                if status.get('limit_order_available', False):
                    return True, "OTC режим (доступны лимитные заявки)"
                return False, "Рыночные заявки недоступны"

            trading_status = status.get('trading_status')
            allowed_statuses = [5, 13, 14]

            if trading_status in allowed_statuses:
                return True, "Торги активны"
            else:
                return True, f"Статус {trading_status} (пробуем)"

        except Exception as e:
            debug(f"Ошибка проверки доступности {figi}: {e}")
            return True, "Не удалось проверить"

    def check_margin_trading_allowed(self) -> Tuple[bool, str]:
        self._wait_for_rate_limit()

        with Client(self.token) as client:
            try:
                margin = client.users.get_margin_attributes(account_id=self.account_id)
                if margin:
                    return True, "OK"
            except Exception as e:
                error_msg = str(e)
                if "50002" in error_msg:
                    return False, "Маржинальная торговля не включена"
                if "50020" in error_msg:
                    return False, "Требуется статус квалифицированного инвестора"
                return False, f"Ошибка: {error_msg[:50]}"

            return False, "Не удалось определить"

    # ========== МЕТОДЫ ДЛЯ СТАКАНА ==========

    def get_best_price(self, figi: str, direction: str) -> Optional[float]:
        orderbook = self.get_orderbook(figi, depth=1)
        if not orderbook:
            return None

        if direction == "BUY":
            return orderbook.get('best_bid')
        else:
            return orderbook.get('best_ask')

    def get_orderbook(self, figi: str, depth: int = 10) -> Optional[Dict[str, Any]]:
        try:
            with Client(self.token) as client:
                orderbook = client.market_data.get_order_book(figi=figi, depth=depth)

                bids = []
                for bid in orderbook.bids:
                    bids.append({
                        'price': float(quotation_to_decimal(bid.price)),
                        'quantity': int(bid.quantity)
                    })

                asks = []
                for ask in orderbook.asks:
                    asks.append({
                        'price': float(quotation_to_decimal(ask.price)),
                        'quantity': int(ask.quantity)
                    })

                return {
                    'figi': figi,
                    'depth': depth,
                    'bids': bids,
                    'asks': asks,
                    'best_bid': float(quotation_to_decimal(orderbook.bids[0].price)) if orderbook.bids else None,
                    'best_ask': float(quotation_to_decimal(orderbook.asks[0].price)) if orderbook.asks else None,
                    'bid_volume': sum(bid.quantity for bid in orderbook.bids),
                    'ask_volume': sum(ask.quantity for ask in orderbook.asks),
                    'last_price': float(quotation_to_decimal(orderbook.last_price)) if orderbook.last_price else None,
                    'close_price': float(quotation_to_decimal(orderbook.close_price)) if orderbook.close_price else None,
                    'limit_up': float(quotation_to_decimal(orderbook.limit_up)) if orderbook.limit_up else None,
                    'limit_down': float(quotation_to_decimal(orderbook.limit_down)) if orderbook.limit_down else None,
                }
        except Exception as e:
            from trading_bot.logger import error as log_error
            log_error(f"Ошибка получения стакана для {figi}: {e}")
            return None

    # ========== ОСТАЛЬНЫЕ МЕТОДЫ (сокращённо, но рабочие) ==========

    def get_orderbook_text(self, figi: str, ticker: str = None, depth: int = 5) -> str:
        orderbook = self.get_orderbook(figi, depth=depth)

        if orderbook is None:
            return f"❌ Не удалось получить стакан для {ticker or figi}"

        if not orderbook.get('bids') or not orderbook.get('asks'):
            return f"📭 <b>Стакан {ticker or figi} пуст</b>\n\nНет активных заявок"

        ticker_str = ticker or figi[:8]
        bids = orderbook['bids'][:depth]
        asks = orderbook['asks'][:depth]

        max_volume_width = max(
            max([len(str(b.get('quantity', 0))) for b in bids] + [4]) if bids else 4,
            max([len(str(a.get('quantity', 0))) for a in asks] + [4]) if asks else 4
        )

        lines = [
            f"📊 <b>СТАКАН {ticker_str}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "   🟢 <b>ПОКУПКА (BID)</b>          <b>ПРОДАЖА (ASK)</b> 🔴",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]

        if asks:
            for i in range(min(depth, len(asks)) - 1, -1, -1):
                ask = asks[i]
                ask_price = ask.get('price', 0)
                ask_qty = ask.get('quantity', 0)
                lines.append(f"   {ask_price:>8.2f}₽  {ask_qty:>{max_volume_width}} шт")
        else:
            lines.append("   ❌ Нет заявок на продажу")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if bids:
            for bid in bids:
                bid_price = bid.get('price', 0)
                bid_qty = bid.get('quantity', 0)
                lines.append(f"   {bid_price:>8.2f}₽  {bid_qty:>{max_volume_width}} шт")
        else:
            lines.append("   ❌ Нет заявок на покупку")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        best_bid = orderbook.get('best_bid')
        best_ask = orderbook.get('best_ask')

        if best_bid and best_ask:
            spread = best_ask - best_bid
            spread_pct = spread / best_bid * 100 if best_bid > 0 else 0
            spread_status = "🟢" if spread_pct < 0.2 else "🟡" if spread_pct < 0.5 else "🔴"

            lines.extend([
                f"{spread_status} <b>СПРЕД:</b> {spread:.2f}₽ ({spread_pct:.2f}%)",
                f"💰 <b>ЛУЧШАЯ ЦЕНА:</b> {best_bid:.2f}₽ / {best_ask:.2f}₽",
            ])

        return "\n".join(lines)

    # ========== АЛИАСЫ ==========

    def sell_short(self, figi: str, quantity: int, use_market: bool = None) -> bool:
        return self.sell(figi, quantity, use_market)


# Глобальная переменная для позиций
position_entries = {}


# Единый экземпляр
tbank = TBankClient()


print("=" * 60)
print("✅ TBankClient успешно загружен с Render фиксом (порт 80)")
print("=" * 60)