#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CandleBuilder.py - УНИВЕРСАЛЬНЫЙ ПОСТРОИТЕЛЬ СВЕЧЕЙ (PRODUCTION READY)
Объединяет:
- Получение свечей из MOEX ISS API (бесплатно)
- Получение свечей из Alfa API (FinInfoCandleEntity)
- Построение свечей из реального потока сделок
- Кэширование и оптимизация
"""

# ✅ ДОБАВИТЬ ЭТОТ БЛОК В САМОЕ НАЧАЛО (перед всеми импортами)
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в PATH
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import threading
import numpy as np
import pandas as pd
import asyncio
import logging
import random
import json
from typing import Dict, List, Optional, Any, Tuple, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
from collections import defaultdict, deque
import aiohttp

# Импорт Logger - ИСПРАВЛЕНО: используем локальный логгер
from trading_bot.logger import info, error, warning, debug
from trading_bot.cache.unified_cache import UnifiedCache, USE_UNIFIED_CACHE


# ==================== ОПРЕДЕЛЕНИЕ CANDLE (ВСТРОЕННОЕ) ====================
@dataclass
class Candle:
    interval: str = "1min"
    open_time: Optional[datetime] = None
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    is_closed: bool = False
    has_trades: bool = False

    def update(self, price: float, volume: int, timestamp: datetime):
        """Обновление свечи новой сделкой"""
        if self.open_time is None:
            # Первая сделка в свече
            self.open_time = timestamp
            self.open = price
            self.high = price
            self.low = price
            self.has_trades = True
        else:
            # Обновляем существующую свечу
            self.high = max(self.high, price)
            self.low = min(self.low, price)

        self.close = price
        self.volume += volume

    def to_dict(self) -> Dict:
        return {
            'interval': self.interval,
            'open_time': self.open_time.isoformat() if self.open_time else None,
            'open': self.open,
            'high': self.high,
            'low': self.low if self.has_trades else self.high,
            'close': self.close,
            'volume': self.volume,
            'is_closed': self.is_closed
        }

logger = logging.getLogger(__name__)

# ==================== КОНСТАНТЫ ====================

INTERVAL_SECONDS = {
    "1min": 60, "2min": 120, "3min": 180, "5min": 300, "10min": 600,
    "15min": 900, "30min": 1800, "1hour": 3600, "2hour": 7200,
    "3hour": 10800, "4hour": 14400, "6hour": 21600, "12hour": 43200,
    "1day": 86400, "1week": 604800, "1month": 2592000
}


# ==================== ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ ====================

class TechnicalIndicators:
    """Технические индикаторы - production ready с полной обработкой ошибок"""

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        """
        Расчет RSI (Relative Strength Index)
        Возвращает значение от 0 до 100
        """
        if not prices or len(prices) < period + 1:
            return 50.0

        try:
            deltas = np.diff(prices[-period - 1:])
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)

            avg_gain = np.mean(gains) if len(gains) > 0 else 0
            avg_loss = np.mean(losses) if len(losses) > 0 else 0

            if avg_loss == 0:
                return 100.0 if avg_gain > 0 else 50.0

            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return round(rsi, 2)

        except Exception as e:
            logger.error(f"RSI calculation error: {e}")
            return 50.0

    @staticmethod
    def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
        """
        Расчет MACD (Moving Average Convergence Divergence)
        """
        if not prices or len(prices) < slow + signal:
            return {'macd': 0.0, 'signal': 0.0, 'histogram': 0.0}

        try:
            series = pd.Series(prices)
            exp1 = series.ewm(span=fast, adjust=False).mean()
            exp2 = series.ewm(span=slow, adjust=False).mean()
            macd_line = exp1 - exp2
            signal_line = macd_line.ewm(span=signal, adjust=False).mean()

            return {
                'macd': round(float(macd_line.iloc[-1]), 6),
                'signal': round(float(signal_line.iloc[-1]), 6),
                'histogram': round(float(macd_line.iloc[-1] - signal_line.iloc[-1]), 6)
            }
        except Exception as e:
            logger.error(f"MACD calculation error: {e}")
            return {'macd': 0.0, 'signal': 0.0, 'histogram': 0.0}

    @staticmethod
    def calculate_bollinger(prices: List[float], period: int = 20, std_dev: float = 2.0) -> Dict:
        """
        Расчет полос Боллинджера
        """
        if not prices or len(prices) < period:
            current = prices[-1] if prices else 0
            return {
                'middle': current,
                'upper': current,
                'lower': current,
                'current': current,
                'percent_b': 0.5,
                'bandwidth': 0.0
            }

        try:
            recent = prices[-period:]
            ma = np.mean(recent)
            std = np.std(recent)
            current = prices[-1]

            upper = ma + (std * std_dev)
            lower = ma - (std * std_dev)

            # Percent B
            percent_b = (current - lower) / (upper - lower) if upper != lower else 0.5
            percent_b = max(0.0, min(1.0, percent_b))

            # Bandwidth
            bandwidth = ((upper - lower) / ma) * 100 if ma != 0 else 0

            return {
                'middle': round(ma, 4),
                'upper': round(upper, 4),
                'lower': round(lower, 4),
                'current': round(current, 4),
                'percent_b': round(percent_b, 4),
                'bandwidth': round(bandwidth, 2)
            }
        except Exception as e:
            logger.error(f"Bollinger calculation error: {e}")
            return {'middle': 0, 'upper': 0, 'lower': 0, 'current': 0, 'percent_b': 0.5, 'bandwidth': 0}

    @staticmethod
    def calculate_moving_averages(prices: List[float], periods: List[int] = None) -> Dict:
        """
        Расчет скользящих средних
        """
        if periods is None:
            periods = [5, 10, 20, 50, 100, 200]

        result = {}
        if not prices:
            return {f'MA_{p}': None for p in periods}

        try:
            for period in periods:
                if len(prices) >= period:
                    result[f'MA_{period}'] = round(float(np.mean(prices[-period:])), 4)
                else:
                    result[f'MA_{period}'] = None
            return result
        except Exception as e:
            logger.error(f"MA calculation error: {e}")
            return {f'MA_{p}': None for p in periods}

    @staticmethod
    def calculate_atr(candles: List[Dict], period: int = 14) -> float:
        """
        Расчет ATR (Average True Range)
        """
        if not candles or len(candles) < period + 1:
            return 0.0

        try:
            tr_list = []
            for i in range(1, len(candles)):
                high = candles[i].get('high', 0)
                low = candles[i].get('low', 0)
                prev_close = candles[i - 1].get('close', 0)

                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_list.append(tr)

            if not tr_list:
                return 0.0

            return round(float(np.mean(tr_list[-period:])), 4)
        except Exception as e:
            logger.error(f"ATR calculation error: {e}")
            return 0.0

    @staticmethod
    def calculate_vwap(candles: List[Dict]) -> float:
        """
        Расчет VWAP (Volume Weighted Average Price)
        """
        if not candles:
            return 0.0

        try:
            total_value = 0.0
            total_volume = 0.0

            for candle in candles:
                typical_price = (candle.get('high', 0) + candle.get('low', 0) + candle.get('close', 0)) / 3
                volume = candle.get('volume', 0)
                total_value += typical_price * volume
                total_volume += volume

            return round(total_value / total_volume, 4) if total_volume > 0 else 0.0
        except Exception as e:
            logger.error(f"VWAP calculation error: {e}")
            return 0.0

    @staticmethod
    def calculate_obv(prices: List[float], volumes: List[float]) -> List[float]:
        """
        Расчет OBV (On-Balance Volume)
        """
        if not prices or not volumes or len(prices) != len(volumes):
            return []

        try:
            obv = [0.0]
            for i in range(1, len(prices)):
                if prices[i] > prices[i - 1]:
                    obv.append(obv[-1] + volumes[i])
                elif prices[i] < prices[i - 1]:
                    obv.append(obv[-1] - volumes[i])
                else:
                    obv.append(obv[-1])
            return obv
        except Exception as e:
            logger.error(f"OBV calculation error: {e}")
            return []


# ==================== MOEX API КЛИЕНТ (PRODUCTION READY) ====================

class MoexAPIClient:
    """
    Production-ready клиент для бесплатного API Московской биржи (ISS)
    С полной обработкой ошибок, ретраями и rate limiting
    """

    BASE_URL = "https://iss.moex.com/iss"

    # Доступные интервалы MOEX
    INTERVAL_MAP = {
        '1min': '1', '2min': '2', '3min': '3', '5min': '5', '10min': '10',
        '15min': '15', '30min': '30', '1hour': '60', '2hour': '120',
        '4hour': '240', '1day': '24', '1week': '7', '1month': '31'
    }

    # Обратное отображение
    INTERVAL_NAMES = {v: k for k, v in INTERVAL_MAP.items()}

    def __init__(
            self,
            cache_ttl: int = 3600,
            max_retries: int = 3,
            timeout: int = 30,
            rate_limit: float = 0.2
    ):
        self._session: Optional[aiohttp.ClientSession] = None
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self.timeout = timeout
        self.rate_limit = rate_limit
        self._cache: Dict[str, Tuple[datetime, Any]] = {}
        self._lock = asyncio.Lock()
        self._is_closing = False
        self.request_count = 0
        self.error_count = 0
        self._last_request_time: Optional[datetime] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получение или создание сессии"""
        if self._is_closing:
            raise RuntimeError("MoexAPIClient is closed")

        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=50,
                limit_per_host=20,
                ttl_dns_cache=300,
                enable_cleanup_closed=True
            )
            timeout = aiohttp.ClientTimeout(total=self.timeout, connect=10)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; TradingBot/2.0)",
                    "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9"
                }
            )
        return self._session

    async def _rate_limit(self):
        """Rate limiting для соблюдения лимитов API"""
        if self._last_request_time:
            elapsed = (datetime.now() - self._last_request_time).total_seconds()
            if elapsed < self.rate_limit:
                await asyncio.sleep(self.rate_limit - elapsed)
        self._last_request_time = datetime.now()
        self.request_count += 1

    async def request(
            self,
            url: str,
            params: Dict = None,
            timeout: int = None,
            retry_on_error: bool = True
    ) -> Optional[Dict]:
        """Универсальный метод для запросов к MOEX API с улучшенной обработкой ошибок"""
        _timeout = timeout or self.timeout
        max_retries = 5
        retry_delays = [1, 2, 4, 8, 15]

        for attempt in range(max_retries):
            try:
                await self._rate_limit()
                session = await self._get_session()

                if not url.startswith(('http://', 'https://')):
                    full_url = f"{self.BASE_URL}{url}"
                else:
                    full_url = url

                async with session.get(full_url, params=params, timeout=_timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data
                    elif response.status == 429:
                        wait_time = min((attempt + 1) * 2, 30)
                        warning(f"⚠️ MOEX rate limit (429), waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    elif 500 <= response.status < 600:
                        if retry_on_error and attempt < max_retries - 1:
                            wait_time = retry_delays[attempt]
                            warning(f"⚠️ MOEX server error {response.status}, retry in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        error(f"❌ MOEX server error {response.status}")
                        return None
                    else:
                        error(f"❌ MOEX HTTP error {response.status}")
                        return None

            except asyncio.TimeoutError:
                self.error_count += 1
                if retry_on_error and attempt < max_retries - 1:
                    wait_time = retry_delays[attempt]
                    warning(f"⚠️ MOEX timeout, retry {attempt + 1}/{max_retries} in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                error(f"❌ MOEX timeout after {max_retries} attempts")
                return None

            except aiohttp.ClientConnectorError as e:
                if "Connection reset by peer" in str(e):
                    self.error_count += 1
                    warning(f"🔄 MOEX connection reset, reconnecting... attempt {attempt + 1}/{max_retries}")
                    await self._close_session()
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delays[attempt])
                        continue
                else:
                    error(f"❌ MOEX connection error: {e}")
                return None

            except aiohttp.ClientError as e:
                self.error_count += 1
                if retry_on_error and attempt < max_retries - 1:
                    wait_time = retry_delays[attempt]
                    warning(f"⚠️ MOEX client error: {e}, retry in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                error(f"❌ MOEX client error: {e}")
                return None

            except Exception as e:
                self.error_count += 1
                error(f"❌ MOEX unexpected error: {e}")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(retry_delays[attempt])

        return None

    async def get_last_price(self, ticker: str) -> Optional[float]:
        """Получение последней цены по тикеру"""
        if not ticker:
            return None

        ticker = ticker.upper().strip()

        try:
            url = f"/engines/stock/markets/shares/securities/{ticker}.json"
            data = await self.request(url)

            if not data:
                return None

            # Ищем в marketdata
            marketdata = data.get('marketdata', {})
            if marketdata:
                columns = marketdata.get('columns', [])
                rows = marketdata.get('data', [])

                if rows and columns:
                    try:
                        last_idx = columns.index('LAST')
                        if len(rows[0]) > last_idx:
                            price = float(rows[0][last_idx])
                            if price > 0:
                                return price
                    except (ValueError, IndexError):
                        pass

                    try:
                        close_idx = columns.index('CLOSE')
                        if len(rows[0]) > close_idx:
                            price = float(rows[0][close_idx])
                            if price > 0:
                                return price
                    except (ValueError, IndexError):
                        pass

            return None

        except Exception as e:
            debug(f"⚠️ Error getting price for {ticker}: {e}")
            return None

    async def get_security_info(self, ticker: str) -> Optional[Dict]:
        """Получение полной информации о ценной бумаге"""
        if not ticker:
            return None

        ticker = ticker.upper().strip()

        try:
            url = f"/securities/{ticker}.json"
            data = await self.request(url)

            if not data:
                return None

            result = {
                'ticker': ticker,
                'name': ticker,
                'isin': '',
                'shortname': '',
                'secid': ticker,
                'boardid': '',
                'market': ''
            }

            # Парсим description
            description = data.get('description', {})
            if description:
                columns = description.get('columns', [])
                rows = description.get('data', [])

                if rows and columns:
                    row = rows[0]
                    for i, col in enumerate(columns):
                        if i < len(row):
                            if col == 'NAME':
                                result['name'] = row[i]
                            elif col == 'SHORTNAME':
                                result['shortname'] = row[i]
                            elif col == 'ISIN':
                                result['isin'] = row[i]
                            elif col == 'BOARDID':
                                result['boardid'] = row[i]

            return result

        except Exception as e:
            debug(f"⚠️ Error getting security info for {ticker}: {e}")
            return None

    async def find_securities(self, query: str, limit: int = 20) -> List[Dict]:
        """Поиск ценных бумаг по запросу"""
        if not query:
            return []

        try:
            url = "/securities.json"
            params = {'q': query, 'limit': min(limit, 100)}
            data = await self.request(url, params)

            if not data:
                return []

            securities = data.get('securities', {})
            columns = securities.get('columns', [])
            rows = securities.get('data', [])

            results = []
            for row in rows[:limit]:
                result = {}
                for i, col in enumerate(columns):
                    if i < len(row):
                        result[col] = row[i]
                results.append(result)

            return results

        except Exception as e:
            error(f"❌ Error searching securities: {e}")
            return []

    async def get_board_info(self, ticker: str, board: str = "TQBR") -> Optional[Dict]:
        """Получение информации о доске торгов"""
        try:
            url = f"/engines/stock/markets/shares/boards/{board}/securities/{ticker}.json"
            data = await self.request(url)

            if not data:
                return None

            result = {'board': board, 'ticker': ticker}

            # Парсим данные
            marketdata = data.get('marketdata', {})
            if marketdata:
                columns = marketdata.get('columns', [])
                rows = marketdata.get('data', [])

                if rows and columns:
                    row = rows[0]
                    for i, col in enumerate(columns):
                        if i < len(row):
                            result[col.lower()] = row[i]

            return result

        except Exception as e:
            debug(f"⚠️ Error getting board info: {e}")
            return None

    async def close(self):
        """Закрытие клиента"""
        self._is_closing = True

        if self._session and not self._session.closed:
            # Закрываем все открытые соединения
            try:
                await self._session.close()
            except Exception as e:
                debug(f"⚠️ Error closing session: {e}")

            # Принудительно закрываем коннектор
            if self._session.connector:
                try:
                    await self._session.connector.close()
                except Exception as e:
                    debug(f"⚠️ Error closing connector: {e}")

        self._session = None
        self._cache.clear()

        # Даем время на полное закрытие
        await asyncio.sleep(0.1)

        info("🔌 MoexAPIClient closed")

    def clear_cache(self):
        """Очистка кэша"""
        self._cache.clear()
        debug("🗑️ MOEX cache cleared")

    def get_stats(self) -> Dict:
        """Получение статистики"""
        return {
            'requests': self.request_count,
            'errors': self.error_count,
            'cache_size': len(self._cache),
            'is_closing': self._is_closing
        }

    async def _close_session(self):
        """Принудительное закрытие сессии при ошибках соединения"""
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception as e:
                debug(f"⚠️ Error closing session: {e}")
            self._session = None


# ==================== ОСНОВНОЙ КЛАСС CANDLEBUILDER (PRODUCTION READY) ====================

class CandleBuilder:
    """
    Универсальный построитель свечей - PRODUCTION READY (СИНГЛТОН)
    Поддерживает:
    - MOEX ISS API (бесплатно)
    - Alfa API (FinInfoCandleEntity)
    - Построение из реальных сделок
    """

    # ========== СИНГЛТОН ==========
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        """Синглтон — всегда возвращаем один и тот же экземпляр"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
            self,
            api_client: Any = None,
            max_history: int = 200, # Уменьшаем историю с 1000 до 200
            instrument_manager=None,
            enable_moex: bool = True,
            enable_realtime_builder: bool = True,
            test_mode: bool = False
    ):
        # ⚠️ ВАЖНО: если уже инициализирован — ничего не делаем
        if CandleBuilder._initialized:
            return

        self.api_client = api_client
        self.instrument_manager = instrument_manager
        self.max_history = max_history

        self._candle_cache_ttl = 60  # TTL для кэша свечей 60 секунд

        self.enable_moex = enable_moex
        self.enable_realtime_builder = enable_realtime_builder
        self.test_mode = test_mode

        # MOEX клиент — создаётся лениво при первом вызове
        self._moex_client: Optional[MoexAPIClient] = None

        # Хранилища данных
        self.candles: Dict[str, Dict[str, deque]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=max_history)))
        self.current_candles: Dict[str, Dict[str, 'Candle']] = defaultdict(dict)
        self.price_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history * 2))
        self.volume_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history * 2))
        self.tick_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50000))

        # Кэши
        self._candle_cache: Dict[str, Tuple[List[Dict], datetime]] = {}
        self._indicator_cache: Dict[str, Dict] = defaultdict(dict)
        self._last_indicator_update: Dict[str, datetime] = {}

        # Состояние
        self._subscribed_tickers: set = set()
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.running = False
        self.has_real_trades = False
        self.last_trade_time: Optional[datetime] = None
        self.last_trade_price: Optional[float] = None

        # Статистика
        self.stats = {
            "total_trades_processed": 0,
            "total_candles_created": 0,
            "moex_calls": 0,
            "moex_errors": 0,
            "alfa_candle_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "start_time": None,
            "by_figi": defaultdict(lambda: {"trades": 0, "candles": 0})
        }

        self._running = False
        self._thread = None

        # Задачи
        self._tasks: List[asyncio.Task] = []
        self._cleanup_task: Optional[asyncio.Task] = None

        if USE_UNIFIED_CACHE:
            self._unified_cache = UnifiedCache(default_ttl=60, name="candle_builder")

        CandleBuilder._initialized = True
        info("✅ CandleBuilder initialized (Production Ready) SINGLETON")

    # ==================== ИНИЦИАЛИЗАЦИЯ ====================

    async def _ensure_moex_client(self):
        """Гарантированная инициализация MOEX клиента"""
        if self.test_mode:
            debug("🔧 Test mode: MOEX client disabled")
            return

        if self.enable_moex and self._moex_client is None:
            self._moex_client = MoexAPIClient()
            info("📊 MoexAPIClient initialized")

    async def start_builder(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        info("🚀 CandleBuilder started")
        # Небольшая задержка, чтобы поток успел запуститься
        await asyncio.sleep(0.1)

    # ========== ✅ ДОБАВЛЕННЫЕ МЕТОДЫ ==========
    def _run(self):
        """Основной цикл построителя свечей (синхронный)"""
        import asyncio

        # Создаём новый цикл событий для этого потока
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Запускаем основной цикл
            loop.run_until_complete(self._main_loop())
        except Exception as e:
            error(f"Ошибка в цикле CandleBuilder: {e}")
        finally:
            loop.close()

    async def _main_loop(self):
        """Асинхронный основной цикл построителя свечей"""
        info("🕯️ CandleBuilder основной цикл запущен")

        # Счётчики для периодических задач
        cleanup_counter = 0
        stats_counter = 0

        while self._running:
            try:
                # Базовая задержка - 1 секунда
                await asyncio.sleep(1)

                # ========== ПЕРИОДИЧЕСКИЕ ЗАДАЧИ ==========

                # 1. Очистка старого кэша (каждые 5 минут = 300 итераций)
                cleanup_counter += 1
                if cleanup_counter >= 300:
                    cleanup_counter = 0
                    self._cleanup_old_cache()
                    debug("🧹 Очистка кэша CandleBuilder выполнена")

                # 2. Обновление статистики (каждые 10 минут = 600 итераций)
                stats_counter += 1
                if stats_counter >= 600:
                    stats_counter = 0
                    stats = self.get_stats()
                    debug(f"📊 Статистика CandleBuilder: trades={stats['total_trades']}, "
                          f"candles={stats['total_candles']}, instruments={stats['instruments']}")

                # 3. Проверка и закрытие "зависших" свечей
                #    (которые должны были закрыться, но не были закрыты)
                await self._check_and_close_stale_candles()

                # 4. Обновление индикаторов для активных инструментов
                await self._update_active_indicators()

                # 5. Очистка старых тиков (каждые 5 минут)
                if cleanup_counter == 0:  # Только когда был сброшен cleanup_counter
                    self._cleanup_old_ticks()

            except asyncio.CancelledError:
                info("🛑 Основной цикл CandleBuilder отменён")
                break
            except Exception as e:
                error(f"❌ Ошибка в основном цикле CandleBuilder: {e}")
                # Не выходим из цикла, продолжаем работу
                await asyncio.sleep(5)

        info("🕯️ CandleBuilder основной цикл остановлен")

    async def _check_and_close_stale_candles(self):
        """Проверка и закрытие "зависших" свечей"""
        now = datetime.now()

        for figi, intervals in list(self.current_candles.items()):
            for interval_name, candle in list(intervals.items()):
                if candle and candle.open_time and candle.has_trades:
                    interval_seconds = INTERVAL_SECONDS.get(interval_name, 60)
                    elapsed = (now - candle.open_time).total_seconds()

                    # Если свеча должна была закрыться, но не закрыта
                    if elapsed >= interval_seconds + 5:  # +5 секунд запас
                        # Закрываем свечу
                        candle.is_closed = True
                        self.candles[figi][interval_name].append(candle.to_dict())

                        # Создаём новую свечу
                        new_candle = Candle(interval=interval_name)
                        self.current_candles[figi][interval_name] = new_candle

                        self.stats["total_candles_created"] += 1
                        debug(f"🕯️ Принудительно закрыта свеча {figi} {interval_name} "
                              f"(была открыта {elapsed:.0f} сек)")

    async def _update_active_indicators(self):
        """Обновление индикаторов для активных инструментов"""
        # Ограничиваем количество обновлений, чтобы не перегружать систему
        if not hasattr(self, '_indicator_update_counter'):
            self._indicator_update_counter = 0

        self._indicator_update_counter += 1

        # Обновляем индикаторы раз в 10 секунд
        if self._indicator_update_counter < 10:
            return

        self._indicator_update_counter = 0

        # Получаем список активных FIGI
        active_figi_list = list(self.current_candles.keys())

        for figi in active_figi_list[:10]:  # Не более 10 инструментов за раз
            try:
                # Обновляем индикаторы (force_refresh=True)
                await self.get_indicators(figi, "1min", force_refresh=True)
            except Exception as e:
                debug(f"⚠️ Ошибка обновления индикаторов для {figi}: {e}")

    def _cleanup_old_ticks(self):
        """Очистка старых тиков (оставляем только последние 10000)"""
        for figi in list(self.tick_history.keys()):
            if len(self.tick_history[figi]) > 10000:
                # Оставляем только последние 10000 тиков
                ticks = list(self.tick_history[figi])
                self.tick_history[figi] = deque(ticks[-10000:], maxlen=50000)
                debug(f"🗑️ Очищена история тиков для {figi}: {len(ticks)} → {len(self.tick_history[figi])}")

    # ========== КОНЕЦ ДОБАВЛЕННЫХ МЕТОДОВ ==========

    # def start(self):
    #     """Запуск построителя свечей (алиас для start_builder)"""
    #     self.start_builder()

    def stop_builder(self):
        """Остановка построителя свечей (синхронная версия)"""
        self._running = False
        if hasattr(self, '_thread') and self._thread:
            self._thread.join(timeout=5)  # ✅ таймаут 5 секунд
            if self._thread.is_alive():
                warning("⚠️ Поток CandleBuilder не остановился")
        info("🛑 CandleBuilder stopped")

    async def _cleanup_loop(self):
        """Фоновая очистка кэша с проверкой флага running"""
        while self.running:
            try:
                # Проверяем каждые 5 секунд, а не час, чтобы быстрее реагировать на остановку
                for _ in range(12):  # 12 * 5 = 60 секунд
                    if not self.running:
                        break
                    await asyncio.sleep(5)

                if not self.running:
                    break

                self._cleanup_old_cache()
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.running:
                    error(f"Error in cleanup loop: {e}")
                break

    def _cleanup_old_cache(self):
        """Очистка старого кэша"""
        now = datetime.now()
        expired_keys = []

        for key, (_, timestamp) in self._candle_cache.items():
            if (now - timestamp).total_seconds() > 3600:  # 1 час
                expired_keys.append(key)

        for key in expired_keys:
            del self._candle_cache[key]

        if expired_keys:
            debug(f"🗑️ Cleaned {len(expired_keys)} expired cache entries")

    # ==================== ПОЛУЧЕНИЕ СВЕЧЕЙ ИЗ MOEX ====================

    async def get_candles_from_moex(
            self,
            ticker: str,
            interval: str = "1day",
            days: int = 30,
            start_date: Optional[Union[str, datetime]] = None,
            end_date: Optional[Union[str, datetime]] = None
    ) -> List[Dict]:
        """Получение свечей из MOEX ISS API"""
        # В тестовом режиме возвращаем тестовые данные
        if self.test_mode:
            debug(f"🔧 Test mode: returning mock candles for {ticker}")
            return self._generate_mock_candles(ticker, days)

        await self._ensure_moex_client()

        if not self._moex_client:
            error("❌ MOEX client not available")
            return []

        self.stats["moex_calls"] += 1

        try:
            candles = await self._moex_client.get_candles(
                ticker=ticker,
                interval=interval,
                days=days,
                start_date=start_date,
                end_date=end_date
            )

            if candles:
                info(f"📊 Retrieved {len(candles)} candles for {ticker} from MOEX")
                return candles
            else:
                debug(f"⚠️ No candles for {ticker} from MOEX")
                return []

        except Exception as e:
            self.stats["moex_errors"] += 1
            error(f"❌ Error getting MOEX candles for {ticker}: {e}")
            return []

    def _generate_mock_candles(self, ticker: str, days: int) -> List[Dict]:
        """Генерация тестовых свечей для self-test"""
        import random
        candles = []
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        price = 100.0
        for i in range(min(days, 30)):
            date = start_date + timedelta(days=i)
            change = random.uniform(-0.03, 0.03)
            price = price * (1 + change)

            candle = {
                'open': price * 0.99,
                'high': price * 1.02,
                'low': price * 0.98,
                'close': price,
                'volume': random.randint(100000, 1000000),
                'timestamp': date.isoformat(),
                'interval': '1day',
                'is_closed': True
            }
            candles.append(candle)

        return candles

    async def get_current_price_from_moex(self, ticker: str) -> Optional[float]:
        """Получение текущей цены из MOEX"""
        await self._ensure_moex_client()

        if not self._moex_client:
            return None

        try:
            return await self._moex_client.get_last_price(ticker)
        except Exception as e:
            debug(f"⚠️ Error getting current price for {ticker}: {e}")
            return None

    # ==================== ПОЛУЧЕНИЕ СВЕЧЕЙ ИЗ ALFA API ====================

    async def get_candles_from_alfa(
            self,
            figi: str,
            days: int = 30,
            interval: str = "1day"
    ) -> List[Dict]:
        """
        Получение исторических свечей через Alfa API

        Args:
            figi: FIGI идентификатор инструмента
            days: Количество дней
            interval: Интервал свечей
        """
        if not self.api_client:
            warning("⚠️ Alfa API client not available")
            return []

        try:
            self.stats["alfa_candle_calls"] += 1

            # Конвертируем FIGI если нужно
            figi_int = int(figi) if str(figi).isdigit() else figi

            # Запрос к Alfa API
            response = await self.api_client.request("#data.Query", {
                "Type": "FinInfoCandleEntity",
                "Keys": [figi_int],
                "Init": True
            }, timeout=15)

            if not response or not response.get("Payload"):
                debug(f"⚠️ No response from Alfa API for {figi}")
                return []

            payload = json.loads(response["Payload"])
            data = payload.get("Data", [])

            if not data:
                return []

            # Парсим свечи
            candles = []
            cutoff_date = datetime.now() - timedelta(days=days) if days > 0 else None

            for item in data:
                item_id = item.get("IdFI") or item.get("IdObject")
                if item_id != figi_int:
                    continue

                history = item.get("History", [])
                for candle_data in history:
                    try:
                        candle_time = candle_data.get('Time')
                        if not candle_time:
                            continue

                        # Парсим дату
                        if isinstance(candle_time, str):
                            c_time = datetime.fromisoformat(candle_time.replace('Z', '+00:00'))
                        else:
                            c_time = candle_time

                        # Фильтр по дате
                        if cutoff_date and c_time < cutoff_date:
                            continue

                        candle = {
                            'time': candle_time,
                            'open': self._price_to_rub(candle_data.get('Open')),
                            'high': self._price_to_rub(candle_data.get('High')),
                            'low': self._price_to_rub(candle_data.get('Low')),
                            'close': self._price_to_rub(candle_data.get('Close')),
                            'volume': candle_data.get('Volume', 0),
                            'interval': candle_data.get('Interval', interval),
                            'is_closed': True,
                            'timestamp': c_time.isoformat() if hasattr(c_time, 'isoformat') else str(c_time)
                        }

                        if candle['close'] > 0:
                            candles.append(candle)

                    except Exception as e:
                        debug(f"⚠️ Error parsing Alfa candle: {e}")
                        continue

            # Сортируем по времени
            candles.sort(key=lambda x: x.get('timestamp', ''))

            if candles:
                info(f"✅ Retrieved {len(candles)} candles for {figi} from Alfa API")

            return candles

        except Exception as e:
            error(f"❌ Error getting Alfa API candles for {figi}: {e}")
            return []

    def _price_to_rub(self, price: Any) -> float:
        """Конвертация из копеек в рубли"""
        if price is None:
            return 0.0
        try:
            return round(float(price) / 100.0, 4)
        except (ValueError, TypeError):
            return 0.0

    # ==================== УНИВЕРСАЛЬНЫЙ МЕТОД ПОЛУЧЕНИЯ СВЕЧЕЙ ====================

    async def get_candles(
            self,
            identifier: str,
            source: str = "auto",
            interval: str = "1day",
            days: int = 30,
            use_cache: bool = True,
            cache_ttl: int = 300,
            **kwargs  # ← ДОБАВИТЬ ЭТУ СТРОКУ (принимает limit и другие параметры)
    ) -> List[Dict]:
        """
        Универсальное получение свечей из разных источников

        Args:
            identifier: Тикер (для MOEX) или FIGI (для Alfa)
            source: 'moex', 'alfa', 'auto'
            interval: Интервал свечей
            days: Количество дней
            use_cache: Использовать кэш
            cache_ttl: Время жизни кэша в секундах
            **kwargs: Дополнительные параметры (limit и т.д.) - игнорируются

        Returns:
            List[Dict]: Список свечей
        """
        # ========== 1. ВАЛИДАЦИЯ ==========
        if not identifier:
            error("❌ CandleBuilder.get_candles: пустой identifier")
            return []

        # Логируем вызов с дополнительными параметрами
        if kwargs:
            debug(f"📊 CandleBuilder.get_candles: {identifier}, "
                  f"source={source}, interval={interval}, days={days}, "
                  f"доп.параметры={list(kwargs.keys())} (игнорируются)")

        # ========== 2. КЭШ ==========
        cache_key = f"{identifier}_{source}_{interval}_{days}"
        debug(f"🔍 CandleBuilder.get_candles: кэш-ключ={cache_key}")

        if use_cache and cache_key in self._candle_cache:
            cached_data, timestamp = self._candle_cache[cache_key]
            cache_age = (datetime.now() - timestamp).total_seconds()

            if cache_age < cache_ttl:
                self.stats["cache_hits"] += 1
                debug(f"📦 Cache HIT для {identifier} (возраст: {cache_age:.1f}с)")
                return cached_data.copy() if cached_data else []
            else:
                debug(f"⏰ Cache EXPIRED для {identifier}")
        else:
            debug(f"❌ Cache MISS для {identifier}")

        self.stats["cache_misses"] += 1

        # ========== 3. ОПРЕДЕЛЕНИЕ ИСТОЧНИКА ==========
        result = []
        is_ticker = identifier.isalpha() and len(identifier) <= 10
        is_figi = identifier.isdigit() or identifier.startswith('BBG')

        debug(f"🔍 Определение источника: is_ticker={is_ticker}, is_figi={is_figi}")

        # ========== 4. ПОЛУЧЕНИЕ ДАННЫХ ==========
        if source == "moex" or (source == "auto" and is_ticker):
            debug(f"📡 Запрос к MOEX для {identifier}")
            result = await self.get_candles_from_moex(identifier, interval=interval, days=days)
            if result:
                info(f"✅ MOEX: {len(result)} свечей для {identifier}")

        elif source == "alfa" or (source == "auto" and is_figi):
            debug(f"📡 Запрос к Alfa для {identifier}")
            result = await self.get_candles_from_alfa(identifier, days=days)
            if result:
                info(f"✅ Alfa: {len(result)} свечей для {identifier}")

        else:  # AUTO режим
            debug(f"🔄 AUTO режим для {identifier}")
            if is_ticker:
                result = await self.get_candles_from_moex(identifier, interval=interval, days=days)
                if result:
                    info(f"✅ MOEX: {len(result)} свечей для {identifier}")

            if not result and is_figi:
                result = await self.get_candles_from_alfa(identifier, days=days)
                if result:
                    info(f"✅ Alfa: {len(result)} свечей для {identifier}")

        # ========== 5. ОБРАБОТКА LIMIT ==========
        if not result:
            warning(f"⚠️ Не удалось получить свечи для {identifier}")
            return []

        limit = kwargs.get('limit')
        if limit and isinstance(limit, int) and limit > 0:
            if len(result) > limit:
                original = len(result)
                result = result[-limit:]
                debug(f"📏 Обрезано {original} → {len(result)} свечей (limit={limit})")

        # ========== 6. СОХРАНЕНИЕ В КЭШ ==========
        if use_cache and result:
            self._candle_cache[cache_key] = (result.copy(), datetime.now())
            debug(f"💾 Сохранено в кэш: {cache_key} ({len(result)} свечей)")

        # ========== 7. ЛОГИРОВАНИЕ РЕЗУЛЬТАТА ==========
        info(f"📊 CandleBuilder: возвращено {len(result)} свечей для {identifier}")
        if result:
            info(f"   📅 Диапазон: {result[0].get('timestamp', '?')[:10]} → {result[-1].get('timestamp', '?')[:10]}")

        return result

    # ==================== ПОСТРОЕНИЕ СВЕЧЕЙ ИЗ СДЕЛОК ====================

    async def add_trade(
            self,
            symbol: str,  # ✅ ИСПРАВЛЕНИЕ 4: Переименовано с figi на symbol
            price: float,
            volume: int,
            timestamp: datetime
    ):
        """
        Добавление сделки для построения свечей в реальном времени

        Args:
            symbol: Символ инструмента (тикер или FIGI)
            price: Цена сделки
            volume: Объем сделки
            timestamp: Время сделки
        """
        if not self.enable_realtime_builder:
            return

        if not symbol or price <= 0 or volume <= 0:
            warning(f"⚠️ Invalid trade data: symbol={symbol}, price={price}, volume={volume}")
            return

        trade_data = {
            'price': price,
            'volume': volume,
            'timestamp': timestamp
        }

        await self._process_trade(symbol, trade_data)

    async def _process_trade(self, figi: str, trade_data: Dict):
        """Внутренняя обработка сделки"""
        try:
            price = trade_data['price']
            volume = trade_data['volume']
            timestamp = trade_data['timestamp']

            # Сохраняем историю
            self.tick_history[figi].append({
                'price': price,
                'volume': volume,
                'timestamp': timestamp
            })
            self.price_history[figi].append(price)
            self.volume_history[figi].append(volume)

            # Обновляем статистику
            self.stats["total_trades_processed"] += 1
            self.stats["by_figi"][figi]["trades"] += 1
            self.last_trade_time = timestamp
            self.last_trade_price = price
            self.has_real_trades = True

            # Обновляем свечи для всех интервалов
            async with self._locks[figi]:
                intervals = list(INTERVAL_SECONDS.keys())
                for interval_name in intervals:
                    await self._update_candle(figi, interval_name, price, volume, timestamp)

        except Exception as e:
            error(f"❌ Error processing trade for {figi}: {e}")

    async def _update_candle(
            self,
            figi: str,
            interval: str,
            price: float,
            volume: int,
            timestamp: datetime
    ):
        """Обновление свечи для указанного интервала"""
        async with self._locks[figi]:
            current = self.current_candles[figi].get(interval)

            if current is None:
                current = Candle(interval=interval)
                self.current_candles[figi][interval] = current

            # Проверяем нужно ли закрыть текущую свечу
            should_close = False

            if current.open_time is not None:
                elapsed = (timestamp - current.open_time).total_seconds()
                interval_seconds = INTERVAL_SECONDS.get(interval, 60)

                if elapsed >= interval_seconds:
                    should_close = True

            if should_close:
                # Закрываем текущую свечу только если в ней были сделки
                if current.has_trades:
                    current.is_closed = True
                    self.candles[figi][interval].append(current.to_dict())
                    self.stats["total_candles_created"] += 1
                    self.stats["by_figi"][figi]["candles"] += 1

                # Создаем новую свечу
                current = Candle(interval=interval)
                self.current_candles[figi][interval] = current

            # Обновляем текущую свечу
            current.update(price, volume, timestamp)

    # ==================== ПОЛУЧЕНИЕ ДАННЫХ ====================

    async def get_candles_for_figi(
            self,
            figi: str,
            interval: str = "1min",
            limit: int = 100
    ) -> List[Dict]:
        """
        Получение сформированных свечей для FIGI

        Args:
            figi: FIGI инструмента
            interval: Интервал свечей
            limit: Максимальное количество свечей

        Returns:
            List[Dict]: Список свечей
        """
        if figi not in self.candles:
            return []

        candles_deque = self.candles[figi].get(interval)
        if not candles_deque:
            return []

        candles_list = list(candles_deque)
        if limit > 0:
            candles_list = candles_list[-limit:]

        return candles_list

    async def get_current_candle(self, figi: str, interval: str = "1min") -> Optional[Dict]:
        """
        Получение текущей (незакрытой) свечи

        Args:
            figi: FIGI инструмента
            interval: Интервал свечей

        Returns:
            Optional[Dict]: Текущая свеча или None
        """
        current = self.current_candles[figi].get(interval)
        if current and current.open_time and current.has_trades:
            return current.to_dict()
        return None

    async def get_price_history(self, figi: str, limit: int = 100) -> List[float]:
        """
        Получение истории цен

        Args:
            figi: FIGI инструмента
            limit: Максимальное количество значений

        Returns:
            List[float]: Список цен
        """
        if figi not in self.price_history:
            return []

        prices = list(self.price_history[figi])
        if limit > 0:
            prices = prices[-limit:]

        return prices

    async def get_volume_history(self, figi: str, limit: int = 100) -> List[int]:
        """
        Получение истории объемов

        Args:
            figi: FIGI инструмента
            limit: Максимальное количество значений

        Returns:
            List[int]: Список объемов
        """
        if figi not in self.volume_history:
            return []

        volumes = list(self.volume_history[figi])
        if limit > 0:
            volumes = volumes[-limit:]

        return volumes

    async def get_tick_history(self, figi: str, limit: int = 100) -> List[Dict]:
        """
        Получение истории тиков

        Args:
            figi: FIGI инструмента
            limit: Максимальное количество тиков

        Returns:
            List[Dict]: Список тиков
        """
        if figi not in self.tick_history:
            return []

        ticks = list(self.tick_history[figi])
        if limit > 0:
            ticks = ticks[-limit:]

        return ticks

    # ==================== ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ ====================

    async def get_indicators(
            self,
            figi: str,
            interval: str = "1min",
            force_refresh: bool = False
    ) -> Dict:
        """
        Получение технических индикаторов для инструмента

        Args:
            figi: FIGI инструмента
            interval: Интервал свечей
            force_refresh: Принудительное обновление

        Returns:
            Dict: Словарь с индикаторами
        """
        # Проверяем кэш
        cache_key = f"{figi}_{interval}"
        if not force_refresh:
            last_update = self._last_indicator_update.get(cache_key)
            if last_update and (datetime.now() - last_update).total_seconds() < 60:
                if cache_key in self._indicator_cache:
                    return self._indicator_cache[cache_key].copy()

        # Получаем свечи
        candles = await self.get_candles_for_figi(figi, interval, limit=200)
        if not candles:
            return {}

        # Извлекаем цены
        prices = [c['close'] for c in candles if c.get('close', 0) > 0]
        volumes = [c.get('volume', 0) for c in candles]

        if len(prices) < 10:
            return {}

        # Рассчитываем индикаторы
        indicators = {
            'timestamp': datetime.now().isoformat(),
            'figi': figi,
            'interval': interval,
            'last_price': prices[-1] if prices else 0,
            'price_change_1d': self._calculate_price_change(prices, 1),
            'price_change_5d': self._calculate_price_change(prices, 5),
            'price_change_20d': self._calculate_price_change(prices, 20),
        }

        # RSI
        indicators['rsi'] = TechnicalIndicators.calculate_rsi(prices, 14)

        # MACD
        indicators['macd'] = TechnicalIndicators.calculate_macd(prices)

        # Bollinger Bands
        indicators['bollinger'] = TechnicalIndicators.calculate_bollinger(prices)

        # Moving Averages
        indicators['moving_averages'] = TechnicalIndicators.calculate_moving_averages(prices)

        # ATR (если есть свечи)
        if len(candles) >= 15:
            indicators['atr'] = TechnicalIndicators.calculate_atr(candles, 14)

        # VWAP
        indicators['vwap'] = TechnicalIndicators.calculate_vwap(candles)

        # OBV
        if len(prices) == len(volumes) and len(prices) > 1:
            obv = TechnicalIndicators.calculate_obv(prices, volumes)
            indicators['obv'] = obv[-1] if obv else 0

        # Сохраняем в кэш
        self._indicator_cache[cache_key] = indicators
        self._last_indicator_update[cache_key] = datetime.now()

        return indicators

    def _calculate_price_change(self, prices: List[float], days: int) -> float:
        """Расчет изменения цены за N дней"""
        if len(prices) <= days:
            return 0.0

        current = prices[-1]
        previous = prices[-days - 1] if len(prices) > days else prices[0]

        if previous == 0:
            return 0.0

        return round(((current - previous) / previous) * 100, 2)

    # ==================== СТАТИСТИКА И УПРАВЛЕНИЕ ====================

    async def get_ticker_by_figi(self, figi: str) -> Optional[str]:
        if self.instrument_manager and hasattr(self.instrument_manager, 'get_instrument_by_figi'):
            try:
                inst = await self.instrument_manager.get_instrument_by_figi(figi)
                if inst:
                    return inst.get('ticker')
            except Exception as e:
                debug(f"⚠️ Error getting ticker for {figi}: {e}")
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Получение полной статистики"""
        # Конвертируем defaultdict в обычный dict
        by_figi_dict = {}
        for figi, data in self.stats["by_figi"].items():
            by_figi_dict[figi] = dict(data) if hasattr(data, 'items') else data

        uptime = None
        if self.stats["start_time"]:
            uptime = (datetime.now() - self.stats["start_time"]).total_seconds()

        return {
            "total_trades": self.stats["total_trades_processed"],
            "total_candles": self.stats["total_candles_created"],
            "instruments": len(self.stats["by_figi"]),
            "has_real_trades": self.has_real_trades,
            "moex_calls": self.stats["moex_calls"],
            "moex_errors": self.stats["moex_errors"],
            "alfa_candle_calls": self.stats["alfa_candle_calls"],
            "cache_hits": self.stats["cache_hits"],
            "cache_misses": self.stats["cache_misses"],
            "cache_ratio": self._get_cache_ratio(),
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
            "last_trade_price": self.last_trade_price,
            "uptime_seconds": uptime,
            "by_figi": by_figi_dict,
            "running": self.running
        }

    def _get_cache_ratio(self) -> float:
        """Расчет процента попаданий в кэш"""
        total = self.stats["cache_hits"] + self.stats["cache_misses"]
        if total == 0:
            return 0.0
        return round((self.stats["cache_hits"] / total) * 100, 2)

    def clear_cache(self):
        """Очистка всех кэшей"""
        self._candle_cache.clear()
        self._indicator_cache.clear()
        self._last_indicator_update.clear()

        if self._moex_client:
            self._moex_client.clear_cache()

        info("🧹 All caches cleared")

    def reset_stats(self):
        """Сброс статистики"""
        self.stats = {
            "total_trades_processed": 0,
            "total_candles_created": 0,
            "moex_calls": 0,
            "moex_errors": 0,
            "alfa_candle_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "start_time": datetime.now(),
            "by_figi": defaultdict(lambda: {"trades": 0, "candles": 0})
        }
        info("📊 Statistics reset")

    async def clear_instrument_data(self, figi: str):
        """
        Очистка данных по конкретному инструменту

        Args:
            figi: FIGI инструмента
        """
        if figi in self.candles:
            del self.candles[figi]
        if figi in self.current_candles:
            del self.current_candles[figi]
        if figi in self.price_history:
            del self.price_history[figi]
        if figi in self.volume_history:
            del self.volume_history[figi]
        if figi in self.tick_history:
            del self.tick_history[figi]
        if figi in self._locks:
            del self._locks[figi]

        info(f"🗑️ Cleared data for {figi}")

    async def shutdown(self):
        """Полное завершение работы с очисткой всех ресурсов"""
        info("🛑 CandleBuilder shutting down...")

        # 1. Останавливаем основной цикл
        self._running = False

        # 2. Отменяем задачи
        for task in self._tasks:
            if task and not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # 3. Очищаем кэши
        self.clear_cache()

        # 4. Закрываем MOEX клиент
        if self._moex_client:
            await self._moex_client.close()
            self._moex_client = None

        # 5. Сборка мусора
        import gc
        gc.collect()

        info("✅ CandleBuilder shutdown complete")

    @staticmethod
    def calculate_stochastic(candles: List[Dict], period: int = 14) -> Tuple[float, float]:
        """
        Расчет стохастического осциллятора (Stochastic Oscillator)
        Returns: (%K, %D)
        """
        if not candles or len(candles) < period:
            return 50.0, 50.0

        try:
            highs = [c.get('high', c['close']) for c in candles[-period:]]
            lows = [c.get('low', c['close']) for c in candles[-period:]]
            closes = [c['close'] for c in candles[-period:]]

            current_close = closes[-1]
            low_min = min(lows)
            high_max = max(highs)

            if high_max == low_min:
                k = 50.0
            else:
                k = (current_close - low_min) / (high_max - low_min) * 100

            d = k  # упрощённо

            return round(k, 1), round(d, 1)
        except Exception as e:
            logger.error(f"Stochastic calculation error: {e}")
            return 50.0, 50.0

    @staticmethod
    def calculate_all_indicators(prices: List[float], volumes: List[int], candles: List[Dict]) -> Dict[str, Any]:
        """
        Рассчёт ВСЕХ индикаторов одним методом
        """
        result = {}

        # RSI
        result['rsi'] = TechnicalIndicators.calculate_rsi(prices)

        # MACD
        result['macd'] = TechnicalIndicators.calculate_macd(prices)

        # Bollinger
        result['bollinger'] = TechnicalIndicators.calculate_bollinger(prices)

        # Moving Averages
        result['moving_averages'] = TechnicalIndicators.calculate_moving_averages(prices)

        # ATR
        if len(candles) >= 15:
            result['atr'] = TechnicalIndicators.calculate_atr(candles)

        # VWAP
        result['vwap'] = TechnicalIndicators.calculate_vwap(candles)

        # OBV
        if len(prices) == len(volumes) and len(prices) > 1:
            obv = TechnicalIndicators.calculate_obv(prices, volumes)
            result['obv'] = obv[-1] if obv else 0

        # Stochastic
        result['stochastic'] = TechnicalIndicators.calculate_stochastic(candles)

        return result



# ==================== ПРИМЕР ИСПОЛЬЗОВАНИЯ ====================

async def example():
    """Пример использования CandleBuilder"""
    print("\n" + "=" * 70)
    print("📊 CANDLEBUILDER - PRODUCTION READY EXAMPLE")
    print("=" * 70)

    builder = candle_builder

    try:
        # Запускаем
        await builder.start()

        # 1. Получение свечей из MOEX (используем ТИКЕР)
        print("\n📈 1. Getting candles from MOEX (using TICKER):")
        candles = await builder.get_candles("SBER", source="moex", interval="1day", days=7)

        if candles:
            print(f"   Retrieved {len(candles)} candles:")
            for i, c in enumerate(candles[:5]):
                timestamp = c.get('timestamp', 'unknown')[:10]
                print(f"   [{i + 1}] {timestamp}: O={c['open']:.2f}, H={c['high']:.2f}, "
                      f"L={c['low']:.2f}, C={c['close']:.2f}, V={c['volume']:.0f}")
        else:
            print("   No candles retrieved")

        # 2. Симуляция реальных сделок (используем СИМВОЛ)
        print("\n💹 2. Simulating real trades:")
        symbol = "SBER"  # ✅ Теперь это понятно — символ, а не FIGI
        start_time = datetime.now()

        for i in range(10):
            price = 250 + i * 0.5 + random.uniform(-1, 1)
            volume = random.randint(100, 1000)
            # ✅ Используем параметр symbol
            await builder.add_trade(symbol, price, volume, start_time + timedelta(seconds=i * 10))
            print(f"   Trade {i + 1}: price={price:.2f}, volume={volume}")

        await asyncio.sleep(1)

        # 3. Получение сформированных свечей
        print("\n📊 3. Formed candles:")
        candles_1min = await builder.get_candles_for_figi(symbol, "1min", limit=5)

        if candles_1min:
            for i, c in enumerate(candles_1min):
                open_time = c.get('open_time', 'unknown')
                if isinstance(open_time, str):
                    open_time = open_time[11:19] if len(open_time) > 11 else open_time
                print(f"   [{i + 1}] {open_time}: O={c['open']:.2f}, H={c['high']:.2f}, "
                      f"L={c['low']:.2f}, C={c['close']:.2f}, V={c['volume']}")
        else:
            print("   No candles formed yet")

        # 4. Текущая свеча
        print("\n🔥 4. Current candle:")
        current = await builder.get_current_candle(symbol, "1min")
        if current:
            print(f"   Open: {current['open']:.2f}, High: {current['high']:.2f}, "
                  f"Low: {current['low']:.2f}, Close: {current['close']:.2f}")

        # 5. Технические индикаторы
        print("\n📊 5. Technical indicators:")
        indicators = await builder.get_indicators(symbol, "1min")
        if indicators:
            print(f"   RSI(14): {indicators.get('rsi', 0):.2f}")
            print(f"   Price change 1d: {indicators.get('price_change_1d', 0):.2f}%")
            print(f"   VWAP: {indicators.get('vwap', 0):.2f}")

            ma = indicators.get('moving_averages', {})
            if ma.get('MA_20'):
                print(f"   MA(20): {ma['MA_20']:.2f}")

            bb = indicators.get('bollinger', {})
            if bb.get('middle'):
                print(f"   Bollinger: M={bb['middle']:.2f}, U={bb['upper']:.2f}, L={bb['lower']:.2f}")

        # 6. Статистика
        print("\n📈 6. Statistics:")
        stats = builder.get_stats()
        print(f"   Total trades: {stats['total_trades']}")
        print(f"   Total candles: {stats['total_candles']}")
        print(f"   MOEX calls: {stats['moex_calls']}")
        print(f"   Cache hit ratio: {stats['cache_ratio']}%")

    finally:
        # Останавливаем
        await builder.stop()

    print("\n✅ Example completed successfully")
    print("=" * 70)


# ==================== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ====================
# Создаём единственный экземпляр (синглтон через __new__)
candle_builder = CandleBuilder()

