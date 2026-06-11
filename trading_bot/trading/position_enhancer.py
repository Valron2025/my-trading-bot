#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
position_enhancer.py - УСИЛЕНИЕ ПРИБЫЛЬНЫХ ПОЗИЦИЙ
Автоматическое добавление к позиции, когда она идёт в плюс
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from trading_bot.logger import info, success, warning, error, debug
from trading_bot.config import config
from trading_bot.models import OrderSide, Position


class EnhancementLevel(Enum):
    """Уровни усиления позиции"""
    NONE = "none"
    FIRST = "first"
    SECOND = "second"
    THIRD = "third"
    MAX = "max"


@dataclass
class EnhancementState:
    """Состояние усиления для позиции"""
    level: EnhancementLevel = EnhancementLevel.NONE
    initial_quantity: int = 0
    added_quantity: int = 0
    first_target_pct: float = 0.0
    second_target_pct: float = 0.0
    third_target_pct: float = 0.0
    last_enhancement_time: Optional[datetime] = None
    enhancement_prices: List[float] = field(default_factory=list)


class PositionEnhancer:
    """
    УСИЛИТЕЛЬ ПРИБЫЛЬНЫХ ПОЗИЦИЙ
    Логика: при прибыли +3%, +5%, +8% добавляем объём
    """

    def __init__(self, bot):
        self.bot = bot
        self._enhancements: Dict[str, EnhancementState] = {}
        self._last_check_time: Dict[str, datetime] = {}
        self._check_interval_seconds = 60
        self._hourly_enhancement_count = 0
        self._last_hour_reset = datetime.now()

        # Настройки усиления
        self.first_threshold_pct = 3.0
        self.second_threshold_pct = 5.0
        self.third_threshold_pct = 8.0

        self.first_add_pct = 0.50
        self.second_add_pct = 0.30
        self.third_add_pct = 0.20

        self.max_total_multiplier = 2.0
        self.min_quantity_for_enhance = 10
        self.max_enhancements_per_hour = 2

        info("🚀 PositionEnhancer инициализирован")
        info(
            f"   📊 +3% → +{self.first_add_pct * 100:.0f}% | +5% → +{self.second_add_pct * 100:.0f}% | +8% → +{self.third_add_pct * 100:.0f}%")

    def _get_enhancement_state(self, figi: str) -> EnhancementState:
        if figi not in self._enhancements:
            self._enhancements[figi] = EnhancementState()
        return self._enhancements[figi]

    def _reset_hourly_counter(self):
        now = datetime.now()
        if (now - self._last_hour_reset).total_seconds() >= 3600:
            self._hourly_enhancement_count = 0
            self._last_hour_reset = now

    def _can_enhance(self, figi: str, position: Position, current_price: float) -> Tuple[bool, str]:
        state = self._get_enhancement_state(figi)

        if state.level == EnhancementLevel.MAX:
            return False, "максимальный уровень"

        self._reset_hourly_counter()
        if self._hourly_enhancement_count >= self.max_enhancements_per_hour:
            return False, f"лимит в час ({self.max_enhancements_per_hour})"

        if state.last_enhancement_time:
            elapsed = (datetime.now() - state.last_enhancement_time).total_seconds()
            if elapsed < 30:
                return False, f"таймаут ({elapsed:.0f}с)"

        try:
            from trading_bot.api.tbank_client import tbank
            available, _, _ = tbank.get_available_funds()
            if available < 500:
                return False, f"мало средств ({available:.0f}₽)"
        except Exception:
            pass

        return True, "OK"

    def _calculate_additional_quantity(self, position: Position, profit_pct: float) -> Tuple[int, float, str]:
        state = self._get_enhancement_state(position.figi)

        if state.initial_quantity == 0:
            state.initial_quantity = position.quantity

        if state.level == EnhancementLevel.NONE and profit_pct >= self.first_threshold_pct:
            add_qty = int(state.initial_quantity * self.first_add_pct)
            level_desc = "ПЕРВОЕ УСИЛЕНИЕ"
            target = self.first_threshold_pct
        elif state.level == EnhancementLevel.FIRST and profit_pct >= self.second_threshold_pct:
            add_qty = int(state.initial_quantity * self.second_add_pct)
            level_desc = "ВТОРОЕ УСИЛЕНИЕ"
            target = self.second_threshold_pct
        elif state.level == EnhancementLevel.SECOND and profit_pct >= self.third_threshold_pct:
            add_qty = int(state.initial_quantity * self.third_add_pct)
            level_desc = "ТРЕТЬЕ УСИЛЕНИЕ"
            target = self.third_threshold_pct
        else:
            return 0, 0, "нет"

        if add_qty < self.min_quantity_for_enhance:
            return 0, 0, "нет"

        total_after = state.initial_quantity + state.added_quantity + add_qty
        max_allowed = int(state.initial_quantity * self.max_total_multiplier)

        if total_after > max_allowed:
            add_qty = max_allowed - (state.initial_quantity + state.added_quantity)
            if add_qty < self.min_quantity_for_enhance:
                return 0, 0, "максимум"

        return add_qty, target, level_desc

    async def enhance_position(self, position: Position, current_price: float, profit_pct: float) -> bool:
        from trading_bot.api.tbank_client import tbank

        ticker = position.ticker or position.figi[:8]

        can_enhance, reason = self._can_enhance(position.figi, position, current_price)
        if not can_enhance:
            debug(f"   ⏸️ [{ticker}] Усиление невозможно: {reason}")
            return False

        add_quantity, target_pct, level_desc = self._calculate_additional_quantity(position, profit_pct)
        if add_quantity <= 0:
            return False

        info(f"\n{'=' * 70}")
        info(f"🚀 {level_desc} ДЛЯ {ticker}!")
        info(f"{'=' * 70}")
        info(f"   📊 Прибыль: {profit_pct:.2f}% (цель: +{target_pct:.1f}%)")
        info(f"   📈 Текущая позиция: {position.quantity} шт")
        info(f"   ➕ Добавляем: {add_quantity} шт")
        info(f"   📊 Итоговый объём: {position.quantity + add_quantity} шт")

        total_cost = add_quantity * current_price
        info(f"   💰 Стоимость: {total_cost:.2f}₽")

        try:
            available, _, _ = tbank.get_available_funds()
            if total_cost > available * 0.8:
                warning(f"   ⚠️ Недостаточно средств: нужно {total_cost:.0f}₽, есть {available:.0f}₽")
                return False
            info(f"   ✅ Средств достаточно: {available:.0f}₽")
        except Exception as e:
            warning(f"   ⚠️ Ошибка проверки средств: {e}")

        try:
            if position.side == OrderSide.LONG:
                info(f"      🟢 LONG: покупка {add_quantity} шт {ticker} по ~{current_price:.2f}₽")
                success_flag = tbank.buy(position.figi, add_quantity)
            else:
                info(f"      🔴 SHORT: продажа {add_quantity} шт {ticker} по ~{current_price:.2f}₽")
                success_flag = tbank.sell(position.figi, add_quantity)

            if success_flag:
                state = self._get_enhancement_state(position.figi)
                if state.level == EnhancementLevel.NONE:
                    state.level = EnhancementLevel.FIRST
                elif state.level == EnhancementLevel.FIRST:
                    state.level = EnhancementLevel.SECOND
                elif state.level == EnhancementLevel.SECOND:
                    state.level = EnhancementLevel.THIRD
                else:
                    state.level = EnhancementLevel.MAX

                state.added_quantity += add_quantity
                state.last_enhancement_time = datetime.now()
                state.enhancement_prices.append(current_price)
                self._hourly_enhancement_count += 1

                old_qty = position.quantity
                position.quantity += add_quantity
                total_value = (position.avg_price * old_qty) + (current_price * add_quantity)
                position.avg_price = total_value / position.quantity

                success(f"\n✅ УСПЕШНОЕ УСИЛЕНИЕ {ticker}!")
                info(f"   📊 Новая позиция: {position.quantity} шт")
                info(f"   💰 Новая средняя: {position.avg_price:.2f}₽")
                info(f"   🎯 Уровень: {state.level.value}")
                info(f"{'=' * 70}\n")

                try:
                    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
                    telegram = get_telegram_notifier()
                    if telegram:
                        telegram.send_message(
                            f"🚀 **УСИЛЕНИЕ ПОЗИЦИИ!**\n\n"
                            f"📈 {ticker} ({position.side.value})\n"
                            f"➕ Добавлено: {add_quantity} шт\n"
                            f"💰 Цена: {current_price:.2f}₽\n"
                            f"📊 Новая позиция: {position.quantity} шт\n"
                            f"💵 Новая средняя: {position.avg_price:.2f}₽"
                        )
                except Exception:
                    pass

                return True
            else:
                error(f"\n❌ НЕ УДАЛОСЬ УСИЛИТЬ {ticker}!")
                return False

        except Exception as e:
            error(f"❌ Ошибка усиления {ticker}: {e}")
            return False

    async def check_and_enhance(self, position: Position, current_price: float, profit_pct: float) -> bool:
        ticker = position.ticker or position.figi[:8]

        last_check = self._last_check_time.get(position.figi)
        if last_check and (datetime.now() - last_check).total_seconds() < self._check_interval_seconds:
            return False

        self._last_check_time[position.figi] = datetime.now()
        state = self._get_enhancement_state(position.figi)

        if profit_pct > 1.0:
            debug(f"   📊 [{ticker}] Прибыль {profit_pct:.2f}%, уровень усиления: {state.level.value}")

        return await self.enhance_position(position, current_price, profit_pct)

    def get_stats(self) -> Dict[str, Any]:
        return {
            'active_enhancements': len([s for s in self._enhancements.values() if s.level != EnhancementLevel.NONE]),
            'hourly_count': self._hourly_enhancement_count,
            'first_threshold': self.first_threshold_pct,
            'second_threshold': self.second_threshold_pct,
            'third_threshold': self.third_threshold_pct,
        }


_position_enhancer = None


def get_position_enhancer(bot=None) -> PositionEnhancer:
    global _position_enhancer
    if _position_enhancer is None and bot is not None:
        _position_enhancer = PositionEnhancer(bot)
    return _position_enhancer
