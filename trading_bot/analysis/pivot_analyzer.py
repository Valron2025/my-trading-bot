#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pivot_analyzer.py - АНАЛИЗ PIVOT POINTS И УРОВНЕЙ
Реализация из Pine Script S-GR-Combo
"""

import numpy as np
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from datetime import datetime

from trading_bot.logger import info, debug


@dataclass
class PivotPoint:
    """Точка разворота"""
    price: float
    bar_index: int
    is_high: bool  # True = pivot high, False = pivot low
    strength: int  # Количество подтверждающих баров


@dataclass
class PivotLevels:
    """Уровни пивотов"""
    pivot: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


class PivotAnalyzer:
    """
    Анализатор Pivot Points и уровней поддержки/сопротивления
    Полная реализация из Pine Script
    """

    def __init__(self, left_bars: int = 5, right_bars: int = 5):
        self.left_bars = left_bars
        self.right_bars = right_bars
        self._pivots_high: List[PivotPoint] = []
        self._pivots_low: List[PivotPoint] = []

    # ========================================================================
    # 1. ПОИСК PIVOT POINTS
    # ========================================================================

    def find_pivot_highs(
            self,
            high: List[float],
            left_bars: int = None,
            right_bars: int = None
    ) -> List[PivotPoint]:
        """
        Поиск Pivot Highs (вершин)

        Args:
            high: Список цен High
            left_bars: Количество баров слева для подтверждения
            right_bars: Количество баров справа для подтверждения

        Returns:
            Список PivotPoint
        """
        left = left_bars or self.left_bars
        right = right_bars or self.right_bars

        pivots = []

        for i in range(left, len(high) - right):
            is_pivot = True

            # Проверка слева
            for j in range(1, left + 1):
                if high[i] <= high[i - j]:
                    is_pivot = False
                    break

            # Проверка справа
            if is_pivot:
                for j in range(1, right + 1):
                    if high[i] <= high[i + j]:
                        is_pivot = False
                        break

            if is_pivot:
                pivots.append(PivotPoint(
                    price=high[i],
                    bar_index=i,
                    is_high=True,
                    strength=left + right
                ))

        self._pivots_high = pivots
        return pivots

    def find_pivot_lows(
            self,
            low: List[float],
            left_bars: int = None,
            right_bars: int = None
    ) -> List[PivotPoint]:
        """
        Поиск Pivot Lows (впадин)

        Args:
            low: Список цен Low
            left_bars: Количество баров слева для подтверждения
            right_bars: Количество баров справа для подтверждения

        Returns:
            Список PivotPoint
        """
        left = left_bars or self.left_bars
        right = right_bars or self.right_bars

        pivots = []

        for i in range(left, len(low) - right):
            is_pivot = True

            # Проверка слева
            for j in range(1, left + 1):
                if low[i] >= low[i - j]:
                    is_pivot = False
                    break

            # Проверка справа
            if is_pivot:
                for j in range(1, right + 1):
                    if low[i] >= low[i + j]:
                        is_pivot = False
                        break

            if is_pivot:
                pivots.append(PivotPoint(
                    price=low[i],
                    bar_index=i,
                    is_high=False,
                    strength=left + right
                ))

        self._pivots_low = pivots
        return pivots

    # ========================================================================
    # 2. РАСЧЁТ УРОВНЕЙ PIVOT (Traditional, Fibonacci, Woodie, Camarilla)
    # ========================================================================

    @staticmethod
    def calculate_pivot_levels(
            high: float,
            low: float,
            close: float,
            open_price: float = None,
            method: str = "traditional"
    ) -> PivotLevels:
        """
        Расчёт уровней Pivot Points

        Args:
            high: Максимум предыдущего периода
            low: Минимум предыдущего периода
            close: Закрытие предыдущего периода
            open_price: Открытие (для Woodie)
            method: Метод расчёта (traditional, fibonacci, woodie, classic, camarilla)

        Returns:
            PivotLevels с уровнями
        """
        if method == "traditional":
            pivot = (high + low + close) / 3
            r1 = 2 * pivot - low
            r2 = pivot + (high - low)
            r3 = high + 2 * (pivot - low)
            s1 = 2 * pivot - high
            s2 = pivot - (high - low)
            s3 = low - 2 * (high - pivot)

        elif method == "fibonacci":
            pivot = (high + low + close) / 3
            r1 = pivot + 0.382 * (high - low)
            r2 = pivot + 0.618 * (high - low)
            r3 = pivot + 1.000 * (high - low)
            s1 = pivot - 0.382 * (high - low)
            s2 = pivot - 0.618 * (high - low)
            s3 = pivot - 1.000 * (high - low)

        elif method == "woodie":
            if open_price is None:
                open_price = (high + low + close) / 3
            pivot = (high + low + open_price) / 3
            r1 = 2 * pivot - low
            r2 = pivot + (high - low)
            r3 = r1 + (high - low)
            s1 = 2 * pivot - high
            s2 = pivot - (high - low)
            s3 = s1 - (high - low)

        elif method == "classic":
            pivot = (high + low + close) / 3
            r1 = 2 * pivot - low
            r2 = pivot + (high - low)
            r3 = high + 2 * (pivot - low)
            s1 = 2 * pivot - high
            s2 = pivot - (high - low)
            s3 = low - 2 * (high - pivot)

        elif method == "camarilla":
            pivot = (high + low + close) / 3
            r1 = close + (high - low) * 1.1 / 12
            r2 = close + (high - low) * 1.1 / 6
            r3 = close + (high - low) * 1.1 / 4
            s1 = close - (high - low) * 1.1 / 12
            s2 = close - (high - low) * 1.1 / 6
            s3 = close - (high - low) * 1.1 / 4

        else:
            # По умолчанию traditional
            pivot = (high + low + close) / 3
            r1 = 2 * pivot - low
            r2 = pivot + (high - low)
            r3 = high + 2 * (pivot - low)
            s1 = 2 * pivot - high
            s2 = pivot - (high - low)
            s3 = low - 2 * (high - pivot)

        return PivotLevels(
            pivot=round(pivot, 4),
            r1=round(r1, 4),
            r2=round(r2, 4),
            r3=round(r3, 4),
            s1=round(s1, 4),
            s2=round(s2, 4),
            s3=round(s3, 4)
        )

    # ========================================================================
    # 3. ПОИСК БЛИЖАЙШИХ УРОВНЕЙ
    # ========================================================================

    @staticmethod
    def find_nearest_levels(
            current_price: float,
            levels: PivotLevels
    ) -> Dict[str, float]:
        """
        Поиск ближайших уровней поддержки и сопротивления

        Args:
            current_price: Текущая цена
            levels: Рассчитанные уровни

        Returns:
            Dict с ближайшими поддержкой и сопротивлением
        """
        all_levels = [
            ('r3', levels.r3), ('r2', levels.r2), ('r1', levels.r1),
            ('pivot', levels.pivot),
            ('s1', levels.s1), ('s2', levels.s2), ('s3', levels.s3)
        ]

        support = None
        resistance = None

        for name, level in all_levels:
            if level < current_price:
                if support is None or level > support:
                    support = level
            elif level > current_price:
                if resistance is None or level < resistance:
                    resistance = level

        return {
            'support': support if support else current_price * 0.98,
            'resistance': resistance if resistance else current_price * 1.02,
            'distance_to_support': ((current_price - support) / current_price * 100) if support else 0,
            'distance_to_resistance': ((resistance - current_price) / current_price * 100) if resistance else 0
        }

    # ========================================================================
    # 4. АНАЛИЗ ПРОБОЯ УРОВНЕЙ
    # ========================================================================

    @staticmethod
    def analyze_breakout(
            current_price: float,
            previous_price: float,
            level: float,
            direction: str  # "above" или "below"
    ) -> Dict[str, Any]:
        """
        Анализ пробоя уровня

        Args:
            current_price: Текущая цена
            previous_price: Предыдущая цена
            level: Уровень
            direction: Направление пробоя ("above" или "below")

        Returns:
            Dict с информацией о пробое
        """
        if direction == "above":
            broke = previous_price <= level < current_price
        else:
            broke = previous_price >= level > current_price

        if broke:
            strength = abs(current_price - level) / level * 100
            return {
                'broke': True,
                'strength': round(strength, 2),
                'direction': direction,
                'level': level,
                'current_price': current_price
            }

        return {'broke': False}

    # ========================================================================
    # 5. ИНТЕГРАЦИЯ С TP/SL
    # ========================================================================

    @staticmethod
    def get_tp_sl_from_pivots(
            entry_price: float,
            direction: str,  # "LONG" или "SHORT"
            levels: PivotLevels
    ) -> Dict[str, float]:
        """
        Получение уровней TP/SL на основе пивотов

        Args:
            entry_price: Цена входа
            direction: Направление
            levels: Уровни пивотов

        Returns:
            Dict с TP и SL
        """
        if direction == "LONG":
            return {
                'take_profit': levels.r1 if levels.r1 > entry_price else levels.pivot,
                'stop_loss': levels.s1 if levels.s1 < entry_price else levels.s2
            }
        else:
            return {
                'take_profit': levels.s1 if levels.s1 < entry_price else levels.pivot,
                'stop_loss': levels.r1 if levels.r1 > entry_price else levels.r2
            }


# Глобальный экземпляр
pivot_analyzer = PivotAnalyzer()