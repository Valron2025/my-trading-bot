"""Analysis module - технический и фундаментальный анализ"""

# ========== ТЕХНИЧЕСКИЙ АНАЛИЗ ==========
from .technical_analyzer import TechnicalAnalyzer, analyzer
from .strategy_engine import StrategyEngine, create_strategy_engine
from .stock_scanner import StockScanner
from .instrument_filter import InstrumentFilter, instrument_filter
from .validator import TickerValidator, validator
from .performance import PerformanceAnalyzer
from .market_analyzer import MarketAnalyzer, market_analyzer

# ========== ПРОДВИНУТЫЕ СТРАТЕГИИ ==========
from .advanced_strategy import (
    MultiTimeframeAnalyzer,
    VolumeProfileAnalyzer,
    VolatilityHarvester,
    EnhancedLevelFinder,
    SignalAggregator,
    ElliottWaveAnalyzer
)

# ========== ФУНДАМЕНТАЛЬНЫЙ АНАЛИЗ ==========
from .fundamental_analyzer import (
    FundamentalAnalyzer,
    fundamental_analyzer,
    FundamentalMetrics,
    FundamentalSignal
)
from .fundamental_db import FundamentalDatabase
from .fundamental_updater import FundamentalUpdater

# ========== КОРРЕЛЯЦИОННЫЙ АНАЛИЗ ==========
from .correlation_analyzer import correlation_analyzer, CorrelationAnalyzer

# ========== ЕДИНЫЙ СПИСОК ЭКСПОРТА ==========
__all__ = [
    # Технический анализ
    "TechnicalAnalyzer",
    "analyzer",
    "StrategyEngine",
    "create_strategy_engine",
    "StockScanner",
    "InstrumentFilter",
    "instrument_filter",
    "TickerValidator",
    "validator",
    "PerformanceAnalyzer",
    "MarketAnalyzer",
    "market_analyzer",

    # Продвинутые стратегии
    "MultiTimeframeAnalyzer",
    "VolumeProfileAnalyzer",
    "VolatilityHarvester",
    "EnhancedLevelFinder",
    "SignalAggregator",
    "ElliottWaveAnalyzer",

    # Фундаментальный анализ
    "FundamentalAnalyzer",
    "fundamental_analyzer",
    "FundamentalMetrics",
    "FundamentalSignal",
    "FundamentalDatabase",
    "FundamentalUpdater",

    # Корреляционный анализ
    "correlation_analyzer",
    "CorrelationAnalyzer",
]