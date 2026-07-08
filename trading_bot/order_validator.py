"""
Модуль для проверки и подтверждения заявок через T-Invest API
Обеспечивает:
1. Предварительную валидацию перед отправкой
2. Подтверждение создания заявки
3. Отслеживание статуса исполнения
"""

import time
import uuid
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timedelta

from t_tech.invest import Client, OrderType, OrderDirection, RequestError
from t_tech.invest.utils import quotation_to_decimal, decimal_to_quotation

from trading_bot.logger import info, warning, error, success, debug
from trading_bot.utils.figi_resolver import get_figi_resolver


class OrderValidator:
    """
    Класс для валидации и подтверждения заявок
    """

    def __init__(self, token: str, account_id: str):
        self.token = token
        self.account_id = account_id
        self._pending_orders = {}  # {order_id: {status, created_at, ...}}
        self._order_cache = {}  # кэш статусов заявок
        self._cache_ttl = 5  # секунд
        self._figi_resolver = get_figi_resolver()  # Инициализация резолвера FIGI

    # ========== 1. ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА ==========

    def validate_before_send(
            self,
            figi: str,
            quantity: int,
            direction: str,
            price: float = None,
            is_short: bool = False
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Полная предварительная проверка заявки
        """
        ticker = self._figi_resolver.get_ticker_by_figi(figi) or figi[:8]
        info(f"🔍 Валидация заявки: {direction} {quantity} шт, figi={figi[:8]}...")

        additional_info = {}

        with Client(self.token) as client:
            # 1. Проверка количества
            if quantity <= 0:
                return False, f"Количество {quantity} <= 0", additional_info

            # 2. Проверка существования инструмента
            try:
                instrument = client.instruments.get_instrument_by(
                    id=figi,
                    id_type=1  # INSTRUMENT_ID_TYPE_FIGI
                )
                if not instrument or not instrument.instrument:
                    return False, f"Инструмент {figi} не найден", additional_info

                additional_info['instrument_name'] = instrument.instrument.name
                additional_info['lot'] = instrument.instrument.lot
                additional_info['currency'] = instrument.instrument.currency

            except Exception as e:
                return False, f"Ошибка получения информации об инструменте: {e}", additional_info

            # 3. Проверка статуса торгов (GetTradingStatus)
            try:
                trading_status = client.market_data.get_trading_status(instrument_id=figi)

                additional_info['api_trade_available'] = trading_status.api_trade_available_flag
                additional_info['market_order_available'] = trading_status.market_order_available_flag
                additional_info['limit_order_available'] = trading_status.limit_order_available_flag
                additional_info['trading_status'] = trading_status.trading_status

                # Проверяем доступность API торговли
                if not trading_status.api_trade_available_flag:
                    return False, "API торговля недоступна", additional_info

                # ✅ НОВОЕ: Для SHORT если нет рыночных, но есть лимитные - пропускаем
                if direction == "SELL" and is_short:
                    if not trading_status.market_order_available_flag and not trading_status.limit_order_available_flag:
                        return False, f"Нет доступных типов заявок для SHORT {ticker}", additional_info
                    elif not trading_status.market_order_available_flag:
                        # Предупреждаем, но не блокируем - используем лимитные
                        info(f"   ⚠️ {ticker}: рыночные заявки недоступны, будет использована лимитная")
                else:
                    # Для LONG и обычной продажи
                    if price:
                        if not trading_status.limit_order_available_flag:
                            return False, "Лимитные заявки недоступны для этого инструмента", additional_info
                    else:
                        if not trading_status.market_order_available_flag:
                            # ❌ СТАРЫЙ КОД (блокирует):
                            # return False, "Рыночные заявки недоступны для этого инструмента", additional_info
                            
                            # ✅ НОВЫЙ КОД (автоматический переход на лимитную):
                            if trading_status.limit_order_available_flag:
                                info(f"   ⚠️ Рыночные заявки недоступны, используем лимитную")
                                additional_info['use_limit_order'] = True
                                additional_info['limit_price_hint'] = self._get_current_price(client, figi) * 0.99
                                # НЕ БЛОКИРУЕМ, а просто меняем тип заявки
                            else:
                                return False, "Нет доступных типов заявок для этого инструмента", additional_info

            except Exception as e:
                warning(f"⚠️ Ошибка получения статуса торгов: {e}")
                additional_info['trading_status_error'] = str(e)

            # 4. Проверка цены (для лимитных заявок)
            if price:
                if price <= 0:
                    return False, f"Цена {price} <= 0", additional_info

                # Получаем шаг цены
                try:
                    step = self._get_min_price_increment(client, figi)
                    additional_info['min_price_increment'] = step

                    if step > 0:
                        from decimal import Decimal, ROUND_HALF_UP
                        price_decimal = Decimal(str(price))
                        step_decimal = Decimal(str(step))
                        rounded_price = float(price_decimal.quantize(step_decimal, rounding=ROUND_HALF_UP))

                        if abs(price - rounded_price) > 0.0001:
                            return False, f"Цена не кратна шагу {step}, предлагается {rounded_price:.4f}", additional_info

                except Exception as e:
                    warning(f"⚠️ Ошибка получения шага цены: {e}")

            # 5. Проверка максимального количества лотов (GetMaxLots)
            try:
                from decimal import Decimal
                from t_tech.invest.schemas import GetMaxLotsRequest

                if price:
                    price_quotation = decimal_to_quotation(Decimal(str(price)))
                    max_lots_request = GetMaxLotsRequest(
                        account_id=self.account_id,
                        instrument_id=figi,
                        price=price_quotation
                    )
                    max_lots = client.orders.get_max_lots(max_lots_request)
                else:
                    max_lots_request = GetMaxLotsRequest(
                        account_id=self.account_id,
                        instrument_id=figi
                    )
                    max_lots = client.orders.get_max_lots(max_lots_request)

                if direction == "BUY":
                    max_quantity = max_lots.buy_limits.buy_max_lots * additional_info.get('lot', 1)
                else:
                    max_quantity = max_lots.sell_limits.sell_max_lots * additional_info.get('lot', 1)

                additional_info['max_quantity'] = max_quantity

                if max_quantity > 0 and quantity > max_quantity:
                    return False, f"Превышен лимит: {quantity} > {max_quantity} шт", additional_info

            except Exception as e:
                warning(f"⚠️ Ошибка получения max lots (пропускаем): {e}")
                additional_info['max_lots_error'] = str(e)


            # 6. Проверка средств для покупки
            if direction == "BUY":
                try:
                    available, _, _ = self._get_available_funds(client)
                    total_cost = quantity * (price or self._get_current_price(client, figi))

                    additional_info['available_funds'] = available
                    additional_info['required_funds'] = total_cost

                    if total_cost > available:
                        return False, f"Недостаточно средств: нужно {total_cost:.2f}₽, доступно {available:.2f}₽", additional_info

                except Exception as e:
                    warning(f"⚠️ Ошибка проверки средств: {e}")

            # 7. Проверка маржи для SHORT
            if direction == "SELL" and is_short:
                try:
                    margin = client.users.get_margin_attributes(account_id=self.account_id)
                    if margin:
                        available_margin = float(quotation_to_decimal(margin.liquid_portfolio)) - \
                                           float(quotation_to_decimal(margin.starting_margin))
                        additional_info['available_margin'] = available_margin

                        if available_margin <= 0:
                            return False, "Недостаточно маржи для SHORT-позиции", additional_info
                except Exception as e:
                    warning(f"⚠️ Ошибка проверки маржи: {e}")

        info(f"✅ Заявка прошла предварительную валидацию")
        return True, "OK", additional_info

    # ========== 2. ОТПРАВКА С ПОДТВЕРЖДЕНИЕМ ==========

    def send_order_with_confirmation(
            self,
            figi: str,
            quantity: int,
            direction: str,
            order_type: str = "MARKET",
            price: float = None,
            is_short: bool = False,
            max_wait_seconds: int = 10,
            check_interval: float = 0.5
    ) -> Dict[str, Any]:
        """
        Отправка заявки с подтверждением через GetOrderState
        """
        ticker = self._figi_resolver.get_ticker_by_figi(figi) or figi[:8]

        # Предварительная валидация
        is_valid, reason, validation_info = self.validate_before_send(figi, quantity, direction, price, is_short)
        if not is_valid:
            return {
                'success': False,
                'error': reason,
                'validation_info': validation_info,
                'order_id': None
            }

        # Отправка заявки
        order_id = None
        order_request_id = str(uuid.uuid4())

        try:
            with Client(self.token) as client:
                dir_map = {
                    "BUY": OrderDirection.ORDER_DIRECTION_BUY,
                    "SELL": OrderDirection.ORDER_DIRECTION_SELL
                }

                # Формируем цену для лимитной заявки
                price_quotation = None
                if order_type == "LIMIT" and price:
                    from decimal import Decimal, ROUND_HALF_UP
                    step = self._get_min_price_increment(client, figi)
                    price_decimal = Decimal(str(price))
                    step_decimal = Decimal(str(step))
                    rounded_price = float(price_decimal.quantize(step_decimal, rounding=ROUND_HALF_UP))
                    price_quotation = decimal_to_quotation(Decimal(str(rounded_price)))
                    info(f"📊 Цена для лимитной заявки: {rounded_price:.4f}₽ (шаг={step})")

                # Отправляем заявку
                info(f"📡 Отправка {order_type} заявки: {direction} {quantity} шт {ticker}")

                order = client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    price=price_quotation,
                    direction=dir_map[direction],
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_LIMIT if order_type == "LIMIT" else OrderType.ORDER_TYPE_MARKET,
                    order_id=order_request_id,
                    confirm_margin_trade=(direction == "SELL" and is_short)
                )

                if order and order.order_id:
                    order_id = order.order_id
                    info(f"✅ Заявка отправлена, order_id={order_id}")
                else:
                    return {
                        'success': False,
                        'error': 'Заявка не была создана (пустой ответ API)',
                        'order_id': None
                    }

        except Exception as e:
            error_msg = str(e)
            info(f"❌ Ошибка при отправке заявки: {error_msg[:100]}")

            # Обработка известных ошибок
            if "30083" in error_msg:
                return {
                    'success': False,
                    'error': '30083 - инструмент не доступен для торговли',
                    'order_id': None,
                    'block_ticker': True
                }
            elif "30042" in error_msg:
                return {
                    'success': False,
                    'error': '30042 - недостаточно средств или маржи',
                    'order_id': None
                }
            # ✅ ДОБАВЛЕНА ОБРАБОТКА 30240
            elif "30240" in error_msg:
                warning(f"🔐 {ticker}: ОШИБКА 30240 - требуется подтверждение сделок!")
                return {
                    'success': False,
                    'error': '30240 - требуется подтверждение сделок',
                    'order_id': None,
                    'block_ticker': True
                }
            else:
                return {
                    'success': False,
                    'error': error_msg[:200],
                    'order_id': None
                }

        # ========== ПОДТВЕРЖДЕНИЕ ЧЕРЕЗ GETORDERSTATE ==========
        if order_id:
            result = self.confirm_order_created(
                order_id=order_id,
                figi=figi,
                max_wait_seconds=max_wait_seconds,
                check_interval=check_interval
            )
            result['order_id'] = order_id
            result['order_request_id'] = order_request_id
            return result

        return {
            'success': False,
            'error': 'Не удалось получить order_id',
            'order_id': None
        }

    def confirm_order_created(
            self,
            order_id: str,
            figi: str = None,
            max_wait_seconds: int = 10,
            check_interval: float = 0.5
    ) -> Dict[str, Any]:
        """Подтверждение создания заявки через GetOrderState с ожиданием исполнения"""
        ticker = self._figi_resolver.get_ticker_by_figi(figi) if figi else order_id[:8]

        start_time = time.time()
        attempts = 0

        while time.time() - start_time < max_wait_seconds:
            attempts += 1
            time.sleep(check_interval)

            try:
                with Client(self.token) as client:
                    order_state = client.orders.get_order_state(
                        account_id=self.account_id,
                        order_id=order_id
                    )

                    if order_state:
                        # Пробуем разные варианты для совместимости
                        executed = getattr(order_state, 'executed_lots',
                                           getattr(order_state, 'lots_executed', 0))
                        requested = getattr(order_state, 'lots_requested',
                                            getattr(order_state, 'requested_lots', 0))
                        status = str(getattr(order_state, 'order_state',
                                             getattr(order_state, 'state', 'UNKNOWN')))

                        info(f"🔍 Заявка {order_id[:8]}: статус={status}, исполнено={executed}/{requested}")

                        # ✅ Если заявка полностью исполнена
                        if executed >= requested or status in ['FILL', 'EXECUTED']:
                            info(f"✅ Заявка {order_id[:8]} ПОЛНОСТЬЮ ИСПОЛНЕНА!")

                            # Сохраняем в кэш
                            self._order_cache[order_id] = {
                                'status': status,
                                'executed_lots': executed,
                                'requested_lots': requested,
                                'last_check': time.time()
                            }

                            return {
                                'success': True,
                                'found': True,
                                'status': status,
                                'executed_lots': executed,
                                'requested_lots': requested,
                                'is_completed': True,
                                'attempts': attempts,
                                'wait_time': time.time() - start_time
                            }

                        # ✅ Если заявка отменена или отклонена
                        if status in ['CANCELLED', 'REJECTED', 'CANCELED']:
                            warning(f"⚠️ Заявка {order_id[:8]} {status}")

                            self._order_cache[order_id] = {
                                'status': status,
                                'executed_lots': executed,
                                'requested_lots': requested,
                                'last_check': time.time()
                            }

                            return {
                                'success': False,
                                'found': True,
                                'status': status,
                                'executed_lots': executed,
                                'requested_lots': requested,
                                'is_completed': False,
                                'attempts': attempts,
                                'wait_time': time.time() - start_time
                            }

                        # ✅ Заявка активна, но ещё не исполнена — продолжаем ждать
                        debug(f"⏳ Заявка {order_id[:8]}: активна, ждём исполнения ({executed}/{requested})")

                        # Обновляем кэш
                        self._order_cache[order_id] = {
                            'status': status,
                            'executed_lots': executed,
                            'requested_lots': requested,
                            'last_check': time.time()
                        }

                        continue

            except Exception as e:
                error_msg = str(e)
                if "30070" in error_msg:
                    debug(f"⏳ Заявка {order_id[:8]} ещё не появилась (попытка {attempts})")
                    continue
                else:
                    warning(f"⚠️ Ошибка при проверке заявки: {e}")
                    continue

        # Таймаут
        warning(f"❌ Заявка {order_id[:8]} НЕ ИСПОЛНЕНА после {max_wait_seconds}с")

        return {
            'success': False,
            'found': False,
            'error': f'Заявка не исполнена после {max_wait_seconds}с',
            'attempts': attempts,
            'wait_time': time.time() - start_time
        }

    # ========== 3. ОТСЛЕЖИВАНИЕ СТАТУСА ==========

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Получение актуального статуса заявки

        Args:
            order_id: ID заявки

        Returns:
            Dict с информацией о заявке или None
        """
        # Проверяем кэш
        if order_id in self._order_cache:
            cache_entry = self._order_cache[order_id]
            if time.time() - cache_entry.get('last_check', 0) < self._cache_ttl:
                return cache_entry.copy()

        try:
            with Client(self.token) as client:
                order_state = client.orders.get_order_state(
                    account_id=self.account_id,
                    order_id=order_id
                )

                if order_state:
                    result = {
                        'order_id': order_state.order_id,
                        'status': str(order_state.order_state),
                        'executed_lots': order_state.executed_lots,
                        'requested_lots': order_state.lots_requested,
                        'price': float(quotation_to_decimal(order_state.price)) if order_state.price else 0,
                        'direction': "BUY" if order_state.direction == 1 else "SELL",
                        'created_at': getattr(order_state, 'created_at', None)
                    }

                    # Обновляем кэш
                    self._order_cache[order_id] = result.copy()
                    self._order_cache[order_id]['last_check'] = time.time()

                    return result

        except Exception as e:
            debug(f"Ошибка получения статуса заявки {order_id}: {e}")

        return None

    def wait_for_completion(
            self,
            order_id: str,
            max_wait_seconds: int = 30,
            check_interval: float = 1.0
    ) -> Dict[str, Any]:
        """
        Ожидание полного исполнения заявки
        """
        start_time = time.time()
        last_status = None

        while time.time() - start_time < max_wait_seconds:
            status = self.get_order_status(order_id)

            if not status:
                time.sleep(check_interval)
                continue

            executed = status.get('executed_lots', 0)
            requested = status.get('requested_lots', 0)
            status_str = status.get('status', '')

            # ✅ Если заявка полностью исполнена
            if executed >= requested:
                return {
                    'success': True,
                    'executed': True,
                    'executed_lots': executed,
                    'requested_lots': requested,
                    'price': status.get('price', 0),
                    'wait_time': time.time() - start_time,
                    'status': status_str
                }

            # ✅ Если заявка отменена или отклонена
            if 'CANCELLED' in status_str or 'REJECTED' in status_str or 'CANCELED' in status_str:
                warning(f"⚠️ Заявка {order_id[:8]} {status_str}")
                return {
                    'success': False,
                    'executed': False,
                    'reason': f'Заявка {status_str}',
                    'executed_lots': executed,
                    'requested_lots': requested,
                    'wait_time': time.time() - start_time,
                    'status': status_str
                }

            # Логируем изменение статуса
            if status_str != last_status:
                info(f"⏳ Заявка {order_id[:8]}: {status_str} ({executed}/{requested})")
                last_status = status_str

            time.sleep(check_interval)

        return {
            'success': False,
            'executed': False,
            'reason': f'Таймаут ожидания {max_wait_seconds}с',
            'wait_time': time.time() - start_time
        }

    # ========== 4. ПОЛУЧЕНИЕ СПИСКА АКТИВНЫХ ЗАЯВОК ==========

    def get_active_orders(self) -> List[Dict[str, Any]]:
        """
        Получение списка активных заявок через GetOrders

        Returns:
            List[Dict] Список активных заявок
        """
        try:
            with Client(self.token) as client:
                orders = client.orders.get_orders(account_id=self.account_id)
                result = []

                for order in orders.orders:
                    result.append({
                        'order_id': order.order_id,
                        'figi': order.figi,
                        'direction': "BUY" if order.direction == 1 else "SELL",
                        'price': float(quotation_to_decimal(order.price)) if order.price else 0,
                        'quantity': order.lots_requested,
                        'executed_quantity': order.executed_lots,
                        'status': str(order.order_state),
                        'ticker': self._figi_resolver.get_ticker_by_figi(order.figi) or order.figi[:8]
                    })

                info(f"📋 Получено активных заявок: {len(result)}")
                return result

        except Exception as e:
            warning(f"Ошибка получения активных заявок: {e}")
            return []

    # ========== 5. ОТМЕНА ЗАЯВКИ С ПОДТВЕРЖДЕНИЕМ ==========

    def cancel_order_with_confirmation(
            self,
            order_id: str,
            max_wait_seconds: int = 5
    ) -> Dict[str, Any]:
        """
        Отмена заявки с подтверждением

        Args:
            order_id: ID заявки
            max_wait_seconds: Максимальное время ожидания подтверждения

        Returns:
            Dict с результатом
        """
        info(f"🔄 Отмена заявки {order_id[:8]}...")

        try:
            with Client(self.token) as client:
                client.orders.cancel_order(
                    account_id=self.account_id,
                    order_id=order_id
                )
                info(f"✅ Заявка {order_id[:8]} отменена")

        except Exception as e:
            error_msg = str(e)
            if "30070" in error_msg:
                info(f"ℹ️ Заявка {order_id[:8]} уже не активна")
            else:
                warning(f"⚠️ Ошибка при отмене: {e}")
                return {'success': False, 'error': error_msg}

        # Подтверждаем отмену
        start_time = time.time()
        while time.time() - start_time < max_wait_seconds:
            status = self.get_order_status(order_id)
            if not status:
                # Заявка не найдена - отменена
                return {
                    'success': True,
                    'cancelled': True,
                    'wait_time': time.time() - start_time
                }

            status_str = status.get('status', '')
            if 'CANCELLED' in status_str:
                return {
                    'success': True,
                    'cancelled': True,
                    'wait_time': time.time() - start_time
                }

            time.sleep(0.5)

        return {
            'success': True,
            'cancelled': True,
            'confirmed': False,
            'wait_time': time.time() - start_time
        }

    # ========== 6. ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def _get_min_price_increment(self, client, figi: str) -> float:
        """Получение минимального шага цены"""
        try:
            instrument = client.instruments.get_instrument_by(id=figi, id_type=1)
            if instrument and instrument.instrument:
                step = instrument.instrument.min_price_increment
                return float(step) if step else 0.01
        except Exception:
            pass
        return 0.01

    def _get_current_price(self, client, figi: str) -> float:
        """Получение текущей цены"""
        try:
            last_prices = client.market_data.get_last_prices(figi=[figi])
            if last_prices and last_prices.last_prices:
                return float(quotation_to_decimal(last_prices.last_prices[0].price))
        except Exception:
            pass
        return 0.0

    def _get_available_funds(self, client) -> Tuple[float, float, float]:
        """Получение доступных средств"""
        try:
            margin = client.users.get_margin_attributes(account_id=self.account_id)
            if margin:
                total = float(quotation_to_decimal(margin.liquid_portfolio))
                starting = float(quotation_to_decimal(margin.starting_margin))
                free = total - starting
                return max(0.0, free), total, 0.0
        except Exception:
            pass

        try:
            portfolio = client.operations.get_portfolio(account_id=self.account_id)
            total = float(quotation_to_decimal(portfolio.total_amount_portfolio))
            return total, total, 0.0
        except Exception:
            pass

        return 0.0, 0.0, 0.0


# ========== ПРИМЕР ИСПОЛЬЗОВАНИЯ ==========

def example_usage():
    """
    Пример использования OrderValidator
    """
    import os
    from dotenv import load_dotenv

    load_dotenv()
    token = os.getenv("TBANK_TOKEN")

    if not token:
        print("❌ Токен не найден")
        return

    # Получаем account_id
    with Client(token) as client:
        accounts = client.users.get_accounts().accounts
        if not accounts:
            print("❌ Нет доступных счетов")
            return
        account_id = accounts[0].id

    # Создаём валидатор
    validator = OrderValidator(token, account_id)

    # Пример 1: Валидация перед отправкой
    print("\n" + "=" * 50)
    print("ПРИМЕР 1: ВАЛИДАЦИЯ ЗАЯВКИ")
    print("=" * 50)

    is_valid, reason, info = validator.validate_before_send(
        figi="BBG000B9XRY4",  # SBER
        quantity=10,
        direction="BUY",
        price=250.0
    )

    print(f"Валидация: {'✅ ПРОЙДЕНА' if is_valid else '❌ НЕ ПРОЙДЕНА'}")
    print(f"Причина: {reason}")
    print(f"Доп. информация: {info}")

    # Пример 2: Отправка заявки с подтверждением
    print("\n" + "=" * 50)
    print("ПРИМЕР 2: ОТПРАВКА ЗАЯВКИ С ПОДТВЕРЖДЕНИЕМ")
    print("=" * 50)

    # Для реальной отправки раскомментировать:
    """
    result = validator.send_order_with_confirmation(
        figi="BBG000B9XRY4",  # SBER
        quantity=1,
        direction="BUY",
        order_type="MARKET",
        is_short=False,
        max_wait_seconds=10
    )

    print(f"Результат отправки: {'✅ УСПЕХ' if result.get('success') else '❌ НЕУДАЧА'}")
    print(f"Статус: {result.get('status', 'N/A')}")
    print(f"Исполнено: {result.get('executed_lots', 0)}/{result.get('requested_lots', 0)}")
    print(f"Order ID: {result.get('order_id', 'N/A')}")
    """

    # Пример 3: Получение активных заявок
    print("\n" + "=" * 50)
    print("ПРИМЕР 3: АКТИВНЫЕ ЗАЯВКИ")
    print("=" * 50)

    active_orders = validator.get_active_orders()
    print(f"Активных заявок: {len(active_orders)}")
    for order in active_orders[:3]:
        print(
            f"  - {order.get('ticker')}: {order.get('direction')} {order.get('quantity')} шт, статус={order.get('status')}")


if __name__ == "__main__":
    example_usage()