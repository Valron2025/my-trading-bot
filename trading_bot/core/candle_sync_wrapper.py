# candle_sync_wrapper.py
"""Синхронная обёртка для CandleBuilder - ИСПРАВЛЕННАЯ ВЕРСИЯ"""

import asyncio
import threading
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime
from pathlib import Path
import sys
import time

# Добавляем корневую директорию
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trading_bot.logger import debug, info, warning

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_initialized = False
_init_lock = threading.Lock()
_candle_builder_instance = None


def _get_candle_builder():
    global _candle_builder_instance
    if _candle_builder_instance is None:
        try:
            from trading_bot.core.candle_builder import candle_builder
            _candle_builder_instance = candle_builder
            debug("✅ CandleBuilder instance loaded (lazy)")
        except ImportError as e:
            warning(f"⚠️ Не удалось импортировать CandleBuilder: {e}")
            return None
        except Exception as e:
            warning(f"⚠️ Ошибка загрузки CandleBuilder: {e}")
            return None
    return _candle_builder_instance


def _run_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def init_candle_builder(test_mode: bool = False):
    global _loop, _thread, _initialized

    with _init_lock:
        if _initialized:
            return _get_candle_builder()

    try:
        print(f"🔄 Инициализация CandleBuilder (test_mode={test_mode})...")

        _loop = asyncio.new_event_loop()
        _thread = threading.Thread(target=_run_loop, args=(_loop,), daemon=True)
        _thread.start()

        time.sleep(0.2)  # Даём время потоку запуститься

        candle_builder = _get_candle_builder()
        if candle_builder is None:
            print("❌ Не удалось получить экземпляр CandleBuilder")
            return None

        # ✅ Теперь это работает, потому что start_builder - async
        future = asyncio.run_coroutine_threadsafe(candle_builder.start_builder(), _loop)
        future.result(timeout=10)  # Ждём до 10 секунд

        _initialized = True
        print(f"✅ CandleBuilder initialized (test_mode={test_mode})")
        return candle_builder

    except asyncio.TimeoutError:
        print("⏰ Таймаут инициализации CandleBuilder")
        return None
    except Exception as e:
        print(f"⚠️ CandleBuilder init failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_candles_sync(ticker: str, interval_minutes: int = 5, days: int = 30) -> List[Tuple[float, float]]:
    global _initialized, _loop

    if not ticker:
        print("⚠️ get_candles_sync: пустой тикер")
        return []

    # ✅ Проверяем инициализацию
    if not _initialized or _loop is None or _loop.is_closed():
        init_candle_builder(test_mode=False)

        # ✅ Ждём готовности _loop
        wait_attempts = 0
        while (_loop is None or _loop.is_closed()) and wait_attempts < 50:
            time.sleep(0.1)
            wait_attempts += 1

        if _loop is None or _loop.is_closed():
            print("❌ Не удалось инициализировать event loop")
            return []

    candle_builder = _get_candle_builder()
    if candle_builder is None:
        print("⚠️ CandleBuilder не доступен")
        return []

    interval_map = {
        1: "1min",
        5: "5min",
        10: "10min",
        15: "15min",
        30: "30min",
        60: "1hour"
    }
    interval_str = interval_map.get(interval_minutes, "5min")

    try:
        # ✅ ИСПРАВЛЕНИЕ 3: Проверяем _loop ещё раз перед использованием
        if _loop is None or _loop.is_closed():
            warning("⚠️ Event loop закрыт, переинициализируем...")
            _initialized = False
            init_candle_builder()

            # Снова ждём
            wait_attempts = 0
            while (_loop is None or _loop.is_closed()) and wait_attempts < 50:
                time.sleep(0.1)
                wait_attempts += 1

            if _loop is None or _loop.is_closed():
                return []

            candle_builder = _get_candle_builder()
            if candle_builder is None:
                return []

        future = asyncio.run_coroutine_threadsafe(
            candle_builder.get_candles_from_moex(ticker, interval=interval_str, days=days),
            _loop
        )
        candles_data = future.result(timeout=30)

        if not candles_data:
            future2 = asyncio.run_coroutine_threadsafe(
                candle_builder.get_candles(ticker, source="moex", interval=interval_str, days=days),
                _loop
            )
            candles_data = future2.result(timeout=30)

        if not candles_data:
            debug(f"Нет данных для {ticker}")
            return []

        result = [(c['close'], c.get('volume', 0)) for c in candles_data if c.get('close', 0) > 0]

        if result:
            print(f"✅ CandleBuilder: {len(result)} свечей для {ticker}")

        return result

    except asyncio.TimeoutError:
        print(f"⏰ Таймаут получения свечей для {ticker}")
        return []
    except RuntimeError as e:
        if "event loop is closed" in str(e):
            warning(f"⚠️ Event loop закрыт для {ticker}, переинициализируем...")
            _initialized = False
            return get_candles_sync(ticker, interval_minutes, days)
        print(f"❌ Ошибка Runtime для {ticker}: {e}")
        return []
    except Exception as e:
        print(f"❌ Ошибка получения свечей для {ticker}: {e}")
        return []


async def get_volumes_from_moex(ticker: str, days: int = 5) -> List[int]:
    import aiohttp
    from datetime import datetime, timedelta

    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        ticker_upper = ticker.upper()

        url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker_upper}/candles.json"
        params = {
            'from': start_date.strftime('%Y-%m-%d'),
            'till': end_date.strftime('%Y-%m-%d'),
            'interval': 24,
            'iss.meta': 'off',
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if 'candles' in data:
                        columns = data['candles'].get('columns', [])
                        rows = data['candles'].get('data', [])
                        volume_idx = -1
                        for i, col in enumerate(columns):
                            if col == 'volume':
                                volume_idx = i
                                break
                        if volume_idx == -1:
                            return []
                        volumes = [int(row[volume_idx]) for row in rows if len(row) > volume_idx and row[volume_idx]]
                        return volumes
    except Exception as e:
        debug(f"❌ MOEX ошибка: {e}")
    return []


def get_volumes_from_moex_sync(ticker: str, days: int = 5) -> List[int]:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(get_volumes_from_moex(ticker, days))
        finally:
            loop.close()
    except Exception as e:
        debug(f"Ошибка получения объёмов: {e}")
        return []


def get_current_price_sync(ticker: str) -> Optional[float]:
    global _initialized, _loop  # ✅ global в начале функции

    if not ticker:
        print("⚠️ get_current_price_sync: пустой тикер")
        return None

    if not _initialized:
        init_candle_builder(test_mode=False)

    candle_builder = _get_candle_builder()
    if candle_builder is None:
        print("⚠️ CandleBuilder не доступен")
        return None

    try:
        if _loop is None or _loop.is_closed():
            warning("⚠️ Event loop закрыт, переинициализируем...")
            init_candle_builder()
            candle_builder = _get_candle_builder()
            if candle_builder is None:
                return None

        future = asyncio.run_coroutine_threadsafe(
            candle_builder.get_current_price_from_moex(ticker),
            _loop
        )
        price = future.result(timeout=10)
        if price and price > 0:
            return price
        return None
    except asyncio.TimeoutError:
        print(f"⏰ Таймаут получения цены для {ticker}")
        return None
    except Exception as e:
        print(f"❌ Ошибка получения цены для {ticker}: {e}")
        return None


def shutdown_candle_builder():
    global _loop, _thread, _initialized, _candle_builder_instance

    with _init_lock:
        if not _initialized:
            return

    print("🛑 Shutting down CandleBuilder...")

    candle_builder = _get_candle_builder()

    if candle_builder and _loop:
        try:
            candle_builder.running = False
            future = asyncio.run_coroutine_threadsafe(candle_builder.shutdown(), _loop)
            future.result(timeout=10)
            print("   ✅ CandleBuilder stopped")
        except asyncio.TimeoutError:
            print("   ⚠️ Timeout stopping CandleBuilder")
            _loop.call_soon_threadsafe(_loop.stop)
        except Exception as e:
            print(f"   ⚠️ Error: {e}")

    if _loop:
        try:
            pending = asyncio.all_tasks(_loop)
            for task in pending:
                task.cancel()
            time.sleep(0.5)
            _loop.call_soon_threadsafe(_loop.stop)
            if _thread and _thread.is_alive():
                _thread.join(timeout=5)
            _loop.close()
            print("   ✅ Event loop closed")
        except Exception as e:
            print(f"   ⚠️ Loop error: {e}")

    _loop = None
    _thread = None
    _initialized = False
    _candle_builder_instance = None
    print("🛑 CandleBuilder shutdown complete")


def get_indicators_sync(ticker: str, interval_minutes: int = 5) -> Dict[str, Any]:
    try:
        candles = get_candles_sync(ticker, interval_minutes=interval_minutes, days=30)
        if not candles or len(candles) < 20:
            return {}

        prices = [c[0] for c in candles]

        def calc_rsi(prices, period=14):
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
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0:
                return 100.0 if avg_gain > 0 else 50.0
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return round(rsi, 1)

        return {
            'rsi': calc_rsi(prices),
            'last_price': prices[-1] if prices else 0,
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        print(f"❌ Ошибка получения индикаторов: {e}")
        return {}


def is_candle_builder_ready() -> bool:
    return _initialized and _loop is not None and not _loop.is_closed()


def get_candle_builder_status() -> Dict[str, Any]:
    candle_builder = _get_candle_builder()
    if candle_builder and hasattr(candle_builder, 'get_stats'):
        return candle_builder.get_stats()
    return {
        'initialized': _initialized,
        'loop_running': _loop is not None and not _loop.is_closed() if _loop else False,
        'has_builder': candle_builder is not None
    }

def invalidate_cache_for_ticker(ticker: str):
    """
    Инвалидация кэша MOEX для конкретного тикера
    Вызывается перед анализом нового тикера, чтобы избежать путаницы с данными
    """
    global _candle_builder_instance
    
    if _candle_builder_instance and hasattr(_candle_builder_instance, '_moex_client'):
        moex = _candle_builder_instance._moex_client
        if moex and hasattr(moex, '_cache'):
            # Удаляем кэш для этого тикера (игнорируем регистр)
            ticker_upper = ticker.upper()
            keys_to_delete = [k for k in moex._cache.keys() if ticker_upper in k.upper()]
            for key in keys_to_delete:
                del moex._cache[key]
                debug(f"🧹 Очищен кэш MOEX для {ticker}: {key}")


__all__ = [
    'init_candle_builder',
    'get_candles_sync',
    'get_current_price_sync',
    'shutdown_candle_builder',
    'get_indicators_sync',
    'get_volumes_from_moex_sync',
    'is_candle_builder_ready',
    'get_candle_builder_status'
]
