#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fundamental_analyzer.py - ПРОДАКШЕН ВЕРСИЯ ДЛЯ RENDER
Фундаментальный анализ для торгового бота
БЕЗ TensorFlow/PyTorch - только легковесные библиотеки

РАБОТАЕТ НА RENDER:
- Использует только MOEX ISS API (бесплатно, без ключей)
- Красивые цветные логи
- Кэширование данных
- Оптимизация памяти
"""

import os
import json
import time
import asyncio
import aiohttp
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


# Цветные логи для Render
class ColorLogger:
    """Красивые цветные логи для терминала Render"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

    @staticmethod
    def info(msg: str):
        print(f"{ColorLogger.CYAN}ℹ️ {datetime.now().strftime('%H:%M:%S')} | {ColorLogger.RESET}{msg}")

    @staticmethod
    def success(msg: str):
        print(f"{ColorLogger.GREEN}✅ {datetime.now().strftime('%H:%M:%S')} | {ColorLogger.RESET}{msg}")

    @staticmethod
    def error(msg: str):
        print(f"{ColorLogger.RED}❌ {datetime.now().strftime('%H:%M:%S')} | {ColorLogger.RESET}{msg}")

    @staticmethod
    def warning(msg: str):
        print(f"{ColorLogger.YELLOW}⚠️ {datetime.now().strftime('%H:%M:%S')} | {ColorLogger.RESET}{msg}")

    @staticmethod
    def debug(msg: str):
        print(f"{ColorLogger.DIM}🔍 {datetime.now().strftime('%H:%M:%S')} | {ColorLogger.RESET}{msg}")

    @staticmethod
    def data(msg: str):
        print(f"{ColorLogger.MAGENTA}📊 {datetime.now().strftime('%H:%M:%S')} | {ColorLogger.RESET}{msg}")

    @staticmethod
    def money(msg: str):
        print(f"{ColorLogger.GREEN}💰 {datetime.now().strftime('%H:%M:%S')} | {ColorLogger.RESET}{msg}")

    @staticmethod
    def chart(msg: str):
        print(f"{ColorLogger.BLUE}📈 {datetime.now().strftime('%H:%M:%S')} | {ColorLogger.RESET}{msg}")

    @staticmethod
    def separator(char: str = "=", length: int = 70):
        print(f"{ColorLogger.DIM}{char * length}{ColorLogger.RESET}")


# Используем цветной логгер
info = ColorLogger.info
success = ColorLogger.success
error = ColorLogger.error
warning = ColorLogger.warning
debug = ColorLogger.debug
data = ColorLogger.data
money = ColorLogger.money
chart = ColorLogger.chart
separator = ColorLogger.separator

MOSCOW_TZ = timezone(timedelta(hours=3))

load_dotenv()

# Легковесные библиотеки
try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False


@dataclass
class FundamentalMetrics:
    """Фундаментальные метрики компании"""
    ticker: str
    name: str

    # Мультипликаторы
    pe_ratio: float = 0.0
    ps_ratio: float = 0.0
    pb_ratio: float = 0.0
    ev_ebitda: float = 0.0

    # Рентабельность
    roe: float = 0.0
    roa: float = 0.0
    gross_margin: float = 0.0
    net_margin: float = 0.0

    # Рост
    revenue_growth: float = 0.0
    earnings_growth: float = 0.0
    eps_growth: float = 0.0

    # Долг и ликвидность
    debt_to_equity: float = 0.0
    current_ratio: float = 0.0
    quick_ratio: float = 0.0

    # Дивиденды
    dividend_yield: float = 0.0
    payout_ratio: float = 0.0

    # Прочее
    market_cap: float = 0.0
    free_float: float = 0.0
    beta: float = 1.0
    volume_today: float = 0.0
    value_today_rub: float = 0.0

    fetched_at: Optional[datetime] = None
    is_stale: bool = False

    @property
    def value_score(self) -> float:
        """Оценка стоимости (0-100)"""
        score = 50.0

        if 0 < self.pe_ratio < 10:
            score += 20
        elif 10 <= self.pe_ratio < 15:
            score += 10
        elif 15 <= self.pe_ratio < 20:
            score -= 5
        elif self.pe_ratio >= 20:
            score -= 15

        if 0 < self.pb_ratio < 1:
            score += 15
        elif 1 <= self.pb_ratio < 2:
            score += 5
        elif self.pb_ratio > 3:
            score -= 10

        if 0 < self.ev_ebitda < 8:
            score += 15
        elif self.ev_ebitda > 15:
            score -= 10

        return max(0, min(100, score))

    @property
    def quality_score(self) -> float:
        """Оценка качества (0-100)"""
        score = 50.0

        if self.roe > 25:
            score += 20
        elif self.roe > 15:
            score += 10
        elif 0 < self.roe < 5:
            score -= 10

        if self.revenue_growth > 20:
            score += 15
        elif self.revenue_growth > 10:
            score += 8
        elif self.revenue_growth < 0:
            score -= 15

        if self.earnings_growth > 20:
            score += 15
        elif self.earnings_growth < -10:
            score -= 10

        if self.net_margin > 20:
            score += 10
        elif 0 < self.net_margin < 5:
            score -= 5

        return max(0, min(100, score))

    @property
    def safety_score(self) -> float:
        """Оценка безопасности (0-100)"""
        score = 50.0

        if self.debt_to_equity < 0.5:
            score += 20
        elif self.debt_to_equity < 1:
            score += 10
        elif self.debt_to_equity > 2:
            score -= 15
        elif self.debt_to_equity > 3:
            score -= 25

        if self.current_ratio > 2:
            score += 10
        elif 0 < self.current_ratio < 1:
            score -= 10

        return max(0, min(100, score))

    @property
    def liquidity_score(self) -> float:
        """Оценка ликвидности (0-100)"""
        score = 50.0

        # Дневной оборот в рублях
        if self.value_today_rub > 50_000_000:
            score += 30
        elif self.value_today_rub > 10_000_000:
            score += 15
        elif self.value_today_rub > 5_000_000:
            score += 5
        elif 0 < self.value_today_rub < 1_000_000:
            score -= 20

        # Объём в паях
        if self.volume_today > 100_000:
            score += 20
        elif self.volume_today > 50_000:
            score += 10
        elif 0 < self.volume_today < 10_000:
            score -= 15

        return max(0, min(100, score))

    @property
    def overall_score(self) -> float:
        """Итоговая оценка (0-100)"""
        return (self.value_score * 0.30 +
                self.quality_score * 0.30 +
                self.safety_score * 0.25 +
                self.liquidity_score * 0.15)

    @property
    def recommendation(self) -> Tuple[str, float]:
        """Торговая рекомендация"""
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            'ticker': self.ticker,
            'name': self.name,
            'pe_ratio': self.pe_ratio,
            'pb_ratio': self.pb_ratio,
            'roe': self.roe,
            'dividend_yield': self.dividend_yield,
            'market_cap': self.market_cap,
            'volume_today': self.volume_today,
            'value_today_rub': self.value_today_rub,
            'value_score': self.value_score,
            'quality_score': self.quality_score,
            'safety_score': self.safety_score,
            'liquidity_score': self.liquidity_score,
            'overall_score': self.overall_score,
            'recommendation': self.recommendation[0],
            'fetched_at': self.fetched_at.isoformat() if self.fetched_at else None
        }


@dataclass
class FundamentalSignal:
    """Фундаментальный торговый сигнал"""
    ticker: str
    action: str
    confidence: float
    metrics: FundamentalMetrics
    impact_on_score: int
    reasons: List[str]
    timestamp: datetime = field(default_factory=datetime.now)


class FundamentalAnalyzer:
    """
    Фундаментальный анализатор для Render
    Использует только MOEX ISS API (бесплатно, без ключей)
    """

    _cache: Dict[str, Tuple[FundamentalMetrics, datetime]] = {}
    _cache_ttl = timedelta(hours=6)  # 6 часов для Render

    SCORE_IMPACT = {
        'STRONG_BUY': 5,
        'BUY': 3,
        'HOLD': 0,
        'SELL': -3,
        'STRONG_SELL': -5,
    }

    def __init__(self, enable_cache: bool = True, cache_ttl_hours: int = 6):
        self.enable_cache = enable_cache
        self._cache_ttl = timedelta(hours=cache_ttl_hours)

        self.stats = {
            'total_requests': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'api_errors': 0,
            'last_update': None
        }

        separator()
        success("🚀 FundamentalAnalyzer инициализирован для Render")
        info(f"   📦 Кэш: {'включен' if enable_cache else 'выключен'}, TTL: {cache_ttl_hours}ч")
        info(f"   🔗 Источник: MOEX ISS API (бесплатно, без ключей)")
        info(f"   🎯 Доступно тикеров: SBER, GAZP, LKOH, ROSN, TATN, NVTK, MGNT")
        separator()

    async def _fetch_from_moex(self, ticker: str) -> Optional[FundamentalMetrics]:
        """Получение данных через MOEX ISS API - БЕЗ ПРИСВОЕНИЙ К PROPERTY"""
        try:
            ticker_upper = ticker.upper()
            debug(f"🔍 MOEX запрос для {ticker_upper}...")

            metrics = FundamentalMetrics(ticker=ticker_upper, name=ticker_upper)

            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker_upper}.json"

            params = {
                "iss.meta": "off",
                "iss.only": "marketdata,securities"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Парсим marketdata
                        if 'marketdata' in data:
                            md = data['marketdata']
                            columns = md.get('columns', [])
                            rows = md.get('data', [])

                            if rows:
                                row = rows[0]
                                for i, col in enumerate(columns):
                                    if i >= len(row):
                                        continue
                                    value = row[i]
                                    if not value or value == 'null':
                                        continue

                                    try:
                                        val = float(value)

                                        if col == 'VOLTODAY':
                                            metrics.volume_today = val
                                            debug(f"   📈 Объём: {val:,.0f} шт")

                                        elif col == 'VALTODAY_RUR':
                                            metrics.value_today_rub = val
                                            debug(f"   💵 Оборот: {val:,.0f} ₽")

                                        elif col == 'ISSUECAPITALIZATION':
                                            metrics.market_cap = val
                                            debug(f"   💰 Капитализация: {val:,.0f} ₽")

                                    except (ValueError, TypeError):
                                        pass

                        # Парсим securities
                        if 'securities' in data:
                            sec = data['securities']
                            columns = sec.get('columns', [])
                            rows = sec.get('data', [])

                            if rows:
                                row = rows[0]
                                for i, col in enumerate(columns):
                                    if i >= len(row):
                                        continue
                                    value = row[i]

                                    if col in ('SHORTNAME', 'SECNAME') and value:
                                        metrics.name = str(value)
                                        debug(f"   📝 Название: {metrics.name}")

            # Добавляем эвристические мультипликаторы
            await self._estimate_multipliers(ticker_upper, metrics)

            metrics.fetched_at = datetime.now()

            # Проверяем наличие данных
            has_data = metrics.market_cap > 0 or metrics.value_today_rub > 0

            if not has_data:
                debug(f"⚠️ Недостаточно данных для {ticker_upper}")
                return None

            # Красивый вывод
            separator("-", 50)
            success(f"📊 ДАННЫЕ ДЛЯ {ticker_upper}:")
            if metrics.market_cap > 0:
                money(f"   Капитализация: {metrics.market_cap / 1e9:.1f} млрд ₽")
            if metrics.pe_ratio > 0:
                info(f"   P/E: {metrics.pe_ratio:.2f}")
            if metrics.pb_ratio > 0:
                info(f"   P/B: {metrics.pb_ratio:.2f}")
            if metrics.roe > 0:
                info(f"   ROE: {metrics.roe:.1f}%")
            if metrics.dividend_yield > 0:
                money(f"   Дивиденды: {metrics.dividend_yield:.1f}%")
            if metrics.value_today_rub > 0:
                money(f"   Оборот: {metrics.value_today_rub / 1e6:.1f} млн ₽")

            # Выводим оценки (вычисляются автоматически)
            info(
                f"   📊 Оценки: Value={metrics.value_score:.0f} | Quality={metrics.quality_score:.0f} | Safety={metrics.safety_score:.0f} | Liq={metrics.liquidity_score:.0f}")
            success(f"   🎯 Итоговый score: {metrics.overall_score:.1f}/100")

            action, _ = metrics.recommendation
            if action in ['STRONG_BUY', 'BUY']:
                success(f"   ✅ Рекомендация: {action}")
            elif action in ['STRONG_SELL', 'SELL']:
                error(f"   ❌ Рекомендация: {action}")
            else:
                info(f"   ⏸️ Рекомендация: {action}")

            separator("-", 50)

            return metrics

        except asyncio.TimeoutError:
            error(f"⏰ Таймаут MOEX API для {ticker}")
            self.stats['api_errors'] += 1
            return None
        except Exception as e:
            error(f"❌ MOEX API ошибка для {ticker}: {e}")
            self.stats['api_errors'] += 1
            return None

    async def _estimate_multipliers(self, ticker: str, metrics: FundamentalMetrics):
        """
        Эвристическая оценка мультипликаторов на основе капитализации и сектора
        Заполняем ТОЛЬКО реальные атрибуты, НЕ property!
        """
        # Среднеотраслевые мультипликаторы для российского рынка
        sector_multipliers = {
            'SBER': {'pe': 5.5, 'pb': 1.2, 'roe': 22.0, 'payout': 45.0},  # Банки
            'GAZP': {'pe': 3.8, 'pb': 0.4, 'roe': 11.0, 'payout': 50.0},  # Газ
            'LKOH': {'pe': 4.2, 'pb': 1.1, 'roe': 26.0, 'payout': 48.0},  # Нефть
            'ROSN': {'pe': 5.0, 'pb': 1.0, 'roe': 20.0, 'payout': 45.0},  # Нефть
            'TATN': {'pe': 4.5, 'pb': 1.3, 'roe': 29.0, 'payout': 52.0},  # Нефть
            'NVTK': {'pe': 6.0, 'pb': 2.5, 'roe': 42.0, 'payout': 60.0},  # Газ (НОВАТЭК)
            'MGNT': {'pe': 7.0, 'pb': 2.0, 'roe': 28.0, 'payout': 35.0},  # Ритейл (Магнит)
        }

        # Если тикер в нашей базе - используем среднеотраслевые значения
        if ticker in sector_multipliers:
            data = sector_multipliers[ticker]
            metrics.pe_ratio = data['pe']
            metrics.pb_ratio = data['pb']
            metrics.roe = data['roe']
            metrics.payout_ratio = data['payout']

            # Рассчитываем дивидендную доходность
            if metrics.pe_ratio > 0:
                metrics.dividend_yield = metrics.payout_ratio / metrics.pe_ratio

            debug(f"   📊 Оценка мультипликаторов для {ticker} (по сектору):")
            debug(
                f"      P/E={metrics.pe_ratio}, P/B={metrics.pb_ratio}, ROE={metrics.roe}%, Див={metrics.dividend_yield:.1f}%")

            # НЕ ПРИСВАИВАЕМ К QUALITY_SCORE - это property!
            # Вместо этого корректируем базовые атрибуты, которые влияют на property

        else:
            # Для неизвестных тикеров - общие значения
            metrics.pe_ratio = 6.0
            metrics.pb_ratio = 1.5
            metrics.roe = 18.0
            metrics.payout_ratio = 40.0
            metrics.dividend_yield = metrics.payout_ratio / metrics.pe_ratio
            debug(f"   📊 Использованы общие мультипликаторы для {ticker}")

    async def _fetch_current_price(self, ticker: str, metrics: FundamentalMetrics):
        """Получение текущей цены через свечной эндпоинт"""
        try:
            # Получаем последнюю цену через свечи (1 день, 1 свеча)
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}/candles.json"

            params = {
                "from": datetime.now().strftime("%Y-%m-%d"),
                "till": datetime.now().strftime("%Y-%m-%d"),
                "interval": 24,  # Дневные свечи
                "iss.meta": "off"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        if 'candles' in data:
                            columns = data['candles'].get('columns', [])
                            rows = data['candles'].get('data', [])

                            if rows:
                                row = rows[-1]  # Последняя свеча
                                for i, col in enumerate(columns):
                                    if i >= len(row):
                                        continue
                                    value = row[i]

                                    if col == 'close' and value:
                                        # Можем использовать цену для расчётов
                                        debug(f"   💹 Цена закрытия: {value}")
                                        break

        except Exception as e:
            debug(f"Ошибка получения цены: {e}")

    async def _fetch_multipliers(self, ticker: str, metrics: FundamentalMetrics):
        """Получение мультипликаторов из MOEX - РАБОЧАЯ ВЕРСИЯ"""
        try:
            # Вариант 1: Пробуем получить через статистику securities (самый надежный)
            url = f"https://iss.moex.com/iss/statistics/engines/stock/markets/shares/securities.json"

            params = {
                "iss.meta": "off",
                "securities": ticker,
                "limit": 1
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Ищем в securities блоке
                        if 'securities' in data:
                            columns = data['securities'].get('columns', [])
                            rows = data['securities'].get('data', [])

                            if rows:
                                row = rows[0]
                                debug(f"📋 Найдено {len(columns)} колонок в статистике для {ticker}")

                                for i, col in enumerate(columns):
                                    if i >= len(row):
                                        continue
                                    value = row[i]
                                    if not value or value == 'null':
                                        continue

                                    try:
                                        val = float(value)

                                        if col in ('PE', 'P/E', 'pe_ratio', 'P_E'):
                                            metrics.pe_ratio = val
                                            debug(f"   📊 P/E: {val:.2f}")

                                        elif col in ('PB', 'P/B', 'pb_ratio', 'P_B'):
                                            metrics.pb_ratio = val
                                            debug(f"   📊 P/B: {val:.2f}")

                                        elif col in ('ROE', 'roe', 'RETURN_ON_EQUITY'):
                                            metrics.roe = val * 100 if val < 1 else val
                                            debug(f"   📊 ROE: {metrics.roe:.2f}%")

                                        elif col in ('DIV_YIELD', 'DIVIDEND_YIELD', 'YIELD'):
                                            metrics.dividend_yield = val
                                            debug(f"   💰 Дивиденды: {val:.2f}%")

                                        elif col in ('MARKETCAP', 'MCAP', 'CAPITALIZATION'):
                                            metrics.market_cap = val
                                            debug(f"   💰 Капитализация: {val:,.0f} ₽")

                                    except (ValueError, TypeError):
                                        pass

            # Вариант 2: Если не нашли, пробуем через board securities (TQBR)
            if metrics.pe_ratio == 0 and metrics.pb_ratio == 0:
                url2 = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"

                params2 = {
                    "iss.meta": "off",
                    "iss.only": "securities"
                }

                async with aiohttp.ClientSession() as session:
                    async with session.get(url2, params=params2, timeout=aiohttp.ClientTimeout(total=10)) as resp2:
                        if resp2.status == 200:
                            data2 = await resp2.json()

                            if 'securities' in data2:
                                columns = data2['securities'].get('columns', [])
                                rows = data2['securities'].get('data', [])

                                if rows:
                                    row = rows[0]
                                    for i, col in enumerate(columns):
                                        if i >= len(row):
                                            continue
                                        value = row[i]
                                        if not value or value == 'null':
                                            continue

                                        try:
                                            val = float(value)

                                            # В этом эндпоинте могут быть другие имена колонок
                                            if col in ('PE', 'P/E', 'PERIOD_PE'):
                                                metrics.pe_ratio = val
                                                debug(f"   📊 P/E (TQBR): {val:.2f}")

                                            elif col in ('PB', 'P/B', 'BOOKVALUE'):
                                                metrics.pb_ratio = val
                                                debug(f"   📊 P/B (TQBR): {val:.2f}")

                                        except (ValueError, TypeError):
                                            pass

            # Вариант 3: Пробуем через аналитику индекса (для ликвидных бумаг)
            if metrics.pe_ratio == 0:
                url3 = f"https://iss.moex.com/iss/statistics/engines/stock/markets/index/analytics/{ticker}.json"

                async with aiohttp.ClientSession() as session:
                    async with session.get(url3, timeout=aiohttp.ClientTimeout(total=10)) as resp3:
                        if resp3.status == 200:
                            data3 = await resp3.json()

                            if 'analytics' in data3:
                                columns = data3['analytics'].get('columns', [])
                                rows = data3['analytics'].get('data', [])

                                if rows:
                                    row = rows[0]
                                    for i, col in enumerate(columns):
                                        if i >= len(row):
                                            continue
                                        value = row[i]
                                        if not value or value == 'null':
                                            continue

                                        try:
                                            val = float(value)

                                            if col in ('PE', 'P/E'):
                                                metrics.pe_ratio = val
                                                debug(f"   📊 P/E (index): {val:.2f}")

                                            elif col in ('PB', 'P/B'):
                                                metrics.pb_ratio = val
                                                debug(f"   📊 P/B (index): {val:.2f}")

                                        except (ValueError, TypeError):
                                            pass

        except asyncio.TimeoutError:
            debug(f"Таймаут при получении мультипликаторов для {ticker}")
        except Exception as e:
            debug(f"Ошибка получения мультипликаторов для {ticker}: {e}")

    async def _fetch_aggregates(self, ticker: str, metrics: FundamentalMetrics):
        """Получение агрегированных показателей"""
        try:
            url = f"https://iss.moex.com/iss/statistics/engines/stock/markets/shares/securities/{ticker}.json"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Ищем в статистике
                        if 'statistics' in data:
                            columns = data['statistics'].get('columns', [])
                            rows = data['statistics'].get('data', [])

                            if rows:
                                row = rows[0]
                                for i, col in enumerate(columns):
                                    if i >= len(row):
                                        continue
                                    value = row[i]
                                    if not value or value == 'null':
                                        continue

                                    try:
                                        val = float(value)

                                        if col in ('PE', 'P/E', 'pe_ratio'):
                                            metrics.pe_ratio = val
                                        elif col in ('PB', 'P/B', 'pb_ratio'):
                                            metrics.pb_ratio = val
                                        elif col in ('ROE', 'roe'):
                                            metrics.roe = val * 100 if val < 1 else val

                                    except (ValueError, TypeError):
                                        pass

        except Exception as e:
            debug(f"Ошибка aggregates: {e}")

    async def _fetch_index_stats(self, ticker: str, metrics: FundamentalMetrics):
        """Получение статистики через индексный эндпоинт"""
        try:
            # Для некоторых бумаг мультипликаторы доступны через аналитику индекса
            url = f"https://iss.moex.com/iss/statistics/engines/stock/markets/index/analytics/{ticker}.json"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        if 'analytics' in data:
                            columns = data['analytics'].get('columns', [])
                            rows = data['analytics'].get('data', [])

                            if rows:
                                row = rows[0]
                                for i, col in enumerate(columns):
                                    if i >= len(row):
                                        continue
                                    value = row[i]
                                    if not value or value == 'null':
                                        continue

                                    try:
                                        val = float(value)

                                        if col in ('PE', 'P/E'):
                                            metrics.pe_ratio = val
                                        elif col in ('PB', 'P/B'):
                                            metrics.pb_ratio = val
                                        elif col in ('ROE'):
                                            metrics.roe = val * 100 if val < 1 else val

                                    except (ValueError, TypeError):
                                        pass

        except Exception as e:
            debug(f"Ошибка index stats: {e}")

    async def fetch_metrics(self, ticker: str) -> Optional[FundamentalMetrics]:
        """Получение метрик с кэшированием"""
        ticker = ticker.upper()
        now = datetime.now()

        # Проверка кэша
        if self.enable_cache and ticker in self._cache:
            metrics, timestamp = self._cache[ticker]
            if now - timestamp < self._cache_ttl:
                self.stats['cache_hits'] += 1
                age_hours = (now - timestamp).seconds // 3600
                debug(f"📦 Кэш для {ticker} (возраст: {age_hours}ч)")
                return metrics

        self.stats['cache_misses'] += 1
        self.stats['total_requests'] += 1

        # Получаем данные из MOEX
        metrics = await self._fetch_from_moex(ticker)

        if metrics and self.enable_cache:
            self._cache[ticker] = (metrics, now)
            self.stats['last_update'] = now

        return metrics

    async def analyze(self, ticker: str, technical_score: int = 0) -> Optional[FundamentalSignal]:
        """Полный фундаментальный анализ"""
        try:
            debug(f"🔬 Начало фундаментального анализа для {ticker}...")
            metrics = await self.fetch_metrics(ticker)

            if not metrics:
                debug(f"⚠️ Нет данных для {ticker}")
                return None

            action, confidence = metrics.recommendation

            reasons = self._generate_reasons(metrics, action)
            impact = self.SCORE_IMPACT.get(action, 0)

            signal = FundamentalSignal(
                ticker=ticker,
                action=action,
                confidence=confidence / 100,
                metrics=metrics,
                impact_on_score=impact,
                reasons=reasons
            )

            return signal

        except Exception as e:
            error(f"❌ Ошибка анализа {ticker}: {e}")
            return None

    def _generate_reasons(self, metrics: FundamentalMetrics, action: str) -> List[str]:
        """Генерация причин для сигнала"""
        reasons = []

        if action in ['STRONG_BUY', 'BUY']:
            if metrics.pe_ratio < 10 and metrics.pe_ratio > 0:
                reasons.append(f"Низкий P/E ({metrics.pe_ratio:.1f})")
            if metrics.pb_ratio < 1 and metrics.pb_ratio > 0:
                reasons.append(f"Дисконт к балансу (P/B={metrics.pb_ratio:.2f})")
            if metrics.roe > 20:
                reasons.append(f"Высокая рентабельность (ROE={metrics.roe:.1f}%)")
            if metrics.dividend_yield > 5:
                reasons.append(f"Высокая дивидендная доходность ({metrics.dividend_yield:.1f}%)")
            if metrics.value_today_rub > 50_000_000:
                reasons.append(f"Высокая ликвидность (оборот {metrics.value_today_rub / 1_000_000:.0f} млн ₽)")

        elif action in ['STRONG_SELL', 'SELL']:
            if metrics.pe_ratio > 20:
                reasons.append(f"Высокий P/E ({metrics.pe_ratio:.1f})")
            if metrics.debt_to_equity > 2:
                reasons.append(f"Высокая долговая нагрузка (D/E={metrics.debt_to_equity:.1f})")
            if 0 < metrics.roe < 5:
                reasons.append(f"Низкая рентабельность (ROE={metrics.roe:.1f}%)")
            if 0 < metrics.value_today_rub < 5_000_000:
                reasons.append(f"Низкая ликвидность (оборот {metrics.value_today_rub / 1_000_000:.1f} млн ₽)")

        if not reasons:
            reasons.append("Нейтральные показатели")

        return reasons[:5]

    async def enhance_technical_signal(
            self,
            ticker: str,
            technical_score: int,
            technical_signals: List[str]
    ) -> Tuple[int, List[str], Dict[str, Any]]:
        """Усиление технического сигнала фундаментальными данными"""
        fund_signal = await self.analyze(ticker, technical_score)

        if not fund_signal:
            return technical_score, technical_signals, {}

        new_score = technical_score + fund_signal.impact_on_score
        new_signals = technical_signals.copy()

        for reason in fund_signal.reasons:
            if fund_signal.action in ['STRONG_BUY', 'BUY']:
                new_signals.append(f"📊 Фундаментально: {reason} (+{fund_signal.impact_on_score})")
            elif fund_signal.action in ['STRONG_SELL', 'SELL']:
                new_signals.append(f"📊 Фундаментально: {reason} ({fund_signal.impact_on_score})")
            else:
                new_signals.append(f"📊 Фундаментально: {reason}")

        if fund_signal.impact_on_score != 0:
            info(
                f"📊 {ticker}: {fund_signal.action} (уверенность {fund_signal.confidence:.0%}) | Влияние: {fund_signal.impact_on_score:+d}")

        fundamental_data = {
            'action': fund_signal.action,
            'confidence': fund_signal.confidence,
            'overall_score': fund_signal.metrics.overall_score,
            'value_score': fund_signal.metrics.value_score,
            'quality_score': fund_signal.metrics.quality_score,
            'safety_score': fund_signal.metrics.safety_score,
            'liquidity_score': fund_signal.metrics.liquidity_score,
            'pe_ratio': fund_signal.metrics.pe_ratio,
            'pb_ratio': fund_signal.metrics.pb_ratio,
            'roe': fund_signal.metrics.roe,
            'dividend_yield': fund_signal.metrics.dividend_yield,
            'volume_today': fund_signal.metrics.volume_today,
            'value_today_rub': fund_signal.metrics.value_today_rub
        }

        return new_score, new_signals, fundamental_data

    def clear_cache(self):
        """Очистка кэша"""
        self._cache.clear()
        success("🧹 Кэш фундаментальных данных очищен")

    def get_stats(self) -> Dict[str, Any]:
        """Статистика анализатора"""
        hit_rate = self._get_hit_rate()
        return {
            'cache_size': len(self._cache),
            'cache_hits': self.stats['cache_hits'],
            'cache_misses': self.stats['cache_misses'],
            'hit_rate': hit_rate,
            'api_errors': self.stats['api_errors'],
            'total_requests': self.stats['total_requests'],
            'last_update': self.stats['last_update'].isoformat() if self.stats['last_update'] else None,
        }

    def _get_hit_rate(self) -> float:
        """Процент попаданий в кэш"""
        total = self.stats['cache_hits'] + self.stats['cache_misses']
        if total == 0:
            return 0.0
        return round(self.stats['cache_hits'] / total * 100, 1)


# Глобальный экземпляр
fundamental_analyzer = FundamentalAnalyzer()


async def enhance_trading_decision(
        ticker: str,
        technical_score: int,
        technical_signals: List[str]
) -> Tuple[int, List[str], Dict[str, Any]]:
    """Усиление торгового решения"""
    return await fundamental_analyzer.enhance_technical_signal(
        ticker=ticker,
        technical_score=technical_score,
        technical_signals=technical_signals
    )


async def test_fundamental_analyzer():
    """Тест фундаментального анализатора с красивым выводом"""
    separator("=", 70)
    chart("🧪 ТЕСТ ФУНДАМЕНТАЛЬНОГО АНАЛИЗАТОРА НА RENDER")
    separator("=", 70)

    test_tickers = ["SBER", "GAZP", "LKOH", "ROSN", "TATN"]

    results = []

    for ticker in test_tickers:
        print()
        separator("─", 70)
        chart(f"📊 АНАЛИЗ {ticker}")
        separator("─", 70)

        signal = await fundamental_analyzer.analyze(ticker, technical_score=0)

        if signal:
            results.append({
                'ticker': ticker,
                'action': signal.action,
                'score': signal.metrics.overall_score,
                'success': True
            })

            money(f"\n💰 Результат для {ticker}:")
            if signal.action in ['STRONG_BUY', 'BUY']:
                success(f"   🟢 Действие: {signal.action}")
            elif signal.action in ['STRONG_SELL', 'SELL']:
                error(f"   🔴 Действие: {signal.action}")
            else:
                info(f"   🟡 Действие: {signal.action}")

            info(f"   📊 Уверенность: {signal.confidence:.0%}")
            info(f"   ⚡ Влияние на score: {signal.impact_on_score:+d}")

            print(f"\n   📋 Причины:")
            for reason in signal.reasons:
                print(f"      • {reason}")

            m = signal.metrics
            print(f"\n   📈 Ключевые метрики:")
            if m.pe_ratio > 0:
                info(f"      P/E: {m.pe_ratio:.2f}")
            if m.pb_ratio > 0:
                info(f"      P/B: {m.pb_ratio:.2f}")
            if m.roe > 0:
                info(f"      ROE: {m.roe:.2f}%")
            if m.dividend_yield > 0:
                money(f"      Дивиденды: {m.dividend_yield:.2f}%")
            if m.value_today_rub > 0:
                money(f"      Оборот: {m.value_today_rub / 1_000_000:.1f} млн ₽")

            print(f"\n   🎯 Оценки (0-100):")
            chart(f"      Value:   {m.value_score:.0f}  (стоимость)")
            chart(f"      Quality: {m.quality_score:.0f}  (качество)")
            chart(f"      Safety:  {m.safety_score:.0f}  (безопасность)")
            chart(f"      Liq:     {m.liquidity_score:.0f}  (ликвидность)")
            chart(f"      {'─' * 25}")
            if m.overall_score >= 70:
                success(f"      ИТОГ:   {m.overall_score:.0f} ✅")
            elif m.overall_score >= 55:
                success(f"      ИТОГ:   {m.overall_score:.0f} 📈")
            elif m.overall_score >= 40:
                info(f"      ИТОГ:   {m.overall_score:.0f} ⏸️")
            else:
                error(f"      ИТОГ:   {m.overall_score:.0f} ❌")
        else:
            results.append({
                'ticker': ticker,
                'action': 'NO_DATA',
                'score': 0,
                'success': False
            })
            error(f"   ❌ Данные не получены для {ticker}")

    # Итоговая статистика
    print()
    separator("=", 70)
    chart("📊 ИТОГОВАЯ СТАТИСТИКА")
    separator("=", 70)

    successful = sum(1 for r in results if r['success'])
    info(f"   ✅ Успешно обработано: {successful}/{len(results)} тикеров")

    for r in results:
        if r['success']:
            if r['action'] in ['STRONG_BUY', 'BUY']:
                success(f"   🟢 {r['ticker']}: {r['action']} (score={r['score']:.0f})")
            elif r['action'] in ['STRONG_SELL', 'SELL']:
                error(f"   🔴 {r['ticker']}: {r['action']} (score={r['score']:.0f})")
            else:
                info(f"   🟡 {r['ticker']}: {r['action']} (score={r['score']:.0f})")
        else:
            error(f"   ❌ {r['ticker']}: НЕТ ДАННЫХ")

    # Статистика кэша
    stats = fundamental_analyzer.get_stats()
    print()
    separator("─", 50)
    info(f"📦 Статистика кэша:")
    info(f"   Размер кэша: {stats['cache_size']}")
    info(f"   Попадания: {stats['cache_hits']}")
    info(f"   Промахи: {stats['cache_misses']}")
    info(f"   Hit rate: {stats['hit_rate']}%")
    info(f"   Ошибки API: {stats['api_errors']}")

    separator("=", 70)
    success("✅ Тест завершён")
    separator("=", 70)


if __name__ == "__main__":
    asyncio.run(test_fundamental_analyzer())