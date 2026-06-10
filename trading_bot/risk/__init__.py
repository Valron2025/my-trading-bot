"""Risk management module - управление рисками"""

from .position_manager import PositionManager, position_manager
from .capital_manager import CapitalManager
from .margin_guard import MarginGuard
from .short_controller import ShortController
from .daily_loss_limit import DailyLossLimitChecker
from .circuit_breaker import CircuitBreaker, CircuitBreakerRegistry
from .portfolio_rebalancer import PortfolioRebalancer, get_portfolio_rebalancer

# Для обратной совместимости
_position_manager_instance = None

def get_position_manager() -> PositionManager:
    """Получение глобального экземпляра PositionManager (синглтон)"""
    global _position_manager_instance
    if _position_manager_instance is None:
        _position_manager_instance = position_manager
    return _position_manager_instance

__all__ = [
    "PositionManager",
    "position_manager",
    "get_position_manager",
    "CapitalManager",
    "MarginGuard",
    "ShortController",
    "DailyLossLimitChecker",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "PortfolioRebalancer",
    "get_portfolio_rebalancer",
]
