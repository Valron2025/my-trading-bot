"""Trading Bot Package - Автоматическая торговля на T-Investments"""

import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .bot import TradingBot
from .config import config
from .models import (
    StockCandidate,
    OrderSide,
    Position,
    StockAnalysis,
    calculate_pnl,
    calculate_pnl_with_commission
)
from .cache import TTLCache, price_cache, positions_cache, candles_cache

# Глобальный экземпляр бота (для обратной совместимости)
_trading_bot_instance = None


def get_trading_bot():
    """Получение глобального экземпляра бота (ленивая инициализация)"""
    global _trading_bot_instance
    if _trading_bot_instance is None:
        from .bot import TradingBot
        _trading_bot_instance = TradingBot()
    return _trading_bot_instance


def init_trading_bot():
    """Инициализация глобального экземпляра бота"""
    global _trading_bot_instance
    from .bot import TradingBot
    _trading_bot_instance = TradingBot()
    return _trading_bot_instance


__version__ = "3.0.0"

# Единый список экспортов (без дублирования!)
__all__ = [
    # Основные классы и функции
    "TradingBot",
    "get_trading_bot",
    "init_trading_bot",
    "config",

    # Модели данных
    "StockCandidate",
    "OrderSide",
    "Position",
    "StockAnalysis",

    # Функции расчёта
    "calculate_pnl",
    "calculate_pnl_with_commission",

    # Кэширование
    "TTLCache",
    "price_cache",
    "positions_cache",
    "candles_cache",
]