"""Core components - ядро торговой системы"""

from .trading_loop import TradingLoop
from .session_manager import SessionManager
from .market_checker import MarketChecker
from .settings_manager import SettingsManager
from .shutdown_handler import ShutdownHandler
from .candle_sync_wrapper import (
    init_candle_builder,
    get_candles_sync,
    get_current_price_sync,
    shutdown_candle_builder,
    get_indicators_sync
)
from .candle_builder import CandleBuilder, candle_builder
from .moex_client import MoexClient, moex_client
from .moex_sync_fetcher import MoexSyncFetcher, moex_sync

__all__ = [
    "TradingLoop",
    "SessionManager",
    "MarketChecker",
    "SettingsManager",
    "ShutdownHandler",
    "init_candle_builder",
    "get_candles_sync",
    "get_current_price_sync",
    "shutdown_candle_builder",
    "get_indicators_sync",
    "CandleBuilder",
    "candle_builder",
    "MoexClient",
    "moex_client",
    "MoexSyncFetcher",
    "moex_sync",
]