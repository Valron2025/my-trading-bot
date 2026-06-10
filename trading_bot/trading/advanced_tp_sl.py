#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
advanced_tp_sl.py - ПРОДВИНУТЫЕ УРОВНИ TP/SL
Реализация многоуровневых TP/SL из Pine Script
"""

import threading
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from trading_bot.logger import info, success, error, warning, debug
from trading_bot.analysis.advanced_indicators import advanced_indicators
from trading_bot.analysis.pivot_analyzer import pivot_analyzer, PivotLevels
from trading_bot.api.tbank_client import tbank


class TPLevel(Enum):
    """Уровни тейк-профита"""
    TP1 = "TP1"
    TP2 = "TP2"
    TP3 = "TP3"


@dataclass
class ActivePosition:
    """Активная позиция с уровнями TP/SL"""
    position_id: str
    ticker: str
    figi: str
    avg_price: float
    entry_time: datetime
    direction: str  # LONG или SHORT
    quantity: int

    # Уровни TP/SL
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    sl: float = 0.0

    # Статус уровней
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    sl_hit: bool = False

    # ATR при входе
    entry_atr: float = 0.0

    # Дополнительно
    pivot_levels: Optional[PivotLevels] = None


class AdvancedTPSLManager:
    """
    Продвинутый менеджер TP/SL
    Поддерживает:
    - Многоуровневый тейк-профит (TP1, TP2, TP3)
    - Динамический трейлинг-стоп после достижения TP1
    - ATR-based уровни
    - Pivot-based уровни
    """

    def __init__(self, bot=None):
        self.bot = bot
        self._active_positions: Dict[str, ActivePosition] = {}
        self._lock = threading.RLock()
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None

        # Настройки по умолчанию
        self.atr_period = 14
        self.tp1_mult = 2.0
        self.tp2_mult = 3.5
        self.tp3_mult = 5.0
        self.sl_mult = 2.0
        self.trailing_after_tp1 = True
        self.trailing_step = 0.5  # Шаг трейлинга после TP1 (%)

        info("📊 AdvancedTPSLManager инициализирован")
        info(f"   TP1: {self.tp1_mult}xATR, TP2: {self.tp2_mult}xATR, TP3: {self.tp3_mult}xATR")
        info(f"   SL: {self.sl_mult}xATR, Трейлинг после TP1: {'✅' if self.trailing_after_tp1 else '❌'}")

    def start_manager(self):
        """Запуск мониторинга уровней"""
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        info("🔄 AdvancedTPSLManager: мониторинг запущен")

    def start(self):
        """Алиас для start_manager (для совместимости с bot.py)"""
        info("🔄 Вызван start() -> start_manager()")
        return self.start_manager()

    def stop_manager(self):
        """Остановка мониторинга"""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        info("🛑 AdvancedTPSLManager: мониторинг остановлен")

    def _monitor_loop(self):
        """Цикл мониторинга уровней TP/SL"""
        while self._running:
            try:
                self._check_all_positions()
                import time
                time.sleep(1)  # Проверка каждую секунду
            except Exception as e:
                if self._running:
                    warning(f"Ошибка в мониторинге TP/SL: {e}")

    def _check_all_positions(self):
        """Проверка всех активных позиций"""
        with self._lock:
            for position in list(self._active_positions.values()):
                self._check_single_position(position)

    def _get_current_price(self, figi: str) -> Optional[float]:
        """Получение текущей цены по FIGI"""
        return tbank.get_current_price(figi)

    def _check_single_position(self, position: ActivePosition):
        """Проверка одной позиции на TP/SL"""
        current_price = self._get_current_price(position.figi)
        if not current_price:
            return

        # Проверяем трейлинг стоп
        if self._check_trailing_stop(position, current_price):
            return

        # Проверяем TP уровни
        if self._check_take_profit_levels(position, current_price):
            return

        # Проверяем SL
        self._check_stop_loss(position, current_price)

    def _check_trailing_stop(self, position: ActivePosition, current_price: float) -> bool:
        """
        Проверка и обновление трейлинг-стопа
        Возвращает True, если позиция закрыта по трейлингу
        """
        if not self.trailing_after_tp1 or not position.tp1_hit or position.tp2_hit:
            return False

        new_sl = self._calculate_trailing_stop(position, current_price)
        if new_sl and new_sl != position.sl:
            position.sl = new_sl
            info(f"📈 {position.ticker}: Трейлинг-стоп обновлён до {new_sl:.2f}₽")

            # Проверяем, не сработал ли новый SL
            if position.direction == "LONG" and current_price <= position.sl:
                position.sl_hit = True
                warning(f"🔴 {position.ticker}: Трейлинг-стоп сработал на {position.sl:.2f}₽")
                self._close_position(position, current_price)
                return True
            elif position.direction == "SHORT" and current_price >= position.sl:
                position.sl_hit = True
                warning(f"🔴 {position.ticker}: Трейлинг-стоп сработал на {position.sl:.2f}₽")
                self._close_position(position, current_price)
                return True

        return False

    def _check_take_profit_levels(self, position: ActivePosition, current_price: float) -> bool:
        """
        Проверка TP уровней
        Возвращает True, если позиция закрыта по TP
        """
        # Проверка TP1
        if not position.tp1_hit and not position.sl_hit:
            if position.direction == "LONG" and current_price >= position.tp1:
                position.tp1_hit = True
                success(f"🎯 {position.ticker}: TP1 достигнут на {position.tp1:.2f}₽")
                self._send_notification(position, "TP1")
            elif position.direction == "SHORT" and current_price <= position.tp1:
                position.tp1_hit = True
                success(f"🎯 {position.ticker}: TP1 достигнут на {position.tp1:.2f}₽")
                self._send_notification(position, "TP1")

        # Проверка TP2 (только после TP1)
        if position.tp1_hit and not position.tp2_hit and not position.sl_hit:
            if position.direction == "LONG" and current_price >= position.tp2:
                position.tp2_hit = True
                success(f"🎯 {position.ticker}: TP2 достигнут на {position.tp2:.2f}₽")
                self._send_notification(position, "TP2")
            elif position.direction == "SHORT" and current_price <= position.tp2:
                position.tp2_hit = True
                success(f"🎯 {position.ticker}: TP2 достигнут на {position.tp2:.2f}₽")
                self._send_notification(position, "TP2")

        # Проверка TP3 (только после TP2)
        if position.tp2_hit and not position.tp3_hit and not position.sl_hit:
            if position.direction == "LONG" and current_price >= position.tp3:
                position.tp3_hit = True
                success(f"🎯 {position.ticker}: TP3 достигнут на {position.tp3:.2f}₽")
                self._send_notification(position, "TP3")
            elif position.direction == "SHORT" and current_price <= position.tp3:
                position.tp3_hit = True
                success(f"🎯 {position.ticker}: TP3 достигнут на {position.tp3:.2f}₽")
                self._send_notification(position, "TP3")

        # Если все TP достигнуты, закрываем позицию
        if position.tp3_hit:
            self._close_position(position, current_price)
            return True

        return False

    def _check_stop_loss(self, position: ActivePosition, current_price: float) -> bool:
        """
        Проверка SL уровня
        Возвращает True, если позиция закрыта по SL
        """
        if position.sl_hit:
            return True

        if position.direction == "LONG" and current_price <= position.sl:
            position.sl_hit = True
            warning(f"🔴 {position.ticker}: STOP-LOSS сработал на {position.sl:.2f}₽")
            self._close_position(position, current_price)
            return True
        elif position.direction == "SHORT" and current_price >= position.sl:
            position.sl_hit = True
            warning(f"🔴 {position.ticker}: STOP-LOSS сработал на {position.sl:.2f}₽")
            self._close_position(position, current_price)
            return True

        return False

    def _calculate_trailing_stop(self, position: ActivePosition, current_price: float) -> Optional[float]:
        """Расчёт трейлинг-стопа"""
        if position.direction == "LONG":
            # Для LONG: стоп поднимается вслед за ценой
            new_sl = current_price * (1 - self.trailing_step / 100)
            return max(new_sl, position.sl) if new_sl > position.sl else None
        else:
            # Для SHORT: стоп опускается вслед за ценой
            new_sl = current_price * (1 + self.trailing_step / 100)
            return min(new_sl, position.sl) if new_sl < position.sl else None

    def _send_notification(self, position: ActivePosition, level: str):
        """Отправка уведомления о достижении уровня"""
        try:
            from trading_bot.telegram.telegram_notifier import get_telegram_notifier
            telegram = get_telegram_notifier()
            if telegram:
                price = getattr(position, level.lower())
                profit_pct = ((price - position.avg_price) / position.avg_price * 100)
                if position.direction == "SHORT":
                    profit_pct = -profit_pct

                telegram.send_message(
                    f"🎯 {level} {position.ticker}\n"
                    f"Цена: {price:.2f}₽\n"
                    f"Вход: {position.avg_price:.2f}₽\n"
                    f"Прибыль: {profit_pct:+.2f}%"
                )
        except Exception:
            pass

    def _close_position(self, position: ActivePosition, current_price: float):
        """Закрытие позиции"""
        reason = None
        if position.sl_hit:
            reason = "стоп-лосс"
        elif position.tp3_hit:
            reason = "тейк-профит TP3"
        elif position.tp2_hit:
            reason = "тейк-профит TP2"
        elif position.tp1_hit:
            reason = "тейк-профит TP1"
        else:
            reason = "закрытие"

        info(f"🔒 Закрытие {position.ticker}: {reason} по {current_price:.2f}₽")

        # Закрываем позицию через брокера
        try:
            if position.direction == "LONG":
                success_flag = tbank.sell(position.figi, position.quantity)
            else:
                success_flag = tbank.buy(position.figi, position.quantity)

            if success_flag:
                # Удаляем из активных
                if position.position_id in self._active_positions:
                    del self._active_positions[position.position_id]
                success(f"✅ {position.ticker} закрыт по {reason}")

                # Записываем результат
                if hasattr(self.bot, 'position_sizer') and self.bot.position_sizer:
                    profit_pct = ((current_price - position.avg_price) / position.avg_price * 100)
                    profit_amount = (current_price - position.avg_price) * position.quantity
                    if position.direction == "SHORT":
                        profit_pct = -profit_pct
                        profit_amount = -profit_amount

                    self.bot.position_sizer.record_closed_trade(
                        ticker=position.ticker,
                        entry_price=position.avg_price,
                        exit_price=current_price,
                        quantity=position.quantity,
                        pnl=profit_amount,
                        pnl_pct=profit_pct,
                        entry_time=position.entry_time,
                        exit_time=datetime.now(),
                        holding_minutes=(datetime.now() - position.entry_time).total_seconds() / 60,
                        side=position.direction
                    )
        except Exception as e:
            error(f"❌ Ошибка закрытия {position.ticker}: {e}")

    # ========================================================================
    # ПУБЛИЧНЫЕ МЕТОДЫ
    # ========================================================================

    def add_position_with_atr_levels(
            self,
            ticker: str,
            figi: str,
            entry_price: float,
            quantity: int,
            direction: str,
            high: List[float],
            low: List[float],
            close: List[float],
            period: int = 14
    ) -> Optional[ActivePosition]:
        """
        Добавление позиции с ATR-based уровнями TP/SL

        Args:
            ticker: Тикер
            figi: FIGI
            entry_price: Цена входа
            quantity: Количество
            direction: "LONG" или "SHORT"
            high: История High
            low: История Low
            close: История Close
            period: Период ATR

        Returns:
            ActivePosition или None
        """
        # Рассчитываем ATR
        atr = advanced_indicators._calculate_atr_simple(close, period)

        if atr == 0:
            warning(f"⚠️ {ticker}: ATR = 0, используем стандартные уровни")
            return self.add_position_with_fixed_levels(
                ticker, figi, entry_price, quantity, direction
            )

        # Рассчитываем уровни
        levels = advanced_indicators.calculate_atr_levels(
            entry_price=entry_price,
            atr=atr,
            direction=direction,
            tp1_mult=self.tp1_mult,
            tp2_mult=self.tp2_mult,
            tp3_mult=self.tp3_mult,
            sl_mult=self.sl_mult
        )

        position_id = f"{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        position = ActivePosition(
            position_id=position_id,
            ticker=ticker,
            figi=figi,
            avg_price=entry_price,  # ← ИСПРАВЛЕНО
            entry_time=datetime.now(),
            direction=direction,
            quantity=quantity,
            tp1=levels['tp1'],
            tp2=levels['tp2'],
            tp3=levels['tp3'],
            sl=levels['sl'],
            entry_atr=atr
        )

        with self._lock:
            self._active_positions[position_id] = position

        info(f"📊 {ticker}: Добавлена позиция с ATR-уровнями")
        info(f"   TP1: {levels['tp1']:.2f}₽ (+{levels['tp1_pct']:.2f}%)")
        info(f"   TP2: {levels['tp2']:.2f}₽ (+{levels['tp2_pct']:.2f}%)")
        info(f"   TP3: {levels['tp3']:.2f}₽ (+{levels['tp3_pct']:.2f}%)")
        info(f"   SL:  {levels['sl']:.2f}₽ ({levels['sl_pct']:.2f}%)")

        return position

    def add_position_with_pivot_levels(
            self,
            ticker: str,
            figi: str,
            entry_price: float,
            quantity: int,
            direction: str,
            high: float,
            low: float,
            close: float,
            method: str = "traditional"
    ) -> Optional[ActivePosition]:
        """
        Добавление позиции с Pivot-based уровнями TP/SL

        Args:
            ticker: Тикер
            figi: FIGI
            entry_price: Цена входа
            quantity: Количество
            direction: "LONG" или "SHORT"
            high: Максимум предыдущего периода
            low: Минимум предыдущего периода
            close: Закрытие предыдущего периода
            method: Метод расчёта пивотов

        Returns:
            ActivePosition или None
        """
        # Рассчитываем уровни пивотов
        levels = pivot_analyzer.calculate_pivot_levels(high, low, close, method=method)
        tp_sl = pivot_analyzer.get_tp_sl_from_pivots(entry_price, direction, levels)

        position_id = f"{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        position = ActivePosition(
            position_id=position_id,
            ticker=ticker,
            figi=figi,
            avg_price=entry_price,
            entry_time=datetime.now(),
            direction=direction,
            quantity=quantity,
            tp1=tp_sl['take_profit'],
            tp2=tp_sl['take_profit'] * 1.5 if direction == "LONG" else tp_sl['take_profit'] * 0.5,
            tp3=tp_sl['take_profit'] * 2.0 if direction == "LONG" else tp_sl['take_profit'] * 0.25,
            sl=tp_sl['stop_loss'],
            pivot_levels=levels
        )

        with self._lock:
            self._active_positions[position_id] = position

        info(f"📊 {ticker}: Добавлена позиция с Pivot-уровнями ({method})")
        info(f"   TP: {tp_sl['take_profit']:.2f}₽")
        info(f"   SL: {tp_sl['stop_loss']:.2f}₽")

        return position

    def add_position_with_fixed_levels(
            self,
            ticker: str,
            figi: str,
            entry_price: float,
            quantity: int,
            direction: str,
            tp_pct: float = 1.5,
            sl_pct: float = 1.0
    ) -> Optional[ActivePosition]:
        """
        Добавление позиции с фиксированными процентами TP/SL

        Args:
            ticker: Тикер
            figi: FIGI
            entry_price: Цена входа
            quantity: Количество
            direction: "LONG" или "SHORT"
            tp_pct: Процент тейк-профита
            sl_pct: Процент стоп-лосса

        Returns:
            ActivePosition или None
        """
        if direction == "LONG":
            tp1 = entry_price * (1 + tp_pct / 100)
            tp2 = entry_price * (1 + tp_pct * 1.5 / 100)
            tp3 = entry_price * (1 + tp_pct * 2.5 / 100)
            sl = entry_price * (1 - sl_pct / 100)
        else:
            tp1 = entry_price * (1 - tp_pct / 100)
            tp2 = entry_price * (1 - tp_pct * 1.5 / 100)
            tp3 = entry_price * (1 - tp_pct * 2.5 / 100)
            sl = entry_price * (1 + sl_pct / 100)

        position_id = f"{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        position = ActivePosition(
            position_id=position_id,
            ticker=ticker,
            figi=figi,
            avg_price=entry_price,
            entry_time=datetime.now(),
            direction=direction,
            quantity=quantity,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            sl=sl
        )

        with self._lock:
            self._active_positions[position_id] = position

        info(f"📊 {ticker}: Добавлена позиция с фиксированными уровнями")
        info(f"   TP1: {tp1:.2f}₽ (+{tp_pct:.1f}%)")
        info(f"   TP2: {tp2:.2f}₽ (+{tp_pct * 1.5:.1f}%)")
        info(f"   TP3: {tp3:.2f}₽ (+{tp_pct * 2.5:.1f}%)")
        info(f"   SL:  {sl:.2f}₽ (-{sl_pct:.1f}%)")

        return position

    def get_position_status(self, position_id: str) -> Optional[Dict]:
        """Получение статуса позиции"""
        pos = self._active_positions.get(position_id)
        if not pos:
            return None

        return {
            'position_id': pos.position_id,
            'ticker': pos.ticker,
            'avg_price': pos.avg_price,
            'entry_time': pos.entry_time.isoformat(),
            'direction': pos.direction,
            'quantity': pos.quantity,
            'tp1': pos.tp1,
            'tp2': pos.tp2,
            'tp3': pos.tp3,
            'sl': pos.sl,
            'tp1_hit': pos.tp1_hit,
            'tp2_hit': pos.tp2_hit,
            'tp3_hit': pos.tp3_hit,
            'sl_hit': pos.sl_hit,
            'entry_atr': pos.entry_atr
        }

    def get_all_positions(self) -> List[Dict]:
        """Получение всех активных позиций"""
        return [self.get_position_status(pid) for pid in self._active_positions]

    def remove_position(self, position_id: str) -> bool:
        """Принудительное удаление позиции"""
        with self._lock:
            if position_id in self._active_positions:
                del self._active_positions[position_id]
                return True
        return False

    def get_stats(self) -> Dict:
        """Статистика менеджера"""
        return {
            'active_positions': len(self._active_positions),
            'tp1_mult': self.tp1_mult,
            'tp2_mult': self.tp2_mult,
            'tp3_mult': self.tp3_mult,
            'sl_mult': self.sl_mult,
            'trailing_after_tp1': self.trailing_after_tp1,
            'trailing_step': self.trailing_step,
            'positions': [
                {
                    'ticker': p.ticker,
                    'direction': p.direction,
                    'tp1_hit': p.tp1_hit,
                    'tp2_hit': p.tp2_hit,
                    'tp3_hit': p.tp3_hit
                }
                for p in self._active_positions.values()
            ]
        }


# Глобальный экземпляр
advanced_tpsl_manager = AdvancedTPSLManager()