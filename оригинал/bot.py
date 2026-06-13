"""Основной класс торгового бота - координатор всех компонентов"""

import os
import time
import threading
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

# Импорты из модулей проекта
from .config import config
from .models import StockCandidate, OrderSide
from .logger import info, warning, debug, success, error

# Компоненты
from .core.trading_loop import TradingLoop
from .core.session_manager import SessionManager
from .trading.position_opener import PositionOpener
from .trading.position_closer import PositionCloser
from .trading.position_sizer import PositionSizer
from .risk.position_manager import IcebergOrderManager, TrailingStopManager
from .trading.pre_market_trader import PreMarketTrader
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



def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class TradingBot:
    """Основной класс торгового бота"""

    _global_initialized = False

    def __init__(self):
        # Инициализация кэшей
        self._price_cache = PriceCache(default_ttl=5)
        self._price_cache_long = PriceCache(default_ttl=30)
        self._positions_cache = PositionCache(default_ttl=5)
        self._price_cache_timestamp = {}

        # Устанавливаем лимиты на кэши
        if hasattr(self._price_cache, '_cache'):
            self._price_cache._max_size = 500

        # Инициализация утилит
        self.figi_resolver = FigiResolver()
        self.memory_monitor = MemoryMonitor()

        # Инициализация компонентов
        self.session_manager = SessionManager(self)
        self.position_sizer = PositionSizer(self)
        self.iceberg_manager = None
        self.trailing_manager = None
        # self.pre_market_trader = None  # Оставьте только объявление
        self.position_opener = PositionOpener(self)
        self.position_closer = PositionCloser(self)
        self.stock_scanner = StockScanner(self)
        self.ticker_validator = TickerValidator(self)
        self.performance_analyzer = PerformanceAnalyzer(self)
        self.margin_guard = MarginGuard(self)
        self.short_controller = ShortController(self)
        self.daily_loss_checker = DailyLossLimitChecker(self)
        self.drawdown_tracker = DrawdownTracker(self)

        # Состояние бота
        self._running = True
        self._shutting_down = False
        self._cycle_count = 0
        self._trades = []
        self._last_capital = 0
        self._last_capital_log = 0
        self._start_time = time.time()
        self._lock = threading.Lock()
        self._opened_in_cycle = 0
        self._validation_cache = {}
        self._blocked_figis = {}
        self._initial_capital = 0
        self._initial_capital_saved = False

        self.metrics = None
        self.db = None

        # Пендинги для защиты от дублирования
        self._long_pending = {}
        self._short_pending = {}
        self._updating = set()

        # Флаги для предотвращения повторных вызовов
        self._emergency_closing = False
        self._critical_margin_handling = False
        self._critical_margin_handling_time = 0

        # Инициализация опциональных модулей
        self._init_optional_modules()

        # Торговый цикл
        self.trading_loop = TradingLoop(self)

        # Загрузка оптимизированных параметров
        self._load_all_optimized_params()

        # Управление капиталом (ленивая инициализация)
        self.capital_manager = None
        self.portfolio_rebalancer = None

        info("✅ TradingBot полностью инициализирован")

        self.init_advanced_managers()

    def init_advanced_managers(self):
        """Инициализация дополнительных менеджеров"""
        from .risk.position_manager import IcebergOrderManager, TrailingStopManager
        from .trading.pre_market_trader import PreMarketTrader

        self.iceberg_manager = IcebergOrderManager(self)
        self.trailing_manager = TrailingStopManager(self)
        self.pre_market_trader = PreMarketTrader(self)
        info("✅ Advanced managers initialized")

    async def start_pre_market(self):
        """Запуск pre-market торговли"""
        if self.pre_market_trader:
            await self.pre_market_trader.start()

    async def cleanup_duplicate_orders(self, ticker: str = None) -> Dict:
        """Очистка дублирующихся заявок"""
        from .api.tbank_client import tbank
        return tbank.cleanup_duplicate_orders(ticker)

    def init_capital_management(self, total_capital: float):
        """Инициализация системы управления капиталом"""
        try:
            from .risk.capital_manager import CapitalManager

            if self.capital_manager is None:
                self.capital_manager = CapitalManager(total_capital)

                # Ленивый импорт portfolio_rebalancer
                try:
                    from .risk.portfolio_rebalancer import get_portfolio_rebalancer
                    self.portfolio_rebalancer = get_portfolio_rebalancer(self.capital_manager, self)
                    if self.portfolio_rebalancer:
                        self.portfolio_rebalancer.start()
                except ImportError as e:
                    warning(f"⚠️ Модуль portfolio_rebalancer не найден: {e}")
                    self.portfolio_rebalancer = None

                success("✅ Система управления капиталом запущена")
            else:
                self.capital_manager.update_capital(total_capital)
        except ImportError as e:
            warning(f"⚠️ Модули управления капиталом не найдены: {e}")
        except Exception as e:
            warning(f"⚠️ Ошибка инициализации управления капиталом: {e}")

    def get_capital_stats(self) -> Dict:
        """Получение статистики капитала"""
        if self.capital_manager:
            return self.capital_manager.get_stats()
        return {}

    def get_rebalance_stats(self) -> Dict:
        """Получение статистики ребалансировки"""
        if self.portfolio_rebalancer:
            return self.portfolio_rebalancer.get_stats()
        return {}

    def _init_optional_modules(self):
        """Инициализация опциональных модулей (только один раз глобально)"""
        if TradingBot._global_initialized:
            debug("Опциональные модули уже инициализированы, пропускаем")
            return

        TradingBot._global_initialized = True

        try:
            from trading_bot.core.candle_sync_wrapper import init_candle_builder
            init_candle_builder(test_mode=False)
        except ImportError:
            pass
        except Exception as e:
            warning(f"⚠️ Ошибка инициализации CandleBuilder: {e}")

        try:
            from trading_bot.monitoring.prometheus_metrics import PrometheusMetrics
            # Меняем порт с 8000 на 8001 (8000 уже занят другими процессами)
            self.metrics = PrometheusMetrics(port=8001, enabled=True)
            self.metrics.start_server()
            info("✅ PrometheusMetrics запущен на порту 8001")
        except ImportError:
            self.metrics = None
        except Exception as e:
            warning(f"⚠️ Ошибка инициализации Prometheus: {e}")
            self.metrics = None

        try:
            from trading_bot.data.database_manager import DatabaseManager
            self.db = DatabaseManager("trading_state.db")
        except ImportError:
            self.db = None
        except Exception as e:
            warning(f"⚠️ Ошибка инициализации DatabaseManager: {e}")
            self.db = None

    def _load_all_optimized_params(self):
        """Загрузка оптимизированных параметров"""
        try:
            import json
            from pathlib import Path

            params_file = Path("backtest_results/optimized_params.json")
            if params_file.exists():
                with open(params_file, 'r') as f:
                    self._optimized_params = json.load(f)
                info(f"📊 Загружены оптимизированные параметры для {len(self._optimized_params)} тикеров")
            else:
                self._optimized_params = {}
        except Exception as e:
            debug(f"Ошибка загрузки параметров: {e}")
            self._optimized_params = {}

    # ========== Делегирующие методы ==========

    def _get_current_price(self, figi: str, force_refresh: bool = False,
                           use_long_cache: bool = False) -> Optional[float]:
        """Получение текущей цены с кэшированием"""
        cache = self._price_cache_long if use_long_cache else self._price_cache
        if not force_refresh:
            cached = cache.get(figi)
            if cached is not None:
                return cached

        price = _get_tbank().get_current_price(figi)
        if price and price > 0:
            cache.set(figi, price)
            self._price_cache_timestamp[figi] = time.time()
        return price

    def _get_positions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Получение позиций с кэшированием"""
        if not force_refresh:
            cached = self._positions_cache.get("all_positions")
            if cached is not None:
                return cached

        positions = _get_tbank().get_positions()
        self._positions_cache.set("all_positions", positions, ttl=5)
        return positions

    def _get_ticker_by_figi(self, figi: str) -> Optional[str]:
        """Получение тикера по FIGI"""
        return self.figi_resolver.get_ticker_by_figi(figi)

    def _get_figi_by_ticker(self, ticker: str) -> Optional[str]:
        """Получение FIGI по тикеру"""
        return self.figi_resolver.get_figi_by_ticker(ticker)

    def _calculate_position_size(self, stock: StockCandidate, available_funds: float, score: int = 0) -> int:
        """Расчёт размера позиции"""
        return self.position_sizer.calculate(stock, available_funds, score)

    def _open_long_market(self, stock: StockCandidate, quantity: int) -> bool:
        """Открытие LONG позиции"""
        return self.position_opener.open_long_market(stock, quantity)

    def _open_short_market(self, stock: StockCandidate, quantity: int) -> bool:
        """Открытие SHORT позиции"""
        return self.position_opener.open_short_market(stock, quantity)

    def _find_and_open_positions(self, total_capital: float, available_funds: float,
                                 current_positions: int, minutes_left: int, session: str):
        """Поиск и открытие позиций"""
        try:
            info(f"🔍 _find_and_open_positions: searching for stocks...")
            info(f"   Капитал: {total_capital:.0f}₽, Доступно: {available_funds:.0f}₽")
            info(f"   Текущих позиций: {current_positions}, Максимум: {config.max_positions}")

            # Получаем список кандидатов
            candidates = self._get_available_stocks(available_funds)

            info(f"📊 StockScanner вернул {len(candidates) if candidates else 0} кандидатов")

            if not candidates:
                warning("⚠️ Нет кандидатов для открытия")
                return

            # Сортируем по силе сигнала (от большего к меньшему)
            candidates.sort(key=lambda x: abs(x.analysis.score), reverse=True)

            # Ограничиваем количество новых позиций
            max_new = config.max_positions - current_positions
            candidates = candidates[:max_new]

            info(f"🎯 Будет попытка открыть до {len(candidates)} новых позиций")

            for stock in candidates:
                # Проверяем, что позиция ещё не открыта
                from trading_bot.risk.position_manager import position_manager
                if position_manager.get_position(stock.figi):
                    warning(f"⚠️ Позиция по {stock.ticker} уже существует, пропускаем")
                    continue

                # Расчёт размера позиции
                score = getattr(stock.analysis, 'score', 0)
                quantity = self._calculate_position_size(stock, available_funds, score)

                if quantity <= 0:
                    warning(f"⚠️ {stock.ticker}: размер позиции = 0, пропускаем")
                    continue

                # Проверяем, хватит ли средств для закрытия SHORT
                if stock.side == OrderSide.SHORT:
                    worst_case = stock.price * 1.10
                    needed_for_close = quantity * worst_case * 1.05
                    if needed_for_close > available_funds * 0.9:
                        warning(f"⚠️ {stock.ticker}: недостаточно средств для закрытия SHORT")
                        warning(f"   Нужно: {needed_for_close:.0f}₽, Доступно: {available_funds:.0f}₽")
                        continue

                # Открываем позицию
                if stock.side == OrderSide.LONG:
                    info(f"🟢 Открываем LONG {stock.ticker}: {quantity} шт по {stock.price:.2f}₽")
                    self._open_long_market(stock, quantity)
                else:
                    info(f"🔴 Открываем SHORT {stock.ticker}: {quantity} шт по {stock.price:.2f}₽")
                    self._open_short_market(stock, quantity)

        except Exception as e:
            error(f"Ошибка в _find_and_open_positions: {e}")
            import traceback
            traceback.print_exc()

    def _get_available_stocks(self, available_funds: float) -> List[StockCandidate]:
        """Поиск доступных акций"""
        return self.stock_scanner.scan(available_funds)

    def _validate_ticker(self, ticker: str) -> Tuple[bool, Dict[str, Any]]:
        """Валидация тикера"""
        return self.ticker_validator.validate(ticker)

    def _analyze_performance(self):
        """Анализ эффективности торговли"""
        self.performance_analyzer.analyze()

    def _check_drawdown(self):
        """Проверка просадки"""
        self.drawdown_tracker.check()

    def _save_equity_point(self, value: float):
        """Сохранение точки эквити"""
        self.drawdown_tracker.add_point(value)

    def _check_margin_safety(self) -> Tuple[bool, float]:
        """Проверка безопасности маржи"""
        return self.margin_guard.check_safety()

    def _get_evening_session_tickers(self) -> set:
        """Возвращает множество тикеров для вечерней сессии"""
        return {
            'SBER', 'SBERP', 'VTBR', 'GAZP', 'LKOH', 'ROSN', 'TATN', 'TATNP',
            'NVTK', 'SNGS', 'SNGSP', 'MGNT', 'MTSS', 'CHMF', 'NLMK', 'GMKN',
            'PLZL', 'POLY', 'YNDX', 'TCSG', 'OZON', 'FIXP', 'PIKK', 'MAGN',
            'RUAL', 'AFLT', 'URKA', 'MOEX', 'POSI', 'SIBN', 'AFKS', 'HYDR',
            'PHOR', 'FIVE', 'TRNFP', 'APTK', 'ENPG', 'RSTI', 'IRAO', 'FEES',
        }

    def is_market_open(self, ticker: str = None) -> bool:
        """Проверяет, открыт ли рынок прямо сейчас"""
        try:
            figi = self._get_figi_by_ticker(ticker) if ticker else None
            if not figi:
                return self._is_trading_allowed(ticker)

            tbank = _get_tbank()
            trading_status = tbank.get_trading_status(figi)
            if trading_status:
                if trading_status.get("trading_status") in [5, 13]:
                    return True
                else:
                    return False
        except Exception as e:
            debug(f"Ошибка при запросе статуса торгов: {e}")

        return self._is_trading_allowed(ticker)

    def _is_trading_allowed(self, ticker: str = None) -> bool:
        """Проверка по локальному расписанию"""
        from datetime import time as dt_time

        try:
            now = datetime.now()
            current_time = now.time()
            weekday = now.weekday()
            is_weekend = weekday >= 5

            MAIN_START = dt_time(9, 50)
            MAIN_END = dt_time(18, 59)
            EVENING_START = dt_time(19, 0, 1)
            EVENING_END = dt_time(23, 49, 59)
            WEEKEND_START = dt_time(10, 0)
            WEEKEND_END = dt_time(18, 59)
            MORNING_START = dt_time(6, 50)
            MORNING_END = dt_time(9, 49, 59)
            AUCTION_START = dt_time(18, 55)
            AUCTION_END = dt_time(18, 59, 30)

            if AUCTION_START <= current_time <= AUCTION_END:
                return True

            if is_weekend:
                if WEEKEND_START <= current_time <= WEEKEND_END:
                    return True
                return False

            if MORNING_START <= current_time <= MORNING_END:
                return True

            if MAIN_START <= current_time <= MAIN_END:
                return True

            if EVENING_START <= current_time <= EVENING_END:
                if ticker:
                    evening_tickers = self._get_evening_session_tickers()
                    if ticker.upper() not in evening_tickers:
                        return False
                return True

            return False

        except Exception as e:
            error(f"Ошибка проверки торгов: {e}")
            return True

    def get_available_balance(self) -> float:
        """Получение доступного баланса"""
        try:
            available, total, _ = _get_tbank().get_available_funds()
            return available
        except Exception:
            return 0

    def get_portfolio(self) -> Dict[str, Any]:
        """Получение портфеля"""
        try:
            available, total, _ = _get_tbank().get_available_funds()
            positions = self._get_positions()
            return {
                'cash': available,
                'total_value': total,
                'positions': positions
            }
        except Exception:
            return {'cash': 0, 'total_value': 0, 'positions': []}

    def get_detailed_pnl(self) -> Dict[str, Any]:
        """Детальный расчет P&L"""
        try:
            positions = self._get_positions()
            result = []
            total_pnl = 0

            for pos in positions:
                figi = pos['figi']
                quantity = abs(pos['quantity'])
                avg_price = pos['avg_price']
                current_price = self._get_current_price(figi)
                side = "SHORT" if pos['quantity'] < 0 else "LONG"

                if current_price:
                    if side == "SHORT":
                        pnl = (avg_price - current_price) * quantity
                        pnl_pct = (avg_price - current_price) / avg_price * 100
                    else:
                        pnl = (current_price - avg_price) * quantity
                        pnl_pct = (current_price - avg_price) / avg_price * 100

                    total_pnl += pnl
                    result.append({
                        'figi': figi,
                        'ticker': self._get_ticker_by_figi(figi) or figi[:8],
                        'side': side,
                        'quantity': quantity,
                        'avg_price': avg_price,
                        'current_price': current_price,
                        'net_pnl': pnl,
                        'pnl_pct': pnl_pct
                    })

            return {
                'total_pnl': total_pnl,
                'total_pnl_pct': (total_pnl / self._last_capital * 100) if self._last_capital > 0 else 0,
                'positions': result
            }
        except Exception as e:
            debug(f"Ошибка расчета P&L: {e}")
            return {'total_pnl': 0, 'total_pnl_pct': 0, 'positions': []}

    def get_margin_status(self) -> Dict[str, Any]:
        """Получение статуса маржинальной торговли"""
        try:
            tbank = _get_tbank()
            margin_allowed, margin_reason = tbank.check_margin_trading_allowed()

            if not margin_allowed:
                return {
                    'status': 'disabled',
                    'warning': margin_reason,
                    'critical': False,
                    'margin_rate': 0,
                    'margin_trading_enabled': False
                }

            margin_info = tbank.get_margin_info()
            if not margin_info:
                return {'status': 'unknown', 'critical': False, 'margin_rate': 0}

            margin_rate = margin_info.get('margin_rate', 0)

            if margin_rate >= 85:
                status, critical = 'critical', True
            elif margin_rate >= 70:
                status, critical = 'warning', False
            else:
                status, critical = 'ok', False

            return {
                'status': status,
                'critical': critical,
                'margin_rate': margin_rate,
                'available_margin': margin_info.get('available_margin', 0),
                'used_margin': margin_info.get('used_margin', 0),
                'liquid_portfolio': margin_info.get('liquid_portfolio', 0),
                'margin_trading_enabled': margin_allowed
            }

        except Exception as e:
            error(f"Ошибка проверки маржи: {e}")
            return {'status': 'error', 'critical': False, 'margin_rate': 0}

    def _auto_disable_short_if_needed(self, total_capital: float) -> bool:
        """Автоматическое отключение SHORT"""
        return self.short_controller.auto_disable(total_capital)

    def _check_daily_loss_limit(self) -> bool:
        """Проверка дневного лимита убытка"""
        return self.daily_loss_checker.check()

    def emergency_close_all_positions(self) -> int:
        """Аварийное закрытие всех позиций"""
        return self.position_closer.emergency_close_all()

    def emergency_close_all_shorts(self) -> int:
        """Аварийное закрытие SHORT позиций"""
        return self.position_closer.emergency_close_shorts()

    def check_and_close_if_margin_high(self) -> bool:
        """Проверка маржи и закрытие при необходимости"""
        try:
            tbank = _get_tbank()
            margin_info = tbank.get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)

            if margin_rate > 95:
                error(f"\n🔥 КРИТИЧЕСКАЯ МАРЖА: {margin_rate:.1f}%!")
                self.emergency_close_all_positions()
                return True
            elif margin_rate > 80:
                warning(f"\n⚠️ ВЫСОКАЯ МАРЖА: {margin_rate:.1f}%")
                return False
            return False
        except Exception as e:
            debug(f"Ошибка check_and_close_if_margin_high: {e}")
            return False

    # ========== Публичные методы ==========

    def start(self):
        """Запуск бота"""
        # Запуск торгового цикла
        self.trading_loop.start()

        # ========== ЗАПУСК PRE-MARKET TRADER ТОЛЬКО В PRE-MARKET ВРЕМЯ ==========
        # PreMarketTrader должен работать ТОЛЬКО в pre-market время (6:50-9:50)
        # В другое время он конфликтует с основной торговлей

        try:
            if hasattr(self, 'pre_market_trader') and self.pre_market_trader:
                # Проверяем время - запускаем только в pre-market
                from trading_bot.utils.time_utils import get_moscow_time
                now = get_moscow_time()
                current_time = now.time()
                from datetime import time as dt_time

                PRE_MARKET_START = dt_time(6, 50)
                PRE_MARKET_END = dt_time(9, 50)

                if PRE_MARKET_START <= current_time <= PRE_MARKET_END:
                    info("🌅 Pre-market время, запускаем PreMarketTrader")
                    import asyncio
                    import threading

                    def run_pre_market():
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            loop.run_until_complete(self.start_pre_market())
                        except Exception as e:
                            warning(f"⚠️ Ошибка в PreMarketTrader: {e}")
                        finally:
                            loop.close()

                    thread = threading.Thread(target=run_pre_market, daemon=True)
                    thread.start()
                    info("🌅 PreMarketTrader запущен в фоновом режиме")
                else:
                    info(
                        f"⏸️ Не pre-market время ({current_time.hour}:{current_time.minute}), PreMarketTrader не запущен")
        except Exception as e:
            warning(f"⚠️ Ошибка запуска PreMarketTrader: {e}")

    def stop(self):
        """Остановка бота"""
        # ========== ОСТАНОВКА PRE-MARKET TRADER ==========
        try:
            if hasattr(self, 'pre_market_trader') and self.pre_market_trader:
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.pre_market_trader.stop())
                    debug("🌙 PreMarketTrader остановлен")
                except Exception as e:
                    warning(f"⚠️ Ошибка остановки PreMarketTrader: {e}")
                finally:
                    try:
                        loop.close()
                    except:
                        pass
        except Exception as e:
            warning(f"⚠️ Ошибка остановки PreMarketTrader: {e}")
        # =================================================

        info("🛑 Остановка торгового бота...")
        self._shutting_down = True
        self._running = False

        if self.db:
            self._save_state_to_db()

        self._save_daily_stats()
        warning("⏹️ Бот остановлен по запросу")
        telegram = _get_telegram()
        if telegram and hasattr(telegram, 'send_shutdown'):
            telegram.send_shutdown()

    def _save_state_to_db(self):
        """Сохранение состояния в БД"""
        try:
            from .risk.position_manager import position_manager
            positions = position_manager.get_all_positions()
            position_data = {}
            for figi, pos in positions.items():
                position_data[figi] = {
                    'ticker': self._get_ticker_by_figi(figi),
                    'quantity': pos.quantity,
                    'avg_price': pos.avg_price,
                    'side': pos.side.value if hasattr(pos.side, 'value') else str(pos.side),
                    'entry_time': pos.entry_time.isoformat()
                }
            if self.db:
                self.db.save_positions(position_data)
                self.db.save_cycle_state(self._cycle_count, self._last_capital, 0)
            info("💾 Состояние бота сохранено в БД")
        except Exception as e:
            debug(f"Ошибка сохранения состояния: {e}")

    def _save_daily_stats(self):
        """Сохранение дневной статистики"""
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

    def get_detailed_metrics(self) -> Dict[str, Any]:
        """Получение детальной статистики"""
        with self._lock:
            return {
                'trading_cycles': {'total': self._cycle_count, 'average_time': 0},
                'trading_activity': {
                    'trades_executed': len(self._trades),
                    'signals_generated': self._cycle_count * 10
                },
                'pnl': {
                    'total': sum(t.get('pnl', 0) for t in self._trades),
                    'winning': len([t for t in self._trades if t.get('pnl', 0) > 0]),
                    'losing': len([t for t in self._trades if t.get('pnl', 0) < 0])
                }
            }

    def log_memory_usage(self):
        """Логирование использования памяти"""
        self.memory_monitor.log_usage()

    def health_check(self) -> Dict[str, Any]:
        """Проверка здоровья системы"""
        return {
            'healthy': self._running,
            'components': {
                'api_client': bool(config.tbank_token),
                'portfolio_manager': True,
                'market_analyzer': True
            },
            'basic': {
                'state': 'running' if self._running else 'stopped',
                'cycle_count': self._cycle_count
            }
        }

    def clear_validation_cache(self):
        """Очистка кэша валидации"""
        self._validation_cache.clear()
        info("🧹 Кэш валидации очищен")

    def get_validation_stats(self) -> Dict:
        """Получение статистики валидации"""
        return self._validation_cache


# Создание глобального экземпляра для обратной совместимости
trading_bot = TradingBot()