"""Основной класс торгового бота - координатор всех компонентов"""

import asyncio
import threading
import time
from typing import List, Optional, Dict, Any
from datetime import datetime

from .config import config
from .models import StockCandidate
from .logger import info, success, error, warning, debug

# ========== КОМПОНЕНТЫ ==========
# from .core.trading_loop import TradingLoop
# from .core.session_manager import SessionManager
from .trading.position_opener import PositionOpener
from .trading.position_closer import PositionCloser
from .trading.position_sizer import PositionSizer
from .analysis.stock_scanner import StockScanner
from .analysis.validator import TickerValidator
from .analysis.performance import PerformanceAnalyzer
from .risk.margin_guard import MarginGuard
from .risk.short_controller import ShortController
from .risk.daily_loss_limit import DailyLossLimitChecker
from .monitoring.drawdown_tracker import DrawdownTracker
from .monitoring.memory_monitor import MemoryMonitor
from .cache.price_cache import PriceCache
from .cache.position_cache import PositionCache
from .utils.figi_resolver import FigiResolver
from .utils.time_utils import get_moscow_time, is_pre_market_time, is_weekend_trading_time
from trading_bot.trading.position_closer import position_closer


# ========== ГЛОБАЛЬНЫЕ ЭКЗЕМПЛЯРЫ ==========
from trading_bot.cache import TTLCache

_tbank_instance = None
_telegram_instance = None


def _get_tbank():
    global _tbank_instance
    if _tbank_instance is None:
        try:
            from .api.tbank_client import tbank
            _tbank_instance = tbank
            info("✅ T-Bank API клиент инициализирован")
        except ImportError as e:
            error(f"❌ Ошибка импорта T-Bank API: {e}")
            raise
    return _tbank_instance


def _get_telegram():
    global _telegram_instance
    if _telegram_instance is None:
        try:
            from .telegram.telegram_notifier import get_telegram_notifier
            _telegram_instance = get_telegram_notifier()
            info("✅ Telegram-нотификатор инициализирован")
        except ImportError as e:
            warning(f"⚠️ Ошибка импорта Telegram: {e}")
            _telegram_instance = None
    return _telegram_instance


class TradingBot:
    """Основной класс торгового бота - ТОЛЬКО КООРДИНАЦИЯ"""

    def __init__(self):
        """Инициализация - только создание компонентов и кэшей"""

        # ========== КЭШИ ==========
        self._price_cache = PriceCache(default_ttl=5)
        self._price_cache_long = PriceCache(default_ttl=30)
        self._positions_cache = PositionCache(default_ttl=5)
        self._validation_cache = TTLCache(default_ttl=300, max_size=1000, name="validation_cache")
        self._blocked_figis = TTLCache(default_ttl=600, max_size=500, name="blocked_figis")
        self._long_pending = TTLCache(default_ttl=60, max_size=100, name="long_pending")
        self._short_pending = TTLCache(default_ttl=60, max_size=100, name="short_pending")

        # ========== УТИЛИТЫ ==========
        self.figi_resolver = FigiResolver()
        self.memory_monitor = MemoryMonitor()

        # ========== КОМПОНЕНТЫ ==========
        # self.session_manager = SessionManager(self)
        self._session_manager = None
        # self.position_sizer = PositionSizer(self)
        self._position_sizer = None
        self.position_opener = PositionOpener(self)
        self.position_closer = PositionCloser(self)
        self.stock_scanner = StockScanner(self)
        self.ticker_validator = TickerValidator(self)
        self.performance_analyzer = PerformanceAnalyzer(self)
        self.margin_guard = MarginGuard(self)
        self.short_controller = ShortController(self)
        self.daily_loss_checker = DailyLossLimitChecker(self)
        self.drawdown_tracker = DrawdownTracker(self)

        # ========== СОСТОЯНИЕ БОТА ==========
        self._running = True
        self._shutting_down = False
        self._cycle_count = 0
        self._trades = []
        self._last_capital = 0
        self._start_time = time.time()
        self._lock = threading.Lock()
        self._opened_in_cycle = 0
        self._initial_capital = 0
        self._initial_capital_saved = False
        self._emergency_closing = False
        self._critical_margin_handling = False
        self._updating = set()

        # ========== МЕНЕДЖЕРЫ (ЛЕНИВАЯ ИНИЦИАЛИЗАЦИЯ) ==========
        self.capital_manager = None
        self.portfolio_rebalancer = None
        self.smart_orders_manager = None
        self.pre_market_trader = None
        self.advanced_tpsl_manager = None
        self.advanced_indicators = None
        self.pivot_analyzer = None
        self.fundamental_analyzer = None
        self.fundamental_db = None
        self.fundamental_updater = None
        self.news_analyzer = None

        # ========== ADVANCED КОМПОНЕНТЫ ==========
        self._init_advanced_components()

        # ========== ОПЦИОНАЛЬНЫЕ МОДУЛИ ==========
        self.metrics = None
        self.db = None
        self._init_optional_modules()

        # ========== ICEBERG И TRAILING (В POSITION_MANAGER) ==========
        from trading_bot.risk.position_manager import position_manager
        position_manager.init_advanced_managers(self)
        info("✅ Iceberg и Trailing Stop менеджеры инициализированы в PositionManager")

        # ========== ТОРГОВЫЙ ЦИКЛ ==========
        self._trading_loop = None
        # self.trading_loop = TradingLoop(self)

        # ========== ЗАГРУЗКА ПАРАМЕТРОВ ==========
        self._load_all_optimized_params()
        self._apply_settings()

        info("✅ TradingBot полностью инициализирован")

        # ========== ПРИМЕНЕНИЕ НАСТРОЕК АНАЛИТИКИ ==========
        from trading_bot.core.settings_manager import settings_manager
        settings_manager.apply_analytics_settings(self)

    @property
    def trading_loop(self):
        """Ленивая инициализация TradingLoop"""
        if self._trading_loop is None:
            from .core.trading_loop import TradingLoop
            self._trading_loop = TradingLoop(self)
        return self._trading_loop

    @property
    def session_manager(self):
        """Ленивая инициализация SessionManager"""
        if self._session_manager is None:
            from .core.session_manager import SessionManager
            self._session_manager = SessionManager(self)
        return self._session_manager

    @property
    def position_sizer(self):
        """Ленивая инициализация PositionSizer"""
        if self._position_sizer is None:
            from .trading.position_sizer import PositionSizer
            self._position_sizer = PositionSizer(self)
        return self._position_sizer

    def _init_advanced_components(self):
        """Инициализация продвинутых компонентов"""
        try:
            from .trading.advanced_tp_sl import advanced_tpsl_manager
            self.advanced_tpsl_manager = advanced_tpsl_manager
            self.advanced_tpsl_manager.bot = self
            self.advanced_tpsl_manager.start()
            info("✅ Advanced TPSL Manager инициализирован")
        except Exception as e:
            warning(f"⚠️ Advanced TPSL Manager: {e}")

        try:
            from .analysis.advanced_indicators import advanced_indicators
            self.advanced_indicators = advanced_indicators
            info("✅ Advanced Indicators инициализированы")
        except Exception as e:
            warning(f"⚠️ Advanced Indicators: {e}")

        try:
            from .analysis.pivot_analyzer import pivot_analyzer
            self.pivot_analyzer = pivot_analyzer
            info("✅ Pivot Analyzer инициализирован")
        except Exception as e:
            warning(f"⚠️ Pivot Analyzer: {e}")

        try:
            from .analysis.fundamental_analyzer import FundamentalAnalyzer
            from .analysis.fundamental_db import FundamentalDatabase
            from .analysis.fundamental_updater import FundamentalUpdater
            self.fundamental_analyzer = FundamentalAnalyzer()
            self.fundamental_db = FundamentalDatabase()
            self.fundamental_updater = FundamentalUpdater(
                db=self.fundamental_db,
                analyzer=self.fundamental_analyzer
            )
            info("✅ FundamentalAnalyzer инициализирован")
        except Exception as e:
            warning(f"⚠️ FundamentalAnalyzer: {e}")

        try:
            from .analysis.news_sentiment import NewsSentimentAnalyzer
            self.news_analyzer = NewsSentimentAnalyzer()
            info("✅ NewsSentimentAnalyzer инициализирован")
        except Exception as e:
            warning(f"⚠️ NewsSentimentAnalyzer: {e}")

        try:
            from .trading.smart_orders import smart_orders_manager
            self.smart_orders_manager = smart_orders_manager
            info("✅ SmartOrderManager инициализирован")
        except Exception as e:
            warning(f"⚠️ SmartOrderManager: {e}")

        try:
            from .trading.pre_market_trader import PreMarketTrader
            self.pre_market_trader = PreMarketTrader(self)
            info("✅ PreMarketTrader инициализирован")
        except Exception as e:
            warning(f"⚠️ PreMarketTrader: {e}")

    def _init_optional_modules(self):
        """Инициализация опциональных модулей"""
        try:
            from trading_bot.core.candle_sync_wrapper import init_candle_builder
            init_candle_builder(test_mode=False)
        except ImportError:
            pass
        except Exception as e:
            warning(f"⚠️ CandleBuilder: {e}")

        try:
            from trading_bot.monitoring.prometheus_metrics import PrometheusMetrics
            self.metrics = PrometheusMetrics(port=8001, enabled=True)
            self.metrics.start_server()
            info("✅ PrometheusMetrics запущен на порту 8001")
        except Exception as e:
            warning(f"⚠️ PrometheusMetrics: {e}")

        try:
            from trading_bot.data.database_manager import DatabaseManager
            self.db = DatabaseManager("trading_state.db")
        except Exception as e:
            warning(f"⚠️ DatabaseManager: {e}")

    def _apply_settings(self):
        """Применение настроек из settings_manager"""
        try:
            from trading_bot.core.settings_manager import settings_manager

            if hasattr(self, 'fundamental_analyzer') and self.fundamental_analyzer:
                self.fundamental_analyzer.enabled = settings_manager.get('fundamental_enabled', True)
                info(f"📊 Фундаментальный анализ: {'✅ ВКЛЮЧЁН' if self.fundamental_analyzer.enabled else '❌ ВЫКЛЮЧЁН'}")

            if hasattr(self, 'news_analyzer') and self.news_analyzer:
                self.news_analyzer.enabled = settings_manager.get('news_enabled', True)
                self.news_analyzer.max_impact = settings_manager.get('sentiment_impact_max', 5)
                info(f"📰 Новостной анализ: {'✅ ВКЛЮЧЁН' if self.news_analyzer.enabled else '❌ ВЫКЛЮЧЁН'}")

            self.use_fundamental_in_trading = settings_manager.get('use_fundamental_in_trading', True)
            info(f"🎯 Использование аналитики в торговле: {'✅ ДА' if self.use_fundamental_in_trading else '❌ НЕТ'}")

            info(f"⚡ Режимы торговли: scalping={settings_manager.get('use_scalping', True)}, "
                 f"swing={settings_manager.get('use_swing', True)}, "
                 f"position={settings_manager.get('use_position', False)}")

            use_margin = settings_manager.get('use_margin', False)
            if hasattr(config, 'use_margin'):
                config.use_margin = use_margin
            info(f"💳 Маржинальная торговля: {'✅ ВКЛЮЧЕНА' if use_margin else '❌ ВЫКЛЮЧЕНА'}")

            use_short = settings_manager.get('short_enabled', False)
            if hasattr(config, 'use_short'):
                config.use_short = use_short
            info(f"🔻 SHORT торговля: {'✅ ВКЛЮЧЕНА' if use_short else '❌ ВЫКЛЮЧЕНА'}")

            info(f"📈 Агрессивность: {settings_manager.get('aggressiveness', 5)}/10")
            info(f"🎫 Score пороги: LONG ≥ {settings_manager.get('score_threshold_long', 2)}, "
                 f"SHORT ≤ {settings_manager.get('score_threshold_short', -2)}")

            info("✅ Настройки применены")
        except Exception as e:
            debug(f"Ошибка применения настроек: {e}")

    def _load_all_optimized_params(self):
        """Загрузка оптимизированных параметров"""
        try:
            import json
            from pathlib import Path
            params_file = Path("backtest_results/optimized_params.json")
            if params_file.exists():
                with open(params_file, 'r', encoding='utf-8') as f:
                    self._optimized_params = json.load(f)
                all_params = list(self._optimized_params.values())
                if all_params:
                    config.risk_per_trade = sum(p.get('risk_per_trade', 0.1) for p in all_params) / len(all_params)
                    config.take_profit_pct = sum(p.get('take_profit_pct', 1.0) for p in all_params) / len(all_params)
                    config.stop_loss_pct = sum(p.get('stop_loss_pct', 0.5) for p in all_params) / len(all_params)
                    avg_confidence = sum(p.get('min_confidence', 1) for p in all_params) / len(all_params)
                    config.long_score_threshold = max(1, int(avg_confidence))
                    config.short_score_threshold = -max(1, int(avg_confidence))
                    info(f"📊 Применены оптимизированные параметры ({len(all_params)} тикеров)")
            else:
                self._optimized_params = {}
        except Exception as e:
            debug(f"Ошибка загрузки параметров: {e}")

    # ========== ПУБЛИЧНЫЕ МЕТОДЫ ==========

    def start(self):
        """Запуск бота"""
        try:
            from trading_bot.risk.position_manager import position_manager
            restored = position_manager.sync_and_recover_positions()
            if restored > 0:
                print(f"🔄 Восстановлено {restored} позиций из брокера")
        except Exception as e:
            print(f"⚠️ Ошибка синхронизации: {e}")

        # ✅ ИСПРАВЛЕНО: используем правильное имя метода
        if hasattr(self.trading_loop, 'start_loop'):
            self.trading_loop.start_loop()
        elif hasattr(self.trading_loop, 'start'):
            self.trading_loop.start()
        else:
            print("⚠️ Не найден метод запуска TradingLoop")

        self._start_pre_market_if_needed()

    def _start_pre_market_if_needed(self):
        """Запуск PreMarketTrader если нужно"""
        try:
            if not self.pre_market_trader:
                return

            now = get_moscow_time()
            current_time = now.time()
            is_weekend = now.weekday() >= 5

            print(f"🕐 Текущее время МСК: {current_time.hour:02d}:{current_time.minute:02d}")

            if is_weekend and not is_weekend_trading_time():
                print("🌙 Выходной день, PreMarketTrader не запущен")
                return

            if is_pre_market_time():
                print("🌅 Pre-market время, запускаем PreMarketTrader")
                threading.Thread(target=self._run_pre_market, daemon=True).start()
            else:
                print(f"⏸️ Не pre-market время, PreMarketTrader не запущен")
        except Exception as e:
            print(f"⚠️ Ошибка запуска PreMarketTrader: {e}")

    def _run_pre_market(self):
        """Запуск PreMarketTrader в отдельном потоке"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.pre_market_trader.start_trader())
            print("✅ PreMarketTrader успешно запущен")
        except Exception as e:
            print(f"⚠️ Ошибка в PreMarketTrader: {e}")
        finally:
            loop.close()

    def stop(self):
        """Остановка бота"""
        if self.advanced_tpsl_manager:
            try:
                self.advanced_tpsl_manager.stop()
            except Exception as e:
                warning(f"⚠️ Advanced TPSL Manager: {e}")

        if self.pre_market_trader:
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.pre_market_trader.stop_trader())
            except Exception as e:
                warning(f"⚠️ PreMarketTrader: {e}")
            finally:
                if loop is not None:
                    loop.close()

        info("🛑 Остановка торгового бота...")
        self._shutting_down = True
        self._running = False

        if self.db:
            self._save_state_to_db()
        self._save_daily_stats()

        warning("⏹️ Бот остановлен")
        telegram = _get_telegram()
        if telegram and hasattr(telegram, 'send_shutdown'):
            telegram.send_shutdown()

    def _save_state_to_db(self):
        try:
            from .risk.position_manager import position_manager
            if self.db:
                self.db.save_cycle_state(self._cycle_count, self._last_capital, 0)
                info("💾 Состояние сохранено в БД")
        except Exception as e:
            debug(f"Ошибка сохранения: {e}")

    def _save_daily_stats(self):
        try:
            import csv
            from pathlib import Path
            Path("backtest_results").mkdir(exist_ok=True)
            today = datetime.now().strftime('%Y%m%d')
            if self._trades:
                with open(f"backtest_results/daily_trades_{today}.csv", 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=self._trades[0].keys())
                    writer.writeheader()
                    writer.writerows(self._trades)
        except Exception as e:
            debug(f"Ошибка сохранения статистики: {e}")

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def get_available_balance(self) -> float:
        try:
            available, _, _ = _get_tbank().get_available_funds()
            return available
        except Exception:
            return 0

    def get_portfolio(self) -> Dict[str, Any]:
        try:
            available, total, _ = _get_tbank().get_available_funds()
            positions = self._get_positions()
            return {'cash': available, 'total_value': total, 'positions': positions}
        except Exception as e:
            warning(f"Ошибка get_portfolio: {e}")
            return {'cash': 0, 'total_value': 0, 'positions': []}

    def get_detailed_pnl(self) -> Dict[str, Any]:
        """
        Детальный расчёт P&L по всем открытым позициям

        Returns:
            Dict с полями:
            - total_pnl: общий P&L в рублях
            - total_pnl_pct: общий P&L в процентах
            - positions: список позиций с детальным P&L
            - total_value: общая стоимость портфеля
        """
        try:
            positions = self._get_positions(force_refresh=True)
            tbank = _get_tbank()

            total_pnl = 0.0
            total_value = 0.0
            detailed_positions = []

            for pos in positions:
                figi = pos.get('figi', '')
                quantity = pos.get('quantity', 0)
                avg_price = pos.get('avg_price', 0)

                if quantity == 0 or avg_price == 0:
                    continue

                # Получаем текущую цену
                current_price = tbank.get_current_price(figi)
                if not current_price:
                    current_price = avg_price

                # Расчёт P&L в зависимости от стороны
                if quantity > 0:  # LONG
                    pnl = (current_price - avg_price) * quantity
                    pnl_pct = ((current_price - avg_price) / avg_price) * 100 if avg_price > 0 else 0
                else:  # SHORT
                    pnl = (avg_price - current_price) * abs(quantity)
                    pnl_pct = ((avg_price - current_price) / avg_price) * 100 if avg_price > 0 else 0

                # Получаем тикер
                from trading_bot.api.tbank_client import tbank
                ticker = tbank._get_ticker_by_figi(figi) or figi[:12]

                total_pnl += pnl
                total_value += abs(quantity) * current_price

                detailed_positions.append({
                    'figi': figi,
                    'ticker': ticker,
                    'quantity': abs(quantity),
                    'side': 'LONG' if quantity > 0 else 'SHORT',
                    'avg_price': round(avg_price, 2),
                    'current_price': round(current_price, 2),
                    'pnl': round(pnl, 2),
                    'pnl_pct': round(pnl_pct, 2),
                    'value': round(abs(quantity) * current_price, 2)
                })

            # Общий P&L в процентах
            total_pnl_pct = 0
            total_capital = total_value - total_pnl if total_value > 0 else 0
            if total_capital > 0:
                total_pnl_pct = (total_pnl / total_capital) * 100

            return {
                'total_pnl': round(total_pnl, 2),
                'total_pnl_pct': round(total_pnl_pct, 2),
                'total_value': round(total_value, 2),
                'positions': detailed_positions,
                'positions_count': len(detailed_positions)
            }

        except Exception as e:
            error(f"❌ Ошибка расчёта детального P&L: {e}")
            return {
                'total_pnl': 0,
                'total_pnl_pct': 0,
                'total_value': 0,
                'positions': [],
                'positions_count': 0,
                'error': str(e)
            }

    def get_margin_status(self) -> Dict[str, Any]:
        try:
            tbank = _get_tbank()
            margin_allowed, margin_reason = tbank.check_margin_trading_allowed()
            if not margin_allowed:
                return {'status': 'disabled', 'warning': margin_reason, 'margin_rate': 0}
            margin_info = tbank.get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)
            return {
                'status': 'critical' if margin_rate >= 85 else 'warning' if margin_rate >= 70 else 'ok',
                'margin_rate': margin_rate,
                'margin_trading_enabled': margin_allowed
            }
        except Exception as e:
            return {'status': 'error', 'margin_rate': 0}

    def health_check(self) -> Dict[str, Any]:
        return {
            'healthy': self._running,
            'components': {'api_client': bool(config.tbank_token), 'portfolio_manager': True},
            'basic': {'state': 'running' if self._running else 'stopped', 'cycle_count': self._cycle_count}
        }

    def clear_validation_cache(self):
        """Очистка кэша валидации"""
        try:
            if hasattr(self, '_validation_cache') and self._validation_cache:
                self._validation_cache.clear()
                info("🧹 Кэш валидации очищен")
            else:
                debug("⚠️ _validation_cache не найден, пропускаем очистку")
        except Exception as e:
            debug(f"Ошибка очистки кэша валидации: {e}")

    # ========== ДЕЛЕГИРУЮЩИЕ МЕТОДЫ ==========

    def open_position_auto(self, ticker: str, quantity: int, side: str,
                           price: float = None, use_market: bool = True) -> bool:
        return self.position_opener.open_position_auto(ticker, quantity, side, price, use_market)

    # def emergency_close_all_positions(self) -> int:
    #     """⚠️ ВНИМАНИЕ: Этот метод устарел. Используйте _emergency_close_profitable_only"""
    #     warning("⚠️ Вызван устаревший метод emergency_close_all_positions!")
    #     # return self.position_closer.emergency_close_all()
    #     return 0

    def emergency_close_all_shorts(self) -> int:
        return self.position_closer.close_worst_positions(max_to_close=2)

    def get_capital_stats(self) -> Dict:
        return self.capital_manager.get_stats() if self.capital_manager else {}

    def get_rebalance_stats(self) -> Dict:
        return self.portfolio_rebalancer.get_stats() if self.portfolio_rebalancer else {}

    def get_analyzers_status(self) -> Dict[str, bool]:
        return {
            'fundamental': self.fundamental_analyzer is not None,
            'news': self.news_analyzer is not None,
            'technical': True,
            'advanced_indicators': self.advanced_indicators is not None,
            'pivot_analyzer': self.pivot_analyzer is not None,
            'tpsl_manager': self.advanced_tpsl_manager is not None,
        }

    # ========== ЗАГЛУШКИ ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ ==========

    # def _get_ticker_by_figi(self, figi: str) -> Optional[str]:
    #     """Получение тикера по FIGI (делегирование resolver'у)"""
    #     return self.figi_resolver.get_ticker_by_figi(figi)

    # def _get_figi_by_ticker(self, ticker: str) -> Optional[str]:
    #     """Получение FIGI по тикеру (делегирование resolver'у)"""
    #     return self.figi_resolver.get_figi_by_ticker(ticker)

    # def _place_market_order(self, figi: str, quantity: int, direction: str) -> bool:
    #     """
    #     Размещение рыночной заявки
    #
    #     Args:
    #         figi: FIGI инструмента
    #         quantity: Количество в лотах
    #         direction: "BUY" или "SELL"
    #
    #     Returns:
    #         bool: True если успешно
    #     """
    #     from trading_bot.api.tbank_client import tbank
    #     from trading_bot.logger import info, error, warning, debug
    #
    #     ticker = self._get_ticker_by_figi(figi) or figi[:8]
    #
    #     info(f"📡 РЫНОЧНАЯ ЗАЯВКА: {direction} {quantity} шт {ticker}")
    #
    #     try:
    #         if direction.upper() == "BUY":
    #             result = tbank.buy(figi, quantity, use_market=True)
    #         else:
    #             result = tbank.sell(figi, quantity, use_market=True)
    #
    #         if result:
    #             info(f"✅ Рыночная заявка {direction} {quantity} {ticker} исполнена")
    #             return True
    #         else:
    #             error(f"❌ Рыночная заявка {direction} {quantity} {ticker} не исполнена")
    #             return False
    #
    #     except Exception as e:
    #         error(f"❌ Ошибка рыночной заявки {ticker}: {e}")
    #         return False

    def _place_limit_order(self, figi: str, quantity: int, direction: str, price: float) -> bool:
        """
        Размещение лимитной заявки

        Args:
            figi: FIGI инструмента
            quantity: Количество в лотах
            direction: "BUY" или "SELL"
            price: Лимитная цена

        Returns:
            bool: True если успешно
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, error, warning, debug

        from trading_bot.api.tbank_client import tbank
        ticker = tbank._get_ticker_by_figi(figi) or figi[:8]

        info(f"📋 ЛИМИТНАЯ ЗАЯВКА: {direction} {quantity} шт {ticker} по {price:.2f}₽")

        try:
            result = tbank.place_limit_order(figi, quantity, direction, price)

            if result:
                info(f"✅ Лимитная заявка {direction} {quantity} {ticker} размещена")
                return True
            else:
                error(f"❌ Лимитная заявка {direction} {quantity} {ticker} не размещена")
                return False

        except Exception as e:
            error(f"❌ Ошибка лимитной заявки {ticker}: {e}")
            return False

    def _get_current_price(self, ticker: str) -> float:
        from trading_bot.api.tbank_client import tbank
        figi = tbank._get_figi_by_ticker(ticker) or ticker
        return tbank.get_current_price(figi) or 0

    def _get_positions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        if not force_refresh:
            cached = self._positions_cache.get("all_positions")
            if cached is not None:
                return cached
        positions = _get_tbank().get_positions()
        self._positions_cache.set("all_positions", positions, ttl=5)
        return positions

    def _calculate_position_size(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
        return self.position_sizer.calculate(stock, available_funds, score)

    async def _get_available_stocks(self, available_funds: float) -> List[StockCandidate]:
        return await self.stock_scanner.scan(available_funds)

    async def _find_and_open_positions(self, total_capital, available_funds, current_positions,
                                       minutes_left, session, min_auto_score=4, trading_loop=None):
        """Поиск и открытие позиций"""
        return await self.stock_scanner.find_and_open_positions(
            total_capital=total_capital,
            available_funds=available_funds,
            current_positions=current_positions,
            minutes_left=minutes_left,
            session=session,
            min_auto_score=min_auto_score,
            trading_loop=trading_loop
        )

    def _add_to_blacklist(self, ticker: str, minutes: int = 60):
        try:
            from trading_bot.core.blacklist_manager import blacklist_manager
            blacklist_manager.add_temporary(ticker, ttl_minutes=minutes)
            info(f"⛔ {ticker} добавлен в чёрный список на {minutes} минут")
        except Exception as e:
            debug(f"Ошибка добавления в чёрный список: {e}")

    def _track_smart_order(self, order_id: Optional[str], ticker: str, quantity: int, order_type: str):
        if not hasattr(self, '_smart_orders_tracking'):
            self._smart_orders_tracking = []
        self._smart_orders_tracking.append({
            'order_id': order_id,
            'ticker': ticker,
            'quantity': quantity,
            'type': order_type,
            'time': datetime.now()
        })
        if len(self._smart_orders_tracking) > 100:
            self._smart_orders_tracking = self._smart_orders_tracking[-100:]


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
trading_bot = TradingBot()
