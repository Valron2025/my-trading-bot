#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fundamental_analyzer.py - ПРОДАКШЕН ВЕРСИЯ ДЛЯ RENDER
Фундаментальный анализ для торгового бота
"""

import os
import time
import asyncio
import aiohttp
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Фикс для event loop
try:
    import nest_asyncio

    nest_asyncio.apply()
except ImportError:
    pass


def get_moscow_time() -> datetime:
    """Получение московского времени"""
    return datetime.now(ZoneInfo("Europe/Moscow"))


# Загрузка переменных окружения
from dotenv import load_dotenv

load_dotenv()

# Импорт Yahoo Finance
YFINANCE_AVAILABLE = False
try:
    import yfinance as yf

    YFINANCE_AVAILABLE = True
    logger.info("✅ Yahoo Finance module loaded")
except ImportError:
    logger.warning("⚠️ yfinance not installed. Install: pip install yfinance")


@dataclass
class FundamentalMetrics:
    """Фундаментальные метрики компании"""
    ticker: str
    name: str = ""

    # Мультипликаторы
    pe_ratio: float = 0.0
    pb_ratio: float = 0.0
    roe: float = 0.0

    # Дивиденды
    dividend_yield: float = 0.0

    # Размер и ликвидность
    market_cap: float = 0.0
    volume_today: float = 0.0
    value_today_rub: float = 0.0

    # Долг
    debt_to_equity: float = 0.0

    # Рост
    revenue_growth: float = 0.0

    # Источник данных
    data_source: str = "unknown"
    fetched_at: Optional[datetime] = None

    # Кэш для вычислений
    _scores_cache: Dict[str, float] = field(default_factory=dict)

    def _get_cached_score(self, name: str, calculator) -> float:
        """Получение закэшированной оценки"""
        if name in self._scores_cache:
            return self._scores_cache[name]
        score = calculator()
        self._scores_cache[name] = score
        return score

    @property
    def has_valid_data(self) -> bool:
        """Проверка наличия валидных данных"""
        return (
                (0.1 < self.pe_ratio < 100) or
                (0.1 < self.pb_ratio < 20) or
                (0.1 < self.roe < 100) or
                self.value_today_rub > 1_000_000 or
                self.market_cap > 100_000_000
        )

    @property
    def value_score(self) -> float:
        """Оценка стоимости на основе P/E и P/B"""

        def calculate():
            if self.pe_ratio == 0 and self.pb_ratio == 0:
                return 50.0

            score = 50.0

            # Оценка P/E
            if self.pe_ratio > 0:
                if self.pe_ratio < 10:
                    score += 20
                elif self.pe_ratio < 15:
                    score += 10
                elif self.pe_ratio < 20:
                    score -= 5
                else:
                    score -= 15

            # Оценка P/B
            if self.pb_ratio > 0:
                if self.pb_ratio < 1:
                    score += 15
                elif self.pb_ratio < 2:
                    score += 5
                elif self.pb_ratio > 3:
                    score -= 10

            return max(0, min(100, score))

        return self._get_cached_score("value_score", calculate)

    @property
    def quality_score(self) -> float:
        """Оценка качества на основе ROE и роста"""

        def calculate():
            if self.roe == 0 and self.revenue_growth == 0:
                return 50.0

            score = 50.0

            # Оценка ROE
            if self.roe > 0:
                if self.roe > 25:
                    score += 20
                elif self.roe > 15:
                    score += 10
                elif self.roe < 5:
                    score -= 10

            # Оценка роста выручки
            if self.revenue_growth != 0:
                if self.revenue_growth > 20:
                    score += 15
                elif self.revenue_growth > 10:
                    score += 8
                elif self.revenue_growth < 0:
                    score -= 15

            return max(0, min(100, score))

        return self._get_cached_score("quality_score", calculate)

    @property
    def safety_score(self) -> float:
        """Оценка безопасности на основе долговой нагрузки"""

        def calculate():
            if self.debt_to_equity == 0:
                return 50.0

            score = 50.0

            if self.debt_to_equity < 0.5:
                score += 20
            elif self.debt_to_equity < 1:
                score += 10
            elif self.debt_to_equity > 2:
                score -= 15
            elif self.debt_to_equity > 3:
                score -= 25

            return max(0, min(100, score))

        return self._get_cached_score("safety_score", calculate)

    @property
    def liquidity_score(self) -> float:
        """Оценка ликвидности на основе оборота"""

        def calculate():
            score = 50.0

            if self.value_today_rub > 0:
                if self.value_today_rub > 50_000_000:
                    score += 30
                elif self.value_today_rub > 10_000_000:
                    score += 15
                elif self.value_today_rub > 5_000_000:
                    score += 5
                elif self.value_today_rub < 1_000_000:
                    score -= 20

            return max(0, min(100, score))

        return self._get_cached_score("liquidity_score", calculate)

    @property
    def overall_score(self) -> float:
        """Общая фундаментальная оценка"""

        def calculate():
            has_fundamental = (
                    (0.1 < self.pe_ratio < 100) or
                    (0.1 < self.pb_ratio < 20) or
                    (0.1 < self.roe < 100)
            )

            if has_fundamental:
                score = (
                        self.value_score * 0.30 +
                        self.quality_score * 0.30 +
                        self.safety_score * 0.25 +
                        self.liquidity_score * 0.15
                )
            else:
                score = self.liquidity_score
                if self.market_cap > 1_000_000_000_000:
                    score += 10
                elif self.market_cap > 100_000_000_000:
                    score += 5

            return max(0, min(100, score))

        return self._get_cached_score("overall_score", calculate)

    def get_recommendation(self) -> Tuple[str, float]:
        """Получение рекомендации на основе общей оценки"""
        score = self.overall_score

        if score >= 70:
            return ("STRONG_BUY", score)
        elif score >= 55:
            return ("BUY", score)
        elif score >= 40:
            return ("HOLD", score)
        elif score >= 25:
            return ("SELL", score)
        else:
            return ("STRONG_SELL", score)

    def get_reasons(self, action: str) -> List[str]:
        """Генерация причин для действия"""
        reasons = []

        if action in ['STRONG_BUY', 'BUY']:
            if 0 < self.pe_ratio < 10:
                reasons.append(f"Low P/E ({self.pe_ratio:.1f})")
            if 0 < self.pb_ratio < 1:
                reasons.append(f"Discount to book (P/B={self.pb_ratio:.2f})")
            if self.roe > 20:
                reasons.append(f"High ROE ({self.roe:.1f}%)")
            if self.dividend_yield > 5:
                reasons.append(f"Good dividend ({self.dividend_yield:.1f}%)")
            if self.value_today_rub > 50_000_000:
                reasons.append(f"High liquidity")

        elif action in ['STRONG_SELL', 'SELL']:
            if self.pe_ratio > 20:
                reasons.append(f"High P/E ({self.pe_ratio:.1f})")
            if self.debt_to_equity > 2:
                reasons.append(f"High debt (D/E={self.debt_to_equity:.1f})")
            if 0 < self.roe < 5:
                reasons.append(f"Low ROE ({self.roe:.1f}%)")
            if 0 < self.value_today_rub < 5_000_000:
                reasons.append(f"Low liquidity")

        if not reasons:
            reasons.append("Neutral fundamentals")

        return reasons[:5]

    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        action, score = self.get_recommendation()
        return {
            'ticker': self.ticker,
            'name': self.name,
            'pe_ratio': self.pe_ratio,
            'pb_ratio': self.pb_ratio,
            'roe': self.roe,
            'dividend_yield': self.dividend_yield,
            'market_cap': self.market_cap,
            'value_today_rub': self.value_today_rub,
            'value_score': self.value_score,
            'quality_score': self.quality_score,
            'safety_score': self.safety_score,
            'liquidity_score': self.liquidity_score,
            'overall_score': self.overall_score,
            'recommendation': action,
            'data_source': self.data_source,
            'fetched_at': self.fetched_at.isoformat() if self.fetched_at else None
        }


class FundamentalAnalyzer:
    """Фундаментальный анализатор"""

    def __init__(
            self,
            enable_cache: bool = True,
            cache_ttl_hours: int = 12,
            debug_mode: bool = False
    ):
        self.enable_cache = enable_cache
        self.enabled = True
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.cache: Dict[str, Tuple[FundamentalMetrics, datetime]] = {}

        # Статистика
        self.stats = {
            'total_requests': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'api_errors': 0,
            'yahoo_hits': 0,
            'last_update': None
        }

        # Rate limiter для Yahoo
        self.yahoo_rate_limiter = asyncio.Semaphore(1)
        self.last_yahoo_request = 0
        self.yahoo_min_interval = 4.0  # 15 запросов в минуту

        self._log_init()

    def _log_init(self):
        """Логирование инициализации"""
        logger.info("=" * 60)
        logger.info("🚀 FundamentalAnalyzer initialized for Render")
        logger.info(
            f"   📦 Cache: {'enabled' if self.enable_cache else 'disabled'}, TTL: {self.cache_ttl.total_seconds() / 3600:.0f}h")
        logger.info(f"   📊 Status: {'ENABLED' if self.enabled else 'DISABLED'}")
        logger.info(f"   📈 Yahoo Finance: {'available' if YFINANCE_AVAILABLE else 'not available'}")
        logger.info("=" * 60)

    def set_enabled(self, enabled: bool) -> None:
        """Включение/выключение анализатора"""
        self.enabled = enabled
        status = "ENABLED" if enabled else "DISABLED"
        logger.info(f"📊 FundamentalAnalyzer {status}")

    def is_enabled(self) -> bool:
        """Проверка, включен ли анализатор"""
        return self.enabled

    async def _wait_for_yahoo_rate_limit(self) -> None:
        """Ожидание для соблюдения rate limit Yahoo"""
        async with self.yahoo_rate_limiter:
            now = time.time()
            elapsed = now - self.last_yahoo_request
            if elapsed < self.yahoo_min_interval:
                wait_time = self.yahoo_min_interval - elapsed
                await asyncio.sleep(wait_time)
            self.last_yahoo_request = time.time()

    async def fetch_metrics(self, ticker: str, force_refresh: bool = False) -> Optional[FundamentalMetrics]:
        """
        Получение фундаментальных метрик для тикера

        Args:
            ticker: Тикер акции
            force_refresh: Принудительное обновление кэша

        Returns:
            FundamentalMetrics или None
        """
        if not self.enabled:
            return None

        ticker = ticker.upper()
        now = get_moscow_time()

        # Проверка кэша
        if not force_refresh and self.enable_cache and ticker in self.cache:
            metrics, timestamp = self.cache[ticker]
            cache_age = (now - timestamp).total_seconds()

            if cache_age < self.cache_ttl.total_seconds():
                if metrics.has_valid_data:
                    self.stats['cache_hits'] += 1
                    logger.debug(f"📦 Cache hit for {ticker}")
                    return metrics
                else:
                    del self.cache[ticker]

        self.stats['cache_misses'] += 1
        self.stats['total_requests'] += 1

        # Получение данных
        try:
            metrics = await self._fetch_all_data(ticker)
        except Exception as e:
            self.stats['api_errors'] += 1
            logger.error(f"❌ Failed to fetch data for {ticker}: {e}")
            return None

        # Сохранение в кэш
        if metrics and metrics.has_valid_data and self.enable_cache:
            if len(self.cache) >= 500:
                oldest = next(iter(self.cache))
                del self.cache[oldest]

            self.cache[ticker] = (metrics, now)
            self.stats['last_update'] = now
            logger.debug(f"💾 Cached data for {ticker}")

        return metrics

    async def _fetch_all_data(self, ticker: str) -> Optional[FundamentalMetrics]:
        """Сбор данных из всех источников"""
        ticker_upper = ticker.upper()
        metrics = FundamentalMetrics(ticker=ticker_upper)

        # 1. Рыночные данные из MOEX
        await self._fetch_moex_market_data(ticker, metrics)

        # 2. Статистика из MOEX (P/E, P/B, ROE)
        await self._fetch_moex_statistics(ticker_upper, metrics)

        # 3. Если нет данных из MOEX, пробуем Yahoo Finance
        has_moex_data = (
                (0.1 < metrics.pe_ratio < 100) or
                (0.1 < metrics.pb_ratio < 20) or
                (0.1 < metrics.roe < 100)
        )

        if not has_moex_data and YFINANCE_AVAILABLE:
            yahoo_data = await self._fetch_yahoo_data(ticker_upper)
            if yahoo_data:
                self.stats['yahoo_hits'] += 1
                metrics.data_source = "Yahoo Finance"
                metrics.pe_ratio = yahoo_data.get('pe', metrics.pe_ratio)
                metrics.pb_ratio = yahoo_data.get('pb', metrics.pb_ratio)
                metrics.roe = yahoo_data.get('roe', metrics.roe)
                metrics.dividend_yield = yahoo_data.get('dividend_yield', metrics.dividend_yield)
                metrics.market_cap = yahoo_data.get('market_cap', metrics.market_cap)
        elif has_moex_data:
            metrics.data_source = "MOEX"

        metrics.fetched_at = get_moscow_time()

        if metrics.has_valid_data:
            self._log_metrics(metrics)
            return metrics

        logger.debug(f"⚠️ No valid data for {ticker_upper}")
        return None

    async def _fetch_moex_market_data(self, ticker: str, metrics: FundamentalMetrics) -> None:
        """Получение рыночных данных из MOEX"""
        try:
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
            params = {"iss.meta": "off", "iss.only": "marketdata"}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        marketdata = data.get('marketdata', {})
                        columns = marketdata.get('columns', [])
                        rows = marketdata.get('data', [])

                        if rows:
                            row = rows[0]
                            for idx, col in enumerate(columns):
                                if idx >= len(row):
                                    continue
                                value = row[idx]
                                if value and value != 'null':
                                    try:
                                        val = float(value)
                                        if col == 'VOLTODAY':
                                            metrics.volume_today = val
                                        elif col == 'VALTODAY_RUR':
                                            metrics.value_today_rub = val
                                        elif col == 'ISSUECAPITALIZATION':
                                            metrics.market_cap = val
                                    except (ValueError, TypeError):
                                        pass
        except Exception as e:
            logger.debug(f"MOEX market data error for {ticker}: {e}")

    async def _fetch_moex_statistics(self, ticker: str, metrics: FundamentalMetrics) -> None:
        """Получение статистики из MOEX"""
        try:
            url = f"https://iss.moex.com/iss/statistics/engines/stock/markets/shares/securities/{ticker}.json"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        statistics = data.get('statistics', {})
                        columns = statistics.get('columns', [])
                        rows = statistics.get('data', [])

                        if rows:
                            row = rows[0]
                            for idx, col in enumerate(columns):
                                if idx >= len(row):
                                    continue
                                value = row[idx]
                                if value and value != 'null':
                                    try:
                                        val = float(value)
                                        if col == 'PE' and val > 0 and metrics.pe_ratio == 0:
                                            metrics.pe_ratio = val
                                        elif col == 'PB' and val > 0 and metrics.pb_ratio == 0:
                                            metrics.pb_ratio = val
                                        elif col == 'ROE' and val != 0 and metrics.roe == 0:
                                            metrics.roe = val * 100 if abs(val) < 1 else val
                                        elif col == 'DIV_YIELD' and val > 0 and metrics.dividend_yield == 0:
                                            metrics.dividend_yield = val
                                    except (ValueError, TypeError):
                                        pass
        except Exception as e:
            logger.debug(f"MOEX statistics error for {ticker}: {e}")

    async def _fetch_yahoo_data(self, ticker: str) -> Optional[Dict[str, float]]:
        """Получение данных из Yahoo Finance"""
        await self._wait_for_yahoo_rate_limit()

        yf_ticker = f"{ticker}.ME" if not ticker.endswith('.ME') else ticker

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._get_yahoo_data_sync, yf_ticker)
            return result
        except Exception as e:
            logger.debug(f"Yahoo Finance error for {ticker}: {e}")
            return None

    def _get_yahoo_data_sync(self, yf_ticker: str) -> Optional[Dict[str, float]]:
        """Синхронное получение данных из Yahoo"""
        try:
            stock = yf.Ticker(yf_ticker)
            info = stock.info

            if not info:
                return None

            result = {}

            pe = info.get('trailingPE') or info.get('forwardPE')
            if pe and 0.1 < pe < 100:
                result['pe'] = pe

            pb = info.get('priceToBook')
            if pb and 0.1 < pb < 20:
                result['pb'] = pb

            roe = info.get('returnOnEquity')
            if roe:
                roe_val = roe * 100
                if 0.1 < roe_val < 100:
                    result['roe'] = roe_val

            div_yield = info.get('dividendYield')
            if div_yield:
                div_yield_val = div_yield * 100
                if 0.1 < div_yield_val < 50:
                    result['dividend_yield'] = div_yield_val

            market_cap = info.get('marketCap')
            if market_cap:
                result['market_cap'] = market_cap

            return result if result else None

        except Exception as e:
            logger.debug(f"Yahoo sync error: {e}")
            return None

    def _log_metrics(self, metrics: FundamentalMetrics) -> None:
        """Логирование метрик"""
        action, score = metrics.get_recommendation()

        if metrics.pe_ratio > 0 or metrics.pb_ratio > 0 or metrics.roe > 0:
            logger.info(
                f"📊 {metrics.ticker}: P/E={metrics.pe_ratio:.2f}, P/B={metrics.pb_ratio:.2f}, ROE={metrics.roe:.1f}%")
            logger.info(f"   Score: {metrics.overall_score:.0f} ({action}) | Source: {metrics.data_source}")

    async def get_trading_signal(
            self,
            ticker: str,
            technical_score: int,
            technical_signals: List[str]
    ) -> Tuple[int, List[str], Dict[str, Any]]:
        """
        Получение торгового сигнала с учетом фундаментального анализа

        Args:
            ticker: Тикер акции
            technical_score: Технический score (от -10 до 10)
            technical_signals: Список технических сигналов

        Returns:
            (new_score, updated_signals, fundamental_data)
        """
        if not self.enabled:
            return technical_score, technical_signals, {}

        metrics = await self.fetch_metrics(ticker)

        if not metrics:
            return technical_score, technical_signals, {}

        action, confidence = metrics.get_recommendation()
        reasons = metrics.get_reasons(action)
        impact = self._get_score_impact(action)

        # Расчет нового score
        new_score = self._calculate_new_score(technical_score, impact)

        # Обновление списка сигналов
        new_signals = technical_signals.copy()
        for reason in reasons:
            new_signals.append(f"📊 {reason}")

        new_signals.append(f"📊 Fundamental: {action} ({metrics.data_source})")

        # Логирование
        logger.info(
            f"📊 {ticker}: Fundamental {action} | "
            f"Impact: {impact:+d} | "
            f"Technical: {technical_score:+d} → {new_score:+d}"
        )

        # Подготовка данных
        fundamental_data = {
            'action': action,
            'confidence': confidence / 100,
            'overall_score': metrics.overall_score,
            'pe_ratio': metrics.pe_ratio,
            'pb_ratio': metrics.pb_ratio,
            'roe': metrics.roe,
            'dividend_yield': metrics.dividend_yield,
            'data_source': metrics.data_source,
            'reasons': reasons
        }

        return new_score, new_signals, fundamental_data

    def _get_score_impact(self, action: str) -> int:
        """Получение влияния на score"""
        impacts = {
            'STRONG_BUY': 5,
            'BUY': 3,
            'HOLD': 0,
            'SELL': -3,
            'STRONG_SELL': -5,
        }
        return impacts.get(action, 0)

    def _calculate_new_score(self, technical_score: int, impact: int) -> int:
        """Расчет нового score"""
        if technical_score > 0:
            new_score = technical_score + impact
            return max(0, new_score)
        elif technical_score < 0:
            new_score = technical_score + impact
            return min(0, new_score)
        else:
            return max(-2, min(2, impact))

    def clear_cache(self, ticker: Optional[str] = None) -> int:
        """
        Очистка кэша

        Args:
            ticker: Тикер для очистки (если None, очищает весь кэш)

        Returns:
            Количество очищенных записей
        """
        if ticker:
            ticker = ticker.upper()
            if ticker in self.cache:
                del self.cache[ticker]
                logger.debug(f"🗑️ Cache cleared for {ticker}")
                return 1
            return 0
        else:
            count = len(self.cache)
            self.cache.clear()
            logger.debug(f"🗑️ Cache fully cleared ({count} entries)")
            return count

    def get_statistics(self) -> Dict[str, Any]:
        """
        Получение статистики работы анализатора

        Returns:
            Словарь со статистикой
        """
        total = self.stats['cache_hits'] + self.stats['cache_misses']
        hit_rate = (self.stats['cache_hits'] / total * 100) if total > 0 else 0

        return {
            'enabled': self.enabled,
            'cache_size': len(self.cache),
            'cache_hits': self.stats['cache_hits'],
            'cache_misses': self.stats['cache_misses'],
            'hit_rate': round(hit_rate, 1),
            'api_errors': self.stats['api_errors'],
            'total_requests': self.stats['total_requests'],
            'yahoo_hits': self.stats['yahoo_hits'],
            'yahoo_available': YFINANCE_AVAILABLE,
            'last_update': self.stats['last_update'].isoformat() if self.stats['last_update'] else None
        }


# Создание глобального экземпляра
fundamental_analyzer = FundamentalAnalyzer()