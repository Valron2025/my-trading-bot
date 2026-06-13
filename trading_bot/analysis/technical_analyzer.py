"""Модуль технического анализа - ПРОФЕССИОНАЛЬНАЯ ВЕРСИЯ"""

import numpy as np  # ✅ ДОБАВЛЕНО
import pandas as pd  # ✅ ДОБАВЛЕНО
import time
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, time as dt_time, timedelta, timezone

from ..models import StockAnalysis
from ..logger import success, warning, debug, info
from .strategy_engine import create_strategy_engine
from trading_bot.analysis.fundamental_analyzer import enhance_trading_decision

# ========== ЧАСОВОЙ ПОЯС ==========
MOSCOW_TZ = timezone(timedelta(hours=3))


# ========== ФУНКЦИЯ ДЛЯ ОТЛОЖЕННОГО ИМПОРТА ==========
def _get_tbank():
    """Получение экземпляра T-Bank клиента (для избежания циклических импортов)"""
    from ..api.tbank_client import tbank
    return tbank


# Импорт синхронной обёртки для CandleBuilder (fallback)
try:
    from ..core.candle_sync_wrapper import get_candles_sync
    CANDLE_BUILDER_AVAILABLE = True
    debug("✅ CandleBuilder available (fallback)")
except ImportError as e:
    CANDLE_BUILDER_AVAILABLE = False
    debug(f"⚠️ CandleBuilder not available: {e}")

# Импорт MOEX синхронного клиента (fallback)
try:
    from ..core.moex_sync_fetcher import moex_sync
    MOEX_AVAILABLE = True
    debug("✅ MOEX sync fetcher available (fallback)")
except ImportError as e:
    MOEX_AVAILABLE = False
    debug(f"⚠️ MOEX sync fetcher not available: {e}")


class TechnicalAnalyzer:
    """Технический анализ акций - профессиональная версия с приоритетом T-Invest API"""

    def __init__(self, engine=None, capital=None):
        self.rsi_period = 14
        self.volume_ratio_period = 5
        self.min_candles_required = 30
        self.atr_period = 14
        self.divergence_lookback = 10

        if engine:
            self.engine = engine
        else:
            # ✅ ИСПРАВЛЕНО: передаём РЕАЛЬНЫЙ капитал
            if capital is None:
                from ..config import config
                capital = config.total_capital or 100000
                info(f"📊 TechnicalAnalyzer: используем капитал {capital:.0f}₽ из конфига")
            self.engine = create_strategy_engine(capital)

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def _get_figi_by_ticker(self, ticker: str, tbank) -> Optional[str]:
        """Получение FIGI по тикеру"""
        try:
            all_shares = tbank.get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('ticker') == ticker:
                    return stock.get('figi')
            return None
        except Exception as e:
            debug(f"Ошибка получения FIGI для {ticker}: {e}")
            return None

    # ========== FALLBACK: MOEX API ==========
    def _fetch_candles_moex(self, ticker: str, interval_minutes: int = 5, days: int = 5) -> List[Tuple[float, float]]:
        """Получение свечей из MOEX (FALLBACK)"""
        if not MOEX_AVAILABLE:
            return []

        try:
            for interval in [interval_minutes, interval_minutes * 2, interval_minutes * 3]:
                candles = moex_sync.get_candles(ticker, interval, days)
                if candles and len(candles) >= self.min_candles_required:
                    success(f"✅ MOEX fallback: {len(candles)} свечей для {ticker}")
                    return candles
                elif candles:
                    debug(f"   MOEX {interval}мин: {len(candles)} свечей (мало)")

            candles_5min = moex_sync.get_candles(ticker, 5, days)
            if len(candles_5min) >= self.min_candles_required:
                info(f"   Используем 5-минутные свечи для {ticker}")
                return candles_5min

            return []
        except Exception as e:
            debug(f"MOEX error for {ticker}: {e}")
            return []

    # ========== FALLBACK: CandleBuilder ==========
    def _fetch_candles_candlebuilder(self, ticker: str, interval_minutes: int = 5, days: int = 5) -> List[Tuple[float, float]]:
        """Получение свечей из CandleBuilder (FALLBACK)"""
        if not CANDLE_BUILDER_AVAILABLE:
            return []

        try:
            candles = get_candles_sync(ticker, interval_minutes, days)
            if candles and len(candles) >= self.min_candles_required:
                success(f"✅ CandleBuilder fallback: {len(candles)} свечей для {ticker}")
                return candles
            return []
        except Exception as e:
            debug(f"CandleBuilder error for {ticker}: {e}")
            return []

    # ========== ОСНОВНОЙ МЕТОД fetch_candles ==========
    def fetch_candles(self, ticker: str, interval_minutes: int = None, days: int = 30) -> List[Tuple[float, float]]:
        """Получение свечей с приоритетом T-Invest API"""
        ticker = ticker.upper()

        if interval_minutes is None:
            interval_minutes = self._get_optimal_interval(ticker, days)
            info(f"📊 Авто-интервал для {ticker}: {interval_minutes} мин")

        # ========== 1. T-Invest API ==========
        try:
            tbank = _get_tbank()
            figi = self._get_figi_by_ticker(ticker, tbank)
            if not figi:
                warning(f"⚠️ Не найден FIGI для {ticker}")
                return []

            max_days = 7 if interval_minutes <= 5 else 30
            actual_days = min(days, max_days)
            if actual_days < days:
                debug(f"   T-Invest API: ограничиваем период с {days} до {actual_days} дней")

            candles = tbank.get_candles(figi, days=actual_days, interval_minutes=interval_minutes)
            if candles and len(candles) >= self.min_candles_required:
                success(f"✅ T-Invest API: {len(candles)} свечей для {ticker}")
                return candles
            elif candles:
                debug(f"   T-Invest API: {len(candles)} свечей (мало для анализа)")
        except Exception as e:
            warning(f"⚠️ T-Invest API ошибка для {ticker}: {e}")

        # ========== 2. FALLBACK: MOEX ==========
        info(f"🔄 T-Invest не вернул данные, пробуем MOEX для {ticker}...")
        candles = self._fetch_candles_moex(ticker, interval_minutes, days)
        if candles:
            return candles

        # ========== 3. FALLBACK: CandleBuilder ==========
        info(f"🔄 MOEX не вернул данные, пробуем CandleBuilder для {ticker}...")
        candles = self._fetch_candles_candlebuilder(ticker, interval_minutes, days)
        if candles:
            return candles

        warning(f"⚠️ Не удалось получить данные для {ticker}")
        return []

    # ========== ДИНАМИЧЕСКИЙ ИНТЕРВАЛ ==========
    def _get_optimal_interval(self, ticker: str, days: int = 30) -> int:
        """
        Автоматический выбор интервала НА ОСНОВЕ ДАННЫХ, а не времени
        """
        try:
            candles = self.fetch_candles(ticker, interval_minutes=5, days=min(days, 7))
            if not candles or len(candles) < 20:
                return 5

            prices = [c[0] for c in candles]

            # Расчёт волатильности
            returns = []
            for i in range(1, len(prices)):
                if prices[i - 1] > 0:
                    returns.append(abs((prices[i] - prices[i - 1]) / prices[i - 1]))

            avg_volatility = sum(returns) / len(returns) * 100 if returns else 1.0

            # Выбор интервала на основе волатильности
            if avg_volatility > 1.5:
                interval = 1
            elif avg_volatility > 0.8:
                interval = 5
            elif avg_volatility > 0.3:
                interval = 15
            else:
                interval = 30

            debug(f"📊 Динамический интервал для {ticker}: {interval} мин (волатильность={avg_volatility:.2f}%)")
            return interval

        except Exception as e:
            debug(f"Ошибка определения интервала для {ticker}: {e}")
            return 5

    # ========== АНАЛИЗ АКЦИИ ==========
    async def analyze_stock(self, figi: str, name: str, ticker: str = None, is_backtest: bool = False) -> StockAnalysis:
        """Профессиональный анализ акции с интеграцией методов из книги"""

        if is_backtest:
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="BACKTEST_MODE",
                signals=["Бэктест: используйте встроенный анализатор"]
            )

        if not ticker:
            ticker = name

        tbank = _get_tbank()

        # Получаем свечи
        candles = self.fetch_candles(ticker, interval_minutes=5, days=5)

        if not candles or len(candles) < self.min_candles_required:
            warning(f"⚠️ Недостаточно данных для {ticker}")
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="HOLD (недостаточно данных)",
                signals=[f"⚠️ Мало данных ({len(candles)} свечей)"]
            )

        # Преобразуем свечи в словари для анализа
        candle_dicts = []
        for c in candles:
            candle_dicts.append({
                'open': c[5] if len(c) > 5 else c[0],
                'high': c[3] if len(c) > 3 else c[0],
                'low': c[4] if len(c) > 4 else c[0],
                'close': c[0],
                'volume': c[1]
            })

        prices = [c['close'] for c in candle_dicts]
        volumes = [c['volume'] for c in candle_dicts]
        current_price = prices[-1]

        # ========== 1. СВЕЧНОЙ АНАЛИЗ ==========
        candle_patterns = self.analyze_candle_patterns(candle_dicts)

        # ========== 2. УРОВНИ ПОДДЕРЖКИ/СОПРОТИВЛЕНИЯ ==========
        supports, resistances, round_support, round_resistance = self.find_support_resistance_advanced(candle_dicts)

        # ========== 3. БАЗОВЫЙ СИГНАЛ ОТ STRATEGY ENGINE ==========
        signal_result = self.engine.analyze_signal(prices, volumes, name)

        # ========== 4. КОРРЕКТИРОВКА СИГНАЛА НА ОСНОВЕ СВЕЧЕЙ ==========
        adjusted_score = signal_result.score

        # Молот или бычье поглощение -> усиливаем BUY
        if 'hammer' in candle_patterns or 'bullish_engulfing' in candle_patterns:
            if adjusted_score > 0:
                adjusted_score += 2
                signal_result.signals.append(
                    f"📊 Свечной паттерн: {candle_patterns.get('hammer', candle_patterns.get('bullish_engulfing'))}")

        # Повешенный или медвежье поглощение -> усиливаем SELL
        if 'hanging_man' in candle_patterns or 'bearish_engulfing' in candle_patterns:
            if adjusted_score < 0:
                adjusted_score -= 2
                signal_result.signals.append(
                    f"📊 Свечной паттерн: {candle_patterns.get('hanging_man', candle_patterns.get('bearish_engulfing'))}")

        # Доджи -> снижаем уверенность
        if 'doji' in candle_patterns:
            adjusted_score = adjusted_score * 0.5 if abs(adjusted_score) > 0 else 0
            signal_result.signals.append("⚠️ Доджи: неопределённость")

        # ========== 5. КОРРЕКТИРОВКА НА УРОВНЯХ ==========
        # Проверка, что цена у уровня поддержки
        if supports:
            nearest_support = max([s for s in supports if s < current_price], default=None)
            if nearest_support and (current_price - nearest_support) / current_price * 100 < 1.0:
                if adjusted_score > 0:
                    adjusted_score += 1
                    signal_result.signals.append(f"📊 Цена у уровня поддержки {nearest_support:.2f}₽")

        # Проверка, что цена у уровня сопротивления
        if resistances:
            nearest_resistance = min([r for r in resistances if r > current_price], default=None)
            if nearest_resistance and (nearest_resistance - current_price) / current_price * 100 < 1.0:
                if adjusted_score < 0:
                    adjusted_score -= 1
                    signal_result.signals.append(f"📊 Цена у уровня сопротивления {nearest_resistance:.2f}₽")

        # Круглые уровни
        if abs(current_price - round_support) / current_price * 100 < 0.5:
            if adjusted_score > 0:
                adjusted_score += 1
                signal_result.signals.append(f"📊 Круглый уровень поддержки {round_support:.0f}₽")

        if abs(current_price - round_resistance) / current_price * 100 < 0.5:
            if adjusted_score < 0:
                adjusted_score -= 1
                signal_result.signals.append(f"📊 Круглый уровень сопротивления {round_resistance:.0f}₽")

        # Обновляем score
        signal_result.score = adjusted_score

        # ========== 6. ФУНДАМЕНТАЛЬНОЕ УСИЛЕНИЕ ==========
        try:
            enhanced_score, enhanced_signals, fund_data = await enhance_trading_decision(
                ticker=ticker or name,
                technical_score=signal_result.score,
                technical_signals=signal_result.signals
            )

            if enhanced_score != signal_result.score:
                signal_result.score = enhanced_score
                signal_result.signals = enhanced_signals

                # Обновляем сигналы покупки/продажи
                signal_result.buy_signal = enhanced_score >= self.engine.score_threshold_long
                signal_result.sell_signal = enhanced_score <= self.engine.score_threshold_short

                if fund_data:
                    info(f"📊 Фундаментальный анализ {ticker}: {fund_data.get('action', 'Нейтрально')} "
                         f"(оценка: {fund_data.get('overall_score', 0):.0f})")

        except Exception as e:
            debug(f"Фундаментальный анализ временно недоступен: {e}")

        # ========== 7. ФИНАЛЬНАЯ РЕКОМЕНДАЦИЯ ==========
        if signal_result.buy_signal:
            signal_result.recommendation = f"🟢 BUY (score={signal_result.score})"
            success(f"🎯 {name}: СИГНАЛ НА ПОКУПКУ! score={signal_result.score}")
        elif signal_result.sell_signal:
            signal_result.recommendation = f"🔴 SHORT (score={signal_result.score})"
            success(f"🎯 {name}: СИГНАЛ НА SHORT! score={signal_result.score}")
        else:
            signal_result.recommendation = "⚪ HOLD"

        # ========== 8. СОХРАНЯЕМ ДОПОЛНИТЕЛЬНЫЕ ДАННЫЕ ==========
        analysis = StockAnalysis(
            figi=figi,
            name=name,
            score=signal_result.score,
            buy_signal=signal_result.buy_signal,
            sell_signal=signal_result.sell_signal,
            recommendation=signal_result.recommendation,
            signals=signal_result.signals,
            rsi=signal_result.rsi,
            macd=signal_result.macd,
            volume_ratio=signal_result.volume_ratio,
            candle_patterns=candle_patterns,
            support_levels=supports[:3],
            resistance_levels=resistances[:3],
            round_support=round_support,
            round_resistance=round_resistance
        )

        return analysis

    # ========== ДИНАМИЧЕСКИЙ РАСЧЁТ SL/TP ==========
    def calculate_dynamic_sltp(self, prices: List[float], volumes: List[int], side: str) -> Dict[str, float]:
        """Динамический расчёт тейк-профита и стоп-лосса"""
        if len(prices) < 20:
            return {'take_profit': 1.5, 'stop_loss': 0.8, 'trailing': 0.4, 'atr_pct': 0, 'volatility': 0, 'volume_impulse': 1}

        current_price = prices[-1]

        # ATR
        atr = self._calculate_atr(prices, period=14)
        atr_pct = (atr / current_price) * 100 if current_price > 0 else 1.0

        # Волатильность
        volatility = float(np.std(prices[-20:])) / current_price * 100 if current_price > 0 else 1.0

        # Уровни поддержки/сопротивления
        support, resistance = self._find_support_resistance(prices)

        # Объёмный импульс
        avg_volume = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1
        volume_impulse = volumes[-1] / avg_volume if avg_volume > 0 else 1

        if side == "LONG":
            if resistance > current_price:
                take_profit_target = (resistance - current_price) / current_price * 100
            else:
                take_profit_target = atr_pct * 2

            if support < current_price:
                stop_loss_target = (current_price - support) / current_price * 100
            else:
                stop_loss_target = atr_pct * 1.5
        else:
            if support < current_price:
                take_profit_target = (current_price - support) / current_price * 100
            else:
                take_profit_target = atr_pct * 2

            if resistance > current_price:
                stop_loss_target = (resistance - current_price) / current_price * 100
            else:
                stop_loss_target = atr_pct * 1.5

        # Корректировки
        if volatility > 2:
            take_profit_target *= 1.5
            stop_loss_target *= 1.3
        elif volatility < 0.5:
            take_profit_target *= 0.7
            stop_loss_target *= 0.7

        if volume_impulse > 1.5:
            take_profit_target *= 1.2

        take_profit_target = max(0.5, min(5.0, take_profit_target))
        stop_loss_target = max(0.3, min(3.0, stop_loss_target))
        trailing_target = stop_loss_target * 0.4

        return {
            'take_profit': round(take_profit_target, 2),
            'stop_loss': round(stop_loss_target, 2),
            'trailing': round(trailing_target, 2),
            'atr_pct': round(atr_pct, 2),
            'volatility': round(volatility, 2),
            'volume_impulse': round(volume_impulse, 2)
        }

    def _calculate_atr(self, prices: List[float], period: int = 14) -> float:
        """Расчёт ATR (упрощённо)"""
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
        """Нахождение уровней поддержки и сопротивления"""
        if len(prices) < 50:
            return min(prices[-20:]), max(prices[-20:])

        highs = []
        lows = []

        for i in range(2, len(prices) - 2):
            if prices[i] > prices[i-1] and prices[i] > prices[i+1]:
                highs.append(prices[i])
            if prices[i] < prices[i-1] and prices[i] < prices[i+1]:
                lows.append(prices[i])

        support = self._cluster_levels(lows) if lows else min(prices[-20:])
        resistance = self._cluster_levels(highs) if highs else max(prices[-20:])

        return support, resistance

    def _cluster_levels(self, levels: List[float], tolerance: float = 0.5) -> float:
        """Кластеризация близких уровней"""
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

    # ========== ОСТАЛЬНЫЕ МЕТОДЫ ==========
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Расчёт RSI"""
        if len(prices) < period + 1:
            return 50.0

        gains, losses = [], []
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

    def calculate_alligator(self, prices: List[float]) -> Dict[str, Any]:
        """
        Индикатор Аллигатор (Билл Вильямс)
        Глава 5 книги
        """
        if len(prices) < 14:
            return {}

        def smoothed_ma(data, period, shift):
            """Сглаженная скользящая средняя"""
            if len(data) < period + shift:
                return data[-1] if data else 0
            return sum(data[-(period + shift):-shift]) / period

        # Челюсть (Jaw) - синяя линия, период 13, сдвиг 8
        jaw = smoothed_ma(prices, 13, 8)

        # Зубы (Teeth) - красная линия, период 8, сдвиг 5
        teeth = smoothed_ma(prices, 8, 5)

        # Губы (Lips) - зелёная линия, период 5, сдвиг 3
        lips = smoothed_ma(prices, 5, 3)

        # Сигналы Аллигатора
        signals = []

        # Аллигатор спит (боковик)
        if abs(jaw - teeth) < 0.01 and abs(teeth - lips) < 0.01:
            signals.append("ALLIGATOR_SLEEPING - боковое движение, ждите пробуждения")

        # Аллигатор проснулся - тренд вверх
        elif lips > teeth > jaw:
            signals.append("ALLIGATOR_AWAKE_UP - восходящий тренд, к покупке")

        # Аллигатор проснулся - тренд вниз
        elif lips < teeth < jaw:
            signals.append("ALLIGATOR_AWAKE_DOWN - нисходящий тренд, к продаже")

        # Аллигатор ест (гэп между линиями увеличивается)
        elif (lips - teeth) > (teeth - jaw) * 1.5:
            signals.append("ALLIGATOR_FEEDING - тренд ускоряется")

        return {
            'jaw': jaw,
            'teeth': teeth,
            'lips': lips,
            'signals': signals
        }

    def calculate_awesome_oscillator(self, prices: List[float], period_fast: int = 5, period_slow: int = 34) -> float:
        """
        Awesome Oscillator (Билл Вильямс)
        Глава 5 книги - осцилляторы
        """
        if len(prices) < period_slow + 1:
            return 0

        # Медианная цена = (High + Low) / 2, но у нас только close
        median = [p for p in prices]

        # Простая скользящая средняя
        sma_fast = sum(median[-period_fast:]) / period_fast
        sma_slow = sum(median[-period_slow:]) / period_slow

        ao = sma_fast - sma_slow

        # Сигналы AO
        if len(prices) >= period_slow + 2:
            prev_ao = (sum(median[-period_fast - 1:-1]) / period_fast -
                       sum(median[-period_slow - 1:-1]) / period_slow)

            # Пересечение нулевой линии
            if prev_ao < 0 and ao > 0:
                debug(f"📊 AO: пересечение нуля СНИЗУ ВВЕРХ - сигнал к покупке")
            elif prev_ao > 0 and ao < 0:
                debug(f"📊 AO: пересечение нуля СВЕРХУ ВНИЗ - сигнал к продаже")

        return ao

    # ========== МЕТОД analyze_candle_patterns ==========
    def analyze_candle_patterns(self, candles: List[Dict]) -> Dict[str, str]:
        """
        Анализ японских свечных паттернов
        Глава 8 книги "Из пассажира в волки"
        """
        if len(candles) < 3:
            return {}

        patterns = {}

        try:
            # Получаем последние свечи
            last = candles[-1]
            prev = candles[-2]
            prev2 = candles[-3]

            # Параметры последней свечи
            body = abs(last['close'] - last['open'])
            lower_shadow = min(last['open'], last['close']) - last['low']
            upper_shadow = last['high'] - max(last['open'], last['close'])

            # ===== МОЛОТ (Hammer) - разворот вверх =====
            if body > 0 and lower_shadow >= body * 2 and upper_shadow < body * 0.3:
                patterns['hammer'] = '🟢 МОЛОТ - сигнал к покупке'

            # ===== ПОВЕШЕННЫЙ (Hanging Man) - разворот вниз =====
            if body > 0 and lower_shadow >= body * 2 and upper_shadow < body * 0.3:
                if last['close'] < last['open']:
                    patterns['hanging_man'] = '🔴 ПОВЕШЕННЫЙ - сигнал к продаже'

            # ===== ПАДАЮЩАЯ ЗВЕЗДА (Shooting Star) =====
            if body > 0 and upper_shadow >= body * 2 and lower_shadow < body * 0.3:
                patterns['shooting_star'] = '🔴 ПАДАЮЩАЯ ЗВЕЗДА - разворот вниз'

            # ===== ПЕРЕВЁРНУТЫЙ МОЛОТ (Inverted Hammer) =====
            if body > 0 and upper_shadow >= body * 2 and lower_shadow < body * 0.3:
                if last['close'] > last['open']:
                    patterns['inverted_hammer'] = '🟢 ПЕРЕВЁРНУТЫЙ МОЛОТ - возможен рост'

            # ===== ДОДЖИ (Doji) =====
            candle_range = last['high'] - last['low']
            if candle_range > 0 and body <= candle_range * 0.1:
                patterns['doji'] = '⚠️ ДОДЖИ - неопределённость, возможен разворот'

            # ===== ПОГЛОЩЕНИЕ (Engulfing) =====
            prev_body = prev['close'] - prev['open']
            prev_is_bearish = prev['close'] < prev['open']
            curr_is_bullish = last['close'] > last['open']

            # Бычье поглощение
            if prev_is_bearish and curr_is_bullish:
                if last['close'] > prev['open'] and last['open'] < prev['close']:
                    patterns['bullish_engulfing'] = '🟢 БЫЧЬЕ ПОГЛОЩЕНИЕ - сильный сигнал к покупке'

            # Медвежье поглощение
            prev_is_bullish = prev['close'] > prev['open']
            curr_is_bearish = last['close'] < last['open']

            if prev_is_bullish and curr_is_bearish:
                if last['open'] > prev['close'] and last['close'] < prev['open']:
                    patterns['bearish_engulfing'] = '🔴 МЕДВЕЖЬЕ ПОГЛОЩЕНИЕ - сильный сигнал к продаже'

            # ===== ХАРАМИ (Harami) =====
            prev_body_abs = abs(prev_body)
            curr_body_abs = abs(last['close'] - last['open'])

            if prev_body_abs > 0 and curr_body_abs < prev_body_abs * 0.5:
                if last['low'] > prev['low'] and last['high'] < prev['high']:
                    if prev_is_bearish and curr_is_bullish:
                        patterns['bullish_harami'] = '🟢 БЫЧЬЯ ХАРАМИ - разворот вверх'
                    elif prev_is_bullish and curr_is_bearish:
                        patterns['bearish_harami'] = '🔴 МЕДВЕЖЬЯ ХАРАМИ - разворот вниз'

        except Exception as e:
            debug(f"Ошибка анализа свечей: {e}")

        return patterns

    def find_support_resistance_advanced(self, candles: List[Dict]) -> Tuple[List[float], List[float], float, float]:
        """
        Продвинутый поиск уровней поддержки и сопротивления
        Глава 6 книги
        """
        if len(candles) < 30:
            return [], [], 0, 0

        highs = []
        lows = []

        # Поиск локальных экстремумов
        for i in range(2, len(candles) - 2):
            h = candles[i].get('high', candles[i]['close'])
            l = candles[i].get('low', candles[i]['close'])

            # Проверка на вершину
            if (h > candles[i - 1].get('high', 0) and
                    h > candles[i - 2].get('high', 0) and
                    h > candles[i + 1].get('high', 0) and
                    h > candles[i + 2].get('high', 0)):
                highs.append(h)

            # Проверка на впадину
            if (l < candles[i - 1].get('low', float('inf')) and
                    l < candles[i - 2].get('low', float('inf')) and
                    l < candles[i + 1].get('low', float('inf')) and
                    l < candles[i + 2].get('low', float('inf'))):
                lows.append(l)

        # Кластеризация уровней (область, а не точная линия)
        supports = self._cluster_levels_advanced(lows, tolerance=0.3)
        resistances = self._cluster_levels_advanced(highs, tolerance=0.3)

        # Круглые уровни (психологические)
        current_price = candles[-1]['close']
        step = 50 if current_price > 200 else (25 if current_price > 100 else 10)
        round_support = int(current_price / step) * step
        round_resistance = round_support + step

        return supports, resistances, round_support, round_resistance

    def _cluster_levels_advanced(self, levels: List[float], tolerance: float = 0.3) -> List[float]:
        """Кластеризация близких уровней с подсчётом силы"""
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
                # Сохраняем среднее значение кластера
                clusters.append(sum(current_cluster) / len(current_cluster))
                current_cluster = [level]

        if current_cluster:
            clusters.append(sum(current_cluster) / len(current_cluster))

        return clusters

    def log_candle_patterns(self, patterns: Dict[str, str], ticker: str):
        """Логирование найденных свечных паттернов"""
        if patterns:
            for pattern_name, pattern_desc in patterns.items():
                if 'BUY' in pattern_desc or 'покупке' in pattern_desc:
                    success(f"🕯️ {ticker}: {pattern_desc}")
                elif 'SELL' in pattern_desc or 'продаже' in pattern_desc:
                    warning(f"🕯️ {ticker}: {pattern_desc}")
                else:
                    info(f"🕯️ {ticker}: {pattern_desc}")

    def analyze_stock_sync(self, figi: str, name: str, ticker: str = None, is_backtest: bool = False) -> StockAnalysis:
        """Синхронная обёртка для analyze_stock"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            # Если уже есть цикл, создаём новый
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self.analyze_stock(figi, name, ticker, is_backtest))
                return future.result()
        except RuntimeError:
            # Нет запущенного цикла
            return asyncio.run(self.analyze_stock(figi, name, ticker, is_backtest))


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
analyzer = TechnicalAnalyzer()

# ✅ ВТОРОЕ ОПРЕДЕЛЕНИЕ КЛАССА TechnicalAnalyzer УДАЛЕНО
# ✅ БОЛЬШЕ НЕТ ДУБЛИРОВАНИЯ