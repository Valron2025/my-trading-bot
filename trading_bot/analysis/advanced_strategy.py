#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdvancedStrategy.py - ПРОДАКШЕН-РЕАЛИЗАЦИЯ
Мультитаймфреймный анализ, Volume Profile, Grid Trading, Volatility Harvesting
БЕЗ AI - только математика
"""

from typing import List, Dict, Any, Tuple, Optional, Union
from dataclasses import dataclass
from collections import defaultdict, deque


@dataclass
class MultiTimeframeSignal:
    """Сигнал с мультитаймфреймного анализа"""
    tf_1min: str
    tf_5min: str
    tf_15min: str
    tf_1hour: str
    final_signal: str
    confidence: float
    reasons: List[str]


class MultiTimeframeAnalyzer:
    """Мультитаймфреймный анализатор"""

    def __init__(self):
        self.tf_weights = {"1min": 1.0, "5min": 1.5, "15min": 2.0, "1hour": 2.5}

    def analyze_trend(self, prices: List[float]) -> Tuple[str, float]:
        if len(prices) < 20:
            return "HOLD", 0
        ma_short = sum(prices[-5:]) / 5
        ma_medium = sum(prices[-10:]) / 10
        ma_long = sum(prices[-20:]) / 20
        if ma_short > ma_medium > ma_long:
            return "BUY", min(100, (ma_short / ma_long - 1) * 100)
        elif ma_short < ma_medium < ma_long:
            return "SELL", min(100, (ma_long / ma_short - 1) * 100)
        return "HOLD", 0

    def get_signal(self, candles_by_tf: Dict[str, List[Dict]]) -> MultiTimeframeSignal:
        signals = {}
        strengths = {}
        reasons = []

        for tf, candles in candles_by_tf.items():
            if not candles or len(candles) < 20:
                signals[tf] = "HOLD"
                strengths[tf] = 0
                continue
            prices = [c['close'] for c in candles]
            trend_signal, trend_strength = self.analyze_trend(prices)
            signals[tf] = trend_signal
            strengths[tf] = trend_strength
            reasons.append(f"{tf}: {trend_signal} ({trend_strength:.0f})")

        total_buy = 0
        total_sell = 0
        total_weight = 0

        for tf, signal in signals.items():
            weight = self.tf_weights.get(tf, 1)
            total_weight += weight
            if signal == "BUY":
                total_buy += weight * (strengths[tf] / 100)
            elif signal == "SELL":
                total_sell += weight * (strengths[tf] / 100)

        buy_score = (total_buy / total_weight) * 100 if total_weight > 0 else 0
        sell_score = (total_sell / total_weight) * 100 if total_weight > 0 else 0

        if buy_score > sell_score + 20:
            final_signal, confidence = "BUY", buy_score
        elif sell_score > buy_score + 20:
            final_signal, confidence = "SELL", sell_score
        else:
            final_signal, confidence = "HOLD", max(buy_score, sell_score)

        return MultiTimeframeSignal(
            tf_1min=signals.get("1min", "HOLD"),
            tf_5min=signals.get("5min", "HOLD"),
            tf_15min=signals.get("15min", "HOLD"),
            tf_1hour=signals.get("1hour", "HOLD"),
            final_signal=final_signal,
            confidence=confidence,
            reasons=reasons
        )


@dataclass
class VolumeProfilePoint:
    price: float
    volume: float
    is_poc: bool


class VolumeProfileAnalyzer:
    def __init__(self, price_buckets: int = 50):
        self.price_buckets = price_buckets

    def calculate_profile(self, candles: List[Dict]) -> Tuple[List[VolumeProfilePoint], float, float]:
        if not candles:
            return [], 0, 0
        all_prices = []
        all_volumes = []
        for c in candles:
            all_prices.extend([c.get('high', c['close']), c.get('low', c['close']), c['close']])
            all_volumes.extend([c.get('volume', 0)] * 3)
        min_p, max_p = min(all_prices), max(all_prices)
        if min_p == max_p:
            return [], min_p, max_p
        bucket_size = (max_p - min_p) / self.price_buckets
        vol_by_price = defaultdict(float)
        for i, p in enumerate(all_prices):
            idx = min(int((p - min_p) / bucket_size), self.price_buckets - 1)
            vol_by_price[idx] += all_volumes[i]
        max_bucket = max(vol_by_price.items(), key=lambda x: x[1]) if vol_by_price else (0, 0)
        poc_price = min_p + (max_bucket[0] + 0.5) * bucket_size if max_bucket[0] > 0 else min_p
        points = []
        for idx, vol in vol_by_price.items():
            points.append(
                VolumeProfilePoint(price=min_p + (idx + 0.5) * bucket_size, volume=vol, is_poc=idx == max_bucket[0]))
        sorted_points = sorted(points, key=lambda x: x.volume, reverse=True)
        support = sorted_points[0].price if len(sorted_points) > 0 else min_p
        resistance = sorted_points[0].price if len(sorted_points) > 0 else max_p
        if len(sorted_points) > 1:
            support = min(support, sorted_points[1].price)
            resistance = max(resistance, sorted_points[1].price)
        return points, support, resistance

    def get_key_levels(self, current_price: float, profile_points: List[VolumeProfilePoint]) -> Dict[str, float]:
        if not profile_points:
            return {"next_support": current_price * 0.98, "next_resistance": current_price * 1.02}
        sorted_points = sorted(profile_points, key=lambda x: x.price)
        next_support, next_resistance = None, None
        for p in sorted_points:
            if p.price < current_price:
                next_support = p.price
            elif p.price > current_price and next_resistance is None:
                next_resistance = p.price
        return {
            "next_support": next_support if next_support else current_price * 0.98,
            "next_resistance": next_resistance if next_resistance else current_price * 1.02
        }


class VolatilityHarvester:
    def __init__(self):
        self.volatility_history = deque(maxlen=100)

    def calculate_volatility(self, prices: List[float]) -> float:
        if len(prices) < 2:
            return 0
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append(abs((prices[i] - prices[i - 1]) / prices[i - 1]))
        vol = np.mean(returns) * 100 if returns else 0
        self.volatility_history.append(vol)
        return vol

    def get_entry_signal(self, prices: List[float], current_volatility: float) -> Tuple[str, float]:
        if len(prices) < 20:
            return "HOLD", 0
        ma = sum(prices[-20:]) / 20
        std = np.std(prices[-20:])
        upper, lower = ma + std * 2, ma - std * 2
        current = prices[-1]
        if current_volatility > 1.5:
            if current <= lower:
                return "BUY", min(100, (lower - current) / lower * 100)
            elif current >= upper:
                return "SELL", min(100, (current - upper) / upper * 100)
        return "HOLD", 0


class EnhancedLevelFinder:
    def __init__(self, lookback_candles: int = 100):
        self.lookback_candles = lookback_candles

    def find_levels(self, candles: List[Dict]) -> Tuple[List[Union[float, Tuple[float, int]]], List[Union[float, Tuple[float, int]]]]:
        """Нахождение уровней поддержки и сопротивления"""
        if len(candles) < 30:
            return [], []

        candles_limited = candles[-self.lookback_candles:]
        highs, lows = [], []

        # Поиск локальных экстремумов
        for i in range(2, len(candles_limited) - 2):
            h = candles_limited[i].get('high', candles_limited[i]['close'])
            l = candles_limited[i].get('low', candles_limited[i]['close'])

            # Вершина - выше соседей
            if (h > candles_limited[i - 1].get('high', 0) and
                    h > candles_limited[i - 2].get('high', 0) and
                    h > candles_limited[i + 1].get('high', 0) and
                    h > candles_limited[i + 2].get('high', 0)):
                highs.append(h)

            # Впадина - ниже соседей
            if (l < candles_limited[i - 1].get('low', float('inf')) and
                    l < candles_limited[i - 2].get('low', float('inf')) and
                    l < candles_limited[i + 1].get('low', float('inf')) and
                    l < candles_limited[i + 2].get('low', float('inf'))):
                lows.append(l)

        # Кластеризация близких уровней
        supports = self._cluster(lows, tolerance=0.3)
        resistances = self._cluster(highs, tolerance=0.3)

        return supports, resistances

    def find_round_levels(self, current_price: float) -> Dict[str, float]:
        """
        Поиск круглых уровней (книга, Глава 6)
        Психологические уровни: 100, 150, 200, 250 рублей и т.д.
        """
        step = 50 if current_price > 200 else (25 if current_price > 100 else 10)

        nearest_below = (int(current_price / step) * step)
        nearest_above = nearest_below + step

        return {
            'round_support': nearest_below,
            'round_resistance': nearest_above,
            'step': step
        }

    def _cluster(self, prices: List[float], tolerance: float = 0.5) -> List[float]:
        if not prices:
            return []
        sorted_prices = sorted(prices)
        clusters, current = [], [sorted_prices[0]]
        for p in sorted_prices[1:]:
            if abs(p - current[-1]) / current[-1] * 100 < tolerance:
                current.append(p)
            else:
                clusters.append(sum(current) / len(current))
                current = [p]
        if current:
            clusters.append(sum(current) / len(current))
        return clusters

    def get_nearest_levels(self, price: float, supports: List[float], resistances: List[float]) -> Dict[str, float]:
        nearest_support = max([s for s in supports if s < price], default=None) if supports else None
        nearest_resistance = min([r for r in resistances if r > price], default=None) if resistances else None
        return {
            "support": nearest_support if nearest_support else price * 0.98,
            "resistance": nearest_resistance if nearest_resistance else price * 1.02
        }


class SignalAggregator:
    def __init__(self):
        self.weights = {
            "multi_timeframe": 0.35,
            "volume_profile": 0.20,
            "volatility_harvester": 0.20,
            "levels": 0.15,
            "technical": 0.10
        }

    def aggregate(self, mtf_signal: MultiTimeframeSignal,
                  volume_profile_signal: Tuple[str, float],
                  volatility_signal: Tuple[str, float],
                  levels_signal: Tuple[str, float],
                  technical_signal: Tuple[str, float]) -> Dict[str, Any]:
        signals = {
            "multi_timeframe": (mtf_signal.final_signal, mtf_signal.confidence),
            "volume_profile": volume_profile_signal,
            "volatility_harvester": volatility_signal,
            "levels": levels_signal,
            "technical": technical_signal
        }
        buy_score, sell_score, total_weight = 0, 0, 0
        details = {}
        for name, (signal, strength) in signals.items():
            weight = self.weights.get(name, 0.15)
            total_weight += weight
            if signal == "BUY":
                buy_score += weight * (strength / 100)
                details[name] = f"BUY ({strength:.0f}%)"
            elif signal == "SELL":
                sell_score += weight * (strength / 100)
                details[name] = f"SELL ({strength:.0f}%)"
            else:
                details[name] = "HOLD"
        buy_pct = (buy_score / total_weight) * 100 if total_weight > 0 else 0
        sell_pct = (sell_score / total_weight) * 100 if total_weight > 0 else 0
        if buy_pct > sell_pct + 20:
            return {"action": "BUY", "confidence": min(100, buy_pct), "buy_score": buy_pct, "sell_score": sell_pct,
                    "details": details}
        elif sell_pct > buy_pct + 20:
            return {"action": "SELL", "confidence": min(100, sell_pct), "buy_score": buy_pct, "sell_score": sell_pct,
                    "details": details}
        return {"action": "HOLD", "confidence": max(buy_pct, sell_pct), "buy_score": buy_pct, "sell_score": sell_pct,
                "details": details}


class ElliottWaveAnalyzer:
    """
    Волновой анализ Эллиотта (Глава 7 книги)
    """

    def __init__(self):
        self.min_wave_size = 0.01

    def find_waves(self, prices: List[float]) -> List[Dict]:
        """
        Поиск волн на графике
        """
        if len(prices) < 10:
            return []

        waves = []
        direction = None
        wave_start = 0
        wave_start_price = prices[0]

        for i in range(1, len(prices)):
            change = (prices[i] - prices[i - 1]) / prices[i - 1] * 100

            if direction is None:
                direction = 'up' if change > 0 else 'down'
                continue

            if (direction == 'up' and change < 0) or (direction == 'down' and change > 0):
                wave_end = i - 1
                wave_pct = (prices[wave_end] - wave_start_price) / wave_start_price * 100

                if abs(wave_pct) >= self.min_wave_size:
                    waves.append({
                        'direction': direction,
                        'start_idx': wave_start,
                        'end_idx': wave_end,
                        'start_price': wave_start_price,
                        'end_price': prices[wave_end],
                        'change_pct': wave_pct
                    })

                wave_start = i - 1
                wave_start_price = prices[wave_start]
                direction = 'down' if direction == 'up' else 'up'

        return waves

    def identify_impulse(self, waves: List[Dict]) -> Optional[Dict]:
        """Поиск импульсной волны (5 подволн в направлении тренда)"""
        if len(waves) < 5:
            return None

        for i in range(len(waves) - 4):
            if (waves[i]['direction'] == waves[i + 2]['direction'] == waves[i + 4]['direction'] and
                    waves[i + 1]['direction'] != waves[i]['direction'] and
                    waves[i + 3]['direction'] != waves[i]['direction']):

                wave1_pct = abs(waves[i]['change_pct'])
                wave3_pct = abs(waves[i + 2]['change_pct'])
                wave5_pct = abs(waves[i + 4]['change_pct'])

                if wave3_pct > wave1_pct and wave3_pct > wave5_pct:
                    return {
                        'type': 'IMPULSE',
                        'start_idx': waves[i]['start_idx'],
                        'end_idx': waves[i + 4]['end_idx'],
                        'waves': waves[i:i + 5]
                    }

        return None


# Глобальные экземпляры
multi_timeframe_analyzer = MultiTimeframeAnalyzer()
volume_profile_analyzer = VolumeProfileAnalyzer()
volatility_harvester = VolatilityHarvester()
enhanced_level_finder = EnhancedLevelFinder()
signal_aggregator = SignalAggregator()
elliott_wave_analyzer = ElliottWaveAnalyzer()