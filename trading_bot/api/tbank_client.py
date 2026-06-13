"""Клиент для работы с T-Bank API - ПОЛНАЯ ПРОДАКШН ВЕРСИЯ (с TTLCache)"""

import os
import grpc

# Форсируем insecure channel для Render
os.environ['GRPC_DNS_RESOLVER'] = 'native'

# Патчим создание клиента
_original_init = None


def patch_client_for_render():
    """Временный патч для Render"""
    from t_tech.invest import Client

    global _original_init
    if _original_init is None:
        _original_init = Client.__init__

        def patched_init(self, token, app_name=None, channel=None):
            if channel is None:
                # Используем insecure channel для Render
                channel = grpc.insecure_channel('invest-public-api.tbank.ru:443')
            _original_init(self, token, app_name, channel)

        Client.__init__ = patched_init
        print("✅ Render patch applied (insecure channel)")


# Применяем патч
patch_client_for_render()

import time
from functools import wraps
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta, timezone
import uuid
from decimal import Decimal
import signal
from contextlib import contextmanager
from threading import Lock

MOSCOW_TZ = timezone(timedelta(hours=3))

from t_tech.invest import (
    Client,
    CandleInterval,
    OrderType,
    OrderDirection
)
from t_tech.invest.utils import quotation_to_decimal, decimal_to_quotation
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

# Для TimeoutError в декораторе
from socket import timeout as SocketTimeoutError

# Для TTLCache в mark_as_confirmation_required
from trading_bot.cache import TTLCache

# Импорты для унифицированного кэша
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
                    # current_delay *= backoff
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
        # Получаем шаг цены и округляем цену
        step = self._get_min_price_increment_advanced(figi)
        if step > 0:
            original_price = price
            price = round(price / step) * step
            if abs(price - original_price) > 0.001:
                info(f"   💰 Цена скорректирована: {original_price:.4f} → {price:.4f} (шаг={step})")
        # ========================================================

        total = quantity * price
        info(f"📊 BUY {ticker}: {quantity} шт по {price:.2f}₽, сумма {total:.2f}₽")

        available, total_cap, _ = self.get_available_funds()
        if total > available:
            warning(f"⚠️ Недостаточно средств: нужно {total:.2f}₽, доступно {available:.2f}₽")
            return False

        # ОТПРАВКА С ПОДТВЕРЖДЕНИЕМ
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
            info(
                f"   Статус: {result.get('status')}, исполнено: {result.get('executed_lots')}/{result.get('requested_lots')}")

            # Сохраняем позицию
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

        # ✅ ОКРУГЛЕНИЕ ДО ЛОТА
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

        # ========== ✅ ДОБАВИТЬ ОКРУГЛЕНИЕ ЦЕНЫ ДО ШАГА ==========
        step = self._get_min_price_increment_advanced(figi)
        if step > 0:
            original_price = price
            price = round(price / step) * step
            if abs(price - original_price) > 0.001:
                info(f"   💰 Цена скорректирована: {original_price:.4f} → {price:.4f} (шаг={step})")
        # ========================================================

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
            # УДАЛЯЕМ ОБРАБОТКУ 30042 ЗДЕСЬ - пусть идёт в _place_market_order_impl
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
        """
        Проверка статуса заявки через OrderValidator

        Args:
            order_id: ID заявки
            figi: FIGI инструмента (для логирования)

        Returns:
            Dict с информацией о заявке или None
        """
        validator = self._init_validator()
        return validator.get_order_status(order_id)

    # ========== НОВЫЙ МЕТОД ДЛЯ ОЖИДАНИЯ ИСПОЛНЕНИЯ ==========
    def wait_for_order_completion(self, order_id: str, max_wait_seconds: int = 30) -> Dict[str, Any]:
        """
        Ожидание полного исполнения заявки

        Args:
            order_id: ID заявки
            max_wait_seconds: Максимальное время ожидания

        Returns:
            Dict с результатом исполнения
        """
        validator = self._init_validator()
        return validator.wait_for_completion(order_id, max_wait_seconds)

    # ========== НОВЫЙ МЕТОД ДЛЯ ПОЛУЧЕНИЯ АКТИВНЫХ ЗАЯВОК ==========
    def get_active_orders_detailed(self) -> List[Dict[str, Any]]:
        """
        Получение всех активных заявок с детальной информацией (через валидатор)

        Returns:
            List[Dict]: Список активных заявок
        """
        validator = self._init_validator()
        return validator.get_active_orders()

    def _place_limit_order_with_fallback(self, figi: str, quantity: int, direction: str, target_price: float) -> bool:
        """
        Размещение лимитной заявки с fallback на рыночную.
        УЛУЧШЕННАЯ ОБРАБОТКА ВСЕХ ОШИБОК API.

        Обрабатываемые ошибки:
        - 30042: недостаточно средств/маржа → проверка OTC, удаление мёртвых позиций, рекомендации
        - 30099: некорректная цена → коррекция по стакану, fallback на рыночную
        - 30100: некорректная цена → fallback на рыночную
        - 30240: инструмент не поддерживает стоп-ордера → помечаем, используем программный трейлинг
        - 30068: инструмент не торгуется → блокировка
        - 90002: нарушено предусловие → блокировка
        """
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        # ========== 1. ПРОВЕРКА OTC ==========
        if self.is_confirmation_required(figi):
            error(f"❌ {ticker} требует подтверждения сделок (OTC)")
            error(f"   🔧 Закройте позицию вручную в приложении Т-Банк")
            return False

        with Client(self.token) as client:
            try:
                from decimal import Decimal, ROUND_HALF_UP

                # Получаем минимальный шаг цены
                step = self._get_min_price_increment_advanced(figi)
                if step <= 0:
                    step = 0.01

                # Округляем цену до шага
                target_price = round(target_price / step) * step
                target_price = max(target_price, step)

                if direction == "SELL" and target_price < 0.01:
                    target_price = self._round_to_min_increment_advanced(figi, 0.01)

                if target_price <= 0:
                    error(f"❌ Некорректная цена: {target_price}")
                    return False

                # Форматирование цены
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

                # ====================================================================
                # 2. ОБРАБОТКА 30100 - НЕКОРРЕКТНАЯ ЦЕНА
                # ====================================================================
                if "30100" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30100 - некорректная цена {target_price}")
                    info(f"   🔄 Fallback: {direction} {quantity} шт {ticker} через РЫНОК")
                    market_result = self._place_market_order_impl(figi, quantity, direction)
                    if market_result:
                        success(f"✅ Рыночная заявка {direction} {quantity} {ticker} УСПЕШНА (fallback после 30100)")
                        return True
                    return False

                # ====================================================================
                # 3. ОБРАБОТКА 30099 - НЕКОРРЕКТНАЯ ЦЕНА (ОСОБЫЙ СЛУЧАЙ)
                # ====================================================================
                elif "30099" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30099 - некорректная цена для лимитной заявки")
                    warning(f"   🔄 Пробуем скорректировать цену по стакану...")

                    # Пробуем использовать лучшую цену из стакана
                    try:
                        orderbook = self.get_orderbook(figi, depth=1)
                        if direction == "BUY" and orderbook and orderbook.get('best_ask'):
                            corrected_price = orderbook['best_ask'] * 1.01
                            info(f"   📊 Корректируем цену по стакану: {corrected_price:.2f}₽")
                            return self._place_market_order_impl(figi, quantity, direction)
                        elif direction == "SELL" and orderbook and orderbook.get('best_bid'):
                            corrected_price = orderbook['best_bid'] * 0.99
                            info(f"   📊 Корректируем цену по стакану: {corrected_price:.2f}₽")
                            return self._place_market_order_impl(figi, quantity, direction)
                        else:
                            info(f"   📊 Стакан пуст, пробуем рыночную заявку")
                            return self._place_market_order_impl(figi, quantity, direction)
                    except Exception as e:
                        debug(f"   ⚠️ Ошибка получения стакана: {e}")

                    # Fallback на рыночную
                    info(f"   🔄 Пробуем рыночную заявку вместо лимитной")
                    return self._place_market_order_impl(figi, quantity, direction)

                # ====================================================================
                # 4. ОБРАБОТКА 30042 - НЕДОСТАТОЧНО СРЕДСТВ ИЛИ МАРЖИ
                # ====================================================================
                elif "30042" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30042 - недостаточно средств или маржи")

                    # Проверяем, не OTC ли инструмент
                    if self.is_confirmation_required(figi):
                        warning(f"   🔐 {ticker}: OTC инструмент, требуется ручное закрытие")
                        warning(f"   📱 Закройте позицию вручную в приложении Т-Банк")
                        return False

                    # Проверяем наличие позиции у брокера (мёртвые позиции)
                    try:
                        positions = self.get_positions()
                        real_figis = {p['figi'] for p in positions if abs(p.get('quantity', 0)) > 0}
                        if figi not in real_figis:
                            warning(f"   🧹 Позиции {ticker} нет у брокера! Удаляем из менеджера")
                            from trading_bot.risk.position_manager import position_manager
                            position_manager.remove_position(figi)
                            return True  # Считаем успехом, так как позиции нет
                    except Exception as e:
                        debug(f"   ⚠️ Ошибка проверки позиций: {e}")

                    # Рекомендация для пользователя
                    info(f"   💡 РЕКОМЕНДАЦИЯ:")
                    info(f"      → Пополните счёт (нужно ~{quantity * target_price:.0f}₽ для этой операции)")
                    info(f"      → Или закройте часть позиций для освобождения маржи")

                    # Последний шанс - рыночная заявка
                    market_result = self._place_market_order_impl(figi, quantity, direction)
                    if market_result:
                        success(f"✅ Рыночная заявка {direction} {quantity} {ticker} УСПЕШНА (fallback после 30042)")
                        return True

                    return False

                # ====================================================================
                # 5. ОБРАБОТКА 30068 - ИНСТРУМЕНТ НЕ ТОРГУЕТСЯ
                # ====================================================================
                elif "30068" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30068 - инструмент не торгуется или недоступен")
                    warning(f"   ⛔ Добавляем {ticker} в чёрный список на 60 минут")
                    self.mark_as_confirmation_required(figi)

                    # Дополнительная блокировка
                    try:
                        from trading_bot.risk.position_manager import position_manager
                        position_manager.add_temp_skip(figi, minutes=60)
                        position_manager.add_to_blacklist(figi, minutes=60)
                    except Exception as e:
                        debug(f"   ⚠️ Ошибка добавления в чёрный список: {e}")

                    return False

                # ====================================================================
                # 6. ОБРАБОТКА 90002 - НАРУШЕНО ПРЕДУСЛОВИЕ
                # ====================================================================
                elif "90002" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 90002 - нарушено предусловие")
                    warning(f"   ⚠️ Инструмент может требовать подтверждения или недоступен")
                    self.mark_as_confirmation_required(figi)
                    return False

                # ====================================================================
                # 7. ОБРАБОТКА 30240 - НЕ ПОДДЕРЖИВАЕТ СТОП-ОРДЕРА
                # ====================================================================
                elif "30240" in error_msg:
                    warning(f"⚠️ {ticker}: ОШИБКА 30240 - стоп-ордера НЕ ПОДДЕРЖИВАЮТСЯ")
                    warning(f"   🔧 Будет использован ТОЛЬКО ПРОГРАММНЫЙ трейлинг-стоп")
                    self._no_stop_orders.add(figi)
                    return False

                # ====================================================================
                # 8. НЕИЗВЕСТНАЯ ОШИБКА
                # ====================================================================
                else:
                    error(f"❌ НЕИЗВЕСТНАЯ ошибка: {error_msg[:100]}")

                    # Попытка определить ошибку по коду
                    if "50002" in error_msg:
                        info(f"   💡 Маржинальная торговля не включена. Подключите в настройках счёта.")
                    elif "50020" in error_msg:
                        info(f"   💡 Требуется статус квалифицированного инвестора.")
                    elif "70002" in error_msg:
                        warning(f"   🔄 Внутренняя ошибка API, повтор через 2 секунды...")
                        time.sleep(2)
                        return self._place_market_order_impl(figi, quantity, direction)

                    return False

    def _place_market_order(self, figi: str, quantity: int, direction: str, is_short: bool = False) -> bool:
        """
        Внутренний метод для размещения рыночного ордера.
        Используется методами buy() и sell().
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        if direction == "BUY":
            info(f"🟢 РЫНОЧНАЯ ПОКУПКА: {quantity} шт {ticker}")
            return self._place_market_order_impl(figi, quantity, "BUY")
        else:  # SELL
            info(f"🔴 РЫНОЧНАЯ ПРОДАЖА: {quantity} шт {ticker}")
            return self._place_market_order_impl(figi, quantity, "SELL")

    def _place_market_order_impl(self, figi: str, quantity: int, direction: str) -> bool:
        """Реализация рыночного ордера через API T-Invest"""
        from t_tech.invest import OrderDirection, OrderType
        import uuid

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        # ✅ ОКРУГЛЕНИЕ ДО ЛОТА
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

            if "30068" in error_msg:
                warning(f"   ⚠️ {ticker}: ОШИБКА 30068 - инструмент не торгуется или недоступен")
                warning(f"   ⚠️ Добавляем {ticker} в ЧЁРНЫЙ СПИСОК (блокируем на 60 мин)")
                self.mark_as_confirmation_required(figi)
                return False

            elif "90002" in error_msg:
                warning(f"   ⚠️ {ticker}: ОШИБКА 90002 - нарушено предусловие")
                warning(f"   ⚠️ Инструмент может требовать подтверждения или недоступен")
                self.mark_as_confirmation_required(figi)
                return False

            elif "30042" in error_msg:
                # ✅ УНИВЕРСАЛЬНАЯ ОБРАБОТКА 30042: пробуем агрессивную лимитную заявку
                warning(f"   ⚠️ {ticker}: ОШИБКА 30042 - рыночная заявка отклонена")
                warning(f"   🔄 Пробуем АГРЕССИВНУЮ ЛИМИТНУЮ заявку (проскальзывание 3%)...")

                current_price = self.get_current_price(figi)
                if current_price:
                    if direction == "SELL":
                        limit_price = self._round_to_min_increment_advanced(figi, current_price * 0.97)  # -3%
                    else:
                        limit_price = self._round_to_min_increment_advanced(figi, current_price * 1.03)  # +3%

                    info(f"   📋 АГРЕССИВНАЯ ЛИМИТНАЯ: {direction} {quantity} шт {ticker} по {limit_price:.2f}₽")
                    return self.place_limit_order(figi, quantity, direction, limit_price)
                else:
                    error(f"   ❌ Не удалось получить цену для лимитной заявки")
                    return False


            elif "30083" in error_msg:
                warning(f"   ⚠️ {ticker}: ОШИБКА 30083 - инструмент не доступен для торговли")
                warning(f"   ⛔ Добавляем {ticker} в чёрный список на 1 час")
                # Блокируем тикер в PositionSizer (ДЛЯ ОБОИХ ТИПОВ ПОЗИЦИЙ)
                try:
                    from trading_bot.trading.position_sizer import position_sizer
                    import time
                    if not hasattr(position_sizer, '_short_blocked_until'):
                        position_sizer._short_blocked_until = {}
                    if not hasattr(position_sizer, '_long_blocked_until'):
                        position_sizer._long_blocked_until = {}
                    # Блокируем для SHORT и LONG (на всякий случай)
                    position_sizer._short_blocked_until[ticker] = time.time() + 3600
                    position_sizer._long_blocked_until[ticker] = time.time() + 3600
                    info(f"   🔒 {ticker} заблокирован до {time.strftime('%H:%M', time.localtime(time.time() + 3600))}")
                except Exception as e:
                    debug(f"   ⚠️ Не удалось заблокировать {ticker}: {e}")
                return False

            elif "70002" in error_msg or "internal" in error_msg.lower():
                import time
                warning(f"   ⚠️ ВНУТРЕННЯЯ ОШИБКА API (70002) при {direction} {ticker}, повтор через 2 сек...")
                time.sleep(2)
                try:
                    return self._place_market_order_impl(figi, quantity, direction)
                except Exception as retry_error:
                    error(f"   ❌ ПОВТОРНАЯ попытка также НЕ УДАЛАСЬ: {retry_error}")
                    return False

            elif "30240" in error_msg:
                warning(f"   ⚠️ {ticker}: ОШИБКА 30240 - стоп-ордера НЕ ПОДДЕРЖИВАЮТСЯ")
                self._no_stop_orders.add(figi)
                return False

            else:
                warning(f"   ❌ Ошибка рыночного ордера {ticker}: {error_msg[:100]}")
                return False

    def place_limit_order(self, figi: str, quantity: int, direction: str, target_price: float) -> bool:
        """Размещение лимитной заявки с корректным форматированием цены"""
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        if self.is_confirmation_required(figi):
            error(f"❌ {ticker} требует подтверждения сделок")
            return False

        with Client(self.token) as client:
            try:
                from decimal import Decimal, ROUND_HALF_UP

                # ✅ Получаем минимальный шаг цены
                step = self._get_min_price_increment_advanced(figi)
                if step <= 0:
                    step = 0.01

                # ✅ Округляем цену до шага
                target_price = round(target_price / step) * step
                target_price = max(target_price, step)  # Цена не может быть меньше шага

                # ✅ Для продажи проверяем минимальную цену
                if direction == "SELL" and target_price < 0.01:
                    target_price = self._round_to_min_increment_advanced(figi, 0.01)

                if target_price <= 0:
                    error(f"❌ Некорректная цена для лимитной заявки: {target_price}")
                    return False

                # ✅ КРИТИЧЕСКИ ВАЖНО: правильное форматирование Decimal
                # Используем строковое представление с фиксированной точностью
                price_str = f"{target_price:.2f}"
                price_decimal = Decimal(price_str).quantize(Decimal(str(step)), rounding=ROUND_HALF_UP)
                price_quotation = decimal_to_quotation(price_decimal)

                dir_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
                # needs_margin = (direction == "SELL")
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
                    confirm_margin_trade=confirm_margin  # ← ИСПРАВЛЕНО
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
        """Алиас для place_limit_order"""
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

        # Черный список по тикеру
        self._confirmation_required_tickers.add(ticker)
        self._confirmation_blocklist_time[ticker] = time.time()

        # Кэш по FIGI (простой словарь)
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
        """Получение информации о марже с кэшированием"""
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
        """Получение позиций с кэшированием и статусом блокировки"""
        self._wait_for_rate_limit()

        cache_key = "positions"

        # Если принудительное обновление - пропускаем кэш
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

                # Логируем количество позиций
                info(f"📊 Получено позиций от брокера: {len(positions)}")

                # Если позиций нет - очищаем кэш и возвращаем пустой список
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
        """Принудительная очистка кэша позиций"""
        cache_key = "positions"
        positions_cache.delete(cache_key)
        info(f"🧹 Кэш позиций очищен")

    def get_active_orders(self) -> List[Dict[str, Any]]:
        """Получение активных заявок - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        self._wait_for_rate_limit()

        try:
            with Client(self.token) as client:
                orders_response = client.orders.get_orders(account_id=self.account_id)
                result = []

                for order in orders_response.orders:
                    # Направление
                    direction = "BUY" if order.direction == 1 else "SELL"

                    # Цена (пробуем разные поля)
                    price = 0.0
                    if hasattr(order, 'price') and order.price:
                        from t_tech.invest.utils import quotation_to_decimal
                        price = float(quotation_to_decimal(order.price))
                    elif hasattr(order, 'initial_price') and order.initial_price:
                        from t_tech.invest.utils import quotation_to_decimal
                        price = float(quotation_to_decimal(order.initial_price))

                    # Статус
                    status = "ACTIVE"
                    if hasattr(order, 'state'):
                        status = str(order.state)
                    elif hasattr(order, 'order_state'):
                        status = str(order.order_state)

                    # Количество исполненных
                    executed = 0
                    if hasattr(order, 'executed_lots'):
                        executed = order.executed_lots
                    elif hasattr(order, 'lots_executed'):
                        executed = order.lots_executed

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
        """СИНХРОННАЯ ОЧИСТКА ДУБЛИРУЮЩИХСЯ ЗАЯВОК (включая рыночные)"""
        from collections import defaultdict

        try:
            info(f"🧹 Синхронная очистка дубликатов{f' для {ticker}' if ticker else ''}...")

            # Получаем активные заявки
            active_orders = self.get_active_orders()

            if not active_orders:
                debug("   📭 Нет активных заявок для очистки")
                return {"success": True, "cancelled": 0, "message": "Нет активных заявок"}

            # Фильтруем по тикеру если указан
            if ticker:
                active_orders = [o for o in active_orders if o.get('ticker') == ticker]
                if not active_orders:
                    return {"success": True, "cancelled": 0, "message": f"Нет заявок для {ticker}"}

            # Группируем по тикеру и направлению
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

                # Для рыночных заявок (price=0) - оставляем последнюю (самую свежую)
                # Для лимитных - оставляем лучшую цену
                market_orders = [o for o in orders if o.get('price', 0) == 0]
                limit_orders = [o for o in orders if o.get('price', 0) > 0]

                if limit_orders:
                    # Есть лимитные заявки - выбираем лучшую цену
                    if direction == "BUY":
                        best_order = min(limit_orders, key=lambda x: x.get('price', float('inf')))
                        reason = "оставляем лучшую лимитную цену (минимальную)"
                    else:
                        best_order = max(limit_orders, key=lambda x: x.get('price', 0))
                        reason = "оставляем лучшую лимитную цену (максимальную)"
                    # Рыночные заявки тоже отменяем
                    to_cancel = [o for o in orders if o.get('order_id') != best_order.get('order_id')]
                else:
                    # Только рыночные заявки - оставляем последнюю (самую свежую)
                    # Сортируем по order_id (обычно возрастает со временем)
                    orders.sort(key=lambda x: x.get('order_id', ''))
                    best_order = orders[-1]  # последняя
                    to_cancel = orders[:-1]  # остальные
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

    async def cleanup_duplicate_orders(self, ticker: str = None) -> Dict[str, Any]:
        """
        ОЧИСТКА ДУБЛИРУЮЩИХСЯ ЗАЯВОК
        Улучшенная версия с защитой от race conditions

        Args:
            ticker: Опционально - только для указанного тикера

        Returns:
            Dict с результатами очистки
        """
        import asyncio
        from collections import defaultdict

        try:
            info(f"🧹 Очистка дублирующихся заявок{f' для {ticker}' if ticker else ''}...")

            # Получаем активные заявки с защитой от API ошибок
            try:
                active_orders = self.get_active_orders()
            except Exception as e:
                error(f"❌ Не удалось получить активные заявки: {e}")
                return {"success": False, "error": str(e), "cancelled": 0}

            if not active_orders:
                debug("   📭 Нет активных заявок для очистки")
                return {"success": True, "message": "Нет активных заявок", "cancelled": 0}

            # Фильтруем по тикеру если указан
            if ticker:
                active_orders = [o for o in active_orders if o.get('ticker') == ticker]
                if not active_orders:
                    debug(f"   📭 Нет активных заявок для {ticker}")
                    return {"success": True, "message": f"Нет активных заявок для {ticker}", "cancelled": 0}

            # Группируем заявки по ключу (тикер + направление)
            orders_by_key: Dict[str, List[Dict]] = defaultdict(list)

            for order in active_orders:
                order_ticker = order.get('ticker')
                direction = order.get('direction')
                if not order_ticker or not direction:
                    continue
                key = f"{order_ticker}_{direction}"
                orders_by_key[key].append(order)

            cancelled = 0
            failed = 0
            details = []

            for key, orders in orders_by_key.items():
                if len(orders) <= 1:
                    continue

                ticker_key, direction = key.split('_')

                # Определяем лучшую заявку для сохранения
                if direction == "BUY":
                    # Для покупки сохраняем заявку с самой НИЗКОЙ ценой
                    best_order = min(orders, key=lambda x: x.get('price', float('inf')))
                    reason = "оставляем лучшую цену"
                else:
                    # Для продажи сохраняем заявку с самой ВЫСОКОЙ ценой
                    best_order = max(orders, key=lambda x: x.get('price', 0))
                    reason = "оставляем лучшую цену"

                warning(f"   ⚠️ {key}: найдено {len(orders)} заявок")

                # Отменяем все заявки, кроме лучшей
                for order in orders:
                    if order.get('order_id') != best_order.get('order_id'):
                        result = self.cancel_order(order.get('order_id'))
                        if result:
                            cancelled += 1
                            details.append({
                                'order_id': order.get('order_id')[:8],
                                'ticker': ticker_key,
                                'direction': direction,
                                'price': order.get('price', 0),
                                'reason': f"дубликат, {reason}"
                            })
                        else:
                            failed += 1
                            warning(f"      ❌ Не удалось отменить заявку {order.get('order_id')[:8]}")

                info(f"      ✅ {key}: оставлена заявка {best_order.get('order_id')[:8]} "
                     f"по {best_order.get('price', 0):.2f}₽")

            # Логируем результат
            if cancelled > 0:
                success(f"   🧹 ОТМЕНЕНО дублирующихся заявок: {cancelled}")
                for detail in details[:5]:  # Показываем первые 5
                    info(f"      - {detail['ticker']} {detail['direction']}: {detail['reason']}")
                if failed > 0:
                    warning(f"   ⚠️ Не удалось отменить: {failed} заявок")
            else:
                debug("   ✅ Дублирующихся заявок не найдено")

            return {
                "success": True,
                "cancelled": cancelled,
                "failed": failed,
                "details": details[:20]
            }

        except Exception as e:
            error(f"❌ Ошибка очистки дубликатов: {e}")
            import traceback
            debug(traceback.format_exc())
            return {"success": False, "error": str(e), "cancelled": 0}

    def cancel_stale_limit_orders(self, max_age_seconds: int = 300) -> Dict[str, Any]:
        """
        Отмена "зависших" лимитных заявок, которые не исполнились за длительное время

        Args:
            max_age_seconds: Максимальный возраст заявки в секундах (по умолчанию 5 минут)

        Returns:
            Dict с результатами очистки
        """
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
                    # Парсим дату создания
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
        """Получение текущей цены с кэшированием"""
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

    def get_all_shares(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Получение списка акций с кэшированием"""
        self._wait_for_rate_limit()

        cache_key = f"all_shares_{limit}"
        cached_result = instruments_cache.get(cache_key)
        if cached_result is not None:
            return cached_result[:limit] if limit else cached_result

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
                            'asset_uid': stock.asset_uid if hasattr(stock, 'asset_uid') else None,
                        })
                        if len(result) >= limit:
                            break

                instruments_cache.set(cache_key, result, ttl=300)
                info(f"📊 Загружено {len(result)} акций")
                return result
            except Exception as e:
                error(f"Ошибка получения списка акций: {e}")
                return []

    def get_candles(self, figi: str, days: int = 5, interval_minutes: int = 5) -> List[Tuple[float, float]]:
        """Получение свечей с кэшированием и блокировкой для одного FIGI"""
        self._wait_for_rate_limit()

        cache_key = f"{figi}_{days}_{interval_minutes}"

        # Проверяем кэш
        cached_result = candles_cache.get(cache_key)
        if cached_result is not None:
            return cached_result.copy()

        # Блокировка для одного FIGI, чтобы не было дублирующих запросов
        lock_key = f"candle_lock_{figi}"
        if not hasattr(self, '_candle_locks'):
            self._candle_locks = {}

        if lock_key not in self._candle_locks:
            self._candle_locks[lock_key] = Lock()

        with self._candle_locks[lock_key]:
            # Повторно проверяем кэш после получения блокировки
            cached_result = candles_cache.get(cache_key)
            if cached_result is not None:
                return cached_result.copy()

            # Реальный запрос к API
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
        """
        Получение статуса торгов с ДЕТАЛЬНОЙ ИНФОРМАЦИЕЙ
        """
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

                # Добавляем информацию о доступности разных типов заявок
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
        """
        Преобразование кода статуса в понятное описание
        """
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
        """
        ДИАГНОСТИЧЕСКИЙ МЕТОД: Полная проверка инструмента

        Args:
            ticker: Тикер инструмента

        Returns:
            Dict со всей информацией об инструменте
        """
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

        # Находим FIGI
        figi = self._get_figi_by_ticker(ticker)
        if not figi:
            result['error'] = f"FIGI для {ticker} не найден"
            return result

        result['figi'] = figi

        # Получаем информацию об инструменте
        instrument_info = self._get_instrument_by_figi(figi)
        if instrument_info:
            result['instrument_type'] = instrument_info.get('instrument_type')
            result['exchange'] = instrument_info.get('exchange')
            result['details'] = instrument_info

        # Получаем статус торгов
        trading_status = self.get_trading_status(figi)
        result['trading_status'] = trading_status
        result['api_trade_available'] = trading_status.get('api_trade_available')
        result['market_order_available'] = trading_status.get('market_order_available')

        # Финальная проверка
        result['requires_confirmation'] = self.is_confirmation_required(figi)
        result['is_otc'] = result['requires_confirmation']

        return result

    def is_confirmation_required(self, figi: str) -> bool:
        """
        ПРОВЕРКА, ТРЕБУЕТ ЛИ ИНСТРУМЕНТ ПОДТВЕРЖДЕНИЯ СДЕЛОК (OTC)

        🔍 ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ КАЖДОГО ШАГА
        """
        from trading_bot.logger import debug, warning, info
        import time

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        # ========== 1. ПРОВЕРКА КЭША ==========
        now = time.time()
        if figi in self._confirmation_cache:
            cache_time = self._confirmation_cache_time.get(figi, 0)
            if now - cache_time < 3600:  # TTL 1 час
                result = self._confirmation_cache[figi]
                debug(f"   📦 Кэш OTC для {ticker}: {'✅ ДА (OTC)' if result else '❌ НЕТ'}")
                return result

        debug(f"   🔍 ПРОВЕРКА OTC ДЛЯ {ticker} (FIGI={figi[:12]}...)")

        try:
            with Client(self.token) as client:
                # ========== 2. ПОЛУЧАЕМ СТАТУС ТОРГОВ ==========
                debug(f"   📡 ШАГ 1/5: get_trading_status()")
                status = client.market_data.get_trading_status(instrument_id=figi)

                api_available = getattr(status, 'api_trade_available_flag', False)
                market_available = getattr(status, 'market_order_available_flag', False)
                limit_available = getattr(status, 'limit_order_available_flag', False)

                debug(f"      📊 API доступна: {'✅' if api_available else '❌'}")
                debug(f"      📊 Рыночные заявки: {'✅' if market_available else '❌'}")
                debug(f"      📊 Лимитные заявки: {'✅' if limit_available else '❌'}")

                # ========== 3. ПРОВЕРКА ЧЕРЕЗ API ДОСТУПНОСТЬ ==========
                if not api_available:
                    warning(f"   🔐 {ticker}: API торговля НЕ ДОСТУПНА → OTC")
                    self._confirmation_cache[figi] = True
                    self._confirmation_cache_time[figi] = now
                    return True

                # ========== 4. ПРОВЕРКА НАЛИЧИЯ ЗАЯВОК ==========
                if not market_available and not limit_available:
                    warning(f"   🔐 {ticker}: НЕТ доступных типов заявок → OTC")
                    self._confirmation_cache[figi] = True
                    self._confirmation_cache_time[figi] = now
                    return True

                # ========== 5. ПОЛУЧАЕМ ИНФОРМАЦИЮ ОБ ИНСТРУМЕНТЕ (ИСПРАВЛЕНО!) ==========
                debug(f"   📡 ШАГ 2/5: instruments.shares()")

                # ✅ ИСПРАВЛЕНИЕ: используем правильный синтаксис
                try:
                    # Пробуем через shares() с фильтрацией
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

                        # Проверка DEALER
                        if exchange == 'INSTRUMENT_EXCHANGE_DEALER' or 'DEALER' in str(exchange):
                            warning(f"   🔐 {ticker}: ВНЕБИРЖЕВОЙ инструмент (exchange={exchange}) → OTC")
                            self._confirmation_cache[figi] = True
                            self._confirmation_cache_time[figi] = now
                            return True

                        # Проверка квалифицированного инвестора
                        if for_qual:
                            warning(f"   🔐 {ticker}: ТРЕБУЕТ квалифицированного инвестора → OTC")
                            self._confirmation_cache[figi] = True
                            self._confirmation_cache_time[figi] = now
                            return True

                except Exception as e:
                    debug(f"      ⚠️ Не удалось получить информацию об инструменте: {e}")
                    # Не возвращаем OTC при ошибке — слишком рискованно

                # ========== 6. ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: СТАКАН ==========
                debug(f"   📡 ШАГ 3/5: Проверка стакана")
                try:
                    orderbook = client.market_data.get_order_book(figi=figi, depth=1)
                    if orderbook:
                        bid_exists = len(orderbook.bids) > 0 and orderbook.bids[0].quantity > 0
                        ask_exists = len(orderbook.asks) > 0 and orderbook.asks[0].quantity > 0

                        debug(f"      📊 Заявки на покупку: {'✅ есть' if bid_exists else '❌ нет'}")
                        debug(f"      📊 Заявки на продажу: {'✅ есть' if ask_exists else '❌ нет'}")

                        # Если нет заявок ни с одной стороны — возможно OTC
                        if not bid_exists and not ask_exists:
                            warning(f"   🔐 {ticker}: ПУСТОЙ СТАКАН (нет заявок) → подозрение на OTC")
                            # Не возвращаем сразу True, но помечаем как подозрительный
                except Exception as e:
                    debug(f"      ⚠️ Ошибка проверки стакана: {e}")

                # ========== 7. ИТОГ ==========
                debug(f"   📡 ШАГ 4/5: ИТОГОВЫЙ ВЕРДИКТ")
                info(f"   ✅ {ticker}: НЕ ТРЕБУЕТ подтверждения (можно торговать)")

                self._confirmation_cache[figi] = False
                self._confirmation_cache_time[figi] = now
                return False

        except Exception as e:
            error(f"   ❌ ОШИБКА проверки OTC для {ticker}: {e}")
            # При ошибке — лучше вернуть True (OTC), чтобы не рисковать
            self._confirmation_cache[figi] = True
            self._confirmation_cache_time[figi] = now
            return True

    def check_instrument_tradability(self, figi: str) -> Dict[str, Any]:
        """
        ДИАГНОСТИЧЕСКИЙ МЕТОД: полная проверка доступности инструмента
        ТОЛЬКО ЧЕРЕЗ API - БЕЗ ХАРДКОДА

        Args:
            figi: FIGI инструмента

        Returns:
            Dict с полной информацией о доступности
        """
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
                # 1. Получаем статус торгов
                status = client.market_data.get_trading_status(instrument_id=figi)
                result['api_trade_available'] = getattr(status, 'api_trade_available_flag', False)
                result['market_order_available'] = getattr(status, 'market_order_available_flag', False)
                result['limit_order_available'] = getattr(status, 'limit_order_available_flag', False)

                # 2. Получаем информацию об инструменте
                try:
                    instrument = client.instruments.share_by(figi=figi)
                    if hasattr(instrument, 'instrument'):
                        inst = instrument.instrument
                        result['exchange'] = getattr(inst, 'exchange', 'UNKNOWN')
                        result['details']['lot'] = getattr(inst, 'lot', 1)
                        result['details']['currency'] = getattr(inst, 'currency', 'UNKNOWN')
                        result['details']['for_qual_investor'] = getattr(inst, 'for_qual_investor_flag', False)
                except Exception as e:
                    debug(f"Не удалось получить информацию об инструменте {figi}: {e}")

                # 3. Определяем, требует ли подтверждения
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

                # 4. Итоговая доступность для торговли
                result['is_tradable'] = (
                        result['api_trade_available'] and
                        (result['market_order_available'] or result['limit_order_available']) and
                        not result['requires_confirmation']
                )

        except Exception as e:
            result['reason'] = f"Ошибка API: {str(e)[:100]}"
            result['requires_confirmation'] = True  # При ошибке - осторожно

        return result

    def _get_instrument_by_figi(self, figi: str) -> Optional[Dict]:
        """
        Получение детальной информации об инструменте по FIGI
        С КЭШИРОВАНИЕМ
        """
        cache_key = f"instrument_{figi}"
        cached_result = instruments_cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        try:
            with Client(self.token) as client:
                # Пробуем получить как акцию
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

                # Пробуем как облигацию
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

                # Пробуем как ETF
                try:
                    etf = client.instruments.etf_by(id_type=1, id=figi)
                    if etf and etf.instrument:
                        result = {
                            'figi': etf.instrument.figi,
                            'ticker': etf.instrument.ticker,
                            'name': etf.instrument.name,
                            'instrument_type': 'etf',
                            'exchange': self._get_exchange_name(etf.instrument),
                            'for_qual_investor_flag': getattr(etf.instrument, 'for_qual_investor_flag', False),
                            'api_trade_available': getattr(etf.instrument, 'api_trade_available_flag', True),
                            'lot': etf.instrument.lot,
                            'currency': etf.instrument.currency,
                        }
                        instruments_cache.set(cache_key, result, ttl=3600)
                        return result
                except Exception as e:
                    debug(f"Не удалось получить ETF по FIGI {figi}: {e}")

        except Exception as e:
            debug(f"Ошибка получения информации об инструменте {figi}: {e}")

        return None

    def _get_exchange_name(self, instrument) -> str:
        """
        Получение названия биржи из инструмента
        """
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
        """Проверка, поддерживает ли инструмент стоп-ордера"""
        try:
            status = self.get_trading_status(figi)

            # Базовая проверка по статусу торгов
            if not (status.get('market_order_available', False) and status.get('limit_order_available', False)):
                return False

            # ========== ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: пробуем реальный запрос ==========
            # Для некоторых инструментов (ALRS, TATN, CNRU) API говорит, что доступно,
            # но при реальной попытке выдаёт ошибку 30240
            try:
                from t_tech.invest import OrderDirection, StopOrderExpirationType
                from decimal import Decimal

                # Делаем "сухой" запрос (не отправляем реальный ордер)
                # Проверяем, можно ли создать стоп-ордер
                current_price = self.get_current_price(figi)
                if not current_price:
                    return False

                # Пробуем создать тестовый стоп-ордер (с ценой далеко от рынка)
                test_stop_price = current_price * 0.5 if current_price > 0 else 100
                test_stop_price = round(test_stop_price, 2)

                stop_price_quotation = decimal_to_quotation(Decimal(str(test_stop_price)))

                with Client(self.token) as client:
                    # Пытаемся создать стоп-ордер с заведомо невыполнимой ценой
                    # Если API вернёт ошибку 30240 - значит не поддерживается
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
                    # Если дошли сюда - поддерживается
                    return True

            except Exception as e:
                error_msg = str(e)
                if "30240" in error_msg:
                    debug(f"🔐 {figi}: стоп-ордера НЕ поддерживаются (ошибка 30240)")
                    return False
                # Другие ошибки игнорируем, считаем что поддерживается
                return True

        except Exception as e:
            debug(f"Ошибка проверки стоп-ордеров для {figi}: {e}")
            return False

    def _get_min_price_increment(self, figi: str) -> float:
        return 0.01

    def _round_to_min_increment(self, figi: str, price: float) -> float:
        return round(price, 2)

    def _get_ticker_by_figi(self, figi: str) -> Optional[str]:
        """
        Получение тикера по FIGI через единый resolver
        """
        resolver = get_figi_resolver()
        return resolver.get_ticker_by_figi(figi)

    def _get_figi_by_ticker(self, ticker: str) -> Optional[str]:
        """
        Получение FIGI по тикеру через единый resolver
        """
        resolver = get_figi_resolver()
        return resolver.get_figi_by_ticker(ticker)

    def _place_market_order_emergency(self, figi: str, quantity: int, direction: str) -> bool:
        self._wait_for_rate_limit()

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
            except Exception as e:
                debug(f"Ошибка экстренного рыночного ордера: {e}")
                return False

    def is_tradable_automatically(self, figi: str) -> Tuple[bool, str]:
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
        self._wait_for_rate_limit()

        with Client(self.token) as client:
            try:
                dir_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
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
        self._wait_for_rate_limit()

        from t_tech.invest import CandleInterval

        interval_map = {
            "1min": CandleInterval.CANDLE_INTERVAL_1_MIN,
            "5min": CandleInterval.CANDLE_INTERVAL_5_MIN,
            "15min": CandleInterval.CANDLE_INTERVAL_15_MIN,
            "1hour": CandleInterval.CANDLE_INTERVAL_HOUR,
            "1day": CandleInterval.CANDLE_INTERVAL_DAY,
        }

        indicator_map = {"RSI": 3, "MACD": 4, "BB": 1, "SMA": 5, "EMA": 2}

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

    def get_asset_fundamentals(self, asset_uid: str) -> Dict[str, Any]:
        self._wait_for_rate_limit()

        try:
            from t_tech.invest import instruments_pb2

            with self._get_client_context() as client:
                request = instruments_pb2.GetAssetFundamentalsRequest()
                request.assets.append(asset_uid)

                response = client.instruments.get_asset_fundamentals(request)

                if response.fundamentals and len(response.fundamentals) > 0:
                    fund = response.fundamentals[0]

                    result = {
                        'asset_uid': fund.asset_uid,
                        'currency': fund.currency,
                        'pe_ratio_ttm': fund.pe_ratio_ttm if fund.pe_ratio_ttm != 0 else 0.0,
                        'price_to_book_ttm': fund.price_to_book_ttm if fund.price_to_book_ttm != 0 else 0.0,
                        'roe': fund.roe if fund.roe != 0 else 0.0,
                        'dividend_yield_daily_ttm': fund.dividend_yield_daily_ttm if fund.dividend_yield_daily_ttm != 0 else 0.0,
                        'market_capitalization': fund.market_capitalization if fund.market_capitalization != 0 else 0.0,
                        'eps_ttm': fund.eps_ttm if fund.eps_ttm != 0 else 0.0,
                    }

                    debug(f"📊 Фундаментальные данные: P/E={result['pe_ratio_ttm']:.2f}")
                    return result

        except Exception as e:
            debug(f"⚠️ Ошибка GetAssetFundamentals: {e}")

        return {}

    def get_asset_uid_by_ticker(self, ticker: str) -> Optional[str]:
        self._wait_for_rate_limit()

        try:
            with self._get_client_context() as client:
                shares = client.instruments.shares()
                figi = None
                for share in shares.instruments:
                    if share.ticker == ticker.upper():
                        figi = share.figi
                        break

                if not figi:
                    return None

                instrument = client.instruments.get_instrument_by(id=figi, id_type=1)
                if instrument and hasattr(instrument.instrument, 'asset_uid'):
                    return instrument.instrument.asset_uid

        except Exception as e:
            debug(f"Ошибка получения asset_uid для {ticker}: {e}")
            return None

    def get_asset_fundamentals_by_ticker(self, ticker: str) -> Dict[str, Any]:
        self._wait_for_rate_limit()

        try:
            with self._get_client_context() as client:
                assets = client.instruments.get_assets()

                for asset in assets.assets:
                    for instrument in asset.instruments:
                        if instrument.ticker == ticker.upper():
                            fundamentals = client.instruments.get_asset_fundamentals(assets=[asset.uid])
                            if fundamentals.fundamentals:
                                fund = fundamentals.fundamentals[0]
                                return {
                                    'pe_ratio_ttm': fund.pe_ratio_ttm if hasattr(fund, 'pe_ratio_ttm') else 0,
                                    'price_to_book_ttm': fund.price_to_book_ttm if hasattr(fund, 'price_to_book_ttm') else 0,
                                    'roe': fund.roe if hasattr(fund, 'roe') else 0,
                                    'dividend_yield_daily_ttm': fund.dividend_yield_daily_ttm if hasattr(fund, 'dividend_yield_daily_ttm') else 0,
                                }
        except Exception as e:
            debug(f"Ошибка: {e}")
        return {}

    def get_asset_by_ticker(self, ticker: str) -> Optional[Dict[str, Any]]:
        self._wait_for_rate_limit()

        ticker_upper = ticker.upper()

        try:
            with self._get_client_context() as client:
                assets_response = client.instruments.get_assets()

                for asset in assets_response.assets:
                    for instr in asset.instruments:
                        if instr.ticker == ticker_upper:
                            debug(f"✅ Найден актив для {ticker_upper}: {asset.uid}")
                            return {'asset_uid': asset.uid, 'asset_name': asset.name, 'asset_type': asset.type}
        except Exception as e:
            debug(f"⚠️ Ошибка получения актива для {ticker}: {e}")
            return None

    def get_candles_fast(self, figi: str, interval_minutes: int = 1, days: int = 1) -> List[Tuple[float, float]]:
        """
        Получение свечей с быстрым интервалом (только минутные интервалы)
        API T-Invest НЕ поддерживает секундные интервалы!
        """
        self._wait_for_rate_limit()

        from t_tech.invest import CandleInterval

        interval_map = {
            1: CandleInterval.CANDLE_INTERVAL_1_MIN,
            5: CandleInterval.CANDLE_INTERVAL_5_MIN,
            15: CandleInterval.CANDLE_INTERVAL_15_MIN,
        }

        interval = interval_map.get(interval_minutes)
        if not interval:
            debug(f"Неподдерживаемый интервал: {interval_minutes} мин, используем 1 мин")
            interval = CandleInterval.CANDLE_INTERVAL_1_MIN

        with Client(self.token) as client:
            try:
                end_time = datetime.now(MOSCOW_TZ)
                start_time = end_time - timedelta(days=min(days, 1))

                candles = client.market_data.get_candles(
                    figi=figi,
                    from_=start_time,
                    to=end_time,
                    interval=interval
                )
                return [(float(quotation_to_decimal(c.close)), float(quotation_to_decimal(c.volume))) for c in candles.candles]
            except Exception as e:
                debug(f"Ошибка получения быстрых свечей для {figi}: {e}")
                return []

    def _get_min_price_increment_advanced(self, figi: str) -> float:
        try:
            with Client(self.token) as client:
                try:
                    instrument = client.instruments.share_by(figi=figi)
                    if hasattr(instrument.instrument, 'min_price_increment'):
                        step = float(instrument.instrument.min_price_increment)
                        if step > 0:
                            return step
                except Exception as e:
                    debug(f"Не удалось получить шаг цены акции: {e}")

                try:
                    instrument = client.instruments.bond_by(figi=figi)
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

    def place_stop_loss_order(self, figi: str, quantity: int, stop_price: float, side: str) -> bool:
        """
        Установка стоп-лосс заявки

        Args:
            figi: FIGI инструмента
            quantity: Количество
            stop_price: Цена активации стоп-лосса
            side: "LONG" или "SHORT" (сторона ПОЗИЦИИ, а не заявки!)
        """
        self._wait_for_rate_limit()

        with Client(self.token) as client:
            try:
                from t_tech.invest import OrderDirection, StopOrderExpirationType

                # ✅ ИСПРАВЛЕНО: правильное определение направления ЗАЯВКИ
                if side == "LONG":
                    # LONG позиция: стоп-лосс = продажа при падении цены
                    direction = OrderDirection.ORDER_DIRECTION_SELL
                elif side == "SHORT":
                    # SHORT позиция: стоп-лосс = покупка при росте цены
                    direction = OrderDirection.ORDER_DIRECTION_BUY
                else:
                    error(f"Неверная сторона позиции: {side}. Ожидается 'LONG' или 'SHORT'")
                    return False

                # Округляем цену
                stop_price = self._round_to_min_increment_advanced(figi, stop_price)
                current_price = self.get_current_price(figi)

                # Проверяем минимальное расстояние до текущей цены
                if current_price:
                    min_distance = self._get_min_price_increment_advanced(figi) * 2
                    if side == "LONG":
                        # Стоп-лосс должен быть ниже текущей цены
                        if stop_price >= current_price - min_distance:
                            stop_price = self._round_to_min_increment_advanced(figi, current_price - min_distance)
                            warning(f"   ⚠️ Стоп-лосс скорректирован до {stop_price:.2f}₽ (мин. отступ)")
                    else:  # SHORT
                        # Стоп-лосс должен быть выше текущей цены
                        if stop_price <= current_price + min_distance:
                            stop_price = self._round_to_min_increment_advanced(figi, current_price + min_distance)
                            warning(f"   ⚠️ Стоп-лосс скорректирован до {stop_price:.2f}₽ (мин. отступ)")

                if stop_price <= 0:
                    error(f"❌ Некорректная цена стоп-лосса: {stop_price}")
                    return False

                info(f"📊 СТОП-ЛОСС: {side} позиция, {quantity} шт, активация при {stop_price:.2f}₽")

                stop_price_quotation = decimal_to_quotation(Decimal(str(stop_price)))

                order = client.stop_orders.post_stop_order(
                    figi=figi,
                    quantity=quantity,
                    price=None,  # Для стоп-лосса используем stop_price
                    stop_price=stop_price_quotation,
                    direction=direction,
                    account_id=self.account_id,
                    stop_order_type=2,  # Стоп-лосс
                    expiration_type=StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL,
                    order_id=str(uuid.uuid4())
                )

                if order and order.stop_order_id:
                    success(f"✅ Стоп-лосс для {side} позиции установлен на {stop_price:.2f}₽")
                    return True
                return False

            except Exception as e:
                error(f"❌ Ошибка установки стоп-лосса: {e}")
                return False

    def place_take_profit_order(self, figi: str, quantity: int, take_profit_price: float, side: str) -> bool:
        """
        Установка тейк-профит заявки (лимитная заявка)

        Args:
            figi: FIGI инструмента
            quantity: Количество
            take_profit_price: Цена исполнения тейк-профита
            side: "LONG" или "SHORT" (сторона ПОЗИЦИИ, а не заявки!)
        """
        self._wait_for_rate_limit()

        with Client(self.token) as client:
            try:
                from t_tech.invest import OrderDirection, OrderType
                from decimal import Decimal, ROUND_HALF_UP

                # ✅ ИСПРАВЛЕНО: правильное определение направления ЗАЯВКИ
                if side == "LONG":
                    # LONG позиция: тейк-профит = продажа при росте цены
                    direction = OrderDirection.ORDER_DIRECTION_SELL
                elif side == "SHORT":
                    # SHORT позиция: тейк-профит = покупка при падении цены
                    direction = OrderDirection.ORDER_DIRECTION_BUY
                else:
                    error(f"Неверная сторона позиции: {side}. Ожидается 'LONG' или 'SHORT'")
                    return False

                step = self._get_min_price_increment_advanced(figi)
                take_profit_price = self._round_to_min_increment_advanced(figi, take_profit_price)

                # Минимальная цена
                if take_profit_price < 0.01:
                    take_profit_price = self._round_to_min_increment_advanced(figi, 0.01)

                current_price = self.get_current_price(figi)

                # ✅ ИСПРАВЛЕНО: проверка реалистичности тейк-профита
                if current_price:
                    min_distance = step * 2
                    if side == "LONG":
                        # Тейк-профит должен быть ВЫШЕ текущей цены
                        if take_profit_price <= current_price + min_distance:
                            new_price = current_price + min_distance * 3  # Увеличен отступ
                            take_profit_price = self._round_to_min_increment_advanced(figi, new_price)
                            warning(f"   ⚠️ Тейк-профит скорректирован до {take_profit_price:.2f}₽ (мин. отступ)")
                    else:  # SHORT
                        # Тейк-профит должен быть НИЖЕ текущей цены
                        if take_profit_price >= current_price - min_distance:
                            new_price = current_price - min_distance * 3
                            take_profit_price = self._round_to_min_increment_advanced(figi, max(new_price, 0.01))
                            warning(f"   ⚠️ Тейк-профит скорректирован до {take_profit_price:.2f}₽ (мин. отступ)")

                if take_profit_price <= 0:
                    error(f"❌ Некорректная цена тейк-профита: {take_profit_price}")
                    return False

                price_decimal = Decimal(str(take_profit_price)).quantize(Decimal(str(step)), rounding=ROUND_HALF_UP)
                price_quotation = decimal_to_quotation(price_decimal)

                info(f"📊 ТЕЙК-ПРОФИТ: {side} позиция, {quantity} шт, исполнение при {take_profit_price:.2f}₽")

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
                    success(f"✅ Тейк-профит для {side} позиции установлен на {take_profit_price:.2f}₽")
                    return True
                return False

            except Exception as e:
                error(f"❌ Ошибка установки тейк-профита: {e}")
                return False

    def get_max_lots(self, figi: str, direction: str, price: float = None) -> int:
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        try:
            with Client(self.token) as client:
                price_quotation = decimal_to_quotation(Decimal(str(price))) if price else None

                max_lots = client.orders.get_max_lots(
                    account_id=self.account_id,
                    instrument_id=figi,
                    price=price_quotation
                )

                if direction == "BUY":
                    return max_lots.buy_limits.buy_max_lots
                else:
                    return max_lots.sell_limits.sell_max_lots

        except Exception as e:
            debug(f"Ошибка получения max lots для {ticker}: {e}")
            return 0

    def get_order_state(self, order_id: str, figi: str = None) -> Optional[Dict[str, Any]]:
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) if figi else order_id[:8]

        try:
            with Client(self.token) as client:
                order = client.orders.get_order_state(
                    account_id=self.account_id,
                    order_id=order_id
                )

                return {
                    'order_id': order.order_id,
                    'status': str(order.order_state),
                    'executed_lots': order.executed_lots,
                    'requested_lots': order.lots_requested,
                    'executed_commission': float(quotation_to_decimal(order.executed_commission)) if order.executed_commission else 0,
                    'price': float(quotation_to_decimal(order.price)) if order.price else 0,
                    'direction': "BUY" if order.direction == 1 else "SELL"
                }

        except Exception as e:
            debug(f"Ошибка получения статуса заявки {order_id}: {e}")
            return None

    def cancel_all_orders_by_ticker(self, ticker: str) -> Dict[str, Any]:
        self._wait_for_rate_limit()

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
                            client.orders.cancel_order(account_id=self.account_id, order_id=order.order_id)
                            cancelled += 1
                        except Exception as e:
                            debug(f"Не удалось отменить заявку {order.order_id}: {e}")
                            failed += 1

                return {"cancelled": cancelled, "failed": failed}

        except Exception as e:
            error(f"❌ Ошибка отмены заявок для {ticker}: {e}")
            return {"cancelled": 0, "failed": 0, "error": str(e)}

    def get_order_price_info(self, figi: str, direction: str, quantity: int, price: float) -> Dict[str, Any]:
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        try:
            with Client(self.token) as client:
                dir_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
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
                    'lots_requested': response.lots_requested
                }

        except Exception as e:
            warning(f"⚠️ Ошибка расчёта стоимости для {ticker}: {e}")
            return {}

    def validate_order_before_send(self, figi: str, quantity: int, direction: str, price: float = None) -> Tuple[bool, str, Dict]:
        self._wait_for_rate_limit()

        ticker = self._get_ticker_by_figi(figi) or figi[:8]
        info(f"🔍 Валидация заявки {ticker}: {direction} {quantity} лотов")

        additional_info = {}

        if quantity <= 0:
            return False, f"Количество {quantity} <= 0", additional_info

        max_lots = self.get_max_lots(figi, direction, price)
        if max_lots > 0 and quantity > max_lots:
            return False, f"Превышен лимит: {quantity} > {max_lots} лотов", additional_info
        additional_info['max_lots'] = max_lots

        if price:
            if price <= 0:
                return False, f"Цена {price} <= 0", additional_info

            step = self._get_min_price_increment_advanced(figi)
            if step > 0:
                remainder = price % step
                if remainder > 0.0001:
                    suggested = round(price / step) * step
                    return False, f"Цена не кратна шагу {step}, предлагается {suggested:.4f}", additional_info

        status = self.get_trading_status(figi)
        if not status.get('api_trade_available', False):
            return False, "API торговля недоступна", additional_info

        info(f"✅ Заявка {ticker} прошла валидацию")
        return True, "OK", additional_info

    def _is_long_position(self, figi: str) -> bool:
        try:
            positions = self.get_positions()
            for pos in positions:
                if pos.get('figi') == figi and pos.get('quantity', 0) > 0:
                    return True
            return False
        except Exception as e:
            debug(f"Ошибка проверки LONG позиции: {e}")
            return False

    def get_withdraw_limits(self) -> Dict[str, Any]:
        self._wait_for_rate_limit()

        try:
            with Client(self.token) as client:
                limits = client.operations.get_withdraw_limits(account_id=self.account_id)

                result = {'money': [], 'blocked': []}

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

                return result

        except Exception as e:
            warning(f"⚠️ Ошибка получения лимитов вывода: {e}")
            return {'money': [], 'blocked': []}

    def get_account_info(self) -> Dict[str, Any]:
        """
        Получение информации о счетах пользователя

        Returns:
            Dict: Информация о счетах (ID, тип, статус)
        """
        self._wait_for_rate_limit()

        try:
            with Client(self.token) as client:
                accounts_response = client.users.get_accounts()

                if not accounts_response.accounts:
                    return {'accounts': [], 'status': 'no_accounts', 'error': 'Нет доступных счетов'}

                accounts = []
                main_account_id = None

                for acc in accounts_response.accounts:
                    account_info = {
                        'id': acc.id,
                        'type': str(acc.type),
                        'status': str(acc.status),
                        'opened_date': acc.opened_date.isoformat() if acc.opened_date else None,
                        'closed_date': acc.closed_date.isoformat() if acc.closed_date else None,
                    }
                    accounts.append(account_info)

                    # Запоминаем основной счет (тот, который используется в торговле)
                    if self._account_id and acc.id == self._account_id:
                        main_account_id = acc.id

                # Также получаем информацию о пользователе
                info_response = client.users.get_info()
                user_info = {
                    'username': info_response.username if hasattr(info_response, 'username') else None,
                    'tariff': info_response.tariff if hasattr(info_response, 'tariff') else None,
                    'qual_status': info_response.qual_status if hasattr(info_response, 'qual_status') else None,
                }

                return {
                    'accounts': accounts,
                    'user_info': user_info,
                    'main_account_id': main_account_id or (accounts[0]['id'] if accounts else None),
                    'total_accounts': len(accounts),
                    'status': 'ok'
                }

        except Exception as e:
            error(f"Ошибка получения информации о счетах: {e}")
            return {'accounts': [], 'status': 'error', 'error': str(e)}

    # ========== НОВЫЕ МЕТОДЫ ДЛЯ МОДИФИКАЦИИ ЗАЯВОК ==========

    def modify_limit_order(self, order_id: str, new_price: float, quantity: int = None) -> bool:
        """
        Модификация существующей лимитной заявки

        Args:
            order_id: ID заявки для модификации
            new_price: Новая цена
            quantity: Новое количество (если None - оставляем текущее)

        Returns:
            bool: True если успешно, False в противном случае
        """
        self._wait_for_rate_limit()

        info(f"🔧 МОДИФИКАЦИЯ ЗАЯВКИ {order_id[:8]}...")
        info(f"   Новая цена: {new_price:.2f}₽")
        if quantity:
            info(f"   Новое количество: {quantity} шт")

        try:
            with Client(self.token) as client:
                # Получаем информацию о заявке
                orders = client.orders.get_orders(account_id=self.account_id)
                order_info = None

                for order in orders.orders:
                    if order.order_id == order_id:
                        order_info = order
                        break

                if not order_info:
                    warning(f"   ⚠️ Заявка {order_id[:8]} не найдена")
                    return False

                # Отменяем старую заявку
                client.orders.cancel_order(
                    account_id=self.account_id,
                    order_id=order_id
                )
                info(f"   ✅ Старая заявка отменена")

                # Создаём новую с новой ценой
                new_quantity = quantity or order_info.lots_requested
                target_price = self._round_to_min_increment(order_info.figi, new_price)
                price_quotation = decimal_to_quotation(Decimal(str(target_price)))

                dir_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
                direction_str = "BUY" if order_info.direction == 1 else "SELL"

                new_order = client.orders.post_order(
                    figi=order_info.figi,
                    quantity=new_quantity,
                    price=price_quotation,
                    direction=dir_map[direction_str],
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_LIMIT,
                    order_id="",
                    confirm_margin_trade=(direction_str == "SELL")
                )

                if new_order and new_order.order_id:
                    success(f"   ✅ Заявка модифицирована: новая цена {target_price:.2f}₽, ID={new_order.order_id[:8]}")
                    return True
                else:
                    error(f"   ❌ Не удалось создать новую заявку")
                    return False

        except Exception as e:
            error(f"❌ Ошибка модификации заявки {order_id[:8]}: {e}")
            return False

    def get_all_active_orders_detailed(self) -> List[Dict[str, Any]]:
        """
        Получение всех активных заявок с детальной информацией

        Returns:
            List[Dict]: Список заявок с полной информацией
        """
        self._wait_for_rate_limit()

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
                        'ticker': self._get_ticker_by_figi(order.figi) or order.figi[:8],
                        'created_at': getattr(order, 'created_at', None)
                    })

                info(f"📋 Получено {len(result)} активных заявок")
                return result

        except Exception as e:
            debug(f"Ошибка получения заявок: {e}")
            return []

    # ========== НОВЫЕ МЕТОДЫ ДЛЯ РАБОТЫ СО СТАКАНОМ ==========

    def get_best_price(self, figi: str, direction: str) -> Optional[float]:
        """Получить лучшую цену из стакана"""
        orderbook = self.get_orderbook(figi, depth=1)
        if not orderbook:
            return None

        if direction == "BUY":
            return orderbook.get('best_bid')
        else:
            return orderbook.get('best_ask')

    def get_orderbook(self, figi: str, depth: int = 10) -> Optional[Dict[str, Any]]:
        """
        Получить стакан заявок (OrderBook)

        Args:
            figi: Идентификатор инструмента
            depth: Глубина стакана (1-20)

        Returns:
            Dict с bid/ask массивами или None при ошибке
        """
        try:
            with Client(self.token) as client:
                # Пробуем получить стакан
                orderbook = client.market_data.get_order_book(figi=figi, depth=depth)

                # Конвертируем Quotation в float
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
                    'close_price': float(
                        quotation_to_decimal(orderbook.close_price)) if orderbook.close_price else None,
                    'limit_up': float(quotation_to_decimal(orderbook.limit_up)) if orderbook.limit_up else None,
                    'limit_down': float(quotation_to_decimal(orderbook.limit_down)) if orderbook.limit_down else None,
                }
        except Exception as e:
            from trading_bot.logger import error as log_error
            log_error(f"Ошибка получения стакана для {figi}: {e}")
            return None

    def check_liquidity(self, figi: str, required_volume: int = 10000, min_depth: int = 3) -> Dict[str, Any]:
        """
        Проверить ликвидность инструмента через стакан
        """
        orderbook = self.get_orderbook(figi, depth=min_depth)

        if not orderbook:
            return {
                'is_liquid': False,
                'reason': 'Не удалось получить стакан',
                'bid_volume_rub': 0,
                'ask_volume_rub': 0,
                'spread_pct': None
            }

        best_bid = orderbook.get('best_bid')
        best_ask = orderbook.get('best_ask')
        bid_volume_rub = orderbook.get('bid_volume', 0) * (best_bid or 0)
        ask_volume_rub = orderbook.get('ask_volume', 0) * (best_ask or 0)

        spread_pct = None
        if best_bid and best_ask and best_bid > 0:
            spread_pct = (best_ask - best_bid) / best_bid * 100

        # Критерии ликвидности
        is_bid_liquid = bid_volume_rub >= required_volume
        is_ask_liquid = ask_volume_rub >= required_volume
        is_spread_ok = spread_pct is None or spread_pct <= 0.5

        is_liquid = is_bid_liquid and is_ask_liquid and is_spread_ok

        reason = None
        if not is_liquid:
            if not is_bid_liquid:
                reason = f"Малый объём покупки: {bid_volume_rub:.0f}₽ (нужно {required_volume}₽)"
            elif not is_ask_liquid:
                reason = f"Малый объём продажи: {ask_volume_rub:.0f}₽ (нужно {required_volume}₽)"
            elif not is_spread_ok:
                reason = f"Слишком большой спред: {spread_pct:.2f}% (макс 0.5%)"

        return {
            'is_liquid': is_liquid,
            'reason': reason,
            'bid_volume_rub': bid_volume_rub,
            'ask_volume_rub': ask_volume_rub,
            'best_bid': best_bid,
            'best_ask': best_ask,
            'spread_pct': spread_pct,
            'depth': min_depth
        }

    def place_smart_order(self, figi: str, quantity: int, direction: str,
                      max_slippage_pct: float = 0.5) -> Dict[str, Any]:
        """
        Умное выставление заявки с анализом стакана
        """
        from trading_bot.logger import warning as log_warning  # ← ДОБАВИТЬ

        # Получаем стакан
        orderbook = self.get_orderbook(figi, depth=10)
        if not orderbook:
            return {'success': False, 'reason': 'Не удалось получить стакан'}

        best_bid = orderbook.get('best_bid')
        best_ask = orderbook.get('best_ask')

        if not best_bid or not best_ask:
            return {'success': False, 'reason': 'Пустой стакан, нет цен'}

        current_price = best_ask if direction == "BUY" else best_bid
        required_volume_rub = quantity * current_price

        # Проверяем ликвидность для нашего объёма
        if direction == "BUY":
            available_volume = orderbook.get('ask_volume', 0)
            available_volume_rub = available_volume * best_ask
        else:
            available_volume = orderbook.get('bid_volume', 0)
            available_volume_rub = available_volume * best_bid

        # Если ликвидности недостаточно - разбиваем заявку
        if required_volume_rub > available_volume_rub * 0.9:
            log_warning(f"⚠️ Недостаточно ликвидности для {direction} {quantity} шт")
            log_warning(f"   Доступно: {available_volume} шт ({available_volume_rub:.0f}₽)")
            log_warning(f"   Требуется: {quantity} шт ({required_volume_rub:.0f}₽)")
            return self._place_sliced_order(figi, quantity, direction, orderbook)

        # Проверяем проскальзывание
        spread = best_ask - best_bid
        spread_pct = spread / best_bid * 100 if best_bid > 0 else 100

        if spread_pct > max_slippage_pct:
            log_warning(f"⚠️ Большой спред: {spread_pct:.2f}% > {max_slippage_pct}%")
            return {'success': False, 'reason': f'Спред {spread_pct:.2f}% превышает лимит'}

        # Выбираем оптимальный тип заявки
        if spread_pct < 0.1:
            # Узкий спред - можно рыночную
            order_type = "MARKET"
            price = None
        elif spread_pct < max_slippage_pct:
            # Средний спред - лимитная чуть лучше рынка
            if direction == "BUY":
                price = round(best_ask + spread * 0.1, 2)
            else:
                price = round(best_bid - spread * 0.1, 2)
            order_type = "LIMIT"
        else:
            order_type = "LIMIT"
            if direction == "BUY":
                price = best_bid
            else:
                price = best_ask

        # Исполняем заявку
        result = self._execute_smart_order(figi, quantity, direction, order_type, price)

        return result

    def _place_sliced_order(self, figi: str, quantity: int, direction: str,
                        orderbook: Dict) -> Dict[str, Any]:
        """Разбить крупную заявку на части по уровням стакана"""
        from trading_bot.logger import info as log_info, warning as log_warning  # ← ДОБАВИТЬ

        if direction == "BUY":
            levels = orderbook.get('asks', [])
        else:
            levels = orderbook.get('bids', [])

        if not levels:
            return {'success': False, 'reason': 'Нет уровней в стакане'}

        remaining = quantity
        filled = 0
        total_price = 0.0

        for level in levels:
            if remaining <= 0:
                break

            level_qty = level.get('quantity', 0)
            level_price = level.get('price', 0)

            if level_qty <= 0 or level_price <= 0:
                continue

            take_qty = min(remaining, level_qty)

            # ИСПРАВЛЕНО: используем buy/sell вместо place_pending_order
            if direction == "BUY":
                result = self.buy(figi, take_qty)  # ← ИСПРАВЛЕНО
            else:
                result = self.sell(figi, take_qty)  # ← ИСПРАВЛЕНО

            if result:
                filled += take_qty
                total_price += take_qty * level_price
                remaining -= take_qty
                log_info(f"   📊 Часть {take_qty} шт по {level_price:.2f}₽")
            else:
                log_warning(f"   ❌ Не удалось исполнить часть {take_qty} шт")
                break  # Прерываем при первой неудаче

        if filled > 0:
            avg_price = total_price / filled
            log_info(f"✅ Итого: {filled}/{quantity} шт по сред. {avg_price:.2f}₽")
            return {'success': True, 'filled': filled, 'avg_price': avg_price, 'remaining': remaining}

        return {'success': False, 'reason': 'Не удалось исполнить ни одной части', 'filled': 0}

    def _execute_smart_order(self, figi: str, quantity: int, direction: str,
                         order_type: str, price: float = None) -> Dict[str, Any]:
        """Исполнение умной заявки"""
        from trading_bot.logger import info as log_info  # ← ДОБАВИТЬ

        if order_type == "MARKET":
            log_info(f"📊 Рыночная заявка: {direction} {quantity} шт")
            if direction == "BUY":
                success = self.buy(figi, quantity)
            else:
                success = self.sell(figi, quantity)

            return {
                'success': success,
                'type': 'MARKET',
                'filled': quantity if success else 0
            }

        elif order_type == "LIMIT":
            if price is None:
                # Если цена не указана, берем рыночную
                price = self.get_current_price(figi)
                if price is None:
                    return {'success': False, 'reason': 'Не удалось получить цену'}

            log_info(f"📊 Лимитная заявка: {direction} {quantity} шт по {price:.2f}₽")

            # ИСПРАВЛЕНО: используем place_limit_order вместо place_pending_order
            if direction == "BUY":
                success = self.place_limit_order(figi, quantity, "BUY", price)
            else:
                success = self.place_limit_order(figi, quantity, "SELL", price)

            return {
                'success': success,
                'type': 'LIMIT',
                'price': price,
                'filled': quantity if success else 0
            }

        return {'success': False, 'reason': 'Неизвестный тип заявки'}

    def get_orderbook_text(self, figi: str, ticker: str = None, depth: int = 5) -> str:
        """Получить красивое текстовое представление стакана для Telegram"""
        orderbook = self.get_orderbook(figi, depth=depth)

        # Проверка на None
        if orderbook is None:
            return f"❌ Не удалось получить стакан для {ticker or figi}"

        if not orderbook.get('bids') or not orderbook.get('asks'):
            return f"📭 <b>Стакан {ticker or figi} пуст</b>\n\nНет активных заявок"

        ticker_str = ticker or figi[:8]
        bids = orderbook['bids'][:depth]
        asks = orderbook['asks'][:depth]

        # Находим максимальную ширину
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

        # Показываем asks (продажа) сверху вниз
        if asks:
            for i in range(min(depth, len(asks)) - 1, -1, -1):
                ask = asks[i]
                ask_price = ask.get('price', 0)
                ask_qty = ask.get('quantity', 0)
                lines.append(f"   {ask_price:>8.2f}₽  {ask_qty:>{max_volume_width}} шт")
        else:
            lines.append("   ❌ Нет заявок на продажу")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Показываем bids (покупка) сверху вниз
        if bids:
            for bid in bids:
                bid_price = bid.get('price', 0)
                bid_qty = bid.get('quantity', 0)
                lines.append(f"   {bid_price:>8.2f}₽  {bid_qty:>{max_volume_width}} шт")
        else:
            lines.append("   ❌ Нет заявок на покупку")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Добавляем статистику
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

        bid_volume_rub = orderbook.get('bid_volume', 0) * (best_bid or 0)
        ask_volume_rub = orderbook.get('ask_volume', 0) * (best_ask or 0)

        liquidity_status = "✅ ДОСТАТОЧНА" if bid_volume_rub > 5000 and ask_volume_rub > 5000 else "⚠️ НИЗКАЯ"

        lines.extend([
            f"📊 <b>ОБЪЁМ BID:</b> {orderbook.get('bid_volume', 0)} шт ({bid_volume_rub:.0f}₽)",
            f"📊 <b>ОБЪЁМ ASK:</b> {orderbook.get('ask_volume', 0)} шт ({ask_volume_rub:.0f}₽)",
            f"💧 <b>ЛИКВИДНОСТЬ:</b> {liquidity_status}"
        ])

        return "\n".join(lines)

    def close_position_with_retry(self, figi: str, quantity: int, direction: str,
                                  max_attempts: int = 5, emergency_slippage: float = 0.05) -> Dict[str, Any]:
        """
        УМНОЕ ЗАКРЫТИЕ ПОЗИЦИИ С АВТОМАТИЧЕСКИМ ПРОСКАЛЬЗЫВАНИЕМ
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        # ✅ ОКРУГЛЕНИЕ ДО ЛОТА (используем _get_lot_size вместо прямого доступа к _shares_cache)
        original_qty = quantity
        lot_size = self._get_lot_size(figi)  # ← ИСПРАВЛЕНО

        if lot_size > 1:
            lots = quantity // lot_size
            if lots == 0:
                lots = 1
            quantity = lots * lot_size

            if quantity != original_qty:
                warning(f"🔄 CLOSE {ticker}: округление {original_qty} → {quantity} (лот={lot_size})")

        info(f"\n{'🔒' * 40}")
        info(f"🔒 УМНОЕ ЗАКРЫТИЕ {ticker}")
        info(f"   Сторона: {direction}")
        info(f"   Запрошено: {original_qty} шт")
        info(f"   Исполняется: {quantity} шт")
        info(f"   Лотность: {lot_size}")
        info(f"{'🔒' * 40}")

        errors = []
        current_price = None
        price_fetch_failures = 0
        MAX_PRICE_FAILURES = 3

        for attempt in range(max_attempts):
            try:
                current_price = self.get_current_price(figi)
                if not current_price:
                    price_fetch_failures += 1
                    if price_fetch_failures >= MAX_PRICE_FAILURES:
                        error(f"   ❌ Не удалось получить цену {MAX_PRICE_FAILURES} раз подряд. Прерываем закрытие.")
                        break
                    errors.append(f"Попытка {attempt + 1}: не удалось получить цену")
                    time.sleep(1)
                    continue

                price_fetch_failures = 0

                slippage_step = emergency_slippage / max_attempts
                current_slippage = slippage_step * (attempt + 1)

                if direction == "SELL":
                    price = round(current_price * (1 - current_slippage), 2)
                else:
                    price = round(current_price * (1 + current_slippage), 2)

                price = max(price, 0.01)

                info(f"   Попытка {attempt + 1}/{max_attempts}:")
                info(f"      Текущая цена: {current_price:.2f}₽")
                info(f"      Цена закрытия: {price:.2f}₽ (проскальзывание {current_slippage * 100:.2f}%)")

                result = self.place_limit_order(figi, quantity, direction, price)

                if result:
                    success(f"✅ {ticker} УСПЕШНО ЗАКРЫТ по {price:.2f}₽!")
                    blacklist_manager.report_success(ticker)
                    return {
                        'success': True,
                        'price': price,
                        'slippage_pct': current_slippage * 100,
                        'attempts': attempt + 1,
                        'quantity': quantity,
                        'original_quantity': original_qty
                    }
                else:
                    errors.append(f"Попытка {attempt + 1}: заявка отклонена")

            except Exception as e:
                error_msg = str(e)
                errors.append(f"Попытка {attempt + 1}: {error_msg[:50]}")

                if "30042" in error_msg:
                    warning(f"   ⚠️ {ticker}: ОШИБКА 30042 - проверяем наличие позиции у брокера...")

                    try:
                        positions = self.get_positions()
                        real_figi_set = {p['figi'] for p in positions if abs(p.get('quantity', 0)) > 0}

                        if figi not in real_figi_set:
                            warning(f"   🧹 Позиции {ticker} нет у брокера! Удаляем из менеджера")
                            position_manager.remove_position(figi)
                            return {
                                'success': True,
                                'already_closed': True,
                                'price': current_price if current_price else 0,
                                'attempts': attempt + 1,
                                'reason': 'Позиция уже закрыта у брокера'
                            }

                        if self.is_confirmation_required(figi):
                            info(f"   📋 {ticker} - OTC инструмент, требуется ручное закрытие")
                            position_manager.remove_position(figi)
                            return {
                                'success': False,
                                'requires_manual': True,
                                'price': current_price if current_price else 0,
                                'attempts': attempt + 1,
                                'reason': 'OTC инструмент - требуется ручное закрытие'
                            }

                    except Exception as check_error:
                        debug(f"   Ошибка проверки позиций: {check_error}")

                    if attempt == max_attempts - 1:
                        warning(f"   ⚡ Последний шанс: рыночная заявка")
                        market_result = self._place_market_order_impl(figi, quantity, direction)
                        if market_result:
                            success(f"✅ {ticker} ЗАКРЫТ рыночной заявкой!")
                            blacklist_manager.report_success(ticker)
                            return {
                                'success': True,
                                'price': current_price if current_price else 0,
                                'slippage_pct': 0,
                                'attempts': attempt + 1,
                                'type': 'MARKET',
                                'quantity': quantity
                            }

                elif attempt == max_attempts - 1:
                    warning(f"   ⚡ Последний шанс: рыночная заявка")
                    market_result = self._place_market_order_impl(figi, quantity, direction)
                    if market_result:
                        success(f"✅ {ticker} ЗАКРЫТ рыночной заявкой!")
                        blacklist_manager.report_success(ticker)
                        return {
                            'success': True,
                            'price': current_price if current_price else 0,
                            'slippage_pct': 0,
                            'attempts': attempt + 1,
                            'type': 'MARKET',
                            'quantity': quantity
                        }

            time.sleep(1)

        error(f"\n❌ НЕ УДАЛОСЬ ЗАКРЫТЬ {ticker} после {max_attempts} попыток!")
        blacklist_manager.add_temporary(ticker, ttl_minutes=60)
        warning(f"⛔ {ticker} автоматически добавлен в чёрный список на 1 час")

        return {
            'success': False,
            'errors': errors,
            'attempts': max_attempts,
            'blocked': True,
            'quantity': quantity,
            'original_quantity': original_qty
        }

    # ========== FALLBACK МЕТОДЫ ДЛЯ НАДЁЖНОЙ РАБОТЫ ==========

    def buy_with_fallback(self, figi: str, quantity: int, price: float = None) -> Dict[str, Any]:
        """
        ПОКУПКА С FALLBACK - перебирает все стратегии
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        if price is None:
            price = self.get_current_price(figi)
            if not price:
                return {'success': False, 'error': 'Не удалось получить цену'}

        # Стратегии в порядке приоритета
        strategies = [
            {'name': 'Рыночная (с маржей)', 'func': lambda: self._place_market_order_impl(figi, quantity, "BUY"),
             'confirm': True},
            {'name': 'Рыночная (без маржи)', 'func': lambda: self._place_market_order_impl(figi, quantity, "BUY"),
             'confirm': False},
            {'name': f'Лимитная +1% ({price * 1.01:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "BUY", price * 1.01), 'confirm': False},
            {'name': f'Лимитная +2% ({price * 1.02:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "BUY", price * 1.02), 'confirm': False},
            {'name': f'Лимитная +5% ({price * 1.05:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "BUY", price * 1.05), 'confirm': False},
        ]

        errors = []
        for strategy in strategies:
            try:
                info(f"   🔄 Попытка: {strategy['name']}")
                # Временно устанавливаем confirm_margin_trade
                original = getattr(self, '_temp_confirm_margin', None)
                self._temp_confirm_margin = strategy['confirm']

                result = strategy['func']()

                if original is not None:
                    self._temp_confirm_margin = original
                elif hasattr(self, '_temp_confirm_margin'):
                    delattr(self, '_temp_confirm_margin')

                if result:
                    success(f"✅ ПОКУПКА {ticker} УСПЕШНА: {strategy['name']}")
                    return {'success': True, 'strategy': strategy['name'], 'price': price}
                else:
                    errors.append(f"{strategy['name']}: не удалась")
            except Exception as e:
                errors.append(f"{strategy['name']}: {str(e)[:50]}")
                if hasattr(self, '_temp_confirm_margin'):
                    delattr(self, '_temp_confirm_margin')
            time.sleep(0.5)

        error(f"❌ НЕ УДАЛОСЬ КУПИТЬ {ticker} после {len(strategies)} стратегий")
        return {'success': False, 'errors': errors, 'ticker': ticker}

    def sell_with_fallback(self, figi: str, quantity: int, price: float = None) -> Dict[str, Any]:
        """
        ПРОДАЖА С FALLBACK (для LONG позиций)
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        if price is None:
            price = self.get_current_price(figi)
            if not price:
                return {'success': False, 'error': 'Не удалось получить цену'}

        strategies = [
            {'name': 'Рыночная (с маржей)', 'func': lambda: self._place_market_order_impl(figi, quantity, "SELL"),
             'confirm': True},
            {'name': 'Рыночная (без маржи)', 'func': lambda: self._place_market_order_impl(figi, quantity, "SELL"),
             'confirm': False},
            {'name': f'Лимитная -1% ({price * 0.99:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "SELL", price * 0.99), 'confirm': False},
            {'name': f'Лимитная -2% ({price * 0.98:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "SELL", price * 0.98), 'confirm': False},
            {'name': f'Лимитная -5% ({price * 0.95:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "SELL", price * 0.95), 'confirm': False},
        ]

        errors = []
        for strategy in strategies:
            try:
                info(f"   🔄 Попытка: {strategy['name']}")
                original = getattr(self, '_temp_confirm_margin', None)
                self._temp_confirm_margin = strategy['confirm']

                result = strategy['func']()

                if original is not None:
                    self._temp_confirm_margin = original
                elif hasattr(self, '_temp_confirm_margin'):
                    delattr(self, '_temp_confirm_margin')

                if result:
                    success(f"✅ ПРОДАЖА {ticker} УСПЕШНА: {strategy['name']}")
                    return {'success': True, 'strategy': strategy['name'], 'price': price}
                else:
                    errors.append(f"{strategy['name']}: не удалась")
            except Exception as e:
                errors.append(f"{strategy['name']}: {str(e)[:50]}")
                if hasattr(self, '_temp_confirm_margin'):
                    delattr(self, '_temp_confirm_margin')
            time.sleep(0.5)

        error(f"❌ НЕ УДАЛОСЬ ПРОДАТЬ {ticker} после {len(strategies)} стратегий")
        return {'success': False, 'errors': errors, 'ticker': ticker}

    def close_short_with_fallback(self, figi: str, quantity: int, price: float = None) -> Dict[str, Any]:
        """
        ЗАКРЫТИЕ SHORT ПОЗИЦИИ С FALLBACK
        Специальный метод для SHORT (покупка для закрытия)
        """
        ticker = self._get_ticker_by_figi(figi) or figi[:8]

        if price is None:
            price = self.get_current_price(figi)
            if not price:
                return {'success': False, 'error': 'Не удалось получить цену'}

        info(f"\n🔒 ЗАКРЫТИЕ SHORT {ticker} С FALLBACK")
        info(f"   Количество: {quantity} шт, Текущая цена: {price:.2f}₽")

        # Стратегии для SHORT (покупка для закрытия)
        strategies = [
            {'name': 'Рыночная (с маржей)', 'func': lambda: self._place_market_order_impl(figi, quantity, "BUY"),
             'confirm': True, 'slippage': 0},
            {'name': 'Рыночная (без маржи)', 'func': lambda: self._place_market_order_impl(figi, quantity, "BUY"),
             'confirm': False, 'slippage': 0},
            {'name': f'Лимитная +2% ({price * 1.02:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "BUY", price * 1.02), 'confirm': False,
             'slippage': 2},
            {'name': f'Лимитная +5% ({price * 1.05:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "BUY", price * 1.05), 'confirm': False,
             'slippage': 5},
            {'name': f'Лимитная +10% ({price * 1.10:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "BUY", price * 1.10), 'confirm': False,
             'slippage': 10},
            {'name': f'Лимитная +15% ({price * 1.15:.2f}₽)',
             'func': lambda: self.place_limit_order(figi, quantity, "BUY", price * 1.15), 'confirm': False,
             'slippage': 15},
        ]

        errors = []
        for strategy in strategies:
            try:
                info(f"\n   📍 Попытка: {strategy['name']}")
                if strategy['slippage'] > 0:
                    info(f"      Проскальзывание: +{strategy['slippage']}%")

                original = getattr(self, '_temp_confirm_margin', None)
                self._temp_confirm_margin = strategy['confirm']

                result = strategy['func']()

                if original is not None:
                    self._temp_confirm_margin = original
                elif hasattr(self, '_temp_confirm_margin'):
                    delattr(self, '_temp_confirm_margin')

                if result:
                    success(f"\n✅ SHORT {ticker} ЗАКРЫТ: {strategy['name']}")
                    return {
                        'success': True,
                        'strategy': strategy['name'],
                        'slippage_pct': strategy['slippage'],
                        'price': price
                    }
                else:
                    errors.append(f"{strategy['name']}: не удалась")
                    warning(f"   ❌ {strategy['name']} не удалась")

            except Exception as e:
                error_msg = str(e)
                errors.append(f"{strategy['name']}: {error_msg[:50]}")
                warning(f"   ❌ {strategy['name']} не удалась: {error_msg[:80]}")

                # ========== ОБРАБОТКА ОШИБКИ 30240 ==========
                if "30240" in error_msg:
                    warning(f"\n🔐 {ticker}: ОШИБКА 30240 - ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ!")
                    warning(f"   НЕВОЗМОЖНО ЗАКРЫТЬ АВТОМАТИЧЕСКИ!")
                    warning(f"   📱 Закройте позицию ВРУЧНУЮ в приложении Т-Банк!")

                    # Отправляем Telegram
                    try:
                        from trading_bot.telegram.telegram_notifier import get_telegram_notifier
                        telegram = get_telegram_notifier()
                        if telegram:
                            telegram.send_error(
                                f"🚨 **ТРЕБУЕТСЯ РУЧНОЕ ЗАКРЫТИЕ!**\n\n"
                                f"Инструмент {ticker} требует подтверждения сделок!\n"
                                f"Невозможно закрыть автоматически.\n\n"
                                f"📊 Позиция: SHORT {quantity} шт\n"
                                f"💰 Цена: {price:.2f}₽\n\n"
                                f"**Закройте вручную в приложении Т-Банк!**"
                            )
                    except Exception as e:
                        debug(f"Ошибка отправки Telegram: {e}")

                    return {'success': False, 'requires_manual': True, 'error': '30240', 'ticker': ticker}

                if hasattr(self, '_temp_confirm_margin'):
                    delattr(self, '_temp_confirm_margin')

            time.sleep(1)

        error(f"\n❌ НЕ УДАЛОСЬ ЗАКРЫТЬ SHORT {ticker} после {len(strategies)} стратегий")
        return {'success': False, 'errors': errors, 'ticker': ticker}

    # ========== WEBSOCKET ДЛЯ РЕАЛЬНОГО ВРЕМЕНИ ==========

    async def connect_websocket(self, ticker: str, callback):
        """
        Подключение к WebSocket для получения цен в реальном времени

        Args:
            ticker: Тикер акции
            callback: Функция обратного вызова (price, timestamp)
        """
        try:
            import websockets
            import json

            # Получаем FIGI
            figi = self._get_figi_by_ticker(ticker)
            if not figi:
                warning(f"❌ WebSocket: не найден FIGI для {ticker}")
                return None

            # Формируем URL для WebSocket Т-Банка
            ws_url = f"wss://invest-public-api.tinkoff.ru/ws/trading/v1/marketdata/stream"

            async with websockets.connect(ws_url) as websocket:
                # Отправляем запрос на подписку
                subscribe_msg = {
                    "event": "subscribe",
                    "figi": figi,
                    "interval": "1min"
                }
                await websocket.send(json.dumps(subscribe_msg))
                info(f"📡 WebSocket: подписка на {ticker} ({figi})")

                # Слушаем сообщения
                async for message in websocket:
                    data = json.loads(message)
                    if 'price' in data:
                        price = float(data['price'])
                        timestamp = datetime.now()

                        # Вызываем callback
                        if callback:
                            await callback(ticker, price, timestamp)

        except ImportError:
            warning("⚠️ websockets не установлен. Установите: pip install websockets")
            return None
        except Exception as e:
            error(f"❌ WebSocket ошибка для {ticker}: {e}")
            return None

    # ========== АЛИАСЫ ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ ==========

    def sell_short(self, figi: str, quantity: int, use_market: bool = None) -> bool:
        """
        Алиас для sell() - для обратной совместимости
        Используется в некоторых частях кода для SHORT позиций
        """
        return self.sell(figi, quantity, use_market)


# Глобальная переменная для позиций (если используется в коде)
position_entries = {}


# Единый экземпляр
tbank = TBankClient()