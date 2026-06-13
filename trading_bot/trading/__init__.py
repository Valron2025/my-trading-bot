"""Trading module - торговые операции"""

from .position_opener import PositionOpener
from .position_closer import PositionCloser
from .position_sizer import PositionSizer
from .order_placement import OrderPlacement

# Импортируем из risk, а не из trading
from ..risk.position_manager import IcebergOrderManager, TrailingStopManager
from .pre_market_trader import PreMarketTrader

__all__ = [
    "PositionOpener",
    "PositionCloser",
    "PositionSizer",
    "OrderPlacement",
    "IcebergOrderManager",
    "TrailingStopManager",
    "PreMarketTrader",
]