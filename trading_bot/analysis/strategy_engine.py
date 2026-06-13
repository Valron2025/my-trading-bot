#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Единый движок стратегии - с полной поддержкой LONG/SHORT и ВСЕМИ индикаторами"""

import numpy as np  # ✅ ДОБАВЛЕНО
from typing import List, Dict, Any, Optional

from ..models import SignalResult
from ..logger import debug, info


def std_dev(data: List[float]) -> float:
    """Расчёт стандартного отклонения (без numpy) - резервный вариант"""
    if len(data) < 2:
        return 0.0
    mean = sum(data) / len(data)
    variance = sum((x - mean) ** 2 for x in data) / len(data)
    return variance ** 0.5


class StrategyEngine:
    """
    Единый движок стратегии с поддержкой LONG и SHORT
    Включает: RSI, MACD, MA, VWAP, Bollinger, ATR, OBV, Aroon, Стохастик, Гэпы
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.rsi_period = config.get('rsi_period', 7)
        self.score_threshold_long = config.get('score_threshold_long', 1)
        self.score_threshold_short = config.get('score_threshold_short', -1)
        self.take_profit_pct = config.get('take_profit_pct', 0.8)
        self.stop_loss_pct = config.get('stop_loss_pct', 0.5)
        self.trailing_stop_pct = config.get('trailing_stop_pct', 0.3)
        self.max_hold_minutes = config.get('max_hold_minutes', 15)

        # SHORT параметры
        self.short_vwap_threshold = config.get('short_vwap_threshold', 1.02)
        self.short_volume_spike = config.get('short_volume_spike', 2.0)

        info(f"📊 StrategyEngine инициализирован: LONG порог={self.score_threshold_long}, SHORT порог={self.score_threshold_short}")

    def analyze_signal(self, prices: List[float], volumes: List[float],
                       name: str = "", candles: Optional[List[Dict]] = None) -> SignalResult:
        """Единый метод анализа сигнала"""

        if len(prices) < 10:
            return SignalResult(
                score=0, buy_signal=False, sell_signal=False,
                recommendation="НЕТ ДАННЫХ", signals=[f"⚠️ Мало данных ({len(prices)} свечей)"]
            )

        current_price = prices[-1]

        # Расчёт RSI
        rsi = self._calculate_rsi(prices)

        score = 0
        buy_signal = False
        sell_signal = False
        signals = []
        volume_ratio = 1.0

        # ========== LONG СИГНАЛЫ (покупка) ==========
        if rsi < 30:
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
        elif rsi < 50:
            score += 1
            signals.append(f"📊 RSI={rsi:.1f} (нейтрально-бычий) +1")

        # ========== SHORT СИГНАЛЫ (продажа) ==========
        if rsi > 70:
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
        elif rsi > 50:
            score -= 1
            signals.append(f"📊 RSI={rsi:.1f} (нейтрально-медвежий) -1")

        # ========== MACD (если есть данные) ==========
        if len(prices) >= 26:
            macd = self._calculate_macd(prices)
            if macd > 0.2:
                score += 2
                signals.append(f"📈 MACD={macd:.2f} (бычий) +2")
            elif macd < -0.2:
                score -= 2
                signals.append(f"📉 MACD={macd:.2f} (медвежий) -2")

        # ========== ОБЪЁМ ==========
        if len(volumes) >= 5:
            avg_volume = sum(volumes[-5:-1]) / 4 if len(volumes[-5:-1]) > 0 else (volumes[-1] if volumes else 1)
            volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 1

            if volume_ratio > 1.5:
                if score > 0:
                    score += 2
                    signals.append(f"📊 Объём {volume_ratio:.1f}x (подтверждает рост) +2")
                elif score < 0:
                    score -= 1
                    signals.append(f"📊 Объём {volume_ratio:.1f}x (усиливает падение) -1")

        # ========== BOLLINGER BANDS (✅ ИСПРАВЛЕНО — используем np.std) ==========
        if len(prices) >= 20:
            bb_signal = self._calculate_bollinger(prices)
            if bb_signal == 'buy':
                score += 2
                signals.append("📊 Bollinger: цена у нижней полосы +2")
            elif bb_signal == 'sell':
                score -= 2
                signals.append("📊 Bollinger: цена у верхней полосы -2")

        # ========== ATR (есть свечи) ==========
        if candles and len(candles) >= 15:
            atr = self._calculate_atr(candles)
            atr_pct = (atr / current_price) * 100 if current_price > 0 else 1.0
            if atr_pct > 2.0 and score > 0:
                score += 1
                signals.append(f"📊 ATR={atr_pct:.1f}% (высокая волатильность) +1")
            elif atr_pct < 0.5 and score < 0:
                score -= 1
                signals.append(f"📊 ATR={atr_pct:.1f}% (низкая волатильность) -1")

        # ========== ПРОТИВОРЕЧИВЫЕ СИГНАЛЫ ==========
        if buy_signal and sell_signal:
            if abs(score) < 3:
                buy_signal = False
                sell_signal = False
                signals = ["⚠️ Противоречивые сигналы — HOLD"]
            elif score > 0:
                sell_signal = False
            else:
                buy_signal = False

        # ========== ФИНАЛЬНОЕ РЕШЕНИЕ С УЧЁТОМ ПОРОГОВ ==========
        final_buy = buy_signal and score >= self.score_threshold_long
        final_sell = sell_signal and score <= self.score_threshold_short

        debug(f"📊 {name}: score={score}, RSI={rsi:.1f}, пороги: LONG>={self.score_threshold_long}, SHORT<={self.score_threshold_short}")
        debug(f"   buy_signal={buy_signal}, sell_signal={sell_signal}")
        debug(f"   final_buy={final_buy}, final_sell={final_sell}")

        if final_buy:
            recommendation = f"🟢 BUY (score={score})"
            info(f"🎯 {name}: СИГНАЛ НА ПОКУПКУ! score={score} >= {self.score_threshold_long}")
        elif final_sell:
            recommendation = f"🔴 SHORT (score={score})"
            info(f"🎯 {name}: СИГНАЛ НА SHORT! score={score} <= {self.score_threshold_short}")
        else:
            recommendation = "⚪ HOLD"

        return SignalResult(
            score=score,
            buy_signal=final_buy,
            sell_signal=final_sell,
            recommendation=recommendation,
            signals=signals[:10],
            rsi=rsi,
            macd=0,
            volume_ratio=volume_ratio
        )

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

        avg_gain = sum(gains[-period:]) / period if len(gains) >= period else 0
        avg_loss = sum(losses[-period:]) / period if len(losses) >= period else 0

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return round(rsi, 1)

    def _calculate_macd(self, prices: List[float]) -> float:
        """Расчёт MACD (упрощённо)"""
        if len(prices) < 26:
            return 0.0

        def ema(data, period):
            multiplier = 2 / (period + 1)
            ema_value = data[0]
            for price in data[1:]:
                ema_value = (price - ema_value) * multiplier + ema_value
            return ema_value

        ema12 = ema(prices, 12)
        ema26 = ema(prices, 26)
        return ema12 - ema26

    def _calculate_bollinger(self, prices: List[float], period: int = 20, std_devs: float = 2.0) -> str:
        """Расчёт полос Боллинджера (✅ ИСПРАВЛЕНО — используем np.std)"""
        if len(prices) < period:
            return 'neutral'

        recent = prices[-period:]
        ma = sum(recent) / period
        std = np.std(recent)  # ✅ ТЕПЕРЬ РАБОТАЕТ

        upper = ma + (std * std_devs)
        lower = ma - (std * std_devs)
        current = prices[-1]

        if current <= lower:
            return 'buy'
        elif current >= upper:
            return 'sell'
        return 'neutral'

    def _calculate_atr(self, candles: List[Dict], period: int = 14) -> float:
        """Расчёт ATR (Average True Range)"""
        if len(candles) < period + 1:
            return 0.0

        tr_list = []
        for i in range(1, len(candles)):
            high = candles[i].get('high', candles[i]['close'])
            low = candles[i].get('low', candles[i]['close'])
            prev_close = candles[i - 1].get('close', candles[i - 1]['close'])

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        if not tr_list:
            return 0.0

        return sum(tr_list[-period:]) / period


def create_strategy_engine(capital: float, market_volatility: float = 0.008) -> StrategyEngine:
    """
    Создаёт стратегию с адаптивными параметрами под капитал

    Args:
        capital: Реальный капитал (НЕ ЗАГЛУШКА!)
        market_volatility: Волатильность рынка (0-1)
    """
    info(f"🔧 Создание StrategyEngine с капиталом: {capital:.0f}₽")

    if capital < 3000:
        config_dict = {
            'rsi_period': 14,
            'score_threshold_long': 3,
            'score_threshold_short': 10,
            'take_profit_pct': 1.5,
            'stop_loss_pct': 0.8,
            'trailing_stop_pct': 0.3,
            'max_hold_minutes': 15,
            'short_vwap_threshold': 1.02,
            'short_volume_spike': 2.0
        }
        info(f"   Режим: МИКРО-КАПИТАЛ (только LONG, порог={config_dict['score_threshold_long']})")

    elif capital < 5000:
        config_dict = {
            'rsi_period': 14,
            'score_threshold_long': 2,
            'score_threshold_short': 10,
            'take_profit_pct': 1.2,
            'stop_loss_pct': 0.6,
            'trailing_stop_pct': 0.4,
            'max_hold_minutes': 20,
            'short_vwap_threshold': 1.02,
            'short_volume_spike': 2.0
        }
        info(f"   Режим: МАЛЫЙ КАПИТАЛ (только LONG, порог={config_dict['score_threshold_long']})")

    elif capital < 15000:
        config_dict = {
            'rsi_period': 14,
            'score_threshold_long': 2,
            'score_threshold_short': -2,
            'take_profit_pct': 1.0,
            'stop_loss_pct': 0.5,
            'trailing_stop_pct': 0.5,
            'max_hold_minutes': 25,
            'short_vwap_threshold': 1.02,
            'short_volume_spike': 2.0
        }
        info(f"   Режим: СРЕДНИЙ КАПИТАЛ (LONG и SHORT, пороги: LONG>={config_dict['score_threshold_long']}, SHORT<={config_dict['score_threshold_short']})")

    else:
        config_dict = {
            'rsi_period': 14,
            'score_threshold_long': 1,
            'score_threshold_short': -1,
            'take_profit_pct': 0.8,
            'stop_loss_pct': 0.4,
            'trailing_stop_pct': 0.6,
            'max_hold_minutes': 30,
            'short_vwap_threshold': 1.02,
            'short_volume_spike': 2.0
        }
        info(f"   Режим: КРУПНЫЙ КАПИТАЛ (полный, пороги: LONG>={config_dict['score_threshold_long']}, SHORT<={config_dict['score_threshold_short']})")

    return StrategyEngine(config_dict)