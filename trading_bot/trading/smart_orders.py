#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Smart Orders - Умные заявки
- Iceberg: скрытие крупных заявок
- TWAP: растягивание заявки во времени
- Slippage protection: защита от проскальзывания
"""

import asyncio
import time
import uuid
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque

from trading_bot.logger import info, success, error, warning, debug
from trading_bot.api.tbank_client import tbank


@dataclass
class SmartOrder:
    """Умная заявка"""
    order_id: str
    figi: str
    ticker: str
    direction: str  # BUY / SELL
    total_quantity: int
    executed_quantity: int = 0
    remaining_quantity: int = 0
    iceberg_size: int = 0  # Размер одной "айсберг" части
    twap_interval: float = 0  # Интервал между частями (секунды)
    limit_price: float = 0  # Лимитная цена
    slippage_tolerance: float = 0.005  # Допустимое проскальзывание (0.5%)
    status: str = "pending"  # pending, active, completed, cancelled
    created_at: datetime = field(default_factory=datetime.now)
    last_execution: Optional[datetime] = None
    executions: List[Dict] = field(default_factory=list)


class SmartOrderManager:
    """
    Управление умными заявками
    - Iceberg orders: скрытие реального размера
    - TWAP: распределение во времени
    """

    def __init__(self):
        self.orders: Dict[str, SmartOrder] = {}
        self.active_orders: Dict[str, SmartOrder] = {}
        self.is_running = False
        self._executor_task = None

        info("🧊 SmartOrderManager инициализирован")

    # ========================================================================
    # 1. ICEBERG ORDERS (АЙСБЕРГ)
    # ========================================================================

    async def place_iceberg_order(
            self,
            figi: str,
            ticker: str,
            direction: str,
            total_quantity: int,
            iceberg_size: int,
            limit_price: float,
            slippage_tolerance: float = 0.005
    ) -> Optional[str]:
        """
        Размещение айсберг-заявки

        Args:
            figi: FIGI инструмента
            ticker: Тикер
            direction: BUY / SELL
            total_quantity: Общее количество
            iceberg_size: Размер одной части (видимая часть)
            limit_price: Лимитная цена
            slippage_tolerance: Допустимое проскальзывание
        """
        if iceberg_size <= 0 or iceberg_size > total_quantity:
            iceberg_size = total_quantity

        order_id = str(uuid.uuid4())[:8]

        order = SmartOrder(
            order_id=order_id,
            figi=figi,
            ticker=ticker,
            direction=direction,
            total_quantity=total_quantity,
            remaining_quantity=total_quantity,
            iceberg_size=iceberg_size,
            limit_price=limit_price,
            slippage_tolerance=slippage_tolerance
        )

        self.orders[order_id] = order
        self.active_orders[order_id] = order

        info(f"🧊 АЙСБЕРГ ЗАЯВКА {order_id}: {direction} {ticker}")
        info(f"   📊 Всего: {total_quantity} шт, Видимая часть: {iceberg_size} шт")
        info(f"   💰 Цена: {limit_price:.2f}₽")

        # Размещаем первую часть
        success_ = await self._send_part(order_id, iceberg_size)

        if success_:
            order.status = "active"
            return order_id
        else:
            del self.orders[order_id]
            del self.active_orders[order_id]
            return None

    async def _send_part(self, order_id: str, quantity: int) -> bool:
        """Отправка одной части айсберг-заявки"""
        order = self.orders.get(order_id)
        if not order:
            return False

        try:
            # ✅ place_limit_order - синхронный метод, не нужно await
            if order.direction == "BUY":
                result = tbank.place_limit_order(
                    order.figi, quantity, "BUY", order.limit_price
                )
            else:
                result = tbank.place_limit_order(
                    order.figi, quantity, "SELL", order.limit_price
                )

            if result:
                order.executed_quantity += quantity
                order.remaining_quantity -= quantity
                order.last_execution = datetime.now()
                order.executions.append({
                    'time': order.last_execution,
                    'quantity': quantity,
                    'price': order.limit_price
                })

                info(
                    f"   ✅ Часть {quantity} шт отправлена (всего исполнено {order.executed_quantity}/{order.total_quantity})")

                if order.remaining_quantity <= 0:
                    order.status = "completed"
                    if order_id in self.active_orders:
                        del self.active_orders[order_id]
                    success(f"🧊 Айсберг {order_id} полностью исполнен!")

                return True

            return False

        except Exception as e:
            error(f"❌ Ошибка отправки части {order_id}: {e}")
            return False

    # ========================================================================
    # 2. TWAP ORDERS
    # ========================================================================

    async def place_twap_order(
            self,
            figi: str,
            ticker: str,
            direction: str,
            total_quantity: int,
            duration_minutes: int,
            limit_price: float,
            slippage_tolerance: float = 0.005
    ) -> Optional[str]:
        """
        Размещение TWAP-заявки (растянутой во времени)

        Args:
            figi: FIGI инструмента
            ticker: Тикер
            direction: BUY / SELL
            total_quantity: Общее количество
            duration_minutes: Длительность растягивания
            limit_price: Лимитная цена
        """
        if duration_minutes <= 0:
            duration_minutes = 1

        order_id = str(uuid.uuid4())[:8]

        # Рассчитываем размер частей
        parts = max(1, duration_minutes)
        part_size = max(1, total_quantity // parts)
        interval_seconds = (duration_minutes * 60) / parts

        order = SmartOrder(
            order_id=order_id,
            figi=figi,
            ticker=ticker,
            direction=direction,
            total_quantity=total_quantity,
            remaining_quantity=total_quantity,
            iceberg_size=part_size,
            twap_interval=interval_seconds,
            limit_price=limit_price,
            slippage_tolerance=slippage_tolerance
        )

        self.orders[order_id] = order
        self.active_orders[order_id] = order

        info(f"⏰ TWAP ЗАЯВКА {order_id}: {direction} {ticker}")
        info(f"   📊 Всего: {total_quantity} шт, Частей: {parts} по {part_size} шт")
        info(f"   ⏱ Интервал: {interval_seconds:.1f} сек, Длительность: {duration_minutes} мин")

        # Запускаем асинхронную отправку
        asyncio.create_task(self._execute_twap(order_id))

        return order_id

    async def _execute_twap(self, order_id: str):
        """Исполнение TWAP заявки"""
        order = self.orders.get(order_id)
        if not order:
            return

        order.status = "active"

        while order.remaining_quantity > 0:
            # Отправляем часть
            part = min(order.iceberg_size, order.remaining_quantity)
            success_ = await self._send_part(order_id, part)

            if not success_:
                warning(f"⚠️ TWAP {order_id}: ошибка отправки части")

            # Ждём следующий интервал
            if order.remaining_quantity > 0:
                await asyncio.sleep(order.twap_interval)

        order.status = "completed"
        if order_id in self.active_orders:
            del self.active_orders[order_id]
        success(f"⏰ TWAP {order_id} полностью исполнен!")

    # ========================================================================
    # 3. УПРАВЛЕНИЕ ЗАЯВКАМИ
    # ========================================================================

    def cancel_order(self, order_id: str) -> bool:
        """Отмена умной заявки"""
        if order_id in self.active_orders:
            del self.active_orders[order_id]
            info(f"🛑 Отменена умная заявка {order_id}")
            return True
        return False

    def get_status(self, order_id: str) -> Optional[Dict]:
        """Получение статуса заявки"""
        order = self.orders.get(order_id)
        if not order:
            return None

        return {
            'order_id': order.order_id,
            'ticker': order.ticker,
            'direction': order.direction,
            'total': order.total_quantity,
            'executed': order.executed_quantity,
            'remaining': order.remaining_quantity,
            'progress': order.executed_quantity / order.total_quantity * 100,
            'status': order.status,
            'executions': len(order.executions)
        }

    def get_active_orders(self) -> List[Dict]:
        """Список активных заявок"""
        return [self.get_status(oid) for oid in self.active_orders.keys()]


# Создаём глобальный экземпляр для использования в других модулях
try:
    smart_orders_manager = SmartOrderManager()
    info("🧊 SmartOrderManager глобальный экземпляр создан")
except Exception as e:
    warning(f"⚠️ Ошибка создания SmartOrderManager: {e}")
    smart_orders_manager = None

# Для обратной совместимости
smart_orders = smart_orders_manager
