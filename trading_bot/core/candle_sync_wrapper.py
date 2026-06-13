# candle_sync_wrapper.py
"""Синхронная обёртка для CandleBuilder - ИСПРАВЛЕННАЯ ВЕРСИЯ (использует синглтон)"""

import asyncio
import threading
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime
from pathlib import Path
import sys
import time
from trading_bot.logger import debug

# Добавляем корневую директорию
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ✅ ИСПРАВЛЕНО: импортируем готовый синглтон из candle_builder
from trading_bot.core.candle_builder import candle_builder

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_initialized = False
_init_lock = threading.Lock()


def _run_loop(loop: asyncio.AbstractEventLoop):
    """Запуск event loop в отдельном потоке"""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def init_candle_builder(test_mode: bool = False):
    """Инициализация CandleBuilder в фоновом потоке (только один раз)"""
    global _loop, _thread, _initialized

    with _init_lock:
        if _initialized:
            return candle_builder

    try:
        print(f"🔄 Инициализация CandleBuilder (test_mode={test_mode})...")

        _loop = asyncio.new_event_loop()
        _thread = threading.Thread(target=_run_loop, args=(_loop,), daemon=True)
        _thread.start()

        # Даём время на запуск потока
        time.sleep(0.1)

        # ✅ ИСПРАВЛЕНО: используем уже существующий синглтон
        # Не создаём новый CandleBuilder, а запускаем существующий
        future = asyncio.run_coroutine_threadsafe(candle_builder.start(), _loop)
        future.result(timeout=10)

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


def get_candles_sync(
        ticker: str,
        interval_minutes: int = 5,
        days: int = 30
) -> List[Tuple[float, float]]:
    """Синхронное получение свечей"""
    if not ticker:
        print("⚠️ get_candles_sync: пустой тикер")
        return []

    if not _initialized:
        init_candle_builder(test_mode=False)

    if not candle_builder:
        print("⚠️ CandleBuilder не инициализирован")
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
    except Exception as e:
        print(f"❌ Ошибка получения свечей для {ticker}: {e}")
        return []


def get_current_price_sync(ticker: str) -> Optional[float]:
    """Синхронное получение текущей цены"""
    if not ticker:
        print("⚠️ get_current_price_sync: пустой тикер")
        return None

    if not _initialized:
        init_candle_builder(test_mode=False)

    if not candle_builder:
        print("⚠️ CandleBuilder не инициализирован")
        return None

    try:
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
    """Остановка CandleBuilder - с корректным завершением cleanup_loop"""
    global _loop, _thread, _initialized

    with _init_lock:
        if not _initialized:
            return

    print("🛑 Shutting down CandleBuilder...")

    if candle_builder and _loop:
        try:
            # Устанавливаем флаг running в False для остановки cleanup_loop
            candle_builder.running = False

            future = asyncio.run_coroutine_threadsafe(candle_builder.shutdown(), _loop)
            future.result(timeout=10)
            print("   ✅ CandleBuilder stopped")
        except asyncio.TimeoutError:
            print("   ⚠️ Timeout stopping CandleBuilder, forcing stop...")
            _loop.call_soon_threadsafe(_loop.stop)
        except Exception as e:
            print(f"   ⚠️ Error: {e}")

    if _loop:
        try:
            # Отменяем все задачи с таймаутом
            pending = asyncio.all_tasks(_loop)
            for task in pending:
                task.cancel()

            # Даем время на отмену
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
    print("🛑 CandleBuilder shutdown complete")


def get_indicators_sync(ticker: str, interval_minutes: int = 5) -> Dict[str, Any]:
    """Получение технических индикаторов синхронно"""
    try:
        candles = get_candles_sync(ticker, interval_minutes=interval_minutes, days=30)
        if not candles or len(candles) < 20:
            print(f"⚠️ Недостаточно данных для индикаторов {ticker}: {len(candles) if candles else 0} свечей")
            return {}

        prices = [c[0] for c in candles]
        volumes = [c[1] for c in candles]

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
        print(f"❌ Ошибка получения индикаторов для {ticker}: {e}")
        return {}


# Экспорт
__all__ = [
    'init_candle_builder',
    'get_candles_sync',
    'get_current_price_sync',
    'shutdown_candle_builder',
    'get_indicators_sync'
]