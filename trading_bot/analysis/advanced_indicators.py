#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
advanced_indicators.py - ПРОДВИНУТЫЕ ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ
Реализация индикаторов из Pine Script:
- SuperTrend
- Half Trend
- Ichimoku Cloud
- Donchian Channel
- DMI/ADX
- Parabolic SAR
- Stochastic
- CCI
- Range Filter
- VWAP
- ATR-based уровни
"""

import numpy as np
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from collections import deque

from trading_bot.logger import info, debug, warning


@dataclass
class SuperTrendResult:
    """Результат расчёта SuperTrend"""
    trend: int  # 1 = uptrend, -1 = downtrend
    upper_band: float
    lower_band: float
    super_trend: float


@dataclass
class IchimokuResult:
    """Результат расчёта Ichimoku Cloud"""
    conversion_line: float  # Tenkan-sen (9)
    base_line: float  # Kijun-sen (26)
    leading_span_a: float  # Senkou Span A (26 ahead)
    leading_span_b: float  # Senkou Span B (52)
    lagging_span: float  # Chikou Span (26 behind)
    cloud_green: bool  # A > B


@dataclass
class DMICResult:
    """Результат расчёта DMI/ADX"""
    adx: float
    plus_di: float
    minus_di: float
    trend: int  # 1 = +DI > -DI, -1 = -DI > +DI


class AdvancedIndicators:
    """
    Продвинутые технические индикаторы
    Полная реализация индикаторов из Pine Script
    """

    # ========================================================================
    # 1. SUPERTREND
    # ========================================================================

    @staticmethod
    def calculate_supertrend(
            high: List[float],
            low: List[float],
            close: List[float],
            period: int = 10,
            multiplier: float = 3.0
    ) -> SuperTrendResult:
        """
        Расчёт SuperTrend индикатора

        Args:
            high: Список цен High
            low: Список цен Low
            close: Список цен Close
            period: Период ATR (по умолчанию 10)
            multiplier: Множитель ATR (по умолчанию 3.0)

        Returns:
            SuperTrendResult с трендом и полосами
        """
        if len(close) < period + 1:
            return SuperTrendResult(trend=0, upper_band=close[-1] if close else 0,
                                    lower_band=close[-1] if close else 0,
                                    super_trend=close[-1] if close else 0)

        # Расчёт ATR
        atr = AdvancedIndicators._calculate_atr(high, low, close, period)

        # Базовые полосы
        basic_upper = (np.array(high) + np.array(low)) / 2 + multiplier * atr
        basic_lower = (np.array(high) + np.array(low)) / 2 - multiplier * atr

        # Инициализация
        final_upper = np.zeros(len(close))
        final_lower = np.zeros(len(close))
        trend = np.zeros(len(close))

        for i in range(1, len(close)):
            final_upper[i] = basic_upper[i] if (basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
            final_lower[i] = basic_lower[i] if (basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]) else final_lower[i - 1]

            if trend[i - 1] == 1:
                trend[i] = 1 if close[i] > final_lower[i] else -1
            else:
                trend[i] = -1 if close[i] < final_upper[i] else 1

        current_trend = int(trend[-1])
        super_trend = final_upper[-1] if current_trend == -1 else final_lower[-1]

        return SuperTrendResult(
            trend=current_trend,
            upper_band=final_upper[-1],
            lower_band=final_lower[-1],
            super_trend=super_trend
        )

    # ========================================================================
    # 2. HALF TREND (ИСПРАВЛЕНО)
    # ========================================================================

    @staticmethod
    def calculate_half_trend(
            high: List[float],
            low: List[float],
            close: List[float],
            amplitude: int = 2,
            channel_deviation: float = 2.0
    ) -> Dict[str, Any]:
        """
        Расчёт Half Trend индикатора

        Args:
            high: Список цен High
            low: Список цен Low
            close: Список цен Close
            amplitude: Амплитуда (по умолчанию 2)
            channel_deviation: Отклонение канала (по умолчанию 2.0)

        Returns:
            Dict с трендом и уровнями
        """
        if len(close) < amplitude * 2:
            return {'trend': 0, 'ht': close[-1] if close else 0, 'up': 0, 'down': 0}

        # ✅ ИСПРАВЛЕНО: _calculate_atr возвращает массив, берём последнее значение
        atr_array = AdvancedIndicators._calculate_atr(high, low, close, 100)
        atr = atr_array[-1] / 2 if len(atr_array) > 0 else 0
        dev = channel_deviation * atr

        ht_trend = 0
        max_low_price = low[-amplitude] if len(low) > amplitude else low[0]
        min_high_price = high[-amplitude] if len(high) > amplitude else high[0]
        ht_up = 0.0
        ht_down = 0.0

        for i in range(1, len(close)):
            high_price = max(high[max(0, i - amplitude):i + 1])
            low_price = min(low[max(0, i - amplitude):i + 1])

            if ht_trend == 1:
                max_low_price = max(low_price, max_low_price)
                if high_price < max_low_price and close[i] < low[i - 1]:
                    ht_trend = 0
                    min_high_price = high_price
            else:
                min_high_price = min(high_price, min_high_price)
                if low_price > min_high_price and close[i] > high[i - 1]:
                    ht_trend = 1
                    max_low_price = low_price

            if ht_trend == 0:
                ht_up = max(max_low_price, ht_up) if i > 0 else max_low_price
            else:
                ht_down = min(min_high_price, ht_down) if i > 0 else min_high_price

        ht = ht_up if ht_trend == 0 else ht_down

        return {
            'trend': ht_trend,  # 0 = uptrend, 1 = downtrend
            'ht': ht,
            'up': ht_up,
            'down': ht_down,
            'atr': atr,
            'deviation': dev
        }

    # ========================================================================
    # 3. ICHIMOKU CLOUD
    # ========================================================================

    @staticmethod
    def calculate_ichimoku(
            high: List[float],
            low: List[float],
            close: List[float],
            tenkan_period: int = 9,
            kijun_period: int = 26,
            senkou_b_period: int = 52
    ) -> IchimokuResult:
        """
        Расчёт Ichimoku Cloud (Облако Ишимоку)
        """
        if len(close) < senkou_b_period:
            return IchimokuResult(
                conversion_line=close[-1] if close else 0,
                base_line=close[-1] if close else 0,
                leading_span_a=close[-1] if close else 0,
                leading_span_b=close[-1] if close else 0,
                lagging_span=close[-1] if close else 0,
                cloud_green=False
            )

        def donchian(high_arr, low_arr, period):
            highest = max(high_arr[-period:]) if len(high_arr) >= period else max(high_arr)
            lowest = min(low_arr[-period:]) if len(low_arr) >= period else min(low_arr)
            return (highest + lowest) / 2

        # Tenkan-sen (Conversion Line)
        tenkan = donchian(high, low, tenkan_period)

        # Kijun-sen (Base Line)
        kijun = donchian(high, low, kijun_period)

        # Senkou Span A (Leading Span A)
        senkou_a = (tenkan + kijun) / 2

        # Senkou Span B (Leading Span B)
        senkou_b = donchian(high, low, senkou_b_period)

        # Chikou Span (Lagging Span)
        chikou = close[-kijun_period] if len(close) > kijun_period else close[0]

        return IchimokuResult(
            conversion_line=tenkan,
            base_line=kijun,
            leading_span_a=senkou_a,
            leading_span_b=senkou_b,
            lagging_span=chikou,
            cloud_green=senkou_a > senkou_b
        )

    # ========================================================================
    # 4. DONCHIAN CHANNEL
    # ========================================================================

    @staticmethod
    def calculate_donchian(
            high: List[float],
            low: List[float],
            close: List[float],  # ← ЕСТЬ!
            period: int = 20
    ) -> Dict[str, float]:
        """
        Расчёт Donchian Channel

        Args:
            high: Список цен High
            low: Список цен Low
            close: Список цен Close
            period: Период канала

        Returns:
            Dict с верхней, нижней и средней линиями
        """
        if len(close) < period:
            return {'upper': close[-1] if close else 0,
                    'lower': close[-1] if close else 0,
                    'middle': close[-1] if close else 0,
                    'trend': 0}

        upper = max(high[-period:])
        lower = min(low[-period:])
        middle = (upper + lower) / 2

        # Определение тренда
        trend = 1 if close[-1] > upper else -1 if close[-1] < lower else 0

        return {
            'upper': upper,
            'lower': lower,
            'middle': middle,
            'trend': trend,
            'width': upper - lower
        }

    # ========================================================================
    # 5. DMI / ADX
    # ========================================================================

    @staticmethod
    def calculate_dmi_adx(
            high: List[float],
            low: List[float],
            close: List[float],
            period: int = 14,
            adx_period: int = 14
    ) -> DMICResult:
        """
        Расчёт DMI (Directional Movement Index) и ADX

        Args:
            high: Список цен High
            low: Список цен Low
            close: Список цен Close
            period: Период DMI (14)
            adx_period: Период ADX (14)

        Returns:
            DMICResult с ADX, +DI, -DI и трендом
        """
        if len(close) < period + adx_period:
            return DMICResult(adx=25, plus_di=25, minus_di=25, trend=0)

        plus_dm = []
        minus_dm = []
        tr = []

        for i in range(1, len(close)):
            # True Range
            tr_val = max(high[i] - low[i],
                        abs(high[i] - close[i - 1]),
                        abs(low[i] - close[i - 1]))
            tr.append(tr_val)

            # Directional Movement
            up_move = high[i] - high[i - 1]
            down_move = low[i - 1] - low[i]

            if up_move > down_move and up_move > 0:
                plus_dm.append(up_move)
            else:
                plus_dm.append(0)

            if down_move > up_move and down_move > 0:
                minus_dm.append(down_move)
            else:
                minus_dm.append(0)

        # Сглаживание
        if len(tr) < period:
            return DMICResult(adx=25, plus_di=25, minus_di=25, trend=0)

        smoothed_tr = sum(tr[-period:]) / period
        smoothed_plus_dm = sum(plus_dm[-period:]) / period
        smoothed_minus_dm = sum(minus_dm[-period:]) / period

        plus_di = 100 * smoothed_plus_dm / smoothed_tr if smoothed_tr > 0 else 0
        minus_di = 100 * smoothed_minus_dm / smoothed_tr if smoothed_tr > 0 else 0

        # DX и ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0
        adx = dx  # Упрощённо

        trend = 1 if plus_di > minus_di else -1

        return DMICResult(
            adx=round(adx, 2),
            plus_di=round(plus_di, 2),
            minus_di=round(minus_di, 2),
            trend=trend
        )

    # ========================================================================
    # 6. PARABOLIC SAR
    # ========================================================================

    @staticmethod
    def calculate_parabolic_sar(
            high: List[float],
            low: List[float],
            close: List[float],
            start: float = 0.02,
            increment: float = 0.02,
            maximum: float = 0.2
    ) -> Dict[str, Any]:
        """
        Расчёт Parabolic SAR (PSAR)

        Args:
            high: Список цен High
            low: Список цен Low
            close: Список цен Close
            start: Начальное значение AF (0.02)
            increment: Шаг увеличения AF (0.02)
            maximum: Максимальное значение AF (0.2)

        Returns:
            Dict с значениями SAR и трендом
        """
        if len(close) < 2:
            return {'sar': close[-1] if close else 0, 'trend': 0}

        sar = np.zeros(len(close))
        ep = np.zeros(len(close))
        af = np.zeros(len(close))
        trend = np.zeros(len(close))

        # Инициализация
        trend[0] = 1 if close[1] > close[0] else -1
        sar[0] = low[0] if trend[0] == 1 else high[0]
        ep[0] = high[0] if trend[0] == 1 else low[0]
        af[0] = start

        for i in range(1, len(close)):
            sar[i] = sar[i - 1] + af[i - 1] * (ep[i - 1] - sar[i - 1])

            if trend[i - 1] == 1:
                sar[i] = min(sar[i], low[i - 1], low[i - 2] if i > 1 else low[i - 1])
                if close[i] < sar[i]:
                    trend[i] = -1
                    sar[i] = ep[i - 1]
                    ep[i] = low[i]
                    af[i] = start
                else:
                    trend[i] = 1
                    if high[i] > ep[i - 1]:
                        ep[i] = high[i]
                        af[i] = min(af[i - 1] + increment, maximum)
                    else:
                        ep[i] = ep[i - 1]
                        af[i] = af[i - 1]
            else:
                sar[i] = max(sar[i], high[i - 1], high[i - 2] if i > 1 else high[i - 1])
                if close[i] > sar[i]:
                    trend[i] = 1
                    sar[i] = ep[i - 1]
                    ep[i] = high[i]
                    af[i] = start
                else:
                    trend[i] = -1
                    if low[i] < ep[i - 1]:
                        ep[i] = low[i]
                        af[i] = min(af[i - 1] + increment, maximum)
                    else:
                        ep[i] = ep[i - 1]
                        af[i] = af[i - 1]

        current_trend = int(trend[-1])
        current_sar = sar[-1]

        return {
            'sar': current_sar,
            'trend': current_trend,  # 1 = выше цены (медвежий), -1 = ниже цены (бычий)
            'above_price': current_sar > close[-1]
        }

    # ========================================================================
    # 7. STOCHASTIC
    # ========================================================================

    @staticmethod
    def calculate_stochastic(
            high: List[float],
            low: List[float],
            close: List[float],
            k_period: int = 14,
            d_period: int = 3,
            slowing: int = 3
    ) -> Dict[str, float]:
        """
        Расчёт Stochastic Oscillator

        Args:
            high: Список цен High
            low: Список цен Low
            close: Список цен Close
            k_period: Период %K (14)
            d_period: Период %D (3)
            slowing: Сглаживание (3)

        Returns:
            Dict с %K, %D и сигналом
        """
        if len(close) < k_period + d_period:
            return {'k': 50, 'd': 50, 'signal': 0}

        highest_high = max(high[-k_period:])
        lowest_low = min(low[-k_period:])

        if highest_high == lowest_low:
            k = 50
        else:
            k = 100 * (close[-1] - lowest_low) / (highest_high - lowest_low)

        # Сглаживание %K
        k_values = []
        for i in range(k_period - slowing, k_period):
            hh = max(high[-i:]) if i > 0 else high[-1]
            ll = min(low[-i:]) if i > 0 else low[-1]
            if hh != ll:
                k_values.append(100 * (close[-i] - ll) / (hh - ll))
            else:
                k_values.append(50)

        k_smoothed = sum(k_values[-slowing:]) / slowing if k_values else k

        # %D = SMA of %K
        d = k_smoothed

        # Сигнал (1 = перекуплен, -1 = перепродан)
        signal = 1 if k > 80 else -1 if k < 20 else 0

        return {'k': round(k_smoothed, 2), 'd': round(d, 2), 'signal': signal}

    # ========================================================================
    # 8. CCI (Commodity Channel Index)
    # ========================================================================

    @staticmethod
    def calculate_cci(
            high: List[float],
            low: List[float],
            close: List[float],
            period: int = 20
    ) -> Dict[str, float]:
        """
        Расчёт CCI (Commodity Channel Index)

        Args:
            high: Список цен High
            low: Список цен Low
            close: Список цен Close
            period: Период CCI (20)

        Returns:
            Dict со значением CCI и сигналом
        """
        if len(close) < period:
            return {'cci': 0, 'signal': 0}

        typical_price = [(high[i] + low[i] + close[i]) / 3 for i in range(len(close))]
        sma = sum(typical_price[-period:]) / period

        mean_deviation = 0
        for i in range(period):
            mean_deviation += abs(typical_price[-period + i] - sma)

        mean_deviation /= period

        if mean_deviation == 0:
            cci = 0
        else:
            cci = (typical_price[-1] - sma) / (0.015 * mean_deviation)

        # Сигнал
        signal = 1 if cci > 100 else -1 if cci < -100 else 0

        return {'cci': round(cci, 2), 'signal': signal}

    # ========================================================================
    # 9. RANGE FILTER
    # ========================================================================

    @staticmethod
    def calculate_range_filter(
            close: List[float],
            period: int = 100,
            multiplier: float = 3.0
    ) -> Dict[str, Any]:
        """
        Расчёт Range Filter

        Args:
            close: Список цен Close
            period: Период фильтра (100)
            multiplier: Множитель (3.0)

        Returns:
            Dict с фильтром и направлением
        """
        if len(close) < period:
            return {'filt': close[-1] if close else 0, 'direction': 0, 'upward': 0, 'downward': 0}

        # Smooth Range
        atr = AdvancedIndicators._calculate_atr_simple(close, period)
        smooth_rng = atr * multiplier

        # Range Filter
        filt = [close[0]]
        for i in range(1, len(close)):
            if close[i] > filt[-1] + smooth_rng:
                filt.append(close[i] - smooth_rng)
            elif close[i] < filt[-1] - smooth_rng:
                filt.append(close[i] + smooth_rng)
            else:
                filt.append(filt[-1])

        current_filt = filt[-1]
        direction = 1 if current_filt > filt[-2] else -1 if current_filt < filt[-2] else 0

        return {
            'filt': current_filt,
            'direction': direction,
            'upward': 1 if direction == 1 else 0,
            'downward': 1 if direction == -1 else 0
        }

    # ========================================================================
    # 10. VWAP (Volume Weighted Average Price)
    # ========================================================================

    @staticmethod
    def calculate_vwap(
            high: List[float],
            low: List[float],
            close: List[float],
            volume: List[float]
    ) -> Dict[str, float]:
        """
        Расчёт VWAP (Volume Weighted Average Price)

        Args:
            high: Список цен High
            low: Список цен Low
            close: Список цен Close
            volume: Список объёмов

        Returns:
            Dict со значением VWAP и отклонением
        """
        if not volume or sum(volume[-20:]) == 0:
            return {'vwap': close[-1] if close else 0, 'deviation': 0, 'above': False}

        typical_price = [(high[i] + low[i] + close[i]) / 3 for i in range(len(close))]

        cum_volume = 0
        cum_value = 0

        for i in range(len(close)):
            cum_volume += volume[i]
            cum_value += typical_price[i] * volume[i]

        vwap = cum_value / cum_volume if cum_volume > 0 else close[-1]

        return {
            'vwap': round(vwap, 4),
            'deviation': round((close[-1] - vwap) / vwap * 100, 2),
            'above': close[-1] > vwap
        }

    # ========================================================================
    # 11. ATR-BASED TP/SL LEVELS
    # ========================================================================

    @staticmethod
    def calculate_atr_levels(
            entry_price: float,
            atr: float,
            direction: str,  # "LONG" или "SHORT"
            tp1_mult: float = 2.0,
            tp2_mult: float = 3.5,
            tp3_mult: float = 5.0,
            sl_mult: float = 2.0
    ) -> Dict[str, float]:
        """
        Расчёт уровней TP/SL на основе ATR

        Args:
            entry_price: Цена входа
            atr: Значение ATR
            direction: "LONG" или "SHORT"
            tp1_mult: Множитель для TP1 (2.0)
            tp2_mult: Множитель для TP2 (3.5)
            tp3_mult: Множитель для TP3 (5.0)
            sl_mult: Множитель для SL (2.0)

        Returns:
            Dict с уровнями TP1, TP2, TP3, SL
        """
        if direction.upper() == "LONG":
            return {
                'tp1': entry_price + atr * tp1_mult,
                'tp2': entry_price + atr * tp2_mult,
                'tp3': entry_price + atr * tp3_mult,
                'sl': entry_price - atr * sl_mult,
                'tp1_pct': tp1_mult * (atr / entry_price * 100),
                'tp2_pct': tp2_mult * (atr / entry_price * 100),
                'tp3_pct': tp3_mult * (atr / entry_price * 100),
                'sl_pct': sl_mult * (atr / entry_price * 100)
            }
        else:
            return {
                'tp1': entry_price - atr * tp1_mult,
                'tp2': entry_price - atr * tp2_mult,
                'tp3': entry_price - atr * tp3_mult,
                'sl': entry_price + atr * sl_mult,
                'tp1_pct': tp1_mult * (atr / entry_price * 100),
                'tp2_pct': tp2_mult * (atr / entry_price * 100),
                'tp3_pct': tp3_mult * (atr / entry_price * 100),
                'sl_pct': sl_mult * (atr / entry_price * 100)
            }

    # ========================================================================
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ========================================================================

    @staticmethod
    def _calculate_atr(
            high: List[float],
            low: List[float],
            close: List[float],
            period: int = 14
    ) -> np.ndarray:
        """Расчёт ATR для массива данных"""
        if len(close) < period + 1:
            return np.array([0.0] * len(close))

        tr = np.zeros(len(close))
        for i in range(1, len(close)):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1])
            )

        atr = np.zeros(len(close))
        atr[period] = np.mean(tr[1:period + 1])

        for i in range(period + 1, len(close)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        return atr

    @staticmethod
    def _calculate_atr_simple(close: List[float], period: int = 14) -> float:
        """Простой расчёт ATR (упрощённый)"""
        if len(close) < period + 1:
            return 0.0

        returns = []
        for i in range(1, len(close)):
            if close[i - 1] > 0:
                returns.append(abs(close[i] - close[i - 1]) / close[i - 1])

        if not returns:
            return 0.0

        return sum(returns[-period:]) / period * close[-1]

    @staticmethod
    def calculate_obv(close: List[float], volume: List[float]) -> List[float]:
        """Расчёт On-Balance Volume (OBV)"""
        if not close or not volume or len(close) < 2:
            return []

        obv = [0]
        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv.append(obv[-1] + volume[i])
            elif close[i] < close[i - 1]:
                obv.append(obv[-1] - volume[i])
            else:
                obv.append(obv[-1])
        return obv

    @staticmethod
    def calculate_obv_trend(obv: List[float]) -> str:
        """Определение тренда OBV"""
        if len(obv) < 10:
            return "neutral"

        # Сравниваем последние значения
        if obv[-1] > obv[-5] and obv[-5] > obv[-10]:
            return "up"
        elif obv[-1] < obv[-5] and obv[-5] < obv[-10]:
            return "down"
        return "neutral"

    @staticmethod
    def calculate_aroon(high: List[float], low: List[float], period: int = 25) -> Dict[str, float]:
        """Расчёт Aroon индикатора"""
        if len(high) < period + 1:
            return {"up": 0, "down": 0}

        # Aroon Up
        highest_idx = np.argmax(high[-period:])
        aroon_up = ((period - highest_idx) / period) * 100

        # Aroon Down
        lowest_idx = np.argmin(low[-period:])
        aroon_down = ((period - lowest_idx) / period) * 100

        return {"up": round(aroon_up, 1), "down": round(aroon_down, 1)}


# Глобальный экземпляр
advanced_indicators = AdvancedIndicators()