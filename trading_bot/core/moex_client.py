"""Клиент для получения свечей с MOEX ISS API (бесплатно, без токена) - PRODUCTION READY"""

import aiohttp
import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Any
import logging
from contextlib import asynccontextmanager

from trading_bot.logger import info, error, warning, debug

logger = logging.getLogger(__name__)


class MoexClient:
    """
    Клиент для бесплатного API Московской биржи (MOEX ISS)
    Не требует токена, работает с тикерами напрямую
    PRODUCTION READY - с полным управлением сессиями
    """

    BASE_URL = "https://iss.moex.com/iss"

    INTERVAL_MAP = {
        1: 1, 5: 5, 10: 10, 15: 15, 30: 30, 60: 60, 1440: 24,
    }

    def __init__(self, timeout: int = None, min_interval: float = None):
        # Настройки из переменных окружения
        self.timeout = timeout or int(os.getenv('MOEX_TIMEOUT', '15'))
        self._min_interval = min_interval or float(os.getenv('MOEX_RATE_LIMIT', '0.5'))

        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time: Optional[datetime] = None
        self._cache: Dict[str, Any] = {}
        self._cache_ttl = int(os.getenv('MOEX_CACHE_TTL', '300'))
        self._cache_time: Dict[str, datetime] = {}

        self._is_closing = False
        self._request_count = 0
        self._error_count = 0

        # Статистика ошибок по тикерам
        self._error_stats: Dict[str, int] = {}
        self._skip_until: Dict[str, datetime] = {}
        self._skip_consecutive_errors = int(os.getenv('MOEX_SKIP_AFTER_ERRORS', '3'))
        self._skip_minutes = int(os.getenv('MOEX_SKIP_MINUTES', '30'))

        self._ticker_cache: Dict[str, str] = {}
        self._ticker_cache_time: Dict[str, float] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получение или создание сессии с улучшенной конфигурацией"""
        if self._is_closing:
            raise RuntimeError("MoexClient is closing, cannot create new session")

        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=int(os.getenv('MOEX_CONNECTION_LIMIT', '10')),
                limit_per_host=int(os.getenv('MOEX_CONNECTION_LIMIT_PER_HOST', '5')),
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                force_close=False
            )
            timeout = aiohttp.ClientTimeout(
                total=self.timeout,
                connect=int(os.getenv('MOEX_CONNECT_TIMEOUT', '10')),
                sock_read=int(os.getenv('MOEX_READ_TIMEOUT', '15'))
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; TradingBot/2.0)",
                    "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "Connection": "keep-alive"
                }
            )
            debug("📡 MOEX session created")
        return self._session

    async def _close_session(self):
        """Принудительное закрытие сессии с очисткой всех ресурсов"""
        if self._session and not self._session.closed:
            try:
                # Закрываем все открытые соединения
                await self._session.close()
                debug("🔌 MOEX session closed")
            except Exception as e:
                debug(f"⚠️ Error closing session: {e}")
            finally:
                self._session = None

    async def _rate_limit(self):
        """Rate limiting для соблюдения лимитов API"""
        if self._last_request_time:
            elapsed = (datetime.now() - self._last_request_time).total_seconds()
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = datetime.now()
        self._request_count += 1

    def _should_skip_ticker(self, ticker: str) -> bool:
        """Проверка, нужно ли пропустить тикер из-за частых ошибок"""
        if ticker in self._skip_until:
            if datetime.now() < self._skip_until[ticker]:
                return True
            else:
                # Сбрасываем после разблокировки
                del self._skip_until[ticker]
                self._error_stats[ticker] = 0
        return False

    def _record_error(self, ticker: str):
        """Запись ошибки для тикера"""
        self._error_count += 1
        self._error_stats[ticker] = self._error_stats.get(ticker, 0) + 1

        if self._error_stats[ticker] >= self._skip_consecutive_errors:
            self._skip_until[ticker] = datetime.now() + timedelta(minutes=self._skip_minutes)
            warning(f"🔒 {ticker} заблокирован на {self._skip_minutes} мин после {self._error_stats[ticker]} ошибок")

    def _record_success(self, ticker: str):
        """Сброс счётчика ошибок при успехе"""
        if ticker in self._error_stats:
            self._error_stats[ticker] = 0

    def _get_cache_key(self, ticker: str, interval_minutes: int, days: int) -> str:
        """Формирование ключа кэша"""
        return f"{ticker}_{interval_minutes}_{days}"

    def _get_from_cache(self, key: str) -> Optional[List[Tuple[float, float]]]:
        """Получение данных из кэша"""
        if key in self._cache and key in self._cache_time:
            if (datetime.now() - self._cache_time[key]).total_seconds() < self._cache_ttl:
                return self._cache[key].copy()
        return None

    def _set_cache(self, key: str, data: List[Tuple[float, float]]):
        """Сохранение данных в кэш"""
        self._cache[key] = data.copy()
        self._cache_time[key] = datetime.now()

    @asynccontextmanager
    async def _request_context(self, ticker: str):
        """Контекстный менеджер для безопасных запросов"""
        session = None
        try:
            await self._rate_limit()
            session = await self._get_session()
            yield session
        except aiohttp.ClientConnectorError as e:
            if "Connection reset by peer" in str(e):
                warning(f"🔄 MOEX connection reset for {ticker}, reconnecting...")
                await self._close_session()
            raise
        except Exception as e:
            raise
        finally:
            pass

    async def get_candles(
            self,
            ticker: str,
            interval_minutes: int = 5,
            days: int = 30,
            market: str = "shares",
            board: str = "tqbr",
            use_cache: bool = True
    ) -> List[Tuple[float, float]]:
        """
        Получение свечей с MOEX с улучшенной обработкой ошибок и кэшированием

        Args:
            ticker: Тикер инструмента
            interval_minutes: Интервал в минутах
            days: Количество дней истории
            market: Рынок (shares, bonds, etc.)
            board: Торговая доска
            use_cache: Использовать кэш

        Returns:
            List[Tuple[float, float]]: Список кортежей (close, volume)
        """
        if not ticker:
            warning("Пустой тикер")
            return []

        ticker = ticker.upper().strip()

        # Проверка на блокировку
        if self._should_skip_ticker(ticker):
            debug(f"⏭️ {ticker} временно заблокирован из-за ошибок")
            return []

        # Проверка кэша
        cache_key = self._get_cache_key(ticker, interval_minutes, days)
        if use_cache:
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                debug(f"📦 Cache hit for {ticker}")
                return cached

        moex_interval = self.INTERVAL_MAP.get(interval_minutes, 5)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        url = f"{self.BASE_URL}/engines/stock/markets/{market}/boards/{board}/securities/{ticker}/candles.json"
        params = {
            'interval': moex_interval,
            'from': start_date.strftime("%Y-%m-%d"),
            'till': end_date.strftime("%Y-%m-%d"),
            'start': 0
        }

        max_retries = int(os.getenv('MOEX_MAX_RETRIES', '5'))
        retry_delays = [1, 2, 4, 8, 15]

        for attempt in range(max_retries):
            try:
                async with self._request_context(ticker) as session:
                    debug(f"📡 Request MOEX candles: {ticker} ({interval_minutes}min, {days}days)")

                    async with session.get(url, params=params) as response:
                        if response.status == 429:  # Too Many Requests
                            wait_time = min((attempt + 1) * 2, 30)
                            warning(f"⚠️ MOEX rate limit (429) for {ticker}, waiting {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue

                        if response.status != 200:
                            warning(f"MOEX returned {response.status} for {ticker}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delays[attempt])
                                continue
                            self._record_error(ticker)
                            return []

                        data = await response.json()
                        candles_data = data.get('candles', {})

                        if not candles_data:
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delays[attempt])
                                continue
                            return []

                        columns = candles_data.get('columns', [])
                        rows = candles_data.get('data', [])

                        if not columns or not rows:
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delays[attempt])
                                continue
                            return []

                        try:
                            close_idx = columns.index('close')
                            volume_idx = columns.index('volume')
                        except ValueError:
                            warning(f"Missing close/volume columns for {ticker}")
                            return []

                        result = []
                        for row in rows:
                            if len(row) > max(close_idx, volume_idx):
                                close = float(row[close_idx]) if row[close_idx] else 0
                                volume = float(row[volume_idx]) if row[volume_idx] else 0
                                if close > 0:
                                    result.append((close, volume))

                        if result:
                            info(f"✅ MOEX: {len(result)} candles for {ticker}")
                            self._record_success(ticker)
                            if use_cache:
                                self._set_cache(cache_key, result)
                            return result
                        else:
                            warning(f"No valid candles for {ticker}")
                            return []

            except asyncio.TimeoutError:
                warning(f"⏰ MOEX timeout for {ticker}, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                self._record_error(ticker)
                return []

            except aiohttp.ClientConnectorError as e:
                if "Connection reset by peer" in str(e):
                    warning(f"🔄 MOEX connection reset for {ticker}, retry {attempt + 1}/{max_retries}")
                    await self._close_session()
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delays[attempt])
                        continue
                else:
                    warning(f"🌐 MOEX connection error for {ticker}: {e}")
                self._record_error(ticker)
                return []

            except aiohttp.ClientResponseError as e:
                warning(f"🌐 MOEX response error for {ticker}: {e.status}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                self._record_error(ticker)
                return []

            except aiohttp.ClientError as e:
                warning(f"🌐 MOEX client error for {ticker}: {e}, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                self._record_error(ticker)
                return []

            except Exception as e:
                error(f"❌ Unexpected error for {ticker}: {e}")
                self._record_error(ticker)
                return []

        return []

    def get_candles_sync(
            self,
            ticker: str,
            interval_minutes: int = 5,
            days: int = 30
    ) -> List[Tuple[float, float]]:
        """
        Синхронная обёртка для get_candles
        Безопасно создаёт и закрывает event loop
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self.get_candles(ticker, interval_minutes, days)
                )
                return result
            finally:
                # Закрываем все ожидающие задачи
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
        except Exception as e:
            error(f"Error in get_candles_sync for {ticker}: {e}")
            return []

    # async def get_current_price(self, ticker: str, market: str = "shares") -> Optional[float]:
    #     """Получение текущей цены с MOEX"""
    #     ticker = ticker.upper().strip()
    #
    #     if self._should_skip_ticker(ticker):
    #         return None
    #
    #     url = f"{self.BASE_URL}/engines/stock/markets/{market}/securities/{ticker}.json"
    #
    #     max_retries = 3
    #     for attempt in range(max_retries):
    #         try:
    #             async with self._request_context(ticker) as session:
    #                 async with session.get(url, timeout=10) as response:
    #                     if response.status != 200:
    #                         if attempt < max_retries - 1:
    #                             await asyncio.sleep(1)
    #                             continue
    #                         self._record_error(ticker)
    #                         return None
    #
    #                     data = await response.json()
    #                     marketdata = data.get('marketdata', {})
    #
    #                     if marketdata:
    #                         columns = marketdata.get('columns', [])
    #                         rows = marketdata.get('data', [])
    #
    #                         if rows and columns:
    #                             try:
    #                                 last_idx = columns.index('LAST')
    #                                 if len(rows[0]) > last_idx and rows[0][last_idx]:
    #                                     self._record_success(ticker)
    #                                     return float(rows[0][last_idx])
    #                             except ValueError:
    #                                 pass
    #
    #                             try:
    #                                 close_idx = columns.index('CLOSE')
    #                                 if len(rows[0]) > close_idx and rows[0][close_idx]:
    #                                     self._record_success(ticker)
    #                                     return float(rows[0][close_idx])
    #                             except ValueError:
    #                                 pass
    #
    #                     return None
    #
    #         except asyncio.TimeoutError:
    #             debug(f"⏰ Timeout getting price for {ticker}, attempt {attempt + 1}/{max_retries}")
    #             if attempt < max_retries - 1:
    #                 await asyncio.sleep(1)
    #                 continue
    #             self._record_error(ticker)
    #             return None
    #         except Exception as e:
    #             debug(f"Error getting price for {ticker}: {e}")
    #             if attempt < max_retries - 1:
    #                 await asyncio.sleep(1)
    #                 continue
    #             self._record_error(ticker)
    #             return None
    #
    #     return None

    def get_current_price_sync(self, ticker: str, market: str = "shares") -> Optional[float]:
        """Синхронное получение текущей цены"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.get_current_price(ticker, market))
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
        except Exception as e:
            error(f"Error in get_current_price_sync for {ticker}: {e}")
            return None

    async def get_ticker_by_figi(self, figi: str) -> Optional[str]:
        """Получение тикера по FIGI с кэшированием"""
        if not figi:
            return None

        if not figi.startswith('BBG') and len(figi) <= 10 and figi.isalpha():
            return figi.upper()

        now = time.time()
        if figi in self._ticker_cache:
            cache_time = self._ticker_cache_time.get(figi, 0)
            if now - cache_time < self._cache_ttl:
                return self._ticker_cache[figi]

        try:
            url = f"{self.BASE_URL}/engines/stock/markets/shares/boards/tqbr/securities.json"

            async with self._request_context(figi) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None

                    data = await response.json()
                    securities = data.get('securities', {})
                    columns = securities.get('columns', [])
                    rows = securities.get('data', [])

                    if not columns or not rows:
                        return None

                    try:
                        secid_idx = columns.index('SECID')
                        figi_idx = columns.index('FIGI')
                    except ValueError:
                        return None

                    for row in rows:
                        if len(row) > max(secid_idx, figi_idx):
                            row_figi = row[figi_idx] if figi_idx < len(row) else None
                            if row_figi and row_figi.upper() == figi.upper():
                                ticker = row[secid_idx] if secid_idx < len(row) else None
                                if ticker:
                                    self._ticker_cache[figi] = ticker
                                    self._ticker_cache_time[figi] = now
                                    return ticker

            return None

        except Exception as e:
            debug(f"Error getting ticker by FIGI {figi}: {e}")
            return None

    def clear_cache(self):
        """Очистка всех кэшей"""
        self._cache.clear()
        self._cache_time.clear()
        self._ticker_cache.clear()
        self._ticker_cache_time.clear()
        info("🗑️ MOEX cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики клиента"""
        return {
            'request_count': self._request_count,
            'error_count': self._error_count,
            'cache_size': len(self._cache),
            'errors_by_ticker': self._error_stats.copy(),
            'blocked_tickers': {k: v.isoformat() for k, v in self._skip_until.items()},
            'is_closing': self._is_closing
        }

    def reset_error_stats(self, ticker: str = None):
        """Сброс статистики ошибок"""
        if ticker:
            self._error_stats.pop(ticker, None)
            self._skip_until.pop(ticker, None)
        else:
            self._error_stats.clear()
            self._skip_until.clear()
        info("📊 MOEX error stats reset")

    async def health_check(self) -> Dict[str, Any]:
        """Проверка здоровья клиента"""
        return {
            'healthy': not self._is_closing,
            'session_active': self._session is not None and not self._session.closed,
            'request_count': self._request_count,
            'error_count': self._error_count,
            'cache_size': len(self._cache),
            'blocked_count': len(self._skip_until),
            'timestamp': datetime.now().isoformat()
        }

    async def __aenter__(self):
        """Контекстный менеджер вход"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Контекстный менеджер выход - гарантированное закрытие"""
        await self.close()

    async def close(self):
        """Полное закрытие клиента с очисткой всех ресурсов"""
        if self._is_closing:
            return

        self._is_closing = True
        info("🔌 Closing MoexClient...")

        # Закрываем сессию
        await self._close_session()

        # Очищаем кэши
        self.clear_cache()

        # Даем время на завершение
        await asyncio.sleep(0.1)

        info("✅ MoexClient closed")


# Глобальный экземпляр
moex_client = MoexClient()