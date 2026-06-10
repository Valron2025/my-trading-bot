"""Модели данных для торгового бота - ПРОДАКШЕН ВЕРСИЯ"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime, timedelta, timezone

# ========== ЧАСОВОЙ ПОЯС ==========
MOSCOW_TZ = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    """Текущее московское время"""
    return datetime.now(MOSCOW_TZ)


def ensure_tz(dt: datetime) -> datetime:
    """Добавляет часовой пояс если его нет"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MOSCOW_TZ)
    return dt


class OrderSide(Enum):
    """Сторона ордера (позиции)"""
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def opposite(self) -> "OrderSide":
        """Противоположная сторона"""
        return OrderSide.SHORT if self == OrderSide.LONG else OrderSide.LONG

    @property
    def emoji(self) -> str:
        return "🟢" if self == OrderSide.LONG else "🔴"

    @property
    def name_ru(self) -> str:
        return "Покупка" if self == OrderSide.LONG else "Продажа (SHORT)"


class SignalType(Enum):
    """Тип торгового сигнала"""
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

    @property
    def emoji(self) -> str:
        return {
            SignalType.BUY: "🟢",
            SignalType.SELL: "🔴",
            SignalType.WAIT: "⚪"
        }.get(self, "⚪")


class MarginLevel(Enum):
    """Уровень маржинального риска"""
    SAFE = "safe"        # < 50% - безопасно
    WARNING = "warning"  # 50-70% - предупреждение
    HIGH = "high"        # 70-85% - высокий риск
    CRITICAL = "critical"  # > 85% - критический


@dataclass
class SignalResult:
    """Результат анализа сигнала (из strategy_engine)"""
    score: int
    buy_signal: bool
    sell_signal: bool
    recommendation: str
    signals: List[str]
    rsi: Optional[float] = None
    macd: Optional[float] = None
    volume_ratio: Optional[float] = 1.0

    @property
    def signal_type(self) -> SignalType:
        if self.buy_signal and self.score >= 1:
            return SignalType.BUY
        elif self.sell_signal and self.score <= -1:
            return SignalType.SELL
        return SignalType.WAIT

    @property
    def confidence(self) -> float:
        """Уверенность сигнала в процентах (0-100)"""
        abs_score = abs(self.score)
        return min(100, abs_score * 10)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'score': self.score,
            'signal': self.signal_type.name,
            'confidence': self.confidence,
            'recommendation': self.recommendation,
            'signals': self.signals[:5],
            'rsi': self.rsi,
            'macd': self.macd,
            'volume_ratio': self.volume_ratio
        }


@dataclass
class TradeResult:
    """Результат сделки (для бэктестера и статистики)"""
    entry_price: float
    exit_price: float
    quantity: int
    side: str
    profit_pct: float
    profit_amount: float
    entry_time: datetime
    exit_time: datetime
    reason: str
    commission: float = 0.0
    slippage: float = 0.0

    @property
    def holding_minutes(self) -> float:
        """Время удержания в минутах"""
        delta = ensure_tz(self.exit_time) - ensure_tz(self.entry_time)
        return delta.total_seconds() / 60

    @property
    def is_profitable(self) -> bool:
        return self.profit_amount > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'side': self.side,
            'quantity': self.quantity,
            'entry': self.entry_price,
            'exit': self.exit_price,
            'profit_pct': round(self.profit_pct, 2),
            'profit_amount': round(self.profit_amount, 2),
            'reason': self.reason,
            'holding_minutes': round(self.holding_minutes, 1),
            'commission': round(self.commission, 2)
        }


@dataclass
class StockAnalysis:
    """Результат анализа акции"""
    figi: str = ""
    name: str = ""
    score: float = 0
    buy_signal: bool = False
    sell_signal: bool = False
    recommendation: str = ""
    signals: list = field(default_factory=list)
    rsi: Optional[float] = None
    macd: Optional[float] = None
    volume_ratio: Optional[float] = 1.0

    # Новые поля из книги
    candle_patterns: Dict[str, str] = field(default_factory=dict)
    support_levels: List[float] = field(default_factory=list)
    resistance_levels: List[float] = field(default_factory=list)
    round_support: float = None
    round_resistance: float = None
    alligator_signal: str = None
    awesome_oscillator: float = None
    risk_reward_ratio: float = None
    is_valid_rr: bool = True

    correlation: Optional[float] = None  # Коэффициент корреляции
    correlation_penalty: Optional[float] = None  # Штраф за корреляцию

    used_fundamental: bool = False
    fundamental_impact: int = 0
    news_impact: int = 0
    confidence: float = 0.5  # ← ДОБАВИТЬ ЭТУ СТРОКУ

    technical: Optional[Dict[str, Any]] = None
    fundamental: Optional[Dict[str, Any]] = None
    news: Optional[Dict[str, Any]] = None

    @property
    def side(self) -> Optional[OrderSide]:
        """Определение стороны на основе скора"""
        if self.buy_signal and self.score >= 1:
            return OrderSide.LONG
        elif self.sell_signal and self.score <= -1:
            return OrderSide.SHORT
        return None

    @property
    def signal_strength(self) -> str:
        """Сила сигнала: слабый, средний, сильный"""
        abs_score = abs(self.score)
        if abs_score >= 5:
            return "сильный"
        elif abs_score >= 3:
            return "средний"
        elif abs_score >= 1:
            return "слабый"
        return "нейтральный"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'figi': self.figi,
            'name': self.name,
            'score': self.score,
            'side': self.side.name if self.side else None,
            'strength': self.signal_strength,
            'recommendation': self.recommendation,
            'signals': self.signals[:5],
            'rsi': round(self.rsi, 1) if self.rsi else None,
            'macd': round(self.macd, 4) if self.macd else None,
            'volume_ratio': round(self.volume_ratio, 2) if self.volume_ratio else 1.0
        }


@dataclass
class StockCandidate:
    """Кандидат для открытия позиции"""
    figi: str
    name: str
    price: float
    lot: int
    lot_price: float
    analysis: StockAnalysis
    side: OrderSide
    ticker: str = field(default="")
    rank_score: float = field(default=0.0)
    quantity: int = field(default=0)  # Количество акций для сделки

    @property
    def ticker_display(self) -> str:
        """Отображаемый тикер (fallback на FIGI)"""
        return self.ticker if self.ticker else self.figi[:8]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'ticker': self.ticker_display,
            'name': self.name[:30],
            'price': round(self.price, 2),
            'lot': self.lot,
            'lot_price': round(self.lot_price, 0),
            'side': self.side.name,
            'score': self.analysis.score,
            'rank': self.rank_score,
            'quantity': self.quantity
        }


@dataclass
class Position:
    """Открытая позиция - с полным управлением рисками"""
    figi: str
    ticker: str
    quantity: int
    avg_price: float
    side: OrderSide
    entry_time: datetime
    highest_price: float = 0.0
    lowest_price: float = float('inf')
    take_profit_pct: float = 0.0
    stop_loss_pct: float = 0.0
    trailing_stop_pct: float = 0.0

    # Стоп-приказы
    stop_order_placed: bool = False
    stop_order_id: Optional[str] = None
    take_profit_order_id: Optional[str] = None
    stop_order_price: float = 0.0
    take_profit_price: float = 0.0

    def __post_init__(self):
        if self.side == OrderSide.LONG and self.highest_price == 0.0:
            self.highest_price = self.avg_price
        if self.side == OrderSide.SHORT and self.lowest_price == float('inf'):
            self.lowest_price = self.avg_price
        self.entry_time = ensure_tz(self.entry_time)

    def current_profit_pct(self, current_price: float) -> float:
        """Текущая прибыль в процентах"""
        if self.side == OrderSide.LONG:
            return (current_price - self.avg_price) / self.avg_price * 100
        else:
            return (self.avg_price - current_price) / self.avg_price * 100

    def current_profit_amount(self, current_price: float) -> float:
        """Текущая прибыль в деньгах"""
        if self.side == OrderSide.LONG:
            return (current_price - self.avg_price) * self.quantity
        else:
            return (self.avg_price - current_price) * self.quantity

    def update_high_low(self, current_price: float):
        """Обновление максимума/минимума для трейлинг-стопа"""
        if self.side == OrderSide.LONG and current_price > self.highest_price:
            self.highest_price = current_price
        elif self.side == OrderSide.SHORT and current_price < self.lowest_price:
            self.lowest_price = current_price

    def hold_minutes(self) -> float:
        """Время удержания позиции в минутах"""
        now = now_msk()
        entry = ensure_tz(self.entry_time)
        return (now - entry).total_seconds() / 60

    def hold_hours(self) -> float:
        """Время удержания в часах"""
        return self.hold_minutes() / 60

    @property
    def position_value(self) -> float:
        """Текущая стоимость позиции"""
        return self.quantity * self.avg_price

    def get_trailing_stop_price(self, current_price: float) -> float:
        """Расчёт цены трейлинг-стопа"""
        if self.side == OrderSide.LONG:
            return self.highest_price * (1 - self.trailing_stop_pct / 100)
        else:
            return self.lowest_price * (1 + self.trailing_stop_pct / 100)

    def should_trailing_stop(self, current_price: float) -> bool:
        """Проверка, нужно ли активировать трейлинг-стоп"""
        if self.trailing_stop_pct <= 0:
            return False

        if self.side == OrderSide.LONG:
            return current_price <= self.get_trailing_stop_price(current_price)
        else:
            return current_price >= self.get_trailing_stop_price(current_price)

    def should_timeout(self, max_minutes: int = None) -> bool:
        """Проверка таймаута"""
        if max_minutes is None:
            return False
        return self.hold_minutes() >= max_minutes

    def to_dict(self) -> Dict[str, Any]:
        return {
            'figi': self.figi,
            'quantity': self.quantity,
            'avg_price': round(self.avg_price, 2),
            'side': self.side.name,
            'entry_time': self.entry_time.isoformat(),
            'hold_minutes': round(self.hold_minutes(), 1),
            'highest_price': round(self.highest_price, 2),
            'lowest_price': round(self.lowest_price, 2),
            'stop_order_placed': self.stop_order_placed,
            'take_profit_pct': self.take_profit_pct,
            'stop_loss_pct': self.stop_loss_pct
        }


@dataclass
class MarketConditions:
    """Рыночные условия - для адаптивной настройки"""
    volatility: float
    spread: float
    volume_trend: float
    trend: float
    market_type: str
    trend_direction: str
    risk_factor: float
    vol_level: str = "medium"
    trend_level: str = "sideways"

    @property
    def is_high_volatility(self) -> bool:
        return self.volatility > 0.015

    @property
    def is_low_volatility(self) -> bool:
        return self.volatility < 0.005

    @property
    def is_bull_trend(self) -> bool:
        return self.trend > 0.2

    @property
    def is_bear_trend(self) -> bool:
        return self.trend < -0.2

    @property
    def volume_confirmation(self) -> bool:
        return self.volume_trend > 1.2

    def to_dict(self) -> Dict[str, Any]:
        return {
            'volatility': round(self.volatility * 100, 2),
            'spread': round(self.spread * 100, 2),
            'volume_trend': round(self.volume_trend, 2),
            'trend': round(self.trend, 2),
            'market_type': self.market_type,
            'trend_direction': self.trend_direction,
            'risk_factor': round(self.risk_factor, 2),
            'vol_level': self.vol_level,
            'trend_level': self.trend_level
        }


@dataclass
class PortfolioStats:
    """Статистика портфеля"""
    total_value: float
    own_funds: float
    margin: float
    available_funds: float
    positions_count: int
    total_profit: float = 0.0
    total_invested: float = 0.0
    margin_rate: float = 0.0
    available_margin: float = 0.0
    short_positions_count: int = 0
    long_positions_count: int = 0

    @property
    def margin_level(self) -> MarginLevel:
        """Уровень маржинального риска"""
        if self.margin_rate >= 85:
            return MarginLevel.CRITICAL
        elif self.margin_rate >= 70:
            return MarginLevel.HIGH
        elif self.margin_rate >= 50:
            return MarginLevel.WARNING
        return MarginLevel.SAFE

    @property
    def is_margin_safe(self) -> bool:
        return self.margin_rate < 70

    @property
    def is_critical(self) -> bool:
        return self.margin_rate >= 85

    @property
    def utilization_pct(self) -> float:
        if self.total_value == 0:
            return 0
        return (self.total_invested / self.total_value) * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_value': round(self.total_value, 2),
            'own_funds': round(self.own_funds, 2),
            'margin': round(self.margin, 2),
            'available_funds': round(self.available_funds, 2),
            'available_margin': round(self.available_margin, 2),
            'positions': self.positions_count,
            'long_positions': self.long_positions_count,
            'short_positions': self.short_positions_count,
            'total_profit': round(self.total_profit, 2),
            'margin_rate': round(self.margin_rate, 1),
            'margin_level': self.margin_level.value,
            'utilization': round(self.utilization_pct, 1)
        }


@dataclass
class OrderInfo:
    """Информация о заявке"""
    order_id: str
    figi: str
    side: OrderSide
    quantity: int
    price: float
    status: str
    created_at: datetime
    is_limit: bool = False
    limit_price: float = 0.0
    executed_quantity: int = 0
    executed_price: float = 0.0

    @property
    def is_filled(self) -> bool:
        return self.status == "FILLED"

    @property
    def is_cancelled(self) -> bool:
        return self.status == "CANCELLED"

    @property
    def is_active(self) -> bool:
        return self.status in ["NEW", "PARTIALLY_FILLED"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'order_id': self.order_id,
            'figi': self.figi,
            'side': self.side.name,
            'quantity': self.quantity,
            'price': round(self.price, 2),
            'status': self.status,
            'executed': self.executed_quantity,
            'created_at': self.created_at.isoformat()
        }


# ========== ФУНКЦИИ ДЛЯ РАСЧЁТОВ ==========

def calculate_pnl(entry: float, exit_price: float, quantity: int, side) -> Dict[str, float]:
    """
    Расчёт P&L для сделки

    Args:
        entry: Цена входа
        exit_price: Цена выхода
        quantity: Количество акций
        side: Сторона сделки ("LONG", "SHORT" или OrderSide.LONG, OrderSide.SHORT)

    Returns:
        Dict с полями:
            - gross_pnl: Валовая прибыль/убыток
            - pnl_pct: Процент прибыли/убытка

    Пример:
        >>> calculate_pnl(100, 110, 10, "LONG")
        {'gross_pnl': 100.0, 'pnl_pct': 10.0}

        >>> calculate_pnl(100, 90, 10, "SHORT")
        {'gross_pnl': 100.0, 'pnl_pct': 10.0}
    """
    from trading_bot.logger import debug

    # Нормализуем side
    if hasattr(side, 'value'):
        side_str = side.value
    else:
        side_str = str(side).upper()

    debug(f"📊 Расчёт P&L: entry={entry}, exit={exit_price}, qty={quantity}, side={side_str}")

    if side_str in ["LONG", "BUY"]:
        gross_pnl = (exit_price - entry) * quantity
        pnl_pct = (exit_price - entry) / entry * 100 if entry > 0 else 0
    else:  # SHORT или SELL
        gross_pnl = (entry - exit_price) * quantity
        pnl_pct = (entry - exit_price) / entry * 100 if entry > 0 else 0

    debug(f"📊 Результат: P&L={gross_pnl:.2f}₽ ({pnl_pct:+.2f}%)")

    return {
        'gross_pnl': round(gross_pnl, 2),
        'pnl_pct': round(pnl_pct, 2)
    }


def calculate_pnl_with_commission(
        entry: float,
        exit_price: float,
        quantity: int,
        side,
        commission_pct: float = 0.0005
) -> Dict[str, float]:
    """
    Расчёт P&L с учётом комиссии

    Args:
        entry: Цена входа
        exit_price: Цена выхода
        quantity: Количество акций
        side: Сторона сделки
        commission_pct: Процент комиссии (по умолчанию 0.05%)

    Returns:
        Dict с полями:
            - gross_pnl: Валовая прибыль/убыток
            - net_pnl: Чистая прибыль/убыток (с учётом комиссии)
            - pnl_pct: Процент прибыли/убытка
            - commission: Сумма комиссии
    """
    # Сначала рассчитываем валовую прибыль
    pnl_data = calculate_pnl(entry, exit_price, quantity, side)
    gross_pnl = pnl_data['gross_pnl']
    pnl_pct = pnl_data['pnl_pct']

    # Рассчитываем комиссию (при входе и выходе)
    entry_commission = entry * quantity * commission_pct
    exit_commission = exit_price * quantity * commission_pct
    total_commission = entry_commission + exit_commission

    # Чистая прибыль
    net_pnl = gross_pnl - total_commission

    return {
        'gross_pnl': round(gross_pnl, 2),
        'net_pnl': round(net_pnl, 2),
        'pnl_pct': round(pnl_pct, 2),
        'commission': round(total_commission, 2)
    }


def validate_price(price: float, min_price: float = 0.01, max_price: float = 100000) -> bool:
    """Валидация цены"""
    is_valid = min_price <= price <= max_price
    if not is_valid:
        from trading_bot.logger import warning
        warning(f"⚠️ Цена {price:.4f} вне диапазона [{min_price}, {max_price}]")
    return is_valid


def validate_quantity(quantity: int, lot: int = 1, min_quantity: int = 1) -> bool:
    """Валидация количества (кратно лоту)"""
    is_valid = quantity >= min_quantity and quantity % lot == 0
    if not is_valid:
        from trading_bot.logger import warning
        warning(f"⚠️ Количество {quantity} шт не кратно лоту {lot}")
    return is_valid


def calculate_sltp(
        entry_price: float,
        side: str,
        take_profit_pct: float = None,
        stop_loss_pct: float = None
) -> Dict[str, float]:
    """
    Расчёт уровней стоп-лосса и тейк-профита

    Args:
        entry_price: Цена входа
        side: "LONG" или "SHORT"
        take_profit_pct: Процент тейк-профита (если None - используется из config)
        stop_loss_pct: Процент стоп-лосса (если None - используется из config)

    Returns:
        Dict с ключами: take_profit, stop_loss
    """
    from trading_bot.config import config

    tp_pct = take_profit_pct if take_profit_pct is not None else config.take_profit_pct
    sl_pct = stop_loss_pct if stop_loss_pct is not None else config.stop_loss_pct

    if side.upper() in ["LONG", "BUY"]:
        take_profit = entry_price * (1 + tp_pct / 100)
        stop_loss = entry_price * (1 - sl_pct / 100)
    else:  # SHORT
        take_profit = entry_price * (1 - tp_pct / 100)
        stop_loss = entry_price * (1 + sl_pct / 100)

    return {
        'take_profit': round(take_profit, 2),
        'stop_loss': round(stop_loss, 2),
        'take_profit_pct': tp_pct,
        'stop_loss_pct': sl_pct
    }


# Обновляем __all__ для экспорта
__all__ = [
    'MOSCOW_TZ',
    'now_msk',
    'ensure_tz',
    'OrderSide',
    'SignalType',
    'MarginLevel',
    'SignalResult',
    'TradeResult',
    'StockAnalysis',
    'StockCandidate',
    'Position',
    'MarketConditions',
    'PortfolioStats',
    'OrderInfo',
    'calculate_pnl',
    'calculate_pnl_with_commission',
    'validate_price',
    'validate_quantity',
    'calculate_sltp',
]
