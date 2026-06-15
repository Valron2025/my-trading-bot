"""Модуль технического анализа - ПРОФЕССИОНАЛЬНАЯ ВЕРСИЯ"""

import asyncio
import nest_asyncio
nest_asyncio.apply()
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, time as dt_time, timedelta, timezone
import time
import numpy as np

from trading_bot.cache import TTLCache
from trading_bot.cache.unified_cache import UnifiedCache, USE_UNIFIED_CACHE
from ..models import StockAnalysis
from ..logger import success, warning, debug, info, error
from .strategy_engine import create_strategy_engine

try:
    from trading_bot.analysis.fundamental_analyzer import enhance_trading_decision
    FUNDAMENTAL_AVAILABLE = True
    info("✅ Фундаментальный анализатор загружен")
except ImportError as e:
    FUNDAMENTAL_AVAILABLE = False
    enhance_trading_decision = None
    warning(f"⚠️ Фундаментальный анализатор не загружен: {e}")

# ========== ЧАСОВОЙ ПОЯС ==========
MOSCOW_TZ = timezone(timedelta(hours=3))

# ========== КОНСТАНТЫ ==========
FUNDAMENTAL_TIMEOUT = 1.0
CANDLES_DAYS = 7
CANDLES_INTERVAL = 5

# ========== ДИНАМИЧЕСКИЕ НАСТРОЙКИ ==========
# Минимальное количество свечей для разных тикеров (обновляется из API)
_LOW_LIQUIDITY_TICKERS = set()  # Будет заполняться динамически


def _get_tbank():
    from ..api.tbank_client import tbank
    return tbank


try:
    from ..core.candle_sync_wrapper import get_candles_sync
    CANDLE_BUILDER_AVAILABLE = True
    debug("✅ CandleBuilder available (fallback)")
except ImportError as e:
    CANDLE_BUILDER_AVAILABLE = False
    debug(f"⚠️ CandleBuilder not available: {e}")

try:
    from ..core.moex_sync_fetcher import moex_sync
    MOEX_AVAILABLE = True
    debug("✅ MOEX sync fetcher available (fallback)")
except ImportError as e:
    MOEX_AVAILABLE = False
    debug(f"⚠️ MOEX sync fetcher not available: {e}")


class TechnicalAnalyzer:

    def __init__(self, engine=None, capital=None):
        self.rsi_period = 14
        self.volume_ratio_period = 5
        self.min_candles_required = 20
        self.atr_period = 14
        self.divergence_lookback = 10
        self.enabled = True
        self._analysis_cache = {}
        self._cache_ttl = 60

        from trading_bot.config import config
        from trading_bot.logger import info
        from .strategy_engine import create_strategy_engine

        if capital is None or capital == 0:
            capital = getattr(config, 'total_capital', 100000)
            if capital == 0:
                capital = 100000
                info(f"📊 TechnicalAnalyzer: используем {capital:.0f}₽")

        if engine:
            self.engine = engine
        else:
            self.engine = create_strategy_engine(capital)

        self._low_liquidity_cache = set()
        info(f"📊 TechnicalAnalyzer: min_candles_required={self.min_candles_required}")

    def _update_low_liquidity_tickers(self, ticker: str, is_low_liquidity: bool = True):
        """Динамическое обновление списка низколиквидных тикеров"""
        if is_low_liquidity:
            self._low_liquidity_cache.add(ticker.upper())
        elif ticker.upper() in self._low_liquidity_cache:
            self._low_liquidity_cache.discard(ticker.upper())

    def _get_min_candles_for_ticker(self, ticker: str) -> int:
        """Динамическое определение минимального количества свечей"""
        ticker_upper = ticker.upper()

        if ticker_upper in self._low_liquidity_cache:
            return 15

        try:
            from trading_bot.api.tbank_client import tbank
            figi = tbank._get_figi_by_ticker(ticker_upper)
            if figi:
                # Безопасная проверка - если метод не существует, просто пропускаем
                if hasattr(tbank, 'get_trading_volume'):
                    volume_data = tbank.get_trading_volume(figi, days=5)
                    if volume_data:
                        avg_volume = volume_data.get('avg_volume_rub', 0)
                        if avg_volume < 500000:
                            self._low_liquidity_cache.add(ticker_upper)
                            return 15
        except Exception as e:
            debug(f"Не удалось определить ликвидность {ticker}: {e}")

        return self.min_candles_required

    def _fetch_candles_moex(self, ticker: str, interval_minutes: int = 5, days: int = 7) -> List[Tuple[float, float]]:
        """Получение свечей из MOEX (FALLBACK)"""
        if not MOEX_AVAILABLE:
            return []

        try:
            for interval in [interval_minutes, interval_minutes * 2, interval_minutes * 3]:
                candles = moex_sync.get_candles(ticker, interval, days)
                if candles and len(candles) >= self._get_min_candles_for_ticker(ticker):
                    success(f"✅ MOEX fallback: {len(candles)} свечей для {ticker}")
                    return candles
        except Exception as e:
            debug(f"MOEX error for {ticker}: {e}")
        return []

    def _fetch_candles_candlebuilder(self, ticker: str, interval_minutes: int = 5, days: int = 7) -> List[Tuple[float, float]]:
        """Получение свечей из CandleBuilder (FALLBACK)"""
        if not CANDLE_BUILDER_AVAILABLE:
            return []

        try:
            candles = get_candles_sync(ticker, interval_minutes, days)
            if candles and len(candles) >= self._get_min_candles_for_ticker(ticker):
                success(f"✅ CandleBuilder fallback: {len(candles)} свечей для {ticker}")
                return candles
        except Exception as e:
            debug(f"CandleBuilder error for {ticker}: {e}")
        return []

    def fetch_candles(self, ticker: str, interval_minutes: int = None, days: int = CANDLES_DAYS) -> List[Tuple[float, float]]:
        """Получение свечей с приоритетом T-Invest API"""
        ticker = ticker.upper()
        start_time = time.time()

        if interval_minutes is None:
            interval_minutes = 5

        try:
            from trading_bot.api.tbank_client import tbank
            figi = tbank._get_figi_by_ticker(ticker)
            if not figi:
                return []

            min_candles_needed = self._get_min_candles_for_ticker(ticker)
            actual_days = days

            for attempt in range(3):
                candles = tbank.get_candles(figi, days=actual_days, interval_minutes=interval_minutes)
                if candles and len(candles) >= min_candles_needed:
                    elapsed = time.time() - start_time
                    success(f"✅ T-Invest API: {len(candles)} свечей для {ticker} (время={elapsed:.2f}с)")
                    return candles
                elif candles:
                    actual_days = actual_days * 2
                    if actual_days > 60:
                        break
                else:
                    break
        except Exception as e:
            warning(f"⚠️ T-Invest API ошибка для {ticker}: {e}")

        return []

    def analyze_with_candles(self, figi: str, ticker: str, candles: List, current_price: float) -> Dict[str, Any]:
        """
        СИНХРОННЫЙ анализ акции с уже полученными свечами
        БЕЗ EVENT LOOP - просто работаем с данными
        """
        start_time = time.time()
        info(f"📊 [ТЕХ.АНАЛИЗ] Начинаем анализ {ticker}...")

        try:
            min_needed = self._get_min_candles_for_ticker(ticker)

            if not candles or len(candles) < min_needed:
                return self._empty_result(f"Недостаточно данных ({len(candles) if candles else 0} свечей)")

            # Конвертация свечей
            prices, volumes, candle_dicts = self._convert_candles(candles, current_price)

            if len(prices) < min_needed:
                return self._empty_result(f"Ошибка конвертации: получено {len(prices)} из {len(candles)}")

            # Запуск StrategyEngine (синхронно!)
            signal_result = self.engine.analyze_signal(
                prices=prices,
                volumes=volumes,
                name=ticker,
                figi=figi,
                candles=candle_dicts
            )

            elapsed = time.time() - start_time
            info(f"📊 [ТЕХ.АНАЛИЗ] {ticker}: score={signal_result.score}, сигналов={len(signal_result.signals)}, время={elapsed:.2f}с")

            if signal_result.buy_signal:
                info(f"   🟢 {ticker}: СИГНАЛ НА ПОКУПКУ! score={signal_result.score}")
            elif signal_result.sell_signal:
                info(f"   🔴 {ticker}: СИГНАЛ НА SHORT! score={signal_result.score}")

            return {
                'score': signal_result.score,
                'signals': signal_result.signals,
                'buy_signal': signal_result.buy_signal,
                'sell_signal': signal_result.sell_signal,
                'recommendation': signal_result.recommendation,
                'rsi': signal_result.rsi,
                'macd': signal_result.macd,
                'volume_ratio': signal_result.volume_ratio,
                'take_profit_pct': getattr(signal_result, 'take_profit_pct', 1.2),
                'stop_loss_pct': getattr(signal_result, 'stop_loss_pct', 0.6)
            }

        except Exception as e:
            error(f"📊 [ТЕХ.АНАЛИЗ] {ticker}: ОШИБКА - {e}")
            return self._empty_result(f"Ошибка: {str(e)[:50]}")

    def _empty_result(self, error_msg: str) -> Dict[str, Any]:
        """Возвращает пустой результат анализа"""
        return {
            'score': 0,
            'signals': [error_msg],
            'buy_signal': False,
            'sell_signal': False,
            'recommendation': 'HOLD',
            'rsi': None,
            'macd': None,
            'volume_ratio': 1.0
        }

    def _convert_candles(self, candles: List, current_price: float) -> Tuple[List[float], List[float], List[Dict]]:
        """Конвертация свечей в единый формат"""
        prices = []
        volumes = []
        candle_dicts = []

        fallback_price = current_price if current_price > 0 else 100.0

        for c in candles:
            close_val = None
            volume_val = 0

            if hasattr(c, 'close'):
                close_val = c.close
                volume_val = getattr(c, 'volume', 0)
            elif isinstance(c, dict):
                close_val = c.get('close', fallback_price)
                volume_val = c.get('volume', 0)
            elif isinstance(c, (list, tuple)) and len(c) >= 1:
                close_val = c[0] if c[0] else fallback_price
                volume_val = c[1] if len(c) > 1 and c[1] else 0

            if close_val and close_val > 0:
                prices.append(float(close_val))
                volumes.append(float(volume_val) if volume_val else 0)
                candle_dicts.append({
                    'close': prices[-1],
                    'volume': volumes[-1],
                    'high': prices[-1] * 1.005,
                    'low': prices[-1] * 0.995,
                    'open': prices[-1],
                })

        return prices, volumes, candle_dicts

    async def analyze_stock(self, figi: str, name: str, ticker: str = None, is_backtest: bool = False) -> StockAnalysis:
        """Асинхронный анализ акции"""
        if not self.enabled or is_backtest:
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="HOLD",
                signals=["Анализ отключён"]
            )

        if not ticker:
            ticker = name

        info(f"\n{'='*60}")
        info(f"🔬 ТЕХНИЧЕСКИЙ АНАЛИЗ: {ticker}")
        info(f"{'='*60}")

        candles = self.fetch_candles(ticker, interval_minutes=CANDLES_INTERVAL, days=CANDLES_DAYS)
        min_candles = self._get_min_candles_for_ticker(ticker)

        if not candles or len(candles) < min_candles:
            warning(f"⚠️ Недостаточно данных для {ticker}")
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="HOLD (недостаточно данных)",
                signals=[f"⚠️ Мало данных"]
            )

        # Конвертация свечей
        candle_dicts = []
        for c in candles:
            if isinstance(c, dict):
                candle_dicts.append(c)
            elif isinstance(c, (list, tuple)) and len(c) >= 2:
                candle_dicts.append({
                    'close': c[0], 'volume': c[1],
                    'high': c[0] * 1.005, 'low': c[0] * 0.995, 'open': c[0],
                })
            elif hasattr(c, 'close'):
                candle_dicts.append({
                    'close': c.close, 'volume': getattr(c, 'volume', 0),
                    'high': getattr(c, 'high', c.close), 'low': getattr(c, 'low', c.close),
                    'open': getattr(c, 'open', c.close),
                })

        prices = [c['close'] for c in candle_dicts]
        volumes = [c['volume'] for c in candle_dicts]

        candle_patterns = self.analyze_candle_patterns(candle_dicts)
        supports, resistances, round_support, round_resistance = self.find_support_resistance_advanced(candle_dicts)

        signal_result = self.engine.analyze_signal(
            prices=prices,
            volumes=volumes,
            name=name,
            figi=figi,
            candles=candle_dicts
        )

        adjusted_score = self._adjust_score_by_patterns(
            signal_result.score, candle_patterns, supports, resistances,
            prices[-1], round_support, round_resistance
        )
        if adjusted_score != signal_result.score:
            signal_result.score = adjusted_score

        signal_result = await self._apply_fundamental_enhancement(ticker, signal_result)
        self._set_final_recommendation(signal_result, name)

        return StockAnalysis(
            figi=figi, name=name, score=signal_result.score,
            buy_signal=signal_result.buy_signal, sell_signal=signal_result.sell_signal,
            recommendation=signal_result.recommendation, signals=signal_result.signals,
            rsi=signal_result.rsi, macd=signal_result.macd, volume_ratio=signal_result.volume_ratio,
            candle_patterns=candle_patterns, support_levels=supports[:3],
            resistance_levels=resistances[:3], round_support=round_support, round_resistance=round_resistance
        )

    def _adjust_score_by_patterns(self, score: int, patterns: Dict, supports: List[float],
                                   resistances: List[float], current_price: float,
                                   round_support: float, round_resistance: float) -> int:
        """Корректировка score на основе свечных паттернов и уровней"""
        adjusted = score

        if 'hammer' in patterns or 'bullish_engulfing' in patterns:
            if adjusted > 0:
                adjusted += 2
        if 'hanging_man' in patterns or 'bearish_engulfing' in patterns:
            if adjusted < 0:
                adjusted -= 2
        if 'doji' in patterns:
            adjusted = int(adjusted * 0.5) if abs(adjusted) > 0 else 0

        if supports:
            nearest_support = max([s for s in supports if s < current_price], default=None)
            if nearest_support and (current_price - nearest_support) / current_price * 100 < 1.0:
                if adjusted > 0:
                    adjusted += 1

        if resistances:
            nearest_resistance = min([r for r in resistances if r > current_price], default=None)
            if nearest_resistance and (nearest_resistance - current_price) / current_price * 100 < 1.0:
                if adjusted < 0:
                    adjusted -= 1

        if abs(current_price - round_support) / current_price * 100 < 0.5 and adjusted > 0:
            adjusted += 1
        if abs(current_price - round_resistance) / current_price * 100 < 0.5 and adjusted < 0:
            adjusted -= 1

        return adjusted

    async def _apply_fundamental_enhancement(self, ticker: str, signal_result) -> Any:
        """Применение фундаментального усиления с таймаутом"""
        if not FUNDAMENTAL_AVAILABLE or enhance_trading_decision is None:
            return signal_result

        try:
            enhanced_score, enhanced_signals, fund_data = await asyncio.wait_for(
                enhance_trading_decision(
                    ticker=ticker,
                    technical_score=signal_result.score,
                    technical_signals=signal_result.signals
                ),
                timeout=FUNDAMENTAL_TIMEOUT
            )

            if enhanced_score != signal_result.score:
                signal_result.score = enhanced_score
                signal_result.signals = enhanced_signals
                signal_result.buy_signal = enhanced_score >= self.engine.score_threshold_long
                signal_result.sell_signal = enhanced_score <= self.engine.score_threshold_short

        except asyncio.TimeoutError:
            debug(f"⏰ Таймаут фундаментального анализа для {ticker}")
        except Exception as e:
            debug(f"❌ Фундаментальный анализ недоступен для {ticker}: {e}")

        return signal_result

    def _set_final_recommendation(self, signal_result, name: str):
        """Установка финальной рекомендации"""
        if signal_result.buy_signal:
            signal_result.recommendation = f"🟢 BUY (score={signal_result.score})"
            success(f"🎯 {name}: СИГНАЛ НА ПОКУПКУ! score={signal_result.score}")
        elif signal_result.sell_signal:
            signal_result.recommendation = f"🔴 SHORT (score={signal_result.score})"
            success(f"🎯 {name}: СИГНАЛ НА SHORT! score={signal_result.score}")
        else:
            signal_result.recommendation = "⚪ HOLD"

    # ========== МЕТОДЫ АНАЛИЗА СВЕЧНЫХ ПАТТЕРНОВ ==========
    def analyze_candle_patterns(self, candles: List[Dict]) -> Dict[str, str]:
        if len(candles) < 3:
            return {}
        patterns = {}
        try:
            last = candles[-1]
            prev = candles[-2]
            patterns.update(self._analyze_single_candle_patterns(last))
            patterns.update(self._analyze_double_candle_patterns(last, prev))
        except Exception as e:
            debug(f"Ошибка анализа свечей: {e}")
        return patterns

    def _analyze_single_candle_patterns(self, last: Dict) -> Dict[str, str]:
        patterns = {}
        body = abs(last['close'] - last['open'])
        lower_shadow = min(last['open'], last['close']) - last['low']
        upper_shadow = last['high'] - max(last['open'], last['close'])

        if body > 0 and lower_shadow >= body * 2 and upper_shadow < body * 0.3:
            patterns['hammer'] = '🟢 МОЛОТ - сигнал к покупке'
            if last['close'] < last['open']:
                patterns['hanging_man'] = '🔴 ПОВЕШЕННЫЙ - сигнал к продаже'

        if body > 0 and upper_shadow >= body * 2 and lower_shadow < body * 0.3:
            patterns['shooting_star'] = '🔴 ПАДАЮЩАЯ ЗВЕЗДА - разворот вниз'
            if last['close'] > last['open']:
                patterns['inverted_hammer'] = '🟢 ПЕРЕВЁРНУТЫЙ МОЛОТ - возможен рост'

        candle_range = last['high'] - last['low']
        if candle_range > 0 and body <= candle_range * 0.1:
            patterns['doji'] = '⚠️ ДОДЖИ - неопределённость'

        return patterns

    def _analyze_double_candle_patterns(self, last: Dict, prev: Dict) -> Dict[str, str]:
        patterns = {}
        prev_is_bearish = prev['close'] < prev['open']
        curr_is_bullish = last['close'] > last['open']

        if prev_is_bearish and curr_is_bullish:
            if last['close'] > prev['open'] and last['open'] < prev['close']:
                patterns['bullish_engulfing'] = '🟢 БЫЧЬЕ ПОГЛОЩЕНИЕ'

        prev_is_bullish = prev['close'] > prev['open']
        curr_is_bearish = last['close'] < last['open']
        if prev_is_bullish and curr_is_bearish:
            if last['open'] > prev['close'] and last['close'] < prev['open']:
                patterns['bearish_engulfing'] = '🔴 МЕДВЕЖЬЕ ПОГЛОЩЕНИЕ'

        return patterns

    def find_support_resistance_advanced(self, candles: List[Dict]) -> Tuple[List[float], List[float], float, float]:
        if len(candles) < 30:
            return [], [], 0, 0

        highs, lows = [], []

        for i in range(2, len(candles) - 2):
            h = candles[i].get('high', candles[i]['close'])
            l = candles[i].get('low', candles[i]['close'])

            if (h > candles[i - 1].get('high', 0) and h > candles[i - 2].get('high', 0) and
                h > candles[i + 1].get('high', 0) and h > candles[i + 2].get('high', 0)):
                highs.append(h)

            if (l < candles[i - 1].get('low', float('inf')) and l < candles[i - 2].get('low', float('inf')) and
                l < candles[i + 1].get('low', float('inf')) and l < candles[i + 2].get('low', float('inf'))):
                lows.append(l)

        supports = self._cluster_levels_advanced(lows, tolerance=0.3) if lows else []
        resistances = self._cluster_levels_advanced(highs, tolerance=0.3) if highs else []

        current_price = candles[-1]['close']
        step = 50 if current_price > 200 else (25 if current_price > 100 else 10)
        round_support = int(current_price / step) * step
        round_resistance = round_support + step

        return supports, resistances, round_support, round_resistance

    def _cluster_levels_advanced(self, levels: List[float], tolerance: float = 0.3) -> List[float]:
        if not levels:
            return []
        sorted_levels = sorted(levels)
        clusters = []
        current_cluster = [sorted_levels[0]]
        for level in sorted_levels[1:]:
            diff_pct = abs(level - current_cluster[-1]) / current_cluster[-1] * 100
            if diff_pct < tolerance:
                current_cluster.append(level)
            else:
                clusters.append(sum(current_cluster) / len(current_cluster))
                current_cluster = [level]
        if current_cluster:
            clusters.append(sum(current_cluster) / len(current_cluster))
        return clusters

    def log_candle_patterns(self, patterns: Dict[str, str], ticker: str):
        for pattern_desc in patterns.values():
            if 'BUY' in pattern_desc or 'покупке' in pattern_desc:
                success(f"🕯️ {ticker}: {pattern_desc}")
            elif 'SELL' in pattern_desc or 'продаже' in pattern_desc:
                warning(f"🕯️ {ticker}: {pattern_desc}")

    def calculate_dynamic_sltp(self, prices: List[float], volumes: List[int], side: str) -> Dict[str, float]:
        if len(prices) < 20:
            return {'take_profit': 1.5, 'stop_loss': 0.8, 'trailing': 0.4, 'atr_pct': 0, 'volatility': 0, 'volume_impulse': 1}

        current_price = prices[-1]
        atr = self._calculate_atr(prices, period=14)
        atr_pct = (atr / current_price) * 100 if current_price > 0 else 1.0
        volatility = float(np.std(prices[-20:])) / current_price * 100 if current_price > 0 else 1.0
        support, resistance = self._find_support_resistance(prices)
        avg_volume = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1
        volume_impulse = volumes[-1] / avg_volume if avg_volume > 0 else 1

        if side == "LONG":
            take_profit_target = (resistance - current_price) / current_price * 100 if resistance > current_price else atr_pct * 2
            stop_loss_target = (current_price - support) / current_price * 100 if support < current_price else atr_pct * 1.5
        else:
            take_profit_target = (current_price - support) / current_price * 100 if support < current_price else atr_pct * 2
            stop_loss_target = (resistance - current_price) / current_price * 100 if resistance > current_price else atr_pct * 1.5

        if volatility > 2:
            take_profit_target *= 1.5
            stop_loss_target *= 1.3
        elif volatility < 0.5:
            take_profit_target *= 0.7
            stop_loss_target *= 0.7
        if volume_impulse > 1.5:
            take_profit_target *= 1.2

        return {
            'take_profit': round(max(0.5, min(5.0, take_profit_target)), 2),
            'stop_loss': round(max(0.3, min(3.0, stop_loss_target)), 2),
            'trailing': round(stop_loss_target * 0.4, 2),
            'atr_pct': round(atr_pct, 2),
            'volatility': round(volatility, 2),
            'volume_impulse': round(volume_impulse, 2)
        }

    def _calculate_atr(self, prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 0.0
        returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                returns.append(abs(prices[i] - prices[i-1]) / prices[i-1])
        if returns:
            return sum(returns[-period:]) / period * prices[-1]
        return 0.0

    def _find_support_resistance(self, prices: List[float]) -> Tuple[float, float]:
        if len(prices) < 50:
            return min(prices[-20:]), max(prices[-20:])
        highs, lows = [], []
        for i in range(2, len(prices) - 2):
            if prices[i] > prices[i-1] and prices[i] > prices[i+1]:
                highs.append(prices[i])
            if prices[i] < prices[i-1] and prices[i] < prices[i+1]:
                lows.append(prices[i])
        support = self._cluster_levels(lows) if lows else min(prices[-20:])
        resistance = self._cluster_levels(highs) if highs else max(prices[-20:])
        return support, resistance

    def _cluster_levels(self, levels: List[float], tolerance: float = 0.5) -> float:
        if not levels:
            return 0.0
        levels.sort()
        clusters = []
        current_cluster = [levels[0]]
        for level in levels[1:]:
            if abs(level - current_cluster[-1]) / max(current_cluster[-1], 0.01) * 100 < tolerance:
                current_cluster.append(level)
            else:
                clusters.append(sum(current_cluster) / len(current_cluster))
                current_cluster = [level]
        if current_cluster:
            clusters.append(sum(current_cluster) / len(current_cluster))
        return clusters[0] if clusters else levels[0]


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
analyzer = TechnicalAnalyzer()