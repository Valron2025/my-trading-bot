"""Модуль технического анализа - ПРОФЕССИОНАЛЬНАЯ ВЕРСИЯ"""

import asyncio
import nest_asyncio
nest_asyncio.apply()
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, time as dt_time, timedelta, timezone
from functools import lru_cache
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
FUNDAMENTAL_TIMEOUT = 1.0  # Таймаут фундаментального анализа (сек)
NEWS_TIMEOUT = 3.0  # Таймаут новостного анализа (сек)
CANDLES_DAYS = 7  # Количество дней свечей для анализа
CANDLES_INTERVAL = 5  # Интервал свечей (минут)
MAX_ANALYSIS_TIME = 30.0  # Максимальное время анализа (сек)


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
        self.min_candles_required = 20
        self.atr_period = 14
        self.divergence_lookback = 10
        self.enabled = True
        self._analysis_cache = {}  # Кэш результатов анализа
        self._cache_ttl = 60  # TTL кэша 60 секунд

        from trading_bot.config import config
        from trading_bot.logger import info
        from .strategy_engine import create_strategy_engine

        # Определяем капитал
        if capital is None or capital == 0:
            capital = getattr(config, 'total_capital', 100000)
            if capital == 0:
                capital = 100000
                info(f"📊 TechnicalAnalyzer: капитал не найден, используем {capital:.0f}₽")
            else:
                info(f"📊 TechnicalAnalyzer: используем капитал {capital:.0f}₽ из конфига")

        # Создаём движок стратегии
        if engine:
            self.engine = engine
        else:
            self.engine = create_strategy_engine(capital)

        # Кэш для низколиквидных тикеров
        self._low_liquidity_cache = {}
        info(f"📊 TechnicalAnalyzer: min_candles_required={self.min_candles_required}")
        info(f"📊 TechnicalAnalyzer: кэш анализа включён (TTL={self._cache_ttl}с)")

    def _get_cache_key(self, ticker: str, interval: int, days: int) -> str:
        """Генерация ключа кэша"""
        return f"{ticker}_{interval}_{days}"

    def _get_cached_analysis(self, ticker: str) -> Optional[Dict]:
        """Получение кэшированного анализа"""
        cache_key = self._get_cache_key(ticker, CANDLES_INTERVAL, CANDLES_DAYS)
        if cache_key in self._analysis_cache:
            cached_data, timestamp = self._analysis_cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                debug(f"📊 {ticker}: используем кэшированный анализ (возраст {time.time() - timestamp:.1f}с)")
                return cached_data
            else:
                debug(f"📊 {ticker}: кэш устарел, удаляем")
                del self._analysis_cache[cache_key]
        return None

    def _set_cached_analysis(self, ticker: str, data: Dict):
        """Сохранение анализа в кэш"""
        cache_key = self._get_cache_key(ticker, CANDLES_INTERVAL, CANDLES_DAYS)
        self._analysis_cache[cache_key] = (data, time.time())
        debug(f"📊 {ticker}: результат сохранён в кэш")

    # ========== ДИНАМИЧЕСКОЕ ОПРЕДЕЛЕНИЕ МИНИМАЛЬНОГО КОЛИЧЕСТВА СВЕЧЕЙ ==========
    def _get_min_candles_for_ticker(self, ticker: str) -> int:
        """Динамическое определение минимального количества свечей для тикера"""
        low_liquidity_tickers = {
            "OMZZP", "OMZZ", "KZOS", "YRSBP", "YRSB",
            "CNRU", "CNR", "BSPB", "BSP", "TUZA", "ALRS", "TATN"
        }

        ticker_upper = ticker.upper()

        if ticker_upper in low_liquidity_tickers:
            if ticker_upper not in self._low_liquidity_cache:
                self._low_liquidity_cache[ticker_upper] = True
                debug(f"📊 {ticker}: низколиквидный тикер, min_candles=15")
            return 15
        return self.min_candles_required

    # ========== FALLBACK: MOEX API ==========
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
                elif candles:
                    debug(f"   MOEX {interval}мин: {len(candles)} свечей (мало)")

            candles_5min = moex_sync.get_candles(ticker, 5, days)
            if len(candles_5min) >= self._get_min_candles_for_ticker(ticker):
                info(f"   Используем 5-минутные свечи для {ticker}")
                return candles_5min

            return []
        except Exception as e:
            debug(f"MOEX error for {ticker}: {e}")
            return []

    # ========== FALLBACK: CandleBuilder ==========
    def _fetch_candles_candlebuilder(self, ticker: str, interval_minutes: int = 5, days: int = 7) -> List[Tuple[float, float]]:
        """Получение свечей из CandleBuilder (FALLBACK)"""
        if not CANDLE_BUILDER_AVAILABLE:
            return []

        try:
            candles = get_candles_sync(ticker, interval_minutes, days)
            if candles and len(candles) >= self._get_min_candles_for_ticker(ticker):
                success(f"✅ CandleBuilder fallback: {len(candles)} свечей для {ticker}")
                return candles
            return []
        except Exception as e:
            debug(f"CandleBuilder error for {ticker}: {e}")
            return []

    # ========== ОСНОВНОЙ МЕТОД fetch_candles ==========
    def fetch_candles(self, ticker: str, interval_minutes: int = None, days: int = CANDLES_DAYS) -> List[Tuple[float, float]]:
        """Получение свечей с приоритетом T-Invest API"""
        ticker = ticker.upper()
        start_time = time.time()

        if interval_minutes is None:
            interval_minutes = self._get_optimal_interval(ticker, days)
            debug(f"📊 Авто-интервал для {ticker}: {interval_minutes} мин")

        # ========== 1. T-Invest API ==========
        try:
            from trading_bot.api.tbank_client import tbank

            figi = tbank._get_figi_by_ticker(ticker)
            if not figi:
                warning(f"⚠️ Не найден FIGI для {ticker}")
                return []

            min_candles_needed = self._get_min_candles_for_ticker(ticker)
            actual_days = days

            for attempt in range(3):
                candles = tbank.get_candles(figi, days=actual_days, interval_minutes=interval_minutes)

                if candles and len(candles) >= min_candles_needed:
                    elapsed = time.time() - start_time
                    success(f"✅ T-Invest API: {len(candles)} свечей для {ticker} (нужно {min_candles_needed}, время={elapsed:.2f}с)")
                    return candles
                elif candles:
                    debug(f"   T-Invest API: {len(candles)} свечей (мало, нужно {min_candles_needed}), увеличиваем период...")
                    actual_days = actual_days * 2
                    if actual_days > 60:
                        break
                else:
                    break

        except Exception as e:
            warning(f"⚠️ T-Invest API ошибка для {ticker}: {e}")

        # ========== 2. FALLBACK: CandleBuilder ==========
        debug(f"🔄 T-Invest не вернул данные, пробуем CandleBuilder для {ticker}...")
        candles = self._fetch_candles_candlebuilder(ticker, interval_minutes, days)
        if candles:
            return candles

        # ========== 3. FALLBACK: MOEX ==========
        debug(f"🔄 CandleBuilder не вернул данные, пробуем MOEX для {ticker}...")
        candles = self._fetch_candles_moex(ticker, interval_minutes, days)
        if candles:
            return candles

        warning(f"⚠️ Не удалось получить данные для {ticker}")
        return []

    # ========== ДИНАМИЧЕСКИЙ ИНТЕРВАЛ ==========
    def _get_optimal_interval(self, ticker: str, days: int = 30) -> int:
        """Автоматический выбор интервала на основе волатильности"""
        try:
            from trading_bot.api.tbank_client import tbank

            figi = tbank._get_figi_by_ticker(ticker)
            if not figi:
                return 5

            candles = tbank.get_candles(figi, days=min(days, 7), interval_minutes=5)

            if not candles or len(candles) < 20:
                return 5

            prices = [c[0] for c in candles]

            returns = []
            for i in range(1, len(prices)):
                if prices[i - 1] > 0:
                    returns.append(abs((prices[i] - prices[i - 1]) / prices[i - 1]))

            avg_volatility = sum(returns) / len(returns) * 100 if returns else 1.0

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
        """Профессиональный анализ акции"""
        analysis_start = time.time()

        # Проверка включения
        if not self.enabled:
            debug(f"📊 Технический анализ отключён для {name}")
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="HOLD (технический анализ отключён)",
                signals=["⚙️ Технический анализ отключён в настройках"]
            )

        if is_backtest:
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="BACKTEST_MODE",
                signals=["Бэктест: используйте встроенный анализатор"]
            )

        if not ticker:
            ticker = name

        info(f"\n{'='*60}")
        info(f"🔬 ТЕХНИЧЕСКИЙ АНАЛИЗ: {ticker}")
        info(f"{'='*60}")

        tbank = _get_tbank()

        # Получение свечей
        candles = self.fetch_candles(ticker, interval_minutes=CANDLES_INTERVAL, days=CANDLES_DAYS)

        min_candles = self._get_min_candles_for_ticker(ticker)

        if not candles or len(candles) < min_candles:
            warning(f"⚠️ Недостаточно данных для {ticker} (нужно {min_candles}, есть {len(candles) if candles else 0})")
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="HOLD (недостаточно данных)",
                signals=[f"⚠️ Мало данных ({len(candles) if candles else 0}/{min_candles} свечей)"]
            )

        info(f"✅ {ticker}: достаточно свечей ({len(candles)}/{min_candles})")

        # Универсальная конвертация свечей
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
            else:
                debug(f"⚠️ Неизвестный формат свечи: {type(c)}")
                continue

        prices = [c['close'] for c in candle_dicts]
        volumes = [c['volume'] for c in candle_dicts]
        current_price = prices[-1] if prices else 0

        info(f"📊 Текущая цена: {current_price:.2f}₽")
        info(f"📊 Диапазон цен: {min(prices):.2f}₽ - {max(prices):.2f}₽")

        # Анализ свечных паттернов
        candle_patterns = self.analyze_candle_patterns(candle_dicts)
        if candle_patterns:
            info(f"🕯️ Найдены свечные паттерны: {list(candle_patterns.keys())}")
            self.log_candle_patterns(candle_patterns, ticker)

        # Уровни поддержки/сопротивления
        supports, resistances, round_support, round_resistance = self.find_support_resistance_advanced(candle_dicts)
        if supports:
            info(f"📊 Уровни поддержки: {[f'{s:.2f}' for s in supports[:3]]}")
        if resistances:
            info(f"📊 Уровни сопротивления: {[f'{r:.2f}' for r in resistances[:3]]}")

        # Базовый сигнал от Strategy Engine
        info(f"📊 Запуск StrategyEngine для {ticker}...")
        signal_result = self.engine.analyze_signal(prices, volumes, name, candles=candle_dicts)
        info(f"📊 Базовый score: {signal_result.score}")

        # Корректировка сигнала по паттернам и уровням
        adjusted_score = self._adjust_score_by_patterns(
            signal_result.score, candle_patterns, supports, resistances,
            current_price, round_support, round_resistance
        )

        if adjusted_score != signal_result.score:
            info(f"📊 Корректировка score: {signal_result.score} → {adjusted_score}")
            signal_result.score = adjusted_score

        # Фундаментальное усиление
        info(f"📊 Запрос фундаментального анализа для {ticker}...")
        signal_result = await self._apply_fundamental_enhancement(ticker, signal_result)

        # Финальная рекомендация
        self._set_final_recommendation(signal_result, name)

        analysis_time = time.time() - analysis_start
        info(f"📊 Общее время анализа: {analysis_time:.2f}с")
        info(f"{'='*60}")

        return StockAnalysis(
            figi=figi, name=name, score=signal_result.score,
            buy_signal=signal_result.buy_signal, sell_signal=signal_result.sell_signal,
            recommendation=signal_result.recommendation, signals=signal_result.signals,
            rsi=signal_result.rsi, macd=signal_result.macd, volume_ratio=signal_result.volume_ratio,
            candle_patterns=candle_patterns, support_levels=supports[:3],
            resistance_levels=resistances[:3], round_support=round_support, round_resistance=round_resistance
        )

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========
    def _adjust_score_by_patterns(self, score: int, patterns: Dict, supports: List[float],
                                   resistances: List[float], current_price: float,
                                   round_support: float, round_resistance: float) -> int:
        """Корректировка score на основе свечных паттернов и уровней"""
        adjusted = score
        adjustments = []

        # Свечные паттерны
        if 'hammer' in patterns or 'bullish_engulfing' in patterns:
            if adjusted > 0:
                adjusted += 2
                adjustments.append("бычий паттерн +2")

        if 'hanging_man' in patterns or 'bearish_engulfing' in patterns:
            if adjusted < 0:
                adjusted -= 2
                adjustments.append("медвежий паттерн -2")

        if 'doji' in patterns:
            adjusted = int(adjusted * 0.5) if abs(adjusted) > 0 else 0
            adjustments.append("доджи → score/2")

        # Уровни поддержки/сопротивления
        if supports:
            nearest_support = max([s for s in supports if s < current_price], default=None)
            if nearest_support and (current_price - nearest_support) / current_price * 100 < 1.0:
                if adjusted > 0:
                    adjusted += 1
                    adjustments.append(f"уровень поддержки {nearest_support:.2f} +1")

        if resistances:
            nearest_resistance = min([r for r in resistances if r > current_price], default=None)
            if nearest_resistance and (nearest_resistance - current_price) / current_price * 100 < 1.0:
                if adjusted < 0:
                    adjusted -= 1
                    adjustments.append(f"уровень сопротивления {nearest_resistance:.2f} -1")

        # Круглые уровни
        if abs(current_price - round_support) / current_price * 100 < 0.5 and adjusted > 0:
            adjusted += 1
            adjustments.append(f"круглый уровень поддержки {round_support:.0f} +1")
        if abs(current_price - round_resistance) / current_price * 100 < 0.5 and adjusted < 0:
            adjusted -= 1
            adjustments.append(f"круглый уровень сопротивления {round_resistance:.0f} -1")

        if adjustments:
            debug(f"📊 Корректировки: {', '.join(adjustments)}")

        return adjusted

    async def _apply_fundamental_enhancement(self, ticker: str, signal_result) -> Any:
        """Применение фундаментального усиления с защитой от ошибок и таймаутом"""
        fa_start = time.time()

        # Проверка настроек
        try:
            from trading_bot.core.settings_manager import settings_manager
            fundamental_enabled = settings_manager.get('fundamental_enabled', False)
            use_fundamental_in_trading = settings_manager.get('use_fundamental_in_trading', False)

            if not fundamental_enabled or not use_fundamental_in_trading:
                debug(f"📊 Фундаментальный анализ отключён в настройках для {ticker}")
                return signal_result
        except ImportError:
            debug(f"⚠️ settings_manager не найден, фундаментальный анализ пропущен для {ticker}")
            return signal_result
        except Exception as e:
            debug(f"⚠️ Ошибка проверки настроек для {ticker}: {e}")
            return signal_result

        # Проверка доступности
        if not FUNDAMENTAL_AVAILABLE or enhance_trading_decision is None:
            debug(f"📊 Фундаментальный анализатор недоступен для {ticker}")
            return signal_result

        # Выполнение с таймаутом
        try:
            debug(f"📊 Запуск фундаментального анализа для {ticker} (таймаут {FUNDAMENTAL_TIMEOUT}с)...")

            enhanced_score, enhanced_signals, fund_data = await asyncio.wait_for(
                enhance_trading_decision(
                    ticker=ticker,
                    technical_score=signal_result.score,
                    technical_signals=signal_result.signals
                ),
                timeout=FUNDAMENTAL_TIMEOUT
            )

            fa_time = time.time() - fa_start
            info(f"📊 Фундаментальный анализ {ticker} завершён за {fa_time:.2f}с")

            if enhanced_score != signal_result.score:
                info(f"📊 Фундаментальное усиление: {signal_result.score} → {enhanced_score}")
                signal_result.score = enhanced_score
                signal_result.signals = enhanced_signals
                signal_result.buy_signal = enhanced_score >= self.engine.score_threshold_long
                signal_result.sell_signal = enhanced_score <= self.engine.score_threshold_short

                if fund_data:
                    info(f"📊 Фундаментальные данные: {fund_data.get('action', 'Нейтрально')} "
                         f"(оценка: {fund_data.get('overall_score', 0):.0f})")

        except asyncio.TimeoutError:
            fa_time = time.time() - fa_start
            debug(f"⏰ Таймаут фундаментального анализа для {ticker} ({fa_time:.1f}с > {FUNDAMENTAL_TIMEOUT}с), используем только технический анализ")
        except Exception as e:
            debug(f"❌ Фундаментальный анализ временно недоступен для {ticker}: {e}")

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
            debug(f"⚪ {name}: HOLD, score={signal_result.score}")

    # ========== ДИНАМИЧЕСКИЙ РАСЧЁТ SL/TP ==========
    def calculate_dynamic_sltp(self, prices: List[float], volumes: List[int], side: str) -> Dict[str, float]:
        """Динамический расчёт тейк-профита и стоп-лосса"""
        if len(prices) < 20:
            debug("⚠️ Недостаточно данных для расчёта SL/TP, используем значения по умолчанию")
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
            take_profit_target = (resistance - current_price) / current_price * 100 if resistance > current_price else atr_pct * 2
            stop_loss_target = (current_price - support) / current_price * 100 if support < current_price else atr_pct * 1.5
        else:
            take_profit_target = (current_price - support) / current_price * 100 if support < current_price else atr_pct * 2
            stop_loss_target = (resistance - current_price) / current_price * 100 if resistance > current_price else atr_pct * 1.5

        # Корректировки
        if volatility > 2:
            take_profit_target *= 1.5
            stop_loss_target *= 1.3
            debug(f"📊 Высокая волатильность ({volatility:.1f}%), SL/TP увеличены")
        elif volatility < 0.5:
            take_profit_target *= 0.7
            stop_loss_target *= 0.7
            debug(f"📊 Низкая волатильность ({volatility:.1f}%), SL/TP уменьшены")

        if volume_impulse > 1.5:
            take_profit_target *= 1.2
            debug(f"📊 Объёмный импульс ({volume_impulse:.1f}x), TP увеличен")

        result = {
            'take_profit': round(max(0.5, min(5.0, take_profit_target)), 2),
            'stop_loss': round(max(0.3, min(3.0, stop_loss_target)), 2),
            'trailing': round(stop_loss_target * 0.4, 2),
            'atr_pct': round(atr_pct, 2),
            'volatility': round(volatility, 2),
            'volume_impulse': round(volume_impulse, 2)
        }

        debug(f"📊 Динамические SL/TP: TP={result['take_profit']}%, SL={result['stop_loss']}%, Trailing={result['trailing']}%")
        return result

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

    # ========== МЕТОДЫ АНАЛИЗА СВЕЧНЫХ ПАТТЕРНОВ ==========
    def analyze_candle_patterns(self, candles: List[Dict]) -> Dict[str, str]:
        """Анализ японских свечных паттернов"""
        if len(candles) < 3:
            return {}

        patterns = {}

        try:
            last = candles[-1]
            prev = candles[-2]

            # Одиночные паттерны
            patterns.update(self._analyze_single_candle_patterns(last))

            # Двойные паттерны
            patterns.update(self._analyze_double_candle_patterns(last, prev))

        except Exception as e:
            debug(f"Ошибка анализа свечей: {e}")

        return patterns

    def _analyze_single_candle_patterns(self, last: Dict) -> Dict[str, str]:
        """Анализ одиночных свечных паттернов"""
        patterns = {}
        body = abs(last['close'] - last['open'])
        lower_shadow = min(last['open'], last['close']) - last['low']
        upper_shadow = last['high'] - max(last['open'], last['close'])

        # Молот / Повешенный
        if body > 0 and lower_shadow >= body * 2 and upper_shadow < body * 0.3:
            patterns['hammer'] = '🟢 МОЛОТ - сигнал к покупке'
            if last['close'] < last['open']:
                patterns['hanging_man'] = '🔴 ПОВЕШЕННЫЙ - сигнал к продаже'

        # Падающая звезда / Перевёрнутый молот
        if body > 0 and upper_shadow >= body * 2 and lower_shadow < body * 0.3:
            patterns['shooting_star'] = '🔴 ПАДАЮЩАЯ ЗВЕЗДА - разворот вниз'
            if last['close'] > last['open']:
                patterns['inverted_hammer'] = '🟢 ПЕРЕВЁРНУТЫЙ МОЛОТ - возможен рост'

        # Доджи
        candle_range = last['high'] - last['low']
        if candle_range > 0 and body <= candle_range * 0.1:
            patterns['doji'] = '⚠️ ДОДЖИ - неопределённость, возможен разворот'

        return patterns

    def _analyze_double_candle_patterns(self, last: Dict, prev: Dict) -> Dict[str, str]:
        """Анализ двойных свечных паттернов"""
        patterns = {}
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

        # Харами
        prev_body_abs = abs(prev['close'] - prev['open'])
        curr_body_abs = abs(last['close'] - last['open'])
        if prev_body_abs > 0 and curr_body_abs < prev_body_abs * 0.5:
            if last['low'] > prev['low'] and last['high'] < prev['high']:
                if prev_is_bearish and curr_is_bullish:
                    patterns['bullish_harami'] = '🟢 БЫЧЬЯ ХАРАМИ - разворот вверх'
                elif prev_is_bullish and curr_is_bearish:
                    patterns['bearish_harami'] = '🔴 МЕДВЕЖЬЯ ХАРАМИ - разворот вниз'

        return patterns

    def find_support_resistance_advanced(self, candles: List[Dict]) -> Tuple[List[float], List[float], float, float]:
        """Продвинутый поиск уровней поддержки и сопротивления"""
        if len(candles) < 30:
            return [], [], 0, 0

        highs, lows = [], []

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

        supports = self._cluster_levels_advanced(lows, tolerance=0.3)
        resistances = self._cluster_levels_advanced(highs, tolerance=0.3)

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

    # ========== СИНХРОННАЯ ОБЁРТКА ==========
    def analyze_stock_sync(self, figi: str, name: str, ticker: str = None, is_backtest: bool = False) -> StockAnalysis:
        """Синхронная обёртка для analyze_stock с защитой от закрытого event loop"""
        sync_start = time.time()

        if not self.enabled:
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="HOLD (технический анализ отключён)",
                signals=["⚙️ Технический анализ отключён в настройках"]
            )

        # Проверяем кэш
        if ticker:
            cached = self._get_cached_analysis(ticker)
            if cached:
                debug(f"📊 {ticker}: возвращаем кэшированный результат")
                return StockAnalysis(**cached)

        # Создаём новый event loop
        loop = None
        try:
            # Пытаемся получить текущий loop
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                debug(f"📊 {ticker}: event loop закрыт, создаём новый")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            debug(f"📊 {ticker}: нет event loop, создаём новый")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            debug(f"📊 {ticker}: запуск асинхронного анализа...")
            result = loop.run_until_complete(
                self.analyze_stock(figi, name, ticker, is_backtest)
            )

            # Сохраняем в кэш
            if ticker and result:
                cache_data = {
                    'figi': figi, 'name': name, 'score': result.score,
                    'buy_signal': result.buy_signal, 'sell_signal': result.sell_signal,
                    'recommendation': result.recommendation, 'signals': result.signals,
                    'rsi': result.rsi, 'macd': result.macd, 'volume_ratio': result.volume_ratio
                }
                self._set_cached_analysis(ticker, cache_data)

            sync_time = time.time() - sync_start
            debug(f"📊 {ticker}: синхронный анализ завершён за {sync_time:.2f}с")
            return result

        except asyncio.CancelledError:
            warning(f"📊 {ticker}: анализ отменён")
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="HOLD (анализ отменён)",
                signals=["⏰ Анализ отменён"]
            )
        except Exception as e:
            error(f"❌ Ошибка в analyze_stock_sync для {name}: {e}")
            import traceback
            debug(f"   {traceback.format_exc()}")
            return StockAnalysis(
                figi=figi, name=name, score=0,
                buy_signal=False, sell_signal=False,
                recommendation="HOLD (ошибка анализа)",
                signals=[f"❌ Ошибка: {str(e)[:50]}"]
            )
        finally:
            # Не закрываем loop, если он был создан не здесь
            pass

    # ========== АНАЛИЗ С ГОТОВЫМИ СВЕЧАМИ ==========
    def analyze_with_candles(self, ticker: str, candles: List, current_price: float) -> Dict[str, Any]:
        """Анализ акции с уже полученными свечами (для сканера)"""
        start_time = time.time()
        info(f"📊 [ТЕХ.АНАЛИЗ] Начинаем анализ {ticker}...")
        debug(f"   Тип входных свечей: {type(candles).__name__}, длина: {len(candles) if candles else 0}")

        try:
            min_needed = self._get_min_candles_for_ticker(ticker)

            if not candles or len(candles) < min_needed:
                warning(f"📊 [ТЕХ.АНАЛИЗ] {ticker}: недостаточно свечей ({len(candles) if candles else 0}/{min_needed})")
                return {
                    'score': 0, 'signals': [f'⚠️ Недостаточно данных ({len(candles) if candles else 0} свечей)'],
                    'buy_signal': False, 'sell_signal': False, 'recommendation': 'HOLD',
                    'rsi': None, 'macd': None, 'volume_ratio': 1.0
                }

            # Универсальная конвертация свечей
            prices, volumes, candle_dicts = self._convert_candles(candles, current_price)

            debug(f"   📊 Конвертация: {len(prices)} цен из {len(candles)} свечей")

            if len(prices) < min_needed:
                warning(f"📊 [ТЕХ.АНАЛИЗ] {ticker}: после конвертации {len(prices)} свечей (нужно {min_needed})")
                return {
                    'score': 0, 'signals': [f'⚠️ Ошибка конвертации: получено {len(prices)} из {len(candles)}'],
                    'buy_signal': False, 'sell_signal': False, 'recommendation': 'HOLD',
                    'rsi': None, 'macd': None, 'volume_ratio': 1.0
                }

            debug(f"   📊 Создано {len(candle_dicts)} словарей свечей для индикаторов")

            # Запуск StrategyEngine
            signal_result = self.engine.analyze_signal(
                prices=prices, volumes=volumes, name=ticker, candles=candle_dicts
            )

            elapsed = time.time() - start_time
            info(f"📊 [ТЕХ.АНАЛИЗ] {ticker}: score={signal_result.score}, сигналов={len(signal_result.signals)}, время={elapsed:.2f}с")

            if signal_result.buy_signal:
                info(f"   🟢 {ticker}: СИГНАЛ НА ПОКУПКУ! score={signal_result.score}")
            elif signal_result.sell_signal:
                info(f"   🔴 {ticker}: СИГНАЛ НА SHORT! score={signal_result.score}")
            else:
                debug(f"   ⚪ {ticker}: HOLD, score={signal_result.score}")

            if signal_result.signals:
                debug(f"   📊 {ticker}: сигналы: {', '.join(signal_result.signals[:3])}")

            return {
                'score': signal_result.score, 'signals': signal_result.signals,
                'buy_signal': signal_result.buy_signal, 'sell_signal': signal_result.sell_signal,
                'recommendation': signal_result.recommendation,
                'rsi': signal_result.rsi, 'macd': signal_result.macd, 'volume_ratio': signal_result.volume_ratio
            }

        except Exception as e:
            error(f"📊 [ТЕХ.АНАЛИЗ] {ticker}: ОШИБКА - {e}")
            import traceback
            error(f"   Трассировка: {traceback.format_exc()}")
            return {
                'score': 0, 'signals': [f'❌ Ошибка: {str(e)[:50]}'],
                'buy_signal': False, 'sell_signal': False, 'recommendation': 'HOLD',
                'rsi': None, 'macd': None, 'volume_ratio': 1.0
            }

    def _convert_candles(self, candles: List, current_price: float) -> Tuple[List[float], List[float], List[Dict]]:
        """Конвертация свечей в единый формат"""
        prices = []
        volumes = []
        highs = []
        lows = []
        opens = []

        fallback_price = current_price if current_price > 0 else 100.0

        for idx, c in enumerate(candles):
            close_val = None
            high_val = None
            low_val = None
            open_val = None
            volume_val = 0

            # Объект с атрибутами
            if hasattr(c, 'close'):
                close_val = c.close
                high_val = getattr(c, 'high', close_val)
                low_val = getattr(c, 'low', close_val)
                open_val = getattr(c, 'open', close_val)
                volume_val = getattr(c, 'volume', 0)

            # Словарь
            elif isinstance(c, dict):
                close_val = c.get('close', fallback_price)
                high_val = c.get('high', close_val)
                low_val = c.get('low', close_val)
                open_val = c.get('open', close_val)
                volume_val = c.get('volume', 0)

            # Кортеж/список
            elif isinstance(c, (list, tuple)) and len(c) >= 1:
                close_val = c[0] if c[0] else fallback_price
                volume_val = c[1] if len(c) > 1 and c[1] else 0
                high_val = close_val * 1.005
                low_val = close_val * 0.995
                open_val = close_val

            if close_val and close_val > 0:
                prices.append(float(close_val))
                volumes.append(float(volume_val) if volume_val else 0)
                highs.append(float(high_val) if high_val else close_val)
                lows.append(float(low_val) if low_val else close_val)
                opens.append(float(open_val) if open_val else close_val)

        # Создаём словари свечей
        candle_dicts = []
        for i in range(len(prices)):
            candle_dicts.append({
                'close': prices[i], 'volume': volumes[i] if i < len(volumes) else 0,
                'high': highs[i] if i < len(highs) else prices[i],
                'low': lows[i] if i < len(lows) else prices[i],
                'open': opens[i] if i < len(opens) else prices[i],
            })

        return prices, volumes, candle_dicts


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
analyzer = TechnicalAnalyzer()