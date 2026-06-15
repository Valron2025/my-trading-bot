#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ПРОДАКШН движок стратегии - все индикаторы, максимальная точность"""

import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from ..models import SignalResult
from ..logger import debug, info, warning

# Импортируем все расширенные индикаторы
try:
    from trading_bot.analysis.advanced_indicators import advanced_indicators
    from trading_bot.analysis.pivot_analyzer import pivot_analyzer
    from trading_bot.analysis.advanced_strategy import multi_timeframe_analyzer

    ADVANCED_AVAILABLE = True
    info("✅ Расширенные индикаторы загружены")
except ImportError as e:
    ADVANCED_AVAILABLE = False
    warning(f"⚠️ Расширенные индикаторы не доступны: {e}")


class StrategyEngine:
    """
    ПРОДАКШН движок стратегии
    Использует ВСЕ доступные индикаторы для максимальной точности
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config

        # Основные параметры
        self.rsi_period = config.get('rsi_period', 7)
        self.score_threshold_long = config.get('score_threshold_long', 2)
        self.score_threshold_short = config.get('score_threshold_short', -2)
        self.take_profit_pct = config.get('take_profit_pct', 1.2)
        self.stop_loss_pct = config.get('stop_loss_pct', 0.6)
        self.trailing_stop_pct = config.get('trailing_stop_pct', 0.4)
        self.max_hold_minutes = config.get('max_hold_minutes', 20)
        self.use_dynamic_tp_sl = config.get('use_dynamic_tp_sl', True)

        # ✅ ДОБАВЛЕНА ПРОВЕРКА SHORT ПОРОГА В КЛАССЕ
        if self.score_threshold_short > -2:
            warning(f"⚠️ SHORT порог {self.score_threshold_short} слишком мягкий, корректируем до -2")
            self.score_threshold_short = -2

        # ========== НОВО: Мультитаймфреймный анализ ==========
        self.use_multi_timeframe = config.get('use_multi_timeframe', False)
        self.mtf_boost = config.get('mtf_boost', 3)
        self.mtf_min_confidence = config.get('mtf_min_confidence', 50)
        self.mtf_consensus_bonus = config.get('mtf_consensus_bonus', 2)
        self.tf_weights = config.get('tf_weights', {
            '1min': 1.0, '5min': 1.5, '15min': 2.0, '1hour': 2.5
        })
        self._last_mtf_details = {}

        if self.use_multi_timeframe:
            info(f"   📊 Мультитаймфрейм: ✅ (бонус до ±{self.mtf_boost})")

        # Включение расширенных индикаторов
        self.use_supertrend = config.get('use_supertrend', True)
        self.use_ichimoku = config.get('use_ichimoku', True)
        self.use_dmi_adx = config.get('use_dmi_adx', True)
        self.use_stochastic = config.get('use_stochastic', True)
        self.use_cci = config.get('use_cci', True)
        self.use_psar = config.get('use_psar', True)
        self.use_pivots = config.get('use_pivots', True)
        self.use_vwap = config.get('use_vwap', True)
        self.use_donchian = config.get('use_donchian', True)
        self.use_obv = config.get('use_obv', True)
        self.use_aroon = config.get('use_aroon', True)

        # ➕ ДОБАВЛЕНО: кэш для низколиквидных тикеров
        self._low_liquidity_cache = {}

        info("=" * 60)
        info("🚀 ПРОДАКШН StrategyEngine инициализирован")
        info(f"   🎯 LONG порог={self.score_threshold_long}, SHORT порог={self.score_threshold_short}")
        info(f"   📊 Индикаторы:")
        info(f"      - SuperTrend: {self.use_supertrend}")
        info(f"      - Ichimoku: {self.use_ichimoku}")
        info(f"      - DMI/ADX: {self.use_dmi_adx}")
        info(f"      - Stochastic: {self.use_stochastic}")
        info(f"      - CCI: {self.use_cci}")
        info(f"      - Parabolic SAR: {self.use_psar}")
        info(f"      - Pivot Points: {self.use_pivots}")
        info(f"      - VWAP: {self.use_vwap}")
        info(f"      - Donchian: {self.use_donchian}")
        info(f"      - OBV: {self.use_obv}")
        info(f"      - Aroon: {self.use_aroon}")
        info("=" * 60)

    def calculate_dynamic_tp_sl(self, ticker: str, figi: str = None) -> Tuple[float, float]:
        """
        Расчёт динамических TP/SL на основе ATR и тренда
        ВОЗВРАЩАЕТ: (take_profit_pct, stop_loss_pct)
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import debug, info

        # Fallback на статические значения
        default_tp = self.config.get('take_profit_pct', 1.2)
        default_sl = self.config.get('stop_loss_pct', 0.6)

        if not figi:
            debug(f"⚠️ {ticker}: нет FIGI для динамического TP/SL, использую статику")
            return default_tp, default_sl

        try:
            # Получаем свечи для расчёта ATR
            candles = tbank.get_candles(figi, days=2, interval_minutes=15)

            if not candles or len(candles) < 20:
                debug(f"⚠️ {ticker}: недостаточно свечей для ATR ({len(candles) if candles else 0}/20)")
                return default_tp, default_sl

            # Извлекаем цены
            closes = []
            highs = []
            lows = []

            for c in candles[-30:]:
                if isinstance(c, (list, tuple)) and len(c) >= 2:
                    close_val = c[0]
                    closes.append(close_val)
                    highs.append(close_val * 1.005)
                    lows.append(close_val * 0.995)
                elif isinstance(c, dict):
                    closes.append(c.get('close', 0))
                    highs.append(c.get('high', closes[-1]))
                    lows.append(c.get('low', closes[-1]))
                elif hasattr(c, 'close'):
                    closes.append(c.close)
                    highs.append(getattr(c, 'high', c.close))
                    lows.append(getattr(c, 'low', c.close))

            if len(closes) < 20:
                return default_tp, default_sl

            # Расчёт ATR
            true_ranges = []
            for i in range(1, len(closes)):
                high_low = highs[i] - lows[i]
                high_close = abs(highs[i] - closes[i - 1])
                low_close = abs(lows[i] - closes[i - 1])
                true_ranges.append(max(high_low, high_close, low_close))

            atr = sum(true_ranges[-14:]) / min(14, len(true_ranges))
            current_price = closes[-1]
            atr_pct = (atr / current_price) * 100 if current_price > 0 else 1.5

            # Определение тренда
            ma20 = sum(closes[-20:]) / 20
            ma10 = sum(closes[-10:]) / 10
            current = closes[-1]

            if current > ma20 and ma20 > ma10:
                trend = "bullish"
                tp_multiplier = 2.5
                sl_multiplier = 1.5
            elif current < ma20 and ma20 < ma10:
                trend = "bearish"
                tp_multiplier = 1.2
                sl_multiplier = 1.0
            else:
                trend = "neutral"
                tp_multiplier = 1.8
                sl_multiplier = 1.2

            # Расчёт TP/SL
            take_profit = min(8.0, max(1.0, atr_pct * tp_multiplier))
            stop_loss = min(5.0, max(0.5, atr_pct * sl_multiplier))

            info(f"   📊 {ticker}: ATR={atr_pct:.2f}%, тренд={trend}")
            info(f"   🎯 Динамический TP: +{take_profit:.2f}%, SL: -{stop_loss:.2f}%")

            return take_profit, stop_loss

        except Exception as e:
            debug(f"❌ Ошибка расчёта динамического TP/SL для {ticker}: {e}")
            return default_tp, default_sl

    # ➕ ДОБАВЛЕНО: метод для определения минимального количества свечей
    def _get_min_prices_for_ticker(self, ticker: str) -> int:
        """
        Динамическое определение минимального количества свечей для тикера
        Для низколиквидных тикеров достаточно 15 свечей
        """
        low_liquidity_tickers = {
            "OMZZP", "OMZZ", "KZOS", "YRSBP", "YRSB",
            "CNRU", "CNR", "BSPB", "BSP", "TUZA"
        }
        
        ticker_upper = ticker.upper()
        
        if ticker_upper in low_liquidity_tickers:
            if ticker_upper not in self._low_liquidity_cache:
                self._low_liquidity_cache[ticker_upper] = True
                debug(f"📊 {ticker}: низколиквидный тикер, min_prices=15")
            return 15
        return 20

    def analyze_signal(self,
                       prices: List[float],
                       volumes: List[float],
                       name: str = "",
                       figi: str = None,
                       candles: Optional[List] = None,
                       candles_1min: Optional[List] = None,
                       candles_5min: Optional[List] = None,
                       candles_1hour: Optional[List] = None) -> SignalResult:
        """ПОЛНЫЙ анализ сигнала со ВСЕМИ индикаторами - УНИВЕРСАЛЬНАЯ ВЕРСИЯ"""

        from ..logger import info, warning

        info(f"\n{'=' * 60}")
        info(f"🔬 STRATEGY ENGINE АНАЛИЗ: {name}")
        info(f"{'=' * 60}")

        # ДИАГНОСТИКА ВХОДНЫХ ДАННЫХ
        info(f"   📊 Входные данные:")
        info(f"      prices: {len(prices) if prices else 0} значений")
        info(f"      volumes: {len(volumes) if volumes else 0} значений")
        info(f"      candles: {len(candles) if candles else 0} свечей")

        if candles and len(candles) > 0:
            first = candles[0]
            info(f"      тип candles[0]: {type(first).__name__}")
            if isinstance(first, dict):
                info(f"      ✅ candles[0] - словарь! keys={list(first.keys())[:5]}")
                info(f"         close = {first.get('close', 'N/A')}")
            elif hasattr(first, 'close'):
                info(f"      ✅ candles[0] - объект! .close={first.close}")
            else:
                info(f"      ⚠️ candles[0] - неизвестный тип: {type(first)}")

        info(
            f"      MTF: 1min={len(candles_1min) if candles_1min else 0}, 5min={len(candles_5min) if candles_5min else 0}, 1hour={len(candles_1hour) if candles_1hour else 0}")

        if candles_1min and len(candles_1min) > 0:
            first = candles_1min[0]
            info(f"🔍 [STRATEGY] {name}: формат свечей (1min) = {type(first).__name__}")

        if candles_5min and len(candles_5min) > 0:
            first = candles_5min[0]
            info(f"🔍 [STRATEGY] {name}: формат свечей (5min) = {type(first).__name__}")

        if candles_1hour and len(candles_1hour) > 0:
            first = candles_1hour[0]
            info(f"🔍 [STRATEGY] {name}: формат свечей (1hour) = {type(first).__name__}")

        # ✅ ИСПРАВЛЕНО: динамический порог для низколиквидных тикеров
        min_prices = self._get_min_prices_for_ticker(name)

        if len(prices) < min_prices:
            debug(f"⚠️ Мало данных для {name}: {len(prices)}/{min_prices} свечей")
            return SignalResult(
                score=0, buy_signal=False, sell_signal=False,
                recommendation="НЕТ ДАННЫХ",
                signals=[f"⚠️ Мало данных ({len(prices)} свечей)"]
            )

        current_price = prices[-1]

        # ========== ДИНАМИЧЕСКИЙ TP/SL ==========
        if figi and self.use_dynamic_tp_sl:
            tp_pct, sl_pct = self.calculate_dynamic_tp_sl(name, figi)
            self.take_profit_pct = tp_pct
            self.stop_loss_pct = sl_pct
        else:
            self.take_profit_pct = self.config.get('take_profit_pct', 1.2)
            self.stop_loss_pct = self.config.get('stop_loss_pct', 0.6)

        # ========== УНИВЕРСАЛЬНАЯ ПОДГОТОВКА ДАННЫХ ДЛЯ ИНДИКАТОРОВ ==========
        def get_candle_value(candle, attr, default=0):
            """Универсальное получение значения из свечи"""
            if hasattr(candle, attr):
                return getattr(candle, attr)
            elif isinstance(candle, dict):
                return candle.get(attr, default)
            elif isinstance(candle, (list, tuple)):
                if attr == 'close' and len(candle) > 0:
                    return candle[0]
                elif attr == 'high' and len(candle) > 3:
                    return candle[3]
                elif attr == 'low' and len(candle) > 4:
                    return candle[4]
                elif attr == 'open' and len(candle) > 5:
                    return candle[5]
            return default

        if candles and len(candles) >= 30:
            high = [get_candle_value(c, 'high', prices[i]) for i, c in enumerate(candles)]
            low = [get_candle_value(c, 'low', prices[i]) for i, c in enumerate(candles)]
            close = [get_candle_value(c, 'close', prices[i]) for i, c in enumerate(candles)]
            opens = [get_candle_value(c, 'open', prices[i]) for i, c in enumerate(candles)]
        else:
            high = low = close = opens = prices

        score = 0
        buy_signal = False
        sell_signal = False
        signals = []
        details = {}

        # ========== 1. RSI (основной) ==========
        rsi = self._calculate_rsi(prices, self.rsi_period)
        details['rsi'] = rsi

        if rsi < 25:
            score += 7
            buy_signal = True
            signals.append(f"🔥 RSI={rsi:.1f} (экстремально перепродано) +7")
        elif rsi < 30:
            score += 6
            buy_signal = True
            signals.append(f"📈 RSI={rsi:.1f} (сильно перепродано) +6")
        elif rsi < 35:
            score += 4
            buy_signal = True
            signals.append(f"📈 RSI={rsi:.1f} (перепродано) +4")
        elif rsi < 45:
            score += 2
            signals.append(f"📈 RSI={rsi:.1f} (почти перепродано) +2")
        elif rsi > 75:
            score -= 7
            sell_signal = True
            signals.append(f"🔥 RSI={rsi:.1f} (экстремально перекуплено) -7")
        elif rsi > 70:
            score -= 6
            sell_signal = True
            signals.append(f"📉 RSI={rsi:.1f} (сильно перекуплено) -6")
        elif rsi > 65:
            score -= 4
            sell_signal = True
            signals.append(f"📉 RSI={rsi:.1f} (перекуплено) -4")
        elif rsi > 55:
            score -= 2
            signals.append(f"📉 RSI={rsi:.1f} (почти перекуплено) -2")

        # ========== 2. SUPERTREND ==========
        if self.use_supertrend and ADVANCED_AVAILABLE and len(high) >= 20:
            try:
                st = advanced_indicators.calculate_supertrend(high, low, close, period=10, multiplier=3)
                details['supertrend'] = st.trend
                if st.trend == 1:
                    score += 4
                    signals.append(f"📈 SuperTrend: восходящий тренд +4")
                    buy_signal = True
                elif st.trend == -1:
                    score -= 4
                    signals.append(f"📉 SuperTrend: нисходящий тренд -4")
                    sell_signal = True
            except Exception as e:
                debug(f"SuperTrend error: {e}")

        # ========== 3. ICHIMOKU CLOUD ==========
        if self.use_ichimoku and ADVANCED_AVAILABLE and len(close) >= 52:
            try:
                ichimoku = advanced_indicators.calculate_ichimoku(high, low, close)
                details['ichimoku'] = {'cloud_green': ichimoku.cloud_green}

                if ichimoku.cloud_green and current_price > ichimoku.leading_span_a:
                    score += 3
                    signals.append(f"☁️ Ichimoku: цена над облаком (бычий) +3")
                    buy_signal = True
                elif not ichimoku.cloud_green and current_price < ichimoku.leading_span_a:
                    score -= 3
                    signals.append(f"☁️ Ichimoku: цена под облаком (медвежий) -3")
                    sell_signal = True
                elif ichimoku.cloud_green and current_price > ichimoku.leading_span_b:
                    score += 1
                    signals.append(f"☁️ Ichimoku: бычий облако +1")
            except Exception as e:
                debug(f"Ichimoku error: {e}")

        # ========== 4. DMI/ADX ==========
        if self.use_dmi_adx and ADVANCED_AVAILABLE and len(close) >= 28:
            try:
                dmi = advanced_indicators.calculate_dmi_adx(high, low, close, period=14)
                details['adx'] = dmi.adx

                if dmi.adx > 25:
                    if dmi.trend == 1:
                        score += 3
                        signals.append(f"📊 ADX={dmi.adx:.1f}: сильный бычий тренд +3")
                    elif dmi.trend == -1:
                        score -= 3
                        signals.append(f"📊 ADX={dmi.adx:.1f}: сильный медвежий тренд -3")
                elif dmi.adx < 20:
                    signals.append(f"⚠️ ADX={dmi.adx:.1f}: слабый тренд")
                    if abs(score) < 5:
                        score = int(score * 0.5)
            except Exception as e:
                debug(f"DMI/ADX error: {e}")

        # ========== 5. STOCHASTIC ==========
        if self.use_stochastic and ADVANCED_AVAILABLE and len(close) >= 14:
            try:
                stoch = advanced_indicators.calculate_stochastic(high, low, close, k_period=14, d_period=3)
                details['stoch_k'] = stoch['k']
                details['stoch_d'] = stoch['d']

                if stoch['k'] < 20:
                    score += 3
                    signals.append(f"🔄 Stochastic %K={stoch['k']:.1f} (перепродано) +3")
                    buy_signal = True
                elif stoch['k'] > 80:
                    score -= 3
                    signals.append(f"🔄 Stochastic %K={stoch['k']:.1f} (перекуплено) -3")
                    sell_signal = True
                if stoch['k'] > stoch['d'] and stoch['k'] < 30:
                    score += 2
                    signals.append(f"🔄 Stochastic: бычье пересечение +2")
                elif stoch['k'] < stoch['d'] and stoch['k'] > 70:
                    score -= 2
                    signals.append(f"🔄 Stochastic: медвежье пересечение -2")
            except Exception as e:
                debug(f"Stochastic error: {e}")

        # ========== 6. CCI ==========
        if self.use_cci and ADVANCED_AVAILABLE and len(close) >= 20:
            try:
                cci = advanced_indicators.calculate_cci(high, low, close, period=20)
                details['cci'] = cci['cci']
                if cci['cci'] < -100:
                    score += 3
                    signals.append(f"📊 CCI={cci['cci']:.1f} (перепродано) +3")
                elif cci['cci'] > 100:
                    score -= 3
                    signals.append(f"📊 CCI={cci['cci']:.1f} (перекуплено) -3")
                elif cci['cci'] < -200:
                    score += 4
                    signals.append(f"🔥 CCI={cci['cci']:.1f} (экстремально перепродано) +4")
            except Exception as e:
                debug(f"CCI error: {e}")

        # ========== 7. PARABOLIC SAR ==========
        if self.use_psar and ADVANCED_AVAILABLE and len(close) >= 20:
            try:
                psar = advanced_indicators.calculate_parabolic_sar(high, low, close)
                details['psar'] = 'bullish' if not psar['above_price'] else 'bearish'
                if not psar['above_price']:
                    score += 3
                    signals.append(f"🎯 Parabolic SAR: бычий сигнал +3")
                    buy_signal = True
                else:
                    score -= 3
                    signals.append(f"🎯 Parabolic SAR: медвежий сигнал -3")
                    sell_signal = True
            except Exception as e:
                debug(f"Parabolic SAR error: {e}")

        # ========== 8. PIVOT POINTS ==========
        if self.use_pivots and len(high) >= 2:
            try:
                pivot_levels = pivot_analyzer.calculate_pivot_levels(
                    high=high[-2], low=low[-2], close=close[-2]
                )
                nearest = pivot_analyzer.find_nearest_levels(current_price, pivot_levels)
                details['pivot'] = pivot_levels.pivot
                details['nearest_resistance'] = nearest['resistance']
                details['nearest_support'] = nearest['support']

                if nearest['resistance'] and current_price > pivot_levels.pivot:
                    score += 2
                    signals.append(f"📍 Цена выше пивота, цель {nearest['resistance']:.2f}₽ +2")
                elif nearest['support'] and current_price < pivot_levels.pivot:
                    score -= 2
                    signals.append(f"📍 Цена ниже пивота, поддержка {nearest['support']:.2f}₽ -2")
            except Exception as e:
                debug(f"Pivot Points error: {e}")

        # ========== 9. VWAP ==========
        if self.use_vwap and candles and len(candles) >= 20:
            try:
                vwap_result = advanced_indicators.calculate_vwap(high, low, close, volumes)
                details['vwap'] = vwap_result['vwap']
                details['above_vwap'] = vwap_result['above']

                if vwap_result['above']:
                    score += 2
                    signals.append(f"💹 VWAP: цена выше справедливой (бычий) +2")
                else:
                    score -= 2
                    signals.append(f"💹 VWAP: цена ниже справедливой (медвежий) -2")
            except Exception as e:
                debug(f"VWAP error: {e}")

        # ========== 10. DONCHIAN CHANNEL ==========
        if self.use_donchian and len(close) >= 20:
            try:
                donchian = advanced_indicators.calculate_donchian(high, low, close, period=20)
                details['donchian_upper'] = donchian['upper']
                details['donchian_lower'] = donchian['lower']

                if current_price >= donchian['upper']:
                    score += 3
                    signals.append(f"📈 Donchian: пробой верхней границы +3")
                    buy_signal = True
                elif current_price <= donchian['lower']:
                    score -= 3
                    signals.append(f"📉 Donchian: пробой нижней границы -3")
                    sell_signal = True
            except Exception as e:
                debug(f"Donchian error: {e}")

        # ========== 11. OBV ==========
        if self.use_obv and len(close) >= 20:
            try:
                obv = advanced_indicators.calculate_obv(close, volumes)
                obv_trend = advanced_indicators.calculate_obv_trend(obv)
                details['obv_trend'] = obv_trend

                if obv_trend == 'up' and score > 0:
                    score += 2
                    signals.append(f"📊 OBV: восходящий тренд (подтверждает) +2")
                elif obv_trend == 'down' and score < 0:
                    score -= 2
                    signals.append(f"📊 OBV: нисходящий тренд (подтверждает) -2")
            except Exception as e:
                debug(f"OBV error: {e}")

        # ========== 12. AROON ==========
        if self.use_aroon and len(close) >= 25:
            try:
                aroon = advanced_indicators.calculate_aroon(high, low, period=25)
                details['aroon_up'] = aroon['up']
                details['aroon_down'] = aroon['down']

                if aroon['up'] > 70 and aroon['up'] > aroon['down']:
                    score += 2
                    signals.append(f"📈 Aroon Up={aroon['up']:.1f} (сильный бычий тренд) +2")
                elif aroon['down'] > 70 and aroon['down'] > aroon['up']:
                    score -= 2
                    signals.append(f"📉 Aroon Down={aroon['down']:.1f} (сильный медвежий тренд) -2")
            except Exception as e:
                debug(f"Aroon error: {e}")

        # ========== 13. MACD ==========
        if len(prices) >= 26:
            macd, signal, hist = self._calculate_macd_full(prices)
            details['macd'] = macd
            details['macd_hist'] = hist

            if hist > 0.1:
                score += 2
                signals.append(f"📈 MACD гистограмма={hist:.3f} (бычий) +2")
            elif hist < -0.1:
                score -= 2
                signals.append(f"📉 MACD гистограмма={hist:.3f} (медвежий) -2")

            if hist > 0 and len(signals) > 0 and 'macd' not in str(signals[-1]):
                score += 1
                signals.append(f"📈 MACD: бычье пересечение +1")

        # ========== 14. ОБЪЁМ ==========
        if len(volumes) >= 5:
            avg_volume = sum(volumes[-5:-1]) / 4 if len(volumes[-5:-1]) > 0 else volumes[-1]
            volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 1
            details['volume_ratio'] = volume_ratio

            if volume_ratio > 2.0:
                if score > 0:
                    score += 3
                    signals.append(f"📊 Объём {volume_ratio:.1f}x (сильное подтверждение) +3")
                elif score < 0:
                    score -= 2
                    signals.append(f"📊 Объём {volume_ratio:.1f}x (усиливает падение) -2")
            elif volume_ratio > 1.5:
                if score > 0:
                    score += 2
                    signals.append(f"📊 Объём {volume_ratio:.1f}x (подтверждает) +2")
                elif score < 0:
                    score -= 1
                    signals.append(f"📊 Объём {volume_ratio:.1f}x (усиливает) -1")

        # ========== 15. BOLLINGER BANDS ==========
        if len(prices) >= 20:
            bb_signal, bb_position = self._calculate_bollinger_advanced(prices)
            details['bb_position'] = bb_position

            if bb_signal == 'buy':
                score += 3
                signals.append(f"📊 Bollinger: цена у нижней полосы (потенциал роста) +3")
            elif bb_signal == 'sell':
                score -= 3
                signals.append(f"📊 Bollinger: цена у верхней полосы (потенциал падения) -3")
            elif bb_position < -1:
                score += 1
                signals.append(f"📊 Bollinger: цена ниже средней линии +1")

        # ========== 16. ATR ==========
        if candles and len(candles) >= 15:
            atr = self._calculate_atr(candles)
            atr_pct = (atr / current_price) * 100 if current_price > 0 else 1.0
            details['atr_pct'] = atr_pct

            if atr_pct > 2.5 and score > 2:
                score += 2
                signals.append(f"⚡ ATR={atr_pct:.1f}% (высокая волатильность) +2")
            elif atr_pct < 0.5:
                signals.append(f"💤 ATR={atr_pct:.1f}% (низкая волатильность)")

        # ========== 17. ТРЕНДОВЫЕ МА ==========
        if len(prices) >= 50:
            ma20 = sum(prices[-20:]) / 20
            ma50 = sum(prices[-50:]) / 50

            if current_price > ma20 > ma50:
                score += 2
                signals.append(f"📈 MA20 > MA50: восходящий тренд +2")
            elif current_price < ma20 < ma50:
                score -= 2
                signals.append(f"📉 MA20 < MA50: нисходящий тренд -2")

        # ========== МУЛЬТИТАЙМФРЕЙМНЫЙ АНАЛИЗ ==========
        mtf_score, mtf_details = self._analyze_multi_timeframe(
            candles_1min, candles_5min, candles, candles_1hour
        )

        if mtf_score != 0:
            mtf_signal_type = mtf_details.get('final', 'HOLD')
            mtf_confidence = mtf_details.get('confidence', 0)

            info(f"   📊 MTF влияние: {mtf_score:+d} (сигнал: {mtf_signal_type}, уверенность: {mtf_confidence:.0f}%)")

            score += mtf_score
            if mtf_signal_type == "BUY":
                signals.append(f"📊 MTF: BUY ({mtf_confidence:.0f}%) +{mtf_score}")
            elif mtf_signal_type == "SELL":
                signals.append(f"📊 MTF: SELL ({mtf_confidence:.0f}%) {mtf_score}")

            if 'consensus' in mtf_details:
                if mtf_details['consensus'] == 'strong_buy':
                    signals.append(f"📊 MTF: ВСЕ ТАЙМФРЕЙМЫ BUY! +{self.mtf_consensus_bonus}")
                elif mtf_details['consensus'] == 'strong_sell':
                    signals.append(f"📊 MTF: ВСЕ ТАЙМФРЕЙМЫ SELL! -{self.mtf_consensus_bonus}")

        if mtf_details:
            details['multi_timeframe'] = mtf_details

        # ========== ФИНАЛЬНОЕ РЕШЕНИЕ ==========
        if 'atr_pct' in details:
            if details['atr_pct'] > 3 and abs(score) > 5:
                score = int(score * 0.8)
            elif details['atr_pct'] < 0.8 and abs(score) > 3:
                score = int(score * 1.2)

        final_buy = buy_signal and score >= self.score_threshold_long
        final_sell = sell_signal and score <= self.score_threshold_short

        if final_buy or final_sell:
            info(f"🎯 {name}: ФИНАЛЬНЫЙ СИГНАЛ - score={score}")
            info(
                f"   Детали: RSI={rsi:.1f}, ADX={details.get('adx', 0):.1f}, Vol={details.get('volume_ratio', 1):.1f}x")
            if 'supertrend' in details:
                info(f"   SuperTrend: {details['supertrend']}")
            if 'ichimoku' in details:
                info(f"   Ichimoku: cloud_green={details['ichimoku']['cloud_green']}")

        if final_buy:
            recommendation = f"🟢 BUY (score={score})"
            info(f"🚀 {name}: СИГНАЛ НА ПОКУПКУ! score={score} >= {self.score_threshold_long}")
        elif final_sell:
            recommendation = f"🔴 SHORT (score={score})"
            info(f"🔥 {name}: СИГНАЛ НА SHORT! score={score} <= {self.score_threshold_short}")
        else:
            recommendation = "⚪ HOLD"

        if (final_buy or final_sell) and mtf_score != 0:
            info(f"   📊 MTF: {mtf_details.get('final', 'HOLD')} "
                 f"(1m={mtf_details.get('1min', '-')}, "
                 f"5m={mtf_details.get('5min', '-')}, "
                 f"15m={mtf_details.get('15min', '-')}, "
                 f"1h={mtf_details.get('1hour', '-')})")

        return SignalResult(
            score=score,
            buy_signal=final_buy,
            sell_signal=final_sell,
            recommendation=recommendation,
            signals=signals[:20],
            rsi=rsi,
            macd=details.get('macd', 0),
            volume_ratio=volume_ratio if 'volume_ratio' in details else 1.0,
            take_profit_pct=self.take_profit_pct,  # ← ДОБАВИТЬ
            stop_loss_pct=self.stop_loss_pct  # ← ДОБАВИТЬ
        )

    def _analyze_multi_timeframe(self,
                                 candles_1min: Optional[List[Dict]],
                                 candles_5min: Optional[List[Dict]],
                                 candles_15min: Optional[List[Dict]],
                                 candles_1hour: Optional[List[Dict]]) -> Tuple[int, Dict]:
        """
        Мультитаймфреймный анализ

        Args:
            candles_1min: Свечи 1-минутного таймфрейма
            candles_5min: Свечи 5-минутного таймфрейма
            candles_15min: Свечи 15-минутного таймфрейма
            candles_1hour: Свечи 1-часового таймфрейма

        Returns:
            Tuple: (score, details)
        """
        if not self.use_multi_timeframe:
            return 0, {}

        # Проверяем наличие advanced_strategy
        try:
            from trading_bot.analysis.advanced_strategy import multi_timeframe_analyzer
        except ImportError:
            debug("⚠️ advanced_strategy не доступен")
            return 0, {}

        # Собираем доступные таймфреймы
        candles_by_tf = {}
        if candles_1min and len(candles_1min) >= 20:
            candles_by_tf['1min'] = candles_1min
        if candles_5min and len(candles_5min) >= 20:
            candles_by_tf['5min'] = candles_5min
        if candles_15min and len(candles_15min) >= 20:
            candles_by_tf['15min'] = candles_15min
        if candles_1hour and len(candles_1hour) >= 20:
            candles_by_tf['1hour'] = candles_1hour

        if len(candles_by_tf) < 2:
            return 0, {}

        try:
            mtf_signal = multi_timeframe_analyzer.get_signal(candles_by_tf)

            details = {
                'final': mtf_signal.final_signal,
                'confidence': mtf_signal.confidence,
                '1min': mtf_signal.tf_1min,
                '5min': mtf_signal.tf_5min,
                '15min': mtf_signal.tf_15min,
                '1hour': mtf_signal.tf_1hour,
                'reasons': mtf_signal.reasons
            }

            # Если уверенность слишком низкая - не влияем
            if mtf_signal.confidence < self.mtf_min_confidence:
                debug(f"   📊 MTF: уверенность {mtf_signal.confidence:.0f}% < {self.mtf_min_confidence}% - пропускаем")
                return 0, details

            # Конвертируем сигнал в score
            if mtf_signal.final_signal == "BUY":
                score = min(self.mtf_boost, int(mtf_signal.confidence / 25))
            elif mtf_signal.final_signal == "SELL":
                score = -min(self.mtf_boost, int(mtf_signal.confidence / 25))
            else:
                score = 0

            # Бонус за согласованность таймфреймов
            if mtf_signal.tf_1min == mtf_signal.tf_5min == mtf_signal.tf_15min == "BUY":
                score += self.mtf_consensus_bonus
                details['consensus'] = 'strong_buy'
                debug(f"   📊 MTF: ВСЕ ТАЙМФРЕЙМЫ BUY! +{self.mtf_consensus_bonus}")
            elif mtf_signal.tf_1min == mtf_signal.tf_5min == mtf_signal.tf_15min == "SELL":
                score -= self.mtf_consensus_bonus
                details['consensus'] = 'strong_sell'
                debug(f"   📊 MTF: ВСЕ ТАЙМФРЕЙМЫ SELL! -{self.mtf_consensus_bonus}")

            # Штраф за разнонаправленные сигналы
            signals_list = [mtf_signal.tf_1min, mtf_signal.tf_5min, mtf_signal.tf_15min]
            if len(set(signals_list)) == 3:  # Все три разные
                score = int(score * 0.5)
                details['conflict'] = True
                debug(f"   📊 MTF: конфликт сигналов - уменьшаем влияние на 50%")

            self._last_mtf_details = details
            return score, details

        except Exception as e:
            debug(f"MultiTimeframe error: {e}")
            return 0, {}

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Расчёт RSI"""
        if len(prices) < period + 1:
            return 50.0

        gains = []
        losses = []

        for i in range(1, len(prices)):
            change = prices[i] - prices[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        if len(gains) < period:
            return 50.0

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return round(rsi, 1)

    def _calculate_macd(self, prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> float:
        """
        Расчёт MACD (Moving Average Convergence Divergence)

        Args:
            prices: Список цен
            fast: Быстрый период (по умолчанию 12)
            slow: Медленный период (по умолчанию 26)
            signal: Сигнальный период (по умолчанию 9)

        Returns:
            Значение MACD (разница между быстрой и медленной EMA)
        """
        if len(prices) < slow:
            return 0.0

        def ema(data, period):
            if len(data) < period:
                return data[-1] if data else 0
            multiplier = 2 / (period + 1)
            ema_value = data[0]
            for price in data[1:]:
                ema_value = (price - ema_value) * multiplier + ema_value
            return ema_value

        ema_fast = ema(prices, fast)
        ema_slow = ema(prices, slow)
        macd_value = ema_fast - ema_slow

        return round(macd_value, 4)

    def _calculate_bollinger(self, prices: List[float], period: int = 20, std_devs: float = 2.0) -> float:
        """
        Расчёт сигнала Bollinger Bands (упрощённый для обратной совместимости)

        Args:
            prices: Список цен
            period: Период расчёта
            std_devs: Количество стандартных отклонений

        Returns:
            float: Значение MACD (разница между быстрой и медленной EMA)
        """
        bb_signal, bb_position = self._calculate_bollinger_advanced(prices, period, std_devs)

        # Возвращаем значение для обратной совместимости
        if bb_signal == 'buy':
            return -2.0  # Цена у нижней полосы
        elif bb_signal == 'sell':
            return 2.0  # Цена у верхней полосы
        else:
            return bb_position  # Позиция относительно полос (-1, 0, 1)

    def _calculate_macd_full(self, prices: List[float]) -> tuple:
        """Расчёт MACD с сигнальной линией и гистограммой"""
        if len(prices) < 26:
            return 0.0, 0.0, 0.0

        def ema(data, period):
            multiplier = 2 / (period + 1)
            ema_value = data[0]
            for price in data[1:]:
                ema_value = (price - ema_value) * multiplier + ema_value
            return ema_value

        ema12 = ema(prices, 12)
        ema26 = ema(prices, 26)
        macd = ema12 - ema26

        # Сигнальная линия (EMA 9 от MACD)
        # Для простоты используем текущее значение
        signal = macd * 0.8  # Приближённо
        hist = macd - signal

        return macd, signal, hist

    def _calculate_bollinger_advanced(self, prices: List[float], period: int = 20, std_devs: float = 2.0) -> tuple:
        """Расчёт полос Боллинджера с позицией"""
        if len(prices) < period:
            return 'neutral', 0

        recent = prices[-period:]
        ma = sum(recent) / period
        std = np.std(recent)

        upper = ma + (std * std_devs)
        lower = ma - (std * std_devs)
        current = prices[-1]

        # Позиция относительно полос (-2 до +2)
        if current <= lower:
            return 'buy', -2
        elif current >= upper:
            return 'sell', 2
        elif current < ma - std:
            return 'neutral', -1
        elif current > ma + std:
            return 'neutral', 1
        return 'neutral', 0

    def _calculate_atr(self, candles: List, period: int = 14) -> float:
        """Расчёт ATR (Average True Range) - универсальный"""
        if len(candles) < period + 1:
            return 0.0

        def get_value(candle, attr, default=0):
            if hasattr(candle, attr):
                return getattr(candle, attr)
            elif isinstance(candle, dict):
                return candle.get(attr, default)
            elif isinstance(candle, (list, tuple)):
                if attr == 'high' and len(candle) > 3:
                    return candle[3]
                elif attr == 'low' and len(candle) > 4:
                    return candle[4]
                elif attr == 'close' and len(candle) > 0:
                    return candle[0]
            return default

        tr_list = []
        for i in range(1, len(candles)):
            high = get_value(candles[i], 'high', get_value(candles[i], 'close', 0))
            low = get_value(candles[i], 'low', get_value(candles[i], 'close', 0))
            prev_close = get_value(candles[i - 1], 'close', 0)

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        if not tr_list:
            return 0.0

        return sum(tr_list[-period:]) / period


def create_strategy_engine(capital: float, market_volatility: float = 0.008) -> StrategyEngine:
    """
    Создаёт стратегию с адаптивными параметрами под капитал
    ПОЛНОСТЬЮ АВТОМАТИЧЕСКАЯ НАСТРОЙКА
    """
    from trading_bot.core.settings_manager import settings_manager

    info("=" * 60)
    info("🔧 Создание ПРОДАКШН StrategyEngine")
    info(f"   💰 Капитал: {capital:.0f}₽")
    info("=" * 60)

    # ========== 1. ПОЛУЧАЕМ НАСТРОЙКИ ==========
    aggressiveness = settings_manager.get('aggressiveness', 5)
    use_short_from_settings = settings_manager.get('use_short', False)

    # ========== 2. ОПРЕДЕЛЯЕМ РЕАЛЬНЫЙ СТАТУС SHORT ==========
    use_short = use_short_from_settings

    if use_short and capital < 7000:
        use_short = False
        info(f"   ⚠️ SHORT отключён: капитал {capital:.0f}₽ < 7000₽")

    # ========== 3. ОПРЕДЕЛЯЕМ ПОРОГИ ==========
    score_threshold_long = settings_manager.get('score_threshold_long', 2)

    if use_short:
        score_threshold_short = settings_manager.get('score_threshold_short', -2)
        # ✅ ОГРАНИЧЕНИЕ: порог не мягче -2
        if score_threshold_short > -2:
            score_threshold_short = -2
            info(f"   📊 SHORT порог скорректирован до -2 (не может быть мягче)")
    else:
        score_threshold_short = -999
        info(f"   📊 SHORT отключён")

    # ========== 4. АДАПТАЦИЯ ПОД КАПИТАЛ ==========
    take_profit_pct = 1.0
    stop_loss_pct = 0.5
    trailing_stop_pct = 0.4
    max_hold_minutes = 20

    if capital < 5000:
        score_threshold_long = max(score_threshold_long, 5)  # 4→5
        use_short = False
        score_threshold_short = -999
        take_profit_pct = 0.8
        stop_loss_pct = 0.4
        max_hold_minutes = 15
        info(f"   📊 МИКРО-КАПИТАЛ: консервативная стратегия, SHORT отключён")

    elif capital < 15000:
        score_threshold_long = max(score_threshold_long, 4)  # 3→4
        if use_short:
            score_threshold_short = max(score_threshold_short, -4)  # -3→-4
        take_profit_pct = 1.0
        stop_loss_pct = 0.5
        max_hold_minutes = 20
        info(f"   📊 МАЛЫЙ КАПИТАЛ: умеренная стратегия")

    elif capital < 50000:
        score_threshold_long = max(score_threshold_long, 3)  # 2→3
        if use_short:
            score_threshold_short = max(score_threshold_short, -3)  # -2→-3
        take_profit_pct = 1.2
        stop_loss_pct = 0.6
        max_hold_minutes = 25
        info(f"   📊 СРЕДНИЙ КАПИТАЛ: стандартная стратегия")

    else:
        score_threshold_long = max(2, score_threshold_long)  # 1→2
        if use_short:
            score_threshold_short = max(score_threshold_short, -2)
        take_profit_pct = 1.5
        stop_loss_pct = 0.8
        max_hold_minutes = 30
        info(f"   📊 КРУПНЫЙ КАПИТАЛ: агрессивная стратегия")

    # ========== 5. КОНФИГУРАЦИЯ ==========
    config_dict = {
        'rsi_period': 7,
        'score_threshold_long': score_threshold_long,
        'score_threshold_short': score_threshold_short,
        'take_profit_pct': take_profit_pct,
        'stop_loss_pct': stop_loss_pct,
        'trailing_stop_pct': trailing_stop_pct,
        'max_hold_minutes': max_hold_minutes,
        'use_supertrend': True,
        'use_ichimoku': True,
        'use_dmi_adx': True,
        'use_stochastic': True,
        'use_cci': True,
        'use_psar': True,
        'use_pivots': True,
        'use_vwap': True,
        'use_donchian': True,
        'use_obv': True,
        'use_aroon': True,
    }

    # ========== 6. ФИНАЛЬНЫЙ ВЫВОД ==========
    info("=" * 60)
    info("📊 ИТОГОВЫЕ ПАРАМЕТРЫ StrategyEngine:")
    info(f"   🎯 LONG порог: score ≥ {score_threshold_long}")
    if use_short:
        info(f"   🎯 SHORT порог: score ≤ {score_threshold_short}")
    else:
        info(f"   🎯 SHORT порог: ❌ ОТКЛЮЧЁН")
    info(f"   🎯 Тейк-профит: +{take_profit_pct:.1f}%")
    info(f"   🎯 Стоп-лосс: -{stop_loss_pct:.1f}%")
    info(f"   🎯 Трейлинг-стоп: {trailing_stop_pct:.1f}%")
    info(f"   ⏰ Макс. время удержания: {max_hold_minutes} мин")
    info(f"   🔻 SHORT статус: {'✅ ВКЛЮЧЁН' if use_short else '❌ ВЫКЛЮЧЕН'}")
    info("=" * 60)

    return StrategyEngine(config_dict)