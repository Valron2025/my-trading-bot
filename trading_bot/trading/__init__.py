"""Trading module - торговые операции"""

from .position_opener import PositionOpener
from .position_closer import PositionCloser
from .position_sizer import PositionSizer
from .order_placement import OrderPlacement
from .pre_market_trader import PreMarketTrader
from .smart_orders import SmartOrderManager, smart_orders_manager, smart_orders
from .position_enhancer import PositionEnhancer, get_position_enhancer

# Настройка логгера
import logging
logger = logging.getLogger(__name__)

__all__ = [
    "PositionOpener",
    "PositionCloser",
    "PositionSizer",
    "OrderPlacement",
    "PreMarketTrader",
    "SmartOrderManager",
    "smart_orders_manager",
    "smart_orders",
    "PositionEnhancer",
    "get_position_enhancer",
]

logger.info("💹 Trading module initialized")
