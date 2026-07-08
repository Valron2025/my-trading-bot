"""Trading Bot Package - Автоматическая торговля на T-Investments"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot.bot import TradingBot
from trading_bot.config import config
from trading_bot.models import (
    StockCandidate,
    OrderSide,
    Position,
    StockAnalysis,
    calculate_pnl,
    calculate_pnl_with_commission
)
# ✅ ОДИН ИМПОРТ (уберите второй)
from trading_bot.cache import TTLCache, price_cache, positions_cache, candles_cache

logger = logging.getLogger(__name__)

_trading_bot_instance = None

def get_trading_bot():
    global _trading_bot_instance
    if _trading_bot_instance is None:
        from trading_bot.bot import TradingBot
        _trading_bot_instance = TradingBot()
        logger.info("✅ Глобальный экземпляр TradingBot создан")
    return _trading_bot_instance

__version__ = "3.0.0"

__all__ = [
    "TradingBot",
    "get_trading_bot",
    "config",
    "StockCandidate",
    "OrderSide",
    "Position",
    "StockAnalysis",
    "calculate_pnl",
    "calculate_pnl_with_commission",
    "TTLCache",
    "price_cache",
    "positions_cache",
    "candles_cache",
]

logger.info(f"📦 Trading Bot Package v{__version__} initialized")