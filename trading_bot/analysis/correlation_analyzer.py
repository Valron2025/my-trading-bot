"""Корреляционный анализ - поиск связанных движений между акциями"""

from typing import Dict, List, Optional, Any
import math

from ..logger import info, debug, warning
from ..cache import TTLCache
from trading_bot.cache.cache_manager import TTLCache as UnifiedCache
USE_UNIFIED_CACHE = False


class CorrelationAnalyzer:
    """Анализатор корреляций между акциями"""

    def __init__(self):
        self.enabled = True
        self.correlation_threshold = 0.7
        self.lookback_days = 30
        self._cache = TTLCache(default_ttl=3600, max_size=100, name="correlation_cache")
        
        if USE_UNIFIED_CACHE:
            self._unified_cache = UnifiedCache(default_ttl=3600, name="correlation_analyzer")

        info("✅ CorrelationAnalyzer инициализирован")

    def analyze(self, ticker: str, open_positions: List[str] = None) -> Optional[Dict[str, Any]]:
        """
        Анализ корреляций для тикера

        Args:
            ticker: Тикер для анализа
            open_positions: Список открытых позиций

        Returns:
            Dict с полями: score, penalty, recommendation, correlations
        """
        if not self.enabled:
            return None

        try:
            # Получаем цены тикера
            prices = self._get_price_history(ticker)
            if not prices or len(prices) < 20:
                return None

            # Анализируем корреляции с открытыми позициями
            correlations = []
            max_correlation = 0

            if open_positions:
                for other in open_positions[:20]:  # Ограничиваем для производительности
                    if other == ticker:
                        continue

                    other_prices = self._get_price_history(other)
                    if other_prices and len(other_prices) >= 20:
                        corr = self._calculate_correlation(prices, other_prices)
                        if abs(corr) > self.correlation_threshold:
                            correlations.append({
                                'ticker': other,
                                'correlation': round(corr, 2),
                                'strength': 'strong' if abs(corr) > 0.8 else 'moderate'
                            })
                        max_correlation = max(max_correlation, abs(corr))

            # Рассчитываем penalty (штраф) и score
            penalty = min(1.0, max_correlation * 0.8)

            if penalty == 0:
                score = 2
                recommendation = "✅ Низкая корреляция с портфелем"
            elif penalty < 0.3:
                score = 1
                recommendation = "🟢 Приемлемая корреляция"
            elif penalty < 0.6:
                score = 0
                recommendation = "⚪ Средняя корреляция"
            else:
                score = -2
                recommendation = "🔴 Высокая корреляция с портфелем"

            return {
                'score': score,
                'penalty': penalty,
                'correlations': correlations[:10],
                'recommendation': recommendation,
                'max_correlation': round(max_correlation, 2)
            }

        except Exception as e:
            debug(f"Ошибка корреляционного анализа {ticker}: {e}")
            return None

    def _get_price_history(self, ticker: str) -> List[float]:
        """Получение истории цен для тикера"""
        try:
            from trading_bot.api.tbank_client import tbank
            from trading_bot.utils.figi_resolver import get_figi_resolver

            resolver = get_figi_resolver()
            figi = resolver.get_figi_by_ticker(ticker)
            if not figi:
                return []

            candles = tbank.get_candles(figi, days=self.lookback_days, interval_minutes=60)
            if not candles:
                return []

            return [c[0] for c in candles if c and len(c) > 0]

        except Exception as e:
            debug(f"Ошибка получения цен для {ticker}: {e}")
            return []

    def _calculate_correlation(self, prices1: List[float], prices2: List[float]) -> float:
        """Расчёт коэффициента корреляции Пирсона"""
        min_len = min(len(prices1), len(prices2))
        if min_len < 5:
            return 0

        p1 = prices1[-min_len:]
        p2 = prices2[-min_len:]

        # Считаем доходности
        returns1 = [(p1[i] - p1[i - 1]) / p1[i - 1] for i in range(1, len(p1))]
        returns2 = [(p2[i] - p2[i - 1]) / p2[i - 1] for i in range(1, len(p2))]

        if len(returns1) < 5:
            return 0

        mean1 = sum(returns1) / len(returns1)
        mean2 = sum(returns2) / len(returns2)

        numerator = sum((r1 - mean1) * (r2 - mean2) for r1, r2 in zip(returns1, returns2))
        denom1 = math.sqrt(sum((r1 - mean1) ** 2 for r1 in returns1))
        denom2 = math.sqrt(sum((r2 - mean2) ** 2 for r2 in returns2))

        if denom1 * denom2 == 0:
            return 0

        return numerator / (denom1 * denom2)


# Глобальный экземпляр
correlation_analyzer = CorrelationAnalyzer()
