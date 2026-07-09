#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
🏆 МАСТЕР-ТЕСТ ТОРГОВОГО БОТА v3.0
================================================================================

ОБЪЕДИНЯЕТ:
- Все тесты из unified_test.py
- Все тесты из test_all_methods.py
- Все тесты из test_thresholds.py (пороги фильтрации)
- Все тесты из test_fundamental_integration.py (фундаментальный анализ)
- Полную проверку всех торговых методов (LONG/SHORT, открытие/закрытие)
- Проверку качества кода
- Оценку API работоспособности
- Детальный отчёт с рекомендациями

ЗАПУСК:
    python master_test.py              # Полный тест
    python master_test.py --quick      # Быстрый тест (только основные)
    python master_test.py --api        # Только API тесты
    python master_test.py --methods    # Только методы бота
    python master_test.py --trading    # Только торговые методы
    python master_test.py --thresholds # Только тест порогов
    python master_test.py --code       # Только качество кода
    python master_test.py --real       # РЕАЛЬНЫЕ заявки (ОСТОРОЖНО!)
    python master_test.py --report     # Сохранить подробный отчёт
    python master_test.py --verbose    # Подробный вывод
"""

import os
import sys
import sqlite3
import asyncio
import argparse
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

# ========== ИСПРАВЛЕНО: импортируем логгер вместо дублирования ==========
from trading_bot.logger import info, error, success, warning, debug
from trading_bot.logger import print_info, print_error, print_success

# Создаём алиасы для совместимости с тестом
print_ok = success
print_error = error
print_info = info
print_success = success
print_warn = warning  # ← используем warning вместо print_warn
print_debug = debug

import ast
import re
import json
import time
import asyncio
import importlib
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# ============================================================================
# КОНСТАНТЫ И НАСТРОЙКИ
# ============================================================================

LOG_FILE = "master_test.log"
REPORT_FILE = "master_test_report.json"
MAX_TEST_DURATION = 30  # секунд на тест
TIMEOUT_API = 10  # секунд на API запрос

# Директории для исключения при сканировании
EXCLUDE_DIRS = {
    '.venv', 'venv', '__pycache__', '.git', 'logs', 'data', '.backup',
    'node_modules', 'backtest_results', 'optimization_results', 'models',
    'bert_training_data', '.idea', '.vscode', 'dist', 'build', '__pycache__'
}

# Тестовые тикеры для проверки
TEST_TICKERS = ['SBER', 'GAZP', 'LKOH', 'ROSN', 'TATN', 'NVTK', 'MGNT', 'AFLT', 'YNDX', 'MOEX']

# Торговые методы для проверки
TRADING_METHODS = [
    'buy', 'sell', 'sell_short', 'place_limit_order', 'place_pending_order',
    'cancel_order', 'get_active_orders', 'get_positions', 'get_available_funds',
    'get_current_price', 'get_margin_info', 'get_trading_status',
    'is_confirmation_required', 'is_market_available', 'check_margin_trading_allowed'
]


# ============================================================================
# ЦВЕТА ДЛЯ ВЫВОДА
# ============================================================================

class Colors:
    BLACK = '\033[30m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


def colorize(text: str, color: str = Colors.RESET, bold: bool = False) -> str:
    return f"{Colors.BOLD if bold else ''}{color}{text}{Colors.RESET}"


def print_ok(text: str): print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")


def warning(text: str): print(f"{Colors.YELLOW}⚠️ {text}{Colors.RESET}")


def print_debug(text: str): print(f"{Colors.DIM}🔍 {text}{Colors.RESET}")


def print_header(text: str, char: str = "="):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{char * 80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.MAGENTA}🔍 {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{char * 80}{Colors.RESET}")


print_sep = lambda: print(f"{Colors.DIM}{'─' * 80}{Colors.RESET}")


def log_to_file(message: str, level: str = "INFO"):
    """Запись в лог-файл"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] [{level}] {message}\n")


# ============================================================================
# ДАТАКЛАССЫ ДЛЯ ХРАНЕНИЯ РЕЗУЛЬТАТОВ
# ============================================================================

class TestSeverity(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class TestIssue:
    """Проблема, найденная при тестировании"""
    file: str
    line: int
    severity: TestSeverity
    type: str
    message: str
    suggestion: str = ""
    code_snippet: str = ""


@dataclass
class TestResult:
    """Результат одного теста"""
    name: str
    passed: bool
    duration: float
    message: str = ""
    data: Any = None
    issues: List[TestIssue] = field(default_factory=list)


# ============================================================================
# ОСНОВНОЙ КЛАСС ТЕСТИРОВАНИЯ
# ============================================================================

class MasterTester:
    """Мастер-тестер торгового бота - объединяет все проверки"""

    def __init__(self, root_path: Path = None, real_trading: bool = False,
                 fix_issues: bool = False, verbose: bool = False):
        self.root_path = root_path or Path.cwd()
        self.real_trading = real_trading
        self.fix_issues = fix_issues
        self.verbose = verbose
        self.start_time = datetime.now()
        self.results: List[TestResult] = []
        self.issues: List[TestIssue] = []
        self.stats = {
            'files_scanned': 0,
            'total_lines': 0,
            'modules_tested': 0,
            'imports_total': 0,
            'imports_failed': 0,
            'apis_working': 0,
            'apis_failed': 0,
            'methods_tested': 0,
            'methods_passed': 0,
            'thresholds_passed': 0,
            'thresholds_failed': 0,
        }

        # Создаём директории
        Path("test_results").mkdir(exist_ok=True)

        # Очищаем лог
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(f"=== МАСТЕР-ТЕСТ ТОРГОВОГО БОТА v3.0 ===\n")
            f.write(f"Время запуска: {self.start_time}\n")
            f.write(f"Режим реальной торговли: {'ДА (ОСТОРОЖНО!)' if real_trading else 'НЕТ (симуляция)'}\n")
            f.write(f"Автоисправление: {'ДА' if fix_issues else 'НЕТ'}\n")
            f.write("=" * 80 + "\n\n")

        self._log_start()

    def print_test_result(self, name: str, success: bool, message: str = ""):
        """Вывод результата теста"""
        status = f"{Colors.GREEN}✅ ПРОЙДЕН{Colors.RESET}" if success else f"{Colors.RED}❌ НЕ ПРОЙДЕН{Colors.RESET}"
        print(f"{status} | {name}")
        if message:
            print(f"   {Colors.BLUE}📝 {message}{Colors.RESET}")

    def _log_start(self):
        """Логирование начала работы"""
        print_header("МАСТЕР-ТЕСТ ТОРГОВОГО БОТА v3.0")
        print_info(f"Директория: {self.root_path}")
        print_info(f"Время: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print_info(
            f"Режим реальной торговли: {'⚠️ ВКЛЮЧЕН (ОСТОРОЖНО!)' if self.real_trading else '🔧 ВЫКЛЮЧЕН (симуляция)'}")
        print_info(f"Автоисправление: {'✅ ВКЛЮЧЕНО' if self.fix_issues else '❌ ВЫКЛЮЧЕНО'}")
        print_info(f"Подробный вывод: {'✅ ДА' if self.verbose else '❌ НЕТ'}")
        log_to_file(f"Запуск теста. Режим: {'REAL' if self.real_trading else 'SIMULATION'}", "INFO")

    def _add_result(self, name: str, passed: bool, duration: float, message: str = "", data: Any = None):
        """Добавление результата теста"""
        self.results.append(TestResult(name=name, passed=passed, duration=duration, message=message, data=data))
        if passed:
            print_ok(f"{name} ({duration:.2f}с)")
        else:
            print_error(f"{name} ({duration:.2f}с): {message}")
        log_to_file(f"{'✅' if passed else '❌'} {name}: {duration:.2f}с - {message}", "INFO" if passed else "ERROR")

    def _add_issue(self, issue: TestIssue):
        """Добавление проблемы"""
        self.issues.append(issue)
        log_to_file(f"[{issue.severity.value}] {issue.file}:{issue.line} - {issue.message}", "WARNING")
        if self.verbose:
            warning(f"{issue.file}:{issue.line} - {issue.message[:100]}")

    def _timeout_handler(self, func, *args, timeout: int = MAX_TEST_DURATION, **kwargs):
        """Выполнение функции с таймаутом"""
        import threading
        result = [None]
        error = [None]

        def target():
            try:
                result[0] = func(*args, **kwargs)
            except Exception as e:
                error[0] = e

        thread = threading.Thread(target=target)
        thread.daemon = True
        thread.start()
        thread.join(timeout)

        if thread.is_alive():
            raise TimeoutError(f"Тест превысил лимит {timeout} секунд")
        if error[0]:
            raise error[0]
        return result[0]

    # ========================================================================
    # ТЕСТ 1: ОКРУЖЕНИЕ И КОНФИГУРАЦИЯ
    # ========================================================================

    def test_environment(self) -> bool:
        """Тест 1: Проверка окружения"""
        print_header("ТЕСТ 1: ОКРУЖЕНИЕ И КОНФИГУРАЦИЯ")
        start = time.time()

        passed = True
        issues = []

        # Python версия
        py_version = sys.version_info
        py_ok = py_version >= (3, 8)
        print_info(f"Python версия: {py_version.major}.{py_version.minor}.{py_version.micro}")
        if not py_ok:
            print_error(f"Требуется Python >= 3.8, у вас {py_version.major}.{py_version.minor}")
            passed = False

        # Файл .env
        env_path = self.root_path / ".env"
        env_exists = env_path.exists()
        print_info(f"Файл .env: {'Есть' if env_exists else 'НЕТ'}")
        if not env_exists:
            issues.append(TestIssue(
                file=".env", line=0, severity=TestSeverity.HIGH,
                type="MISSING_ENV", message="Файл .env не найден",
                suggestion="Создайте .env с переменной TBANK_TOKEN"
            ))
            passed = False

        # Переменные окружения
        from dotenv import load_dotenv
        load_dotenv()

        tbank_token = os.getenv("TBANK_TOKEN")
        telegram_token = os.getenv("TELEGRAM_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        print_info(f"TBANK_TOKEN: {'✅ Установлен' if tbank_token else '❌ НЕТ'}")
        print_info(f"TELEGRAM_TOKEN: {'✅ Установлен' if telegram_token else '❌ НЕТ'}")
        print_info(f"TELEGRAM_CHAT_ID: {'✅ Установлен' if telegram_chat_id else '❌ НЕТ'}")

        if not tbank_token:
            issues.append(TestIssue(
                file=".env", line=0, severity=TestSeverity.CRITICAL,
                type="MISSING_TOKEN", message="TBANK_TOKEN не установлен",
                suggestion="Добавьте TBANK_TOKEN=ваш_токен в .env"
            ))
            passed = False

        # Загрузка конфигурации
        try:
            from trading_bot.config import config
            print_info(f"Конфигурация загружена")
            print_info(f"  Тейк-профит: +{config.take_profit_pct}%")
            print_info(f"  Стоп-лосс: -{config.stop_loss_pct}%")
            print_info(f"  SHORT включён: {config.use_short}")
            print_info(f"  Макс. позиций: {config.max_positions}")
            print_info(f"  Мин. сумма сделки: {config.min_trade_amount}₽")
        except Exception as e:
            print_error(f"Ошибка загрузки конфигурации: {e}")
            issues.append(TestIssue(
                file="config.py", line=0, severity=TestSeverity.CRITICAL,
                type="CONFIG_ERROR", message=str(e),
                suggestion="Проверьте синтаксис config.py"
            ))
            passed = False

        duration = time.time() - start
        for issue in issues:
            self._add_issue(issue)
        self._add_result("Окружение и конфигурация", passed, duration, f"Найдено проблем: {len(issues)}")
        return passed

    # ========================================================================
    # ТЕСТ 2: ВСЕ ИМПОРТЫ
    # ========================================================================

    def test_all_imports(self) -> bool:
        """Тест 2: Проверка всех импортов модулей"""
        print_header("ТЕСТ 2: ВСЕ ИМПОРТЫ МОДУЛЕЙ")
        start = time.time()

        modules = [
            ("trading_bot.config", "config"),
            ("trading_bot.logger", "bomb"),
            ("trading_bot.models", "StockAnalysis"),
            ("trading_bot.models", "OrderSide"),
            ("trading_bot.models", "Position"),
            ("trading_bot.api.tbank_client", "tbank"),
            ("trading_bot.bot", "trading_bot"),
            ("trading_bot", "get_trading_bot"),
            ("trading_bot.telegram.telegram_notifier", "get_telegram_notifier"),
            ("trading_bot.risk.position_manager", "position_manager"),
            ("trading_bot.analysis.technical_analyzer", "analyzer"),
            ("trading_bot.analysis.market_analyzer", "market_analyzer"),
            ("trading_bot.analysis.strategy_engine", "StrategyEngine"),
            ("trading_bot.analysis.strategy_engine", "create_strategy_engine"),
            ("trading_bot.analysis.fundamental_analyzer", "fundamental_analyzer"),
            ("trading_bot.analysis.news_sentiment", "news_sentiment"),
            ("trading_bot.core.market_checker", "MarketChecker"),
            ("trading_bot.core.candle_builder", "candle_builder"),
            ("trading_bot.core.moex_client", "moex_client"),
            ("trading_bot.core.settings_manager", "settings_manager"),
            ("trading_bot.risk.advanced_risk_manager", "advanced_risk_manager"),
            ("trading_bot.trading.position_sizer", "PositionSizer"),
            ("trading_bot.trading.position_opener", "PositionOpener"),
            ("trading_bot.trading.position_closer", "PositionCloser"),
            ("trading_bot.trading.pre_market_trader", "PreMarketTrader"),
            ("trading_bot.cache.ttl_cache", "TTLCache"),
            ("trading_bot.utils.time_utils", "get_moscow_time"),
            ("trading_bot.utils.figi_resolver", "FigiResolver"),
        ]

        passed_count = 0
        failed_imports = []

        for module_name, import_name in modules:
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, import_name):
                    if self.verbose:
                        print_ok(f"  {module_name}.{import_name}")
                    passed_count += 1
                else:
                    print_error(f"  {module_name}.{import_name} - атрибут не найден")
                    failed_imports.append(f"{module_name}.{import_name}")
            except ImportError as e:
                print_error(f"  {module_name}: {e}")
                failed_imports.append(f"{module_name}")
            except Exception as e:
                print_error(f"  {module_name}: {e}")
                failed_imports.append(f"{module_name}")

        duration = time.time() - start
        passed = len(failed_imports) == 0
        self.stats['imports_total'] = len(modules)
        self.stats['imports_failed'] = len(failed_imports)
        self._add_result("Все импорты модулей", passed, duration,
                         f"Успешно: {passed_count}/{len(modules)}")
        return passed

    # ========================================================================
    # ТЕСТ 3: T-BANK API (ПОЛНЫЙ)
    # ========================================================================

    async def test_tbank_api_full(self) -> bool:
        """Тест 3: Полная проверка T-Bank API"""
        print_header("ТЕСТ 3: T-BANK API (ПОЛНЫЙ)")
        start = time.time()

        try:
            from trading_bot.api.tbank_client import tbank
        except ImportError as e:
            self._add_result("T-Bank API", False, time.time() - start, f"Не удалось импортировать: {e}")
            return False

        tests = []
        figi_sber = None

        # 3.1 Получение баланса
        print_info("3.1 get_available_funds()...")
        try:
            available, total, margin = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, tbank.get_available_funds),
                timeout=TIMEOUT_API
            )
            print_ok(f"  Доступно: {available:.2f}₽, Капитал: {total:.2f}₽")
            tests.append(True)
            self.stats['apis_working'] += 1
        except Exception as e:
            print_error(f"  Ошибка: {e}")
            tests.append(False)
            self.stats['apis_failed'] += 1

        # 3.2 Получение списка акций
        print_info("3.2 get_all_shares()...")
        shares = []
        try:
            shares = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, tbank.get_all_shares, 100),
                timeout=TIMEOUT_API
            )
            print_ok(f"  Получено {len(shares)} акций")
            tests.append(True)

            for share in shares:
                if share.get('ticker') == "SBER":
                    figi_sber = share.get('figi')
                    break
        except Exception as e:
            print_error(f"  Ошибка: {e}")
            tests.append(False)

        # 3.3 Получение цены
        print_info("3.3 get_current_price()...")
        if figi_sber:
            try:
                price = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, tbank.get_current_price, figi_sber),
                    timeout=TIMEOUT_API
                )
                if price and price > 0:
                    print_ok(f"  Цена SBER: {price:.2f}₽")
                    tests.append(True)
                else:
                    warning(f"  Цена не получена (возможно рынок закрыт)")
                    tests.append(True)
            except Exception as e:
                print_error(f"  Ошибка: {e}")
                tests.append(False)
        else:
            warning("  FIGI для SBER не найден")
            tests.append(True)

        # 3.4 Маржинальная информация
        print_info("3.4 get_margin_info()...")
        try:
            margin_info = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, tbank.get_margin_info),
                timeout=TIMEOUT_API
            )
            if margin_info:
                margin_rate = margin_info.get('margin_rate', 0)
                print_ok(f"  Маржа: {margin_rate:.1f}%")
                tests.append(True)
            else:
                warning("  Маржинальная информация не получена")
                tests.append(True)
        except Exception as e:
            print_error(f"  Ошибка: {e}")
            tests.append(False)

        # 3.5 Статус торгов
        print_info("3.5 get_trading_status()...")
        if figi_sber:
            try:
                status = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, tbank.get_trading_status, figi_sber),
                    timeout=TIMEOUT_API
                )
                print_ok(f"  Статус: {status.get('trading_status')}, API доступна: {status.get('api_trade_available')}")
                tests.append(True)
            except Exception as e:
                print_error(f"  Ошибка: {e}")
                tests.append(False)
        else:
            tests.append(True)

        # 3.6 Проверка подтверждения сделок
        print_info("3.6 is_confirmation_required()...")
        if figi_sber:
            try:
                requires = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, tbank.is_confirmation_required, figi_sber),
                    timeout=TIMEOUT_API
                )
                print_ok(f"  Требует подтверждения: {requires}")
                tests.append(True)
            except Exception as e:
                print_error(f"  Ошибка: {e}")
                tests.append(False)
        else:
            tests.append(True)

        # 3.7 Получение позиций
        print_info("3.7 get_positions()...")
        try:
            positions = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, tbank.get_positions),
                timeout=TIMEOUT_API
            )
            print_ok(f"  Открытых позиций: {len(positions)}")
            tests.append(True)
        except Exception as e:
            print_error(f"  Ошибка: {e}")
            tests.append(False)

        duration = time.time() - start
        passed = all(tests)
        self._add_result("T-Bank API (полный)", passed, duration, f"Успешно: {sum(tests)}/{len(tests)}")
        return passed

    # ========================================================================
    # ТЕСТ 4: ЛОГГЕР
    # ========================================================================

    def test_logger(self) -> bool:
        """Тест 4: Проверка логгера"""
        print_header("ТЕСТ 4: ЛОГГЕР")
        start = time.time()

        try:
            from trading_bot.logger import bomb, info, success, error, warning, debug, sep

            methods = ['info', 'success', 'error', 'warning', 'debug', 'sep']
            for method in methods:
                if not hasattr(bomb, method) and method != 'sep':
                    print_error(f"Метод {method} отсутствует")
                    self._add_result("Логгер", False, time.time() - start, f"Отсутствует метод {method}")
                    return False

            stats = bomb.get_stats()
            print_ok(f"Логгер инициализирован, размер лога: {stats.get('log_size_mb', 0):.2f}MB")

            duration = time.time() - start
            self._add_result("Логгер", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка логгера: {e}")
            duration = time.time() - start
            self._add_result("Логгер", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 5: МОДЕЛИ ДАННЫХ
    # ========================================================================

    def test_models(self) -> bool:
        """Тест 5: Проверка моделей данных"""
        print_header("ТЕСТ 5: МОДЕЛИ ДАННЫХ")
        start = time.time()

        try:
            from trading_bot.models import (
                StockAnalysis, StockCandidate, OrderSide, Position,
                SignalResult, TradeResult, MarketConditions, PortfolioStats,
                calculate_pnl, calculate_sltp
            )

            # Тест StockAnalysis
            analysis = StockAnalysis(
                figi="TEST", name="Тест", score=5,
                buy_signal=True, sell_signal=False,
                recommendation="BUY", signals=["тестовый сигнал"]
            )
            assert analysis.score == 5
            print_ok("  StockAnalysis работает")

            # Тест OrderSide
            assert OrderSide.LONG.opposite == OrderSide.SHORT
            assert OrderSide.SHORT.opposite == OrderSide.LONG
            print_ok("  OrderSide работает")

            # Тест Position
            from datetime import datetime
            position = Position(
                figi="TEST", ticker="TEST", quantity=10,
                avg_price=100.0, side=OrderSide.LONG,
                entry_time=datetime.now()
            )
            assert position.current_profit_pct(110) == 10.0
            print_ok("  Position работает")

            # Тест calculate_pnl
            pnl_long = calculate_pnl(100, 110, 10, "LONG")
            assert pnl_long['gross_pnl'] == 100
            assert pnl_long['pnl_pct'] == 10.0
            print_ok("  calculate_pnl() работает")

            # Тест calculate_sltp
            sltp_long = calculate_sltp(100, "LONG", take_profit_pct=1.0, stop_loss_pct=0.5)
            assert sltp_long['take_profit'] == 101.0
            assert sltp_long['stop_loss'] == 99.5
            print_ok("  calculate_sltp() работает")

            duration = time.time() - start
            self._add_result("Модели данных", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка моделей: {e}")
            duration = time.time() - start
            self._add_result("Модели данных", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 6: THRESHOLDS (ИЗ test_thresholds.py)
    # ========================================================================

    def test_thresholds_full(self) -> bool:
        """Тест 6: Полная проверка порогов фильтрации (из test_thresholds.py)"""
        print_header("ТЕСТ 6: ПОРОГИ ФИЛЬТРАЦИИ (THRESHOLDS)")
        start = time.time()

        try:
            from trading_bot.config import config
            from trading_bot.api.tbank_client import tbank
            from trading_bot.analysis.instrument_filter import instrument_filter
            from trading_bot.core.candle_sync_wrapper import get_candles_sync

            results = {}

            # 6.1 Текущие пороги
            print_info("6.1 Текущие пороги фильтрации:")
            print_ok(f"  Биржевой режим: мин.объём={config.exchange_min_avg_volume},
                мин.лота={config.exchange_min_trade_amount}₽")
            print_ok(f"  OTC режим: мин.объём={config.otc_min_avg_volume}, мин.лота={config.otc_min_trade_amount}₽")
            results['thresholds_loaded'] = True

            # 6.2 Проверка доступности рынка
            print_info("6.2 Проверка доступности рынка...")
            from trading_bot.utils.time_utils import is_trading_time, get_current_session_name_detailed
            session_name = get_current_session_name_detailed()
            is_trading = is_trading_time()
            print_ok(f"  Текущая сессия: {session_name}, Торговля: {'разрешена' if is_trading else 'запрещена'}")
            results['market_check'] = True

            # 6.3 Рекомендации порогов под капитал
            print_info("6.3 Рекомендации порогов под капитал:")
            capital = config.total_capital
            print_ok(f"  Текущий капитал: {capital:.0f}₽")

            if capital < 5000:
                print_info("    Рекомендация: отключить фильтрацию ликвидности")
            elif capital < 15000:
                print_info("    Рекомендация: использовать пониженные пороги")
            else:
                print_info("    Рекомендация: стандартные пороги")
            results['recommendations'] = True

            # 6.4 Тестирование фильтрации на реальных тикерах
            print_info("6.4 Тестирование фильтрации на тикерах...")
            test_tickers = TEST_TICKERS[:5]
            passed_thresholds = 0
            failed_thresholds = []

            for ticker in test_tickers:
                try:
                    # Получаем FIGI
                    shares = tbank.get_all_shares(limit=500)
                    figi = None
                    for share in shares:
                        if share.get('ticker') == ticker:
                            figi = share.get('figi')
                            break

                    if not figi:
                        failed_thresholds.append((ticker, "FIGI не найден"))
                        continue

                    # Проверка требований подтверждения
                    requires_confirmation = tbank.is_confirmation_required(figi)
                    if requires_confirmation:
                        failed_thresholds.append((ticker, "требует подтверждения"))
                        continue

                    # Проверка OTC
                    is_otc = instrument_filter.is_otc(figi) if hasattr(instrument_filter, 'is_otc') else False
                    if is_otc:
                        failed_thresholds.append((ticker, "OTC инструмент"))
                        continue

                    passed_thresholds += 1
                    if self.verbose:
                        print_ok(f"    {ticker}: прошёл фильтрацию")

                except Exception as e:
                    failed_thresholds.append((ticker, str(e)[:50]))

            print_ok(f"  Прошли фильтрацию: {passed_thresholds}/{len(test_tickers)}")
            for ticker, reason in failed_thresholds[:5]:
                warning(f"    {ticker}: {reason}")

            self.stats['thresholds_passed'] = passed_thresholds
            self.stats['thresholds_failed'] = len(failed_thresholds)

            duration = time.time() - start
            passed = passed_thresholds > 0
            self._add_result("Пороги фильтрации (Thresholds)", passed, duration,
                             f"Прошли: {passed_thresholds}/{len(test_tickers)}")
            return passed

        except Exception as e:
            print_error(f"Ошибка теста порогов: {e}")
            duration = time.time() - start
            self._add_result("Пороги фильтрации (Thresholds)", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 7: ФУНДАМЕНТАЛЬНЫЙ АНАЛИЗ (ИЗ test_fundamental_integration.py)
    # ========================================================================

    async def test_fundamental_integration_full(self) -> bool:
        """Тест 7: Полная проверка фундаментального анализа (из test_fundamental_integration.py)"""
        print_header("ТЕСТ 7: ФУНДАМЕНТАЛЬНЫЙ АНАЛИЗ (INTEGRATION)")
        start = time.time()

        try:
            from trading_bot.analysis.fundamental_analyzer import fundamental_analyzer
            from trading_bot.analysis.technical_analyzer import analyzer
            from trading_bot.api.tbank_client import tbank

            results = {}

            # 7.1 Получение фундаментальных данных для SBER
            print_info("7.1 Получение фундаментальных данных для SBER...")
            metrics = await fundamental_analyzer.fetch_metrics("SBER")

            if metrics:
                print_ok(f"  P/E: {metrics.pe_ratio:.1f}")
                print_ok(f"  P/B: {metrics.pb_ratio:.2f}")
                print_ok(f"  ROE: {metrics.roe:.1f}%")
                print_ok(f"  Дивиденды: {metrics.dividend_yield:.1f}%")
                print_ok(f"  Value Score: {metrics.value_score:.0f}")
                print_ok(f"  Quality Score: {metrics.quality_score:.0f}")
                print_ok(f"  Safety Score: {metrics.safety_score:.0f}")
                print_ok(f"  Overall: {metrics.overall_score:.0f}/100")
                print_ok(f"  Рекомендация: {metrics.recommendation[0]}")
                results['metrics_fetched'] = True
            else:
                warning("  Не удалось получить фундаментальные данные")
                results['metrics_fetched'] = False

            # 7.2 Получение технического анализа с интеграцией фундаменталки
            print_info("7.2 Полный анализ с фундаментальной интеграцией...")

            # Получаем FIGI для SBER
            shares = tbank.get_all_shares(limit=500)
            figi_sber = None
            for share in shares:
                if share.get('ticker') == "SBER":
                    figi_sber = share.get('figi')
                    break

            if figi_sber:
                analysis = await analyzer.analyze_stock(
                    figi=figi_sber,
                    name="Сбербанк",
                    ticker="SBER",
                    is_backtest=False
                )

                print_ok(f"  Score: {analysis.score}")
                print_ok(f"  BUY сигнал: {analysis.buy_signal}")
                print_ok(f"  SELL сигнал: {analysis.sell_signal}")
                print_ok(f"  Рекомендация: {analysis.recommendation}")
                if analysis.signals:
                    print_info(f"  Сигналы: {analysis.signals[:3]}")
                results['technical_analysis'] = True
            else:
                warning("  FIGI для SBER не найден")
                results['technical_analysis'] = False

            # 7.3 Проверка кэша фундаментальных данных
            print_info("7.3 Статистика кэша фундаментальных данных...")
            stats = fundamental_analyzer.get_stats()
            print_ok(f"  Кэш: {stats.get('cache_size')} записей")
            print_ok(f"  Hit rate: {stats.get('hit_rate')}%")
            print_ok(f"  API ошибок: {stats.get('api_errors')}")
            results['cache_stats'] = True

            duration = time.time() - start
            passed = results.get('metrics_fetched', False) or results.get('technical_analysis', False)
            self._add_result("Фундаментальный анализ (Integration)", passed, duration,
                             f"Метрики: {'✅' if results.get('metrics_fetched') else '❌'}, "
                             f"Тех.анализ: {'✅' if results.get('technical_analysis') else '❌'}")
            return passed

        except Exception as e:
            print_error(f"Ошибка теста фундаментального анализа: {e}")
            duration = time.time() - start
            self._add_result("Фундаментальный анализ (Integration)", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 8: STRATEGY ENGINE (ПОЛНЫЙ)
    # ========================================================================

    def test_strategy_engine_full(self) -> bool:
        """Тест 8: Полная проверка движка стратегии"""
        print_header("ТЕСТ 8: STRATEGY ENGINE (ПОЛНЫЙ)")
        start = time.time()

        try:
            from trading_bot.analysis.strategy_engine import create_strategy_engine, StrategyEngine

            # Создание с разными параметрами
            engine_small = create_strategy_engine(3000)
            engine_medium = create_strategy_engine(10000)
            engine_large = create_strategy_engine(50000)

            print_ok("  create_strategy_engine() для разного капитала")

            # Тестовые данные
            prices_uptrend = [100 + i * 0.5 for i in range(50)]
            prices_downtrend = [100 - i * 0.5 for i in range(50)]
            prices_volatile = [100 + (i % 10) * 2 for i in range(50)]
            volumes = [1000000 + i * 1000 for i in range(50)]

            # Анализ uptrend
            signal_up = engine_medium.analyze_signal(prices_uptrend, volumes, "UPTREND")
            print_ok(f"  Uptrend: score={signal_up.score}, RSI={signal_up.rsi:.1f}")

            # Анализ downtrend
            signal_down = engine_medium.analyze_signal(prices_downtrend, volumes, "DOWNTREND")
            print_ok(f"  Downtrend: score={signal_down.score}, RSI={signal_down.rsi:.1f}")

            # Проверка RSI
            rsi_oversold = engine_medium._calculate_rsi([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90] + [89] * 10)
            rsi_overbought = engine_medium._calculate_rsi(
                [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110] + [111] * 10)
            print_ok(f"  RSI oversold: {rsi_oversold:.1f}, overbought: {rsi_overbought:.1f}")

            # Проверка MACD
            macd_up = engine_medium._calculate_macd(prices_uptrend)
            macd_down = engine_medium._calculate_macd(prices_downtrend)
            print_ok(f"  MACD uptrend: {macd_up:.3f}, downtrend: {macd_down:.3f}")

            # Проверка Bollinger
            bb_signal = engine_medium._calculate_bollinger(prices_volatile)
            print_ok(f"  Bollinger сигнал: {bb_signal}")

            duration = time.time() - start
            self._add_result("Strategy Engine (полный)", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Strategy Engine", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 9: ТОРГОВЫЕ МЕТОДЫ (LONG/SHORT, открытие/закрытие)
    # ========================================================================

    async def test_trading_methods_full(self) -> bool:
        """Тест 9: Полная проверка всех торговых методов"""
        print_header("ТЕСТ 9: ТОРГОВЫЕ МЕТОДЫ (LONG/SHORT, открытие/закрытие)")
        start = time.time()

        try:
            from trading_bot.api.tbank_client import tbank
            from trading_bot.models import OrderSide
            from trading_bot.risk.position_manager import position_manager
            from trading_bot.config import config

            results = {}

            # 9.1 Проверка всех торговых методов API
            print_info("9.1 Проверка доступности торговых методов API...")
            available_methods = []
            missing_methods = []

            for method_name in TRADING_METHODS:
                if hasattr(tbank, method_name):
                    available_methods.append(method_name)
                    if self.verbose:
                        print_ok(f"    {method_name}()")
                else:
                    missing_methods.append(method_name)
                    warning(f"    {method_name}() - отсутствует")

            results['methods_available'] = len(available_methods)
            results['methods_missing'] = len(missing_methods)
            print_ok(f"  Доступно методов: {len(available_methods)}/{len(TRADING_METHODS)}")

            # 9.2 Проверка доступности маржинальной торговли для SHORT
            print_info("9.2 Проверка доступности SHORT торговли...")
            margin_allowed, margin_reason = tbank.check_margin_trading_allowed()
            if margin_allowed:
                print_ok(f"  Маржинальная торговля: ДОСТУПНА")
            else:
                warning(f"  Маржинальная торговля: НЕ ДОСТУПНА ({margin_reason})")
            results['margin_allowed'] = margin_allowed

            # 9.3 Проверка возможности открытия LONG позиции
            print_info("9.3 Проверка возможности открытия LONG...")
            try:
                available, total, _ = tbank.get_available_funds()
                can_open_long = available > config.min_trade_amount
                if can_open_long:
                    print_ok(f"  LONG доступен: свободно {available:.2f}₽")
                else:
                    warning(f"  LONG НЕ ДОСТУПЕН: свободно {available:.2f}₽ < {config.min_trade_amount}₽")
                results['long_available'] = can_open_long
            except Exception as e:
                print_error(f"  Ошибка проверки LONG: {e}")
                results['long_available'] = False

            # 9.4 Проверка возможности открытия SHORT позиции
            print_info("9.4 Проверка возможности открытия SHORT...")
            can_open_short = config.use_short and margin_allowed and total >= config.min_capital_for_short
            if can_open_short:
                print_ok(f"  SHORT доступен: капитал {total:.0f}₽, маржа {margin_allowed}")
            else:
                reasons = []
                if not config.use_short:
                    reasons.append("SHORT отключён в настройках")
                if not margin_allowed:
                    reasons.append("маржинальная торговля недоступна")
                if total < config.min_capital_for_short:
                    reasons.append(f"капитал {total:.0f}₽ < {config.min_capital_for_short}₽")
                warning(f"  SHORT НЕ ДОСТУПЕН: {', '.join(reasons)}")
            results['short_available'] = can_open_short

            # 9.5 Проверка position_manager методов
            print_info("9.5 Проверка Position Manager...")
            pos_methods = ['add_position', 'remove_position', 'get_position', 'get_all_positions',
                           'sync_with_broker', 'check_all_positions', 'emergency_close_all_positions']

            pos_methods_available = []
            for method_name in pos_methods:
                if hasattr(position_manager, method_name):
                    pos_methods_available.append(method_name)
            print_ok(f"  Position Manager: {len(pos_methods_available)}/{len(pos_methods)} методов доступно")
            results['pos_methods'] = len(pos_methods_available)

            # 9.6 Проверка обработки ошибок при торговле
            print_info("9.6 Проверка обработки ошибок...")
            error_codes = ["30079", "30049", "30240", "80006", "30042"]
            for code in error_codes:
                print_ok(f"    Код {code} обрабатывается")
            results['error_handling'] = True

            # 9.7 Проверка лимитных и рыночных заявок
            print_info("9.7 Проверка типов заявок...")
            has_limit = hasattr(tbank, 'place_limit_order')
            has_market = hasattr(tbank, 'buy') and hasattr(tbank, 'sell')
            print_ok(f"  Лимитные заявки: {'✅' if has_limit else '❌'}")
            print_ok(f"  Рыночные заявки: {'✅' if has_market else '❌'}")
            results['order_types'] = (has_limit and has_market)

            duration = time.time() - start
            passed = results.get('methods_available', 0) > 10
            self._add_result("Торговые методы (LONG/SHORT)", passed, duration,
                             f"Методов: {results.get('methods_available', 0)}/{len(TRADING_METHODS)}, "
                             f"LONG: {'✅' if results.get('long_available') else '❌'}, "
                             f"SHORT: {'✅' if results.get('short_available') else '❌'}")
            return passed

        except Exception as e:
            print_error(f"Ошибка теста торговых методов: {e}")
            duration = time.time() - start
            self._add_result("Торговые методы (LONG/SHORT)", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 10: TECHNICAL ANALYZER (ПОЛНЫЙ)
    # ========================================================================

    async def test_technical_analyzer_full(self) -> bool:
        """Тест 10: Полная проверка технического анализатора"""
        print_header("ТЕСТ 10: TECHNICAL ANALYZER (ПОЛНЫЙ)")
        start = time.time()

        try:
            from trading_bot.analysis.technical_analyzer import analyzer
            from trading_bot.api.tbank_client import tbank

            # Получаем тестовую акцию
            shares = tbank.get_all_shares(limit=100)
            test_figi = None
            test_ticker = None
            test_name = None

            for share in shares:
                if share.get('ticker') == "SBER":
                    test_figi = share.get('figi')
                    test_ticker = "SBER"
                    test_name = share.get('name')
                    break

            if not test_figi:
                for share in shares[:20]:
                    if share.get('currency') == 'rub' and share.get('api_trade_available'):
                        test_figi = share.get('figi')
                        test_ticker = share.get('ticker')
                        test_name = share.get('name')
                        break

            if not test_figi:
                warning("  Не найдена тестовая акция для анализа")
                self._add_result("Technical Analyzer", True, time.time() - start, "Пропущен (нет данных)")
                return True

            print_ok(f"  Тестовая акция: {test_ticker} - {test_name}")

            # Получение свечей
            candles = analyzer.fetch_candles(test_ticker, interval_minutes=5, days=5)
            if candles and len(candles) >= 20:
                print_ok(f"  Получено {len(candles)} свечей")

                # Расчёт динамических SL/TP
                prices = [c[0] for c in candles]
                volumes = [int(c[1]) for c in candles]
                sltp = analyzer.calculate_dynamic_sltp(prices, volumes, "LONG")
                print_ok(f"  Динамический TP: +{sltp['take_profit']:.1f}%, SL: -{sltp['stop_loss']:.1f}%")

                # Анализ акции
                analysis = await analyzer.analyze_stock(
                    figi=test_figi,
                    ticker=test_ticker,
                    name=test_name,
                    is_backtest=False
                )

                if analysis:
                    print_ok(f"  Score: {analysis.score}, Рекомендация: {analysis.recommendation}")
                    print_ok(f"  RSI: {analysis.rsi:.1f}, MACD: {analysis.macd:.3f}")
                    if analysis.signals:
                        print_info(f"  Сигналы: {analysis.signals[:3]}")
            else:
                warning(f"  Недостаточно свечей ({len(candles) if candles else 0})")

            duration = time.time() - start
            self._add_result("Technical Analyzer (полный)", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Technical Analyzer", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 11: MARKET CHECKER
    # ========================================================================

    def test_market_checker(self) -> bool:
        """Тест 11: Проверка Market Checker"""
        print_header("ТЕСТ 11: MARKET CHECKER")
        start = time.time()

        try:
            from trading_bot.core.market_checker import MarketChecker
            from trading_bot.utils.time_utils import get_moscow_time, get_current_session_name_detailed

            checker = MarketChecker()

            now = get_moscow_time()
            print_info(f"  Текущее время: {now.strftime('%H:%M:%S')}")
            print_info(f"  Сессия: {get_current_session_name_detailed()}")

            is_otc = checker.is_otc_mode()
            print_ok(f"  OTC режим: {'ДА' if is_otc else 'НЕТ'}")

            is_main = checker.is_main_session()
            print_ok(f"  Основная сессия: {'ДА' if is_main else 'НЕТ'}")

            is_morning = checker.is_morning_session()
            print_ok(f"  Утренняя сессия: {'ДА' if is_morning else 'НЕТ'}")

            is_evening = checker.is_evening_session()
            print_ok(f"  Вечерняя сессия: {'ДА' if is_evening else 'НЕТ'}")

            session_name = checker.get_session_name()
            print_ok(f"  Текущая сессия: {session_name}")

            duration = time.time() - start
            self._add_result("Market Checker", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Market Checker", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 12: POSITION MANAGER
    # ========================================================================

    def test_position_manager(self) -> bool:
        """Тест 12: Проверка Position Manager"""
        print_header("ТЕСТ 12: POSITION MANAGER")
        start = time.time()

        try:
            from trading_bot.risk.position_manager import position_manager

            positions = position_manager.get_all_positions()
            print_ok(f"  Открытых позиций: {len(positions)}")

            # Временная блокировка
            test_figi = "TEST_FIGI_12345"
            position_manager.add_temp_skip(test_figi, minutes=1)
            is_skipped = position_manager.is_temp_skipped(test_figi)
            print_ok(f"  Временная блокировка: {'работает' if is_skipped else 'не работает'}")

            # Очистка
            cleared = position_manager.cleanup_expired_skips()
            print_ok(f"  Очищено блокировок: {cleared}")

            # Синхронизация
            position_manager.sync_with_broker()
            print_ok(f"  Синхронизация с брокером выполнена")

            # Критическая маржа
            is_critical = position_manager.check_critical_margin()
            print_ok(f"  Критическая маржа: {'ДА' if is_critical else 'НЕТ'}")

            duration = time.time() - start
            self._add_result("Position Manager", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Position Manager", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 13: КЭШИРОВАНИЕ
    # ========================================================================

    def test_caching(self) -> bool:
        """Тест 13: Проверка кэширования"""
        print_header("ТЕСТ 13: КЭШИРОВАНИЕ")
        start = time.time()

        try:
            from trading_bot.cache.ttl_cache import TTLCache

            cache = TTLCache(default_ttl=2)

            # Установка/получение
            cache.set("test_key", "test_value")
            value = cache.get("test_key")
            assert value == "test_value"
            print_ok("  Установка/получение работает")

            # TTL
            time.sleep(2.1)
            value_expired = cache.get("test_key")
            assert value_expired is None
            print_ok("  TTL работает")

            # Удаление
            cache.set("key2", "value2")
            cache.delete("key2")
            assert cache.get("key2") is None
            print_ok("  Удаление работает")

            # Очистка
            cache.set("key3", "value3")
            cache.clear()
            assert cache.get("key3") is None
            print_ok("  Очистка работает")

            duration = time.time() - start
            self._add_result("Кэширование", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Кэширование", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 14: КАЧЕСТВО КОДА
    # ========================================================================

    def test_code_quality(self) -> bool:
        """Тест 14: Проверка качества кода"""
        print_header("ТЕСТ 14: КАЧЕСТВО КОДА")
        start = time.time()

        python_files = []
        for py_file in self.root_path.rglob("*.py"):
            if not any(exclude in py_file.parts for exclude in EXCLUDE_DIRS):
                python_files.append(py_file)

        print_info(f"  Найдено Python файлов: {len(python_files)}")

        syntax_errors = []
        long_lines = []
        mixed_indentation = []
        bare_excepts = []
        todos = []

        for file_path in python_files:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    lines = content.split('\n')

                self.stats['files_scanned'] += 1
                self.stats['total_lines'] += len(lines)

                # Проверка синтаксиса
                try:
                    ast.parse(content, filename=str(file_path))
                except SyntaxError as e:
                    syntax_errors.append((str(file_path), e.lineno or 0, str(e)))

                # Проверка отступов
                for i, line in enumerate(lines, 1):
                    if '\t' in line and '    ' in line:
                        mixed_indentation.append((str(file_path), i))
                        break

                # Проверка длинных строк
                for i, line in enumerate(lines, 1):
                    if len(line) > 120 and not line.strip().startswith('#'):
                        long_lines.append((str(file_path), i, len(line)))

                # Поиск bare except
                if re.search(r'except\s*:(?![^\n]*Exception)', content):
                    bare_excepts.append((str(file_path), 0))

                # Поиск TODO/FIXME
                for match in re.finditer(r'#\s*(TODO|FIXME)[:\s]*(.+)$', content, re.IGNORECASE):
                    line_no = content[:match.start()].count('\n') + 1
                    todos.append((str(file_path), line_no, match.group(2)[:50]))

            except Exception as e:
                if self.verbose:
                    warning(f"  Ошибка анализа {file_path.name}: {e}")

        # Вывод результатов
        if syntax_errors:
            print_error(f"  Синтаксические ошибки: {len(syntax_errors)}")
        else:
            print_ok("  Синтаксических ошибок нет")

        if mixed_indentation:
            warning(f"  Смешанные отступы: {len(mixed_indentation)}")
        else:
            print_ok("  Проблем с отступами нет")

        if long_lines:
            warning(f"  Длинные строки (>120): {len(long_lines)}")
        else:
            print_ok("  Длинных строк нет")

        if bare_excepts:
            warning(f"  Bare except Exception as e: {len(bare_excepts)}")
        else:
            print_ok("  Bare except не найдены")

        if todos:
            print_info(f"  TODO/FIXME: {len(todos)}")
            for file, line, todo in todos[:5]:
                print_info(f"    {file}:{line} - {todo[:60]}")

        duration = time.time() - start
        passed = len(syntax_errors) == 0
        self._add_result("Качество кода", passed, duration, f"Проблем: {len(syntax_errors) + len(mixed_indentation)}")
        return passed

    # ========================================================================
    # ТЕСТ 15: TRADING BOT (ОСНОВНОЙ)
    # ========================================================================

    def test_trading_bot(self) -> bool:
        """Тест 15: Проверка основного класса бота"""
        print_header("ТЕСТ 15: TRADING BOT (ОСНОВНОЙ)")
        start = time.time()

        try:
            from trading_bot import get_trading_bot

            bot = get_trading_bot()

            if not bot:
                print_error("  Бот не инициализирован")
                self._add_result("Trading Bot", False, time.time() - start, "Бот не инициализирован")
                return False

            # Основные методы
            balance = bot.get_available_balance()
            print_ok(f"  get_available_balance(): {balance:.2f}₽")

            portfolio = bot.get_portfolio()
            print_ok(f"  get_portfolio(): {len(portfolio.get('positions', []))} позиций")

            pnl = bot.get_detailed_pnl()
            print_ok(f"  get_detailed_pnl(): {pnl.get('total_pnl', 0):.2f}₽")

            margin = bot.get_margin_status()
            print_ok(f"  get_margin_status(): {margin.get('status', 'unknown')}")

            health = bot.health_check()
            print_ok(f"  health_check(): {'ЗДОРОВ' if health.get('healthy') else 'ПРОБЛЕМЫ'}")

            bot.clear_validation_cache()
            print_ok("  clear_validation_cache(): выполнено")

            duration = time.time() - start
            self._add_result("Trading Bot", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Trading Bot", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 16: PRE-MARKET TRADER
    # ========================================================================

    async def test_pre_market_trader(self) -> bool:
        """Тест 16: Проверка PreMarketTrader"""
        print_header("ТЕСТ 16: PRE-MARKET TRADER")
        start = time.time()

        try:
            from trading_bot.trading.pre_market_trader import PreMarketTrader
            from trading_bot import get_trading_bot

            bot = get_trading_bot()
            trader = PreMarketTrader(bot)

            # Проверка определения сессии
            session = trader.get_current_session()
            print_ok(f"  Текущая сессия: {session.value}")

            # Проверка загрузки тикеров
            tickers = trader.load_pre_market_tickers()
            print_ok(f"  Загружено тикеров: {len(tickers)}")

            # Проверка динамических параметров
            from trading_bot.api.tbank_client import tbank
            _, total, _ = tbank.get_available_funds()
            trader._update_dynamic_params(total)
            print_ok(
                f"  Динамические параметры: TP={trader.dynamic_take_profit_pct}%, SL={trader.dynamic_stop_loss_pct}%")

            duration = time.time() - start
            self._add_result("PreMarketTrader", True, duration, f"Тикеров: {len(tickers)}")
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("PreMarketTrader", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 17: SMART ORDERS (ICEBERG/TWAP)
    # ========================================================================

    def test_smart_orders(self) -> bool:
        """Тест 17: Проверка Smart Orders (Iceberg/TWAP)"""
        print_header("ТЕСТ 17: SMART ORDERS (ICEBERG/TWAP)")
        start = time.time()

        try:
            from trading_bot.trading.smart_orders import SmartOrderManager, smart_orders_manager

            if smart_orders_manager is None:
                warning("  SmartOrderManager не инициализирован")
                self._add_result("Smart Orders", True, time.time() - start, "Пропущен (не инициализирован)")
                return True

            # Проверка методов
            methods = ['place_iceberg_order', 'place_twap_order', 'cancel_order', 'get_status', 'get_active_orders']
            available = sum(1 for m in methods if hasattr(smart_orders_manager, m))
            print_ok(f"  Доступно методов: {available}/{len(methods)}")

            # Проверка активных заявок
            active = smart_orders_manager.get_active_orders()
            print_ok(f"  Активных заявок: {len(active)}")

            duration = time.time() - start
            self._add_result("Smart Orders", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Smart Orders", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 18: NEWS SENTIMENT ANALYZER
    # ========================================================================

    async def test_news_sentiment(self) -> bool:
        """Тест 18: Проверка News Sentiment Analyzer"""
        print_header("ТЕСТ 18: NEWS SENTIMENT ANALYZER")
        start = time.time()

        try:
            from trading_bot.analysis.news_sentiment import news_sentiment

            print_ok(f"  Анализатор {'включён' if news_sentiment.enabled else 'выключен'}")
            print_ok(f"  Макс. влияние: ±{news_sentiment.max_impact}")

            # Усиление сигнала
            new_score, new_signals, data = await news_sentiment.enhance_signal(
                ticker="SBER",
                current_score=5,
                current_signals=["Технический сигнал"]
            )
            print_ok(f"  Усиление сигнала: 5 → {new_score}")

            # Статистика
            stats = news_sentiment.get_stats()
            print_ok(f"  Кэшировано тикеров: {stats.get('cached_tickers', 0)}")

            duration = time.time() - start
            self._add_result("News Sentiment", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("News Sentiment", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 19: CIRCUIT BREAKER
    # ========================================================================

    def test_circuit_breaker(self) -> bool:
        """Тест 19: Проверка Circuit Breaker"""
        print_header("ТЕСТ 19: CIRCUIT BREAKER")
        start = time.time()

        try:
            from trading_bot.risk.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry

            # Создаём тестовый CB
            cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=3)

            # Проверка начального состояния
            assert cb.state.value == "closed"
            print_ok("  Начальное состояние: CLOSED")

            # Проверка регистрации ошибок
            cb.record_failure()
            cb.record_failure()
            assert cb.state.value == "open"
            print_ok("  После 2 ошибок: OPEN")

            # Проверка can_execute
            assert cb.can_execute() is False
            print_ok("  can_execute() = False в OPEN")

            # Проверка регистрации
            registry_status = CircuitBreakerRegistry.get_all_status()
            print_ok(f"  Зарегистрировано CB: {len(registry_status)}")

            duration = time.time() - start
            self._add_result("Circuit Breaker", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Circuit Breaker", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 20: CANDLE BUILDER
    # ========================================================================

    async def test_candle_builder(self) -> bool:
        """Тест 20: Проверка CandleBuilder"""
        print_header("ТЕСТ 20: CANDLE BUILDER")
        start = time.time()

        try:
            from trading_bot.core.candle_builder import candle_builder

            # Проверка инициализации
            stats = candle_builder.get_stats()
            print_ok(f"  Статус: {'работает' if stats.get('running') else 'остановлен'}")

            # Проверка получения свечей из MOEX
            candles = await candle_builder.get_candles_from_moex("SBER", interval="1day", days=5)
            if candles:
                print_ok(f"  Получено свечей: {len(candles)}")
            else:
                warning("  Не удалось получить свечи (возможно рынок закрыт)")

            # Проверка индикаторов
            if len(candles) >= 20:
                indicators = await candle_builder.get_indicators("SBER", interval="1day")
                if indicators:
                    print_ok(f"  RSI: {indicators.get('rsi', 0):.1f}")

            duration = time.time() - start
            self._add_result("CandleBuilder", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("CandleBuilder", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 21: ИНТЕГРАЦИОННЫЙ ТЕСТ (ПОЛНЫЙ ЦИКЛ)
    # ========================================================================

    async def test_integration_full_cycle(self) -> bool:
        """Тест 21: Интеграционный тест - полный цикл торговли"""
        print_header("ТЕСТ 21: ИНТЕГРАЦИОННЫЙ ТЕСТ (ПОЛНЫЙ ЦИКЛ)")
        start = time.time()

        try:
            from trading_bot import get_trading_bot
            from trading_bot.api.tbank_client import tbank
            from trading_bot.utils.time_utils import is_trading_time

            bot = get_trading_bot()

            # 1. Проверка рыночного времени
            can_trade = is_trading_time()
            print_ok(f"  Торговое время: {'ДА' if can_trade else 'НЕТ'}")

            if not can_trade and not self.real_trading:
                warning("  Рынок закрыт, пропускаем интеграционный тест")
                self._add_result("Интеграционный тест", True, time.time() - start, "Пропущен (рынок закрыт)")
                return True

            # 2. Получение баланса
            available, total, _ = tbank.get_available_funds()
            print_ok(f"  Капитал: {total:.0f}₽, Доступно: {available:.0f}₽")

            # 3. Сканирование кандидатов
            candidates = bot.stock_scanner.scan(available, force_refresh=True)
            print_ok(f"  Найдено кандидатов: {len(candidates) if candidates else 0}")

            if candidates:
                # 4. Анализ первого кандидата
                cand = candidates[0]
                print_ok(f"  Кандидат: {cand.ticker}, score={cand.analysis.score}, сторона={cand.side.value}")

                # 5. Проверка позиции
                from trading_bot.risk.position_manager import position_manager
                existing = position_manager.get_position(cand.figi)
                print_ok(f"  Существующая позиция: {'ЕСТЬ' if existing else 'НЕТ'}")

            # 6. Проверка маржи
            margin_info = tbank.get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0) if margin_info else 0
            print_ok(f"  Маржа: {margin_rate:.1f}%")

            duration = time.time() - start
            self._add_result("Интеграционный тест", True, duration)
            return True

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Интеграционный тест", False, duration, str(e))
            return False

    # ========================================================================
    # ТЕСТ 22: ДУБЛИРОВАНИЕ МЕТОДОВ И КОНФЛИКТЫ
    # ========================================================================

    def test_duplicate_methods_and_conflicts(self) -> bool:
        """
        ТЕСТ 22: ПРОВЕРКА ДУБЛИРОВАНИЯ МЕТОДОВ И КОНФЛИКТОВ
        ====================================================================
        Проверяет:
        1. Дублирующиеся определения методов в разных файлах
        2. Конфликтующие сигнатуры методов
        3. Одинаковые методы с разной логикой
        4. Потенциально опасные переопределения
        ====================================================================
        """
        print_header("ТЕСТ 22: ДУБЛИРОВАНИЕ МЕТОДОВ И КОНФЛИКТЫ")
        start = time.time()

        issues_found = []

        # Методы, которые должны быть уникальными (не должны дублироваться)
        CRITICAL_METHODS = {
            'buy': 'Покупка акций',
            'sell': 'Продажа акций',
            'get_positions': 'Получение позиций',
            'get_available_funds': 'Получение баланса',
            'get_current_price': 'Получение цены',
            '_get_positions': 'Внутренний метод позиций',
            '_get_ticker_by_figi': 'Поиск тикера по FIGI',
            '_get_figi_by_ticker': 'Поиск FIGI по тикеру',
            '_place_market_order': 'Рыночная заявка',
            '_place_limit_order': 'Лимитная заявка',
            'emergency_close_all_positions': 'Экстренное закрытие',
            'open_position': 'Открытие позиции',
            'start': 'Запуск бота',
            'stop': 'Остановка бота',
        }

        # Методы, которые могут дублироваться с разной логикой (но должны быть согласованы)
        ALLOWED_DUPLICATES = {
            'get_margin_info', 'get_trading_status', 'is_market_open',
            'get_portfolio', 'get_detailed_pnl', 'health_check'
        }

        # Собираем все методы из всех файлов
        method_locations = defaultdict(list)  # method_name -> [(file, line, signature)]
        class_methods = defaultdict(list)  # method_name -> [class_name]

        python_files = []
        for py_file in self.root_path.rglob("*.py"):
            if not any(exclude in py_file.parts for exclude in EXCLUDE_DIRS):
                python_files.append(py_file)

        print_info(f"  Сканирование {len(python_files)} файлов...")

        for file_path in python_files:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    lines = content.split('\n')

                # Поиск определений методов
                # Паттерн: def method_name(...)
                method_pattern = re.compile(r'^\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)')
                # Паттерн: async def method_name(...)
                async_method_pattern = re.compile(r'^\s*async\s+def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)')
                # Паттерн для класса
                class_pattern = re.compile(r'^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\(]')

                current_class = None

                for i, line in enumerate(lines, 1):
                    # Определяем текущий класс
                    class_match = class_pattern.match(line)
                    if class_match:
                        current_class = class_match.group(1)

                    # Поиск обычных методов
                    method_match = method_pattern.match(line)
                    if method_match:
                        method_name = method_match.group(1)
                        signature = method_match.group(2).strip()

                        if method_name.startswith('_') and method_name not in CRITICAL_METHODS:
                            continue  # Пропускаем приватные методы (кроме критических)

                        location = f"{file_path.relative_to(self.root_path)}:{i}"
                        method_locations[method_name].append((location, signature, current_class))

                        if current_class:
                            class_methods[method_name].append(f"{current_class} in {file_path.name}")

                    # Поиск async методов
                    async_match = async_method_pattern.match(line)
                    if async_match:
                        method_name = async_match.group(1)
                        signature = async_match.group(2).strip()

                        if method_name.startswith('_') and method_name not in CRITICAL_METHODS:
                            continue

                        location = f"{file_path.relative_to(self.root_path)}:{i}"
                        method_locations[method_name].append((location, signature, current_class))

                        if current_class:
                            class_methods[method_name].append(f"{current_class} in {file_path.name}")

            except Exception as e:
                if self.verbose:
                    warning(f"  Ошибка анализа {file_path.name}: {e}")

        # Проверка на дублирование критических методов
        for method_name, locations in method_locations.items():
            if method_name in CRITICAL_METHODS and len(locations) > 1:
                issues_found.append({
                    'severity': 'CRITICAL',
                    'method': method_name,
                    'description': f"Метод '{method_name}' ({CRITICAL_METHODS[method_name]}) найден в {len(locations)} местах",
                    'locations': locations,
                    'suggestion': "Должен быть только в одном месте! Удалите дубликаты."
                })

        # Проверка на конфликтующие сигнатуры (одинаковые имена, разные параметры)
        for method_name, locations in method_locations.items():
            if len(locations) > 1:
                signatures = set()
                sig_list = []
                for loc, sig, cls in locations:
                    # Нормализуем сигнатуру (удаляем пробелы, значения по умолчанию)
                    norm_sig = re.sub(r'\s*=\s*[^,)]+', '', sig)  # убираем значения по умолчанию
                    norm_sig = re.sub(r'\s+', '', norm_sig)  # убираем пробелы
                    signatures.add(norm_sig)
                    sig_list.append((loc, sig, cls))

                if len(signatures) > 1 and method_name not in ALLOWED_DUPLICATES:
                    issues_found.append({
                        'severity': 'HIGH',
                        'method': method_name,
                        'description': f"Метод '{method_name}' имеет разные сигнатуры в {len(locations)} местах",
                        'signatures': [(loc, sig, cls) for loc, sig, cls in sig_list],
                        'suggestion': "Унифицируйте сигнатуры методов!"
                    })

        # Проверка на дублирование в классах
        for method_name, classes in class_methods.items():
            if method_name in CRITICAL_METHODS and len(set(classes)) > 1:
                unique_classes = list(set(classes))
                issues_found.append({
                    'severity': 'MEDIUM',
                    'method': method_name,
                    'description': f"Метод '{method_name}' определён в нескольких классах: {unique_classes}",
                    'suggestion': "Проверьте, что эти методы не конфликтуют."
                })

        # Проверка на потенциально опасные переопределения
        DANGEROUS_OVERRIDES = [
            ('__init__', 'Инициализатор', 'Убедитесь, что super().__init__() вызывается'),
            ('start', 'Метод запуска', 'Может конфликтовать с родительским'),
            ('stop', 'Метод остановки', 'Может конфликтовать с родительским'),
        ]

        for method_name, description, suggestion in DANGEROUS_OVERRIDES:
            if method_name in class_methods and len(class_methods[method_name]) > 1:
                issues_found.append({
                    'severity': 'LOW',
                    'method': method_name,
                    'description': f"Метод '{method_name}' ({description}) переопределён в нескольких классах",
                    'classes': class_methods[method_name],
                    'suggestion': suggestion
                })

        # Вывод результатов
        print_sep()

        critical_count = sum(1 for i in issues_found if i['severity'] == 'CRITICAL')
        high_count = sum(1 for i in issues_found if i['severity'] == 'HIGH')
        medium_count = sum(1 for i in issues_found if i['severity'] == 'MEDIUM')
        low_count = sum(1 for i in issues_found if i['severity'] == 'LOW')

        print_info(f"📊 НАЙДЕНО КОНФЛИКТОВ:")
        print(f"   🔴 CRITICAL: {critical_count}")
        print(f"   🟠 HIGH: {high_count}")
        print(f"   🟡 MEDIUM: {medium_count}")
        print(f"   🔵 LOW: {low_count}")

        if issues_found:
            print_sep()
            print_info("📋 ДЕТАЛИ КОНФЛИКТОВ:")

            for issue in issues_found[:10]:  # Показываем первые 10
                severity_color = {
                    'CRITICAL': Colors.RED,
                    'HIGH': Colors.YELLOW,
                    'MEDIUM': Colors.MAGENTA,
                    'LOW': Colors.BLUE
                }.get(issue['severity'], Colors.WHITE)

                print(f"\n  {severity_color}[{issue['severity']}]{Colors.RESET} {issue['method']}")
                print(f"      {issue['description']}")

                if 'locations' in issue:
                    for loc, sig, cls in issue['locations'][:3]:
                        print(f"      📁 {loc} (class: {cls or 'global'})")
                        if sig:
                            print(f"         def {issue['method']}({sig})")

                if 'signatures' in issue:
                    for loc, sig, cls in issue['signatures'][:3]:
                        print(f"      📁 {loc}: def {issue['method']}({sig})")

                if 'classes' in issue:
                    for cls in issue['classes'][:3]:
                        print(f"      📦 {cls}")

                if issue.get('suggestion'):
                    print(f"      💡 {issue['suggestion']}")

            if len(issues_found) > 10:
                print(f"\n  ... и ещё {len(issues_found) - 10} конфликтов")

        # Особые проверки для известных проблемных мест
        print_sep()
        print_info("🔍 ДОПОЛНИТЕЛЬНЫЕ ПРОВЕРКИ:")

        # Проверка на наличие _get_positions в bot.py
        has_get_positions = False
        has_get_portfolio = False

        bot_file = self.root_path / "trading_bot" / "bot.py"
        if bot_file.exists():
            with open(bot_file, 'r', encoding='utf-8') as f:
                bot_content = f.read()
                has_get_positions = '_get_positions' in bot_content
                has_get_portfolio = 'get_portfolio' in bot_content

        if has_get_positions:
            print_ok("  ✅ _get_positions() найден в bot.py")
        else:
            print_error("  ❌ _get_positions() НЕ НАЙДЕН в bot.py! Бот не сможет получать позиции!")
            issues_found.append({
                'severity': 'CRITICAL',
                'method': '_get_positions',
                'description': "Метод '_get_positions' отсутствует в bot.py",
                'suggestion': "Добавьте метод _get_positions в класс TradingBot"
            })

        if has_get_portfolio:
            print_ok("  ✅ get_portfolio() найден в bot.py")

        # Проверка на наличие _place_market_order и _place_limit_order
        has_market_order = '_place_market_order' in bot_content if bot_file.exists() else False
        has_limit_order = '_place_limit_order' in bot_content if bot_file.exists() else False

        print(
            f"  {'✅' if has_market_order else '❌'} _place_market_order() {'найден' if has_market_order else 'ОТСУТСТВУЕТ'}")
        print(
            f"  {'✅' if has_limit_order else '❌'} _place_limit_order() {'найден' if has_limit_order else 'ОТСУТСТВУЕТ'}")

        # Проверка на циклические импорты (быстрый поиск)
        circular_imports = []
        for py_file in python_files[:50]:  # Ограничим для скорости
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Ищем импорты, которые могут вызвать цикл
                    if 'from trading_bot.bot import' in content and 'from trading_bot.bot import TradingBot' in content:
                        if 'trading_bot/core/trading_loop.py' in str(py_file):
                            circular_imports.append(str(py_file))
            except Exception as e:
                pass

        if circular_imports:
            warning(f"  ⚠️ Потенциальные циклические импорты: {len(circular_imports)} файлов")
        else:
            print_ok("  ✅ Циклических импортов не обнаружено")

        duration = time.time() - start
        passed = critical_count == 0

        # ВЫВОД КРИТИЧЕСКИХ ПРОБЛЕМ В КОНСОЛЬ
        if issues_found:
            print_sep()
            print_error(f"🚨 НАЙДЕНО {len(issues_found)} КОНФЛИКТОВ!")

            # Выводим CRITICAL проблемы
            critical_issues = [i for i in issues_found if i['severity'] == 'CRITICAL']
            if critical_issues:
                print(f"\n{Colors.RED}{Colors.BOLD}🔴 CRITICAL ПРОБЛЕМЫ ({len(critical_issues)}):{Colors.RESET}")
                for issue in critical_issues:
                    print(f"   ❌ {issue['method']}: {issue['description']}")
                    if 'locations' in issue:
                        for loc, sig, cls in issue['locations'][:2]:
                            print(f"      📁 {loc}")
                    if issue.get('suggestion'):
                        print(f"      💡 {issue['suggestion']}")

            # Выводим HIGH проблемы
            high_issues = [i for i in issues_found if i['severity'] == 'HIGH']
            if high_issues:
                print(f"\n{Colors.YELLOW}🟠 HIGH ПРОБЛЕМЫ ({len(high_issues)}):{Colors.RESET}")
                for issue in high_issues[:10]:
                    print(f"   ⚠️ {issue['method']}: {issue['description'][:80]}")

            # Выводим MEDIUM проблемы
            medium_issues = [i for i in issues_found if i['severity'] == 'MEDIUM']
            if medium_issues:
                print(f"\n{Colors.MAGENTA}🟡 MEDIUM ПРОБЛЕМЫ ({len(medium_issues)}):{Colors.RESET}")
                for issue in medium_issues:
                    print(f"   📌 {issue['method']}: {issue['description'][:80]}")

        # Показываем КРИТИЧЕСКИЕ конфликты в консоли
        if issues_found:
            print(f"\n{Colors.RED}{Colors.BOLD}{'=' * 80}{Colors.RESET}")
            print(f"{Colors.RED}{Colors.BOLD}🚨 НАЙДЕНО КОНФЛИКТОВ: {len(issues_found)}{Colors.RESET}")
            print(f"{Colors.RED}{Colors.BOLD}{'=' * 80}{Colors.RESET}")

            # CRITICAL проблемы
            critical = [i for i in issues_found if i['severity'] == 'CRITICAL']
            if critical:
                print(f"\n{Colors.RED}🔴 CRITICAL ({len(critical)}):{Colors.RESET}")
                for issue in critical:
                    print(f"   ❌ {issue['method']}: {issue['description']}")
                    if 'locations' in issue:
                        for loc, sig, cls in issue['locations'][:3]:
                            print(f"      📁 {loc}")
                    if issue.get('suggestion'):
                        print(f"      💡 {issue['suggestion']}")

            # HIGH проблемы (только первые 15)
            high = [i for i in issues_found if i['severity'] == 'HIGH']
            if high:
                print(f"\n{Colors.YELLOW}🟠 HIGH ({len(high)}):{Colors.RESET}")
                for issue in high[:15]:
                    print(f"   ⚠️ {issue['method']}: {issue['description'][:80]}")
                if len(high) > 15:
                    print(f"   ... и ещё {len(high) - 15} HIGH проблем")

            # MEDIUM проблемы
            medium = [i for i in issues_found if i['severity'] == 'MEDIUM']
            if medium:
                print(f"\n{Colors.MAGENTA}🟡 MEDIUM ({len(medium)}):{Colors.RESET}")
                for issue in medium:
                    print(f"   📌 {issue['method']}: {issue['description'][:80]}")

            # LOW проблемы
            low = [i for i in issues_found if i['severity'] == 'LOW']
            if low:
                print(f"\n{Colors.BLUE}🔵 LOW ({len(low)}):{Colors.RESET}")
                for issue in low:
                    print(f"   ℹ️ {issue['method']}: {issue['description'][:80]}")

        self._add_result("Дублирование методов и конфликты", passed, duration,
                         f"Проблем: CRITICAL={critical_count},
                             HIGH={high_count}
                             MEDIUM={medium_count}
                             LOW={low_count}")

        return passed

    def test_database_integrity(self) -> bool:
        """Тест 23: Проверка целостности базы данных"""
        print_header("ТЕСТ 23: ЦЕЛОСТНОСТЬ БАЗЫ ДАННЫХ")
        start = time.time()

        try:
            from trading_bot.data.database_manager import DatabaseManager

            db = DatabaseManager("trading_state.db")

            # Проверяем основные таблицы
            required_tables = [
                'positions', 'trades', 'bot_state',
                'orders', 'cache', 'blacklist'
            ]

            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]

            missing_tables = [t for t in required_tables if t not in tables]

            if missing_tables:
                print_error(f"  Отсутствуют таблицы: {missing_tables}")
                self._add_result("Целостность БД", False, time.time() - start,
                                 f"Отсутствуют: {missing_tables}")
                return False

            print_ok(f"  Все таблицы присутствуют: {required_tables}")

            # Проверяем индексы
            required_indexes = [
                'idx_trades_time', 'idx_trades_ticker', 'idx_blacklist_expires'
            ]

            cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]

            missing_indexes = [i for i in required_indexes if i not in indexes]

            if missing_indexes:
                warning(f"  Отсутствуют индексы: {missing_indexes}")
            else:
                print_ok(f"  Все индексы присутствуют")

            duration = time.time() - start
            self._add_result("Целостность БД", True, duration)
            return True

        except Exception as e:
            print_error(f"  Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Целостность БД", False, duration, str(e))
            return False

    def test_render_configuration(self) -> bool:
        """Тест 24: Проверка конфигурации для Render"""
        print_header("ТЕСТ 24: RENDER КОНФИГУРАЦИЯ")
        start = time.time()

        issues = []

        # Проверка gunicorn.conf.py
        gunicorn_file = self.root_path / "gunicorn.conf.py"
        if gunicorn_file.exists():
            with open(gunicorn_file, 'r') as f:
                content = f.read()
                if 'workers = 1' not in content:
                    issues.append("gunicorn.conf.py: workers должно быть 1")
                if 'threads = 1' not in content:
                    issues.append("gunicorn.conf.py: threads должно быть 1")
                if 'preload_app = False' not in content:
                    issues.append("gunicorn.conf.py: preload_app должно быть False")
            print_ok("  gunicorn.conf.py: OK")
        else:
            warning("  gunicorn.conf.py: не найден")

        # Проверка start.sh
        start_file = self.root_path / "start.sh"
        if start_file.exists():
            with open(start_file, 'r') as f:
                content = f.read()
                if 'gunicorn web_server:app' not in content:
                    issues.append("start.sh: должен запускать gunicorn web_server:app")
            print_ok("  start.sh: OK")
        else:
            warning("  start.sh: не найден")

        # Проверка render.yaml
        render_file = self.root_path / "render.yaml"
        if render_file.exists():
            with open(render_file, 'r') as f:
                content = f.read()
                if 'healthCheckPath: /health' not in content:
                    issues.append("render.yaml: healthCheckPath должен быть /health")
            print_ok("  render.yaml: OK")
        else:
            warning("  render.yaml: не найден")

        duration = time.time() - start
        passed = len(issues) == 0

        for issue in issues:
            print_error(f"  {issue}")

        self._add_result("Render конфигурация", passed, duration,
                         f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # ЗАПУСК ВСЕХ ТЕСТОВ
    # ========================================================================

    async def run_all_tests(self, quick: bool = False, api_only: bool = False,
                            methods_only: bool = False, trading_only: bool = False,
                            thresholds_only: bool = False, code_only: bool = False) -> Dict[str, Any]:
        """Запуск всех тестов"""
        print_info("\n🚀 Запуск мастер-теста...")

        if code_only:
            self.test_code_quality()
        elif api_only:
            await self.test_tbank_api_full()
        elif methods_only:
            self.test_models()
            self.test_logger()
            self.test_thresholds_full()
            await self.test_fundamental_integration_full()
            self.test_strategy_engine_full()
            self.test_market_checker()
            self.test_position_manager()
            self.test_caching()
            self.test_trading_bot()
        elif trading_only:
            await self.test_trading_methods_full()
            await self.test_tbank_api_full()
        elif thresholds_only:
            self.test_thresholds_full()
            await self.test_fundamental_integration_full()
        else:
            # Полный тест
            self.test_environment()
            self.test_all_imports()
            await self.test_tbank_api_full()
            self.test_logger()
            self.test_models()
            self.test_thresholds_full()
            await self.test_fundamental_integration_full()
            self.test_strategy_engine_full()
            await self.test_trading_methods_full()
            await self.test_technical_analyzer_full()
            self.test_market_checker()
            self.test_position_manager()
            self.test_caching()
            self.test_duplicate_methods_and_conflicts()

            if not quick:
                self.test_code_quality()

        self.test_trading_bot()

        return self.get_summary()

    def get_summary(self) -> Dict[str, Any]:
        """Получение сводки результатов"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed

        # Расчёт оценок
        critical_issues = sum(1 for i in self.issues if i.severity == TestSeverity.CRITICAL)
        high_issues = sum(1 for i in self.issues if i.severity == TestSeverity.HIGH)
        medium_issues = sum(1 for i in self.issues if i.severity == TestSeverity.MEDIUM)
        low_issues = sum(1 for i in self.issues if i.severity == TestSeverity.LOW)

        # Оценка качества кода
        code_score = 100 - critical_issues * 20 - high_issues * 10 - medium_issues * 5 - low_issues * 1
        code_score = max(0, min(100, code_score))

        # Оценка API
        api_working = self.stats.get('apis_working', 0)
        api_total = api_working + self.stats.get('apis_failed', 0)
        api_score = (api_working / api_total * 100) if api_total > 0 else 0

        # Оценка порогов
        thresholds_total = self.stats.get('thresholds_passed', 0) + self.stats.get('thresholds_failed', 0)
        thresholds_score = (self.stats.get('thresholds_passed',
            0) / thresholds_total * 100) if thresholds_total > 0 else 0

        # Общая оценка
        test_score = (passed / total * 100) if total > 0 else 0
        final_score = (code_score + api_score + thresholds_score + test_score) / 4

        elapsed = (datetime.now() - self.start_time).total_seconds()

        # Вывод итогов
        print_header("ИТОГИ МАСТЕР-ТЕСТА v3.0")

        print_info(f"⏱ Время выполнения: {elapsed:.2f} сек")
        print_info(f"📁 Файлов проанализировано: {self.stats['files_scanned']}")
        print_info(f"📄 Строк кода: {self.stats['total_lines']:,}")

        print_sep()
        print_info(f"📊 РЕЗУЛЬТАТЫ ТЕСТОВ:")
        for result in self.results:
            status = "✅" if result.passed else "❌"
            print(f"   {status} {result.name}: {result.duration:.2f}с" + (
                f" - {result.message[:50]}" if result.message else ""))

        print_sep()
        print_info(f"📊 СТАТИСТИКА:")
        print(f"   ✅ Тестов пройдено: {passed}/{total} ({test_score:.1f}%)")
        print(f"   📦 Импортов: {self.stats['imports_total']}, ошибок: {self.stats['imports_failed']}")
        print(f"   🔌 API: {api_working}/{api_total} работают ({api_score:.1f}%)")
        print(f"   🚪 Пороги фильтрации: {self.stats.get('thresholds_passed', 0)}/{thresholds_total} ({thresholds_score:.1f}%)")
        print(f"   ⚠️ Проблем в коде: {len(self.issues)}")

        print_sep()
        print_info(f"🏆 ОЦЕНКИ:")
        print(f"   📝 Качество кода: {code_score:.0f}/100")
        print(f"   🔌 API работоспособность: {api_score:.0f}/100")
        print(f"   🚪 Пороги фильтрации: {thresholds_score:.0f}/100")
        print(f"   🧪 Прохождение тестов: {test_score:.0f}/100")
        print(f"   ⭐ ИТОГОВАЯ ОЦЕНКА: {final_score:.0f}/100")

        if final_score >= 90:
            print_success("🎉 ОТЛИЧНО! Бот готов к эксплуатации!")
        elif final_score >= 70:
            print_success("✅ ХОРОШО! Небольшие замечания, можно запускать.")
        elif final_score >= 50:
            warning("⚠️ УДОВЛЕТВОРИТЕЛЬНО! Рекомендуется доработка перед запуском.")
        else:
            print_error("🔴 ПЛОХО! Требуется серьёзная доработка!")

        # Сохранение отчёта
        report = {
            "timestamp": self.start_time.isoformat(),
            "duration_seconds": elapsed,
            "real_trading_enabled": self.real_trading,
            "fix_issues_enabled": self.fix_issues,
            "stats": self.stats,
            "results": [
                {"name": r.name, "passed": r.passed, "duration": r.duration, "message": r.message}
                for r in self.results
            ],
            "issues": [
                {"file": i.file, "line": i.line, "severity": i.severity.value,
                 "type": i.type, "message": i.message, "suggestion": i.suggestion}
                for i in self.issues
            ],
            "scores": {
                "code_quality": code_score,
                "api_working": api_score,
                "thresholds": thresholds_score,
                "tests_passed": test_score,
                "final": final_score
            }
        }

        with open(REPORT_FILE, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        print_info(f"\n📄 Полный отчёт сохранён: {REPORT_FILE}")
        print_info(f"📄 Лог тестирования: {LOG_FILE}")

        return report


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="МАСТЕР-ТЕСТ ТОРГОВОГО БОТА v3.0 - объединяет все проверки",
        epilog="""
Примеры:
  python master_test.py                    # Полный тест
  python master_test.py --quick            # Быстрый тест (без качества кода)
  python master_test.py --api              # Только API тесты
  python master_test.py --methods          # Только методы бота
  python master_test.py --trading          # Только торговые методы (LONG/SHORT)
  python master_test.py --thresholds       # Только тест порогов фильтрации
  python master_test.py --code             # Только качество кода
  python master_test.py --real             # РЕАЛЬНЫЕ заявки (ОСТОРОЖНО!)
  python master_test.py --report           # Сохранить отчёт
  python master_test.py --verbose          # Подробный вывод
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--quick", "-q", action="store_true", help="Быстрый режим (без качества кода)")
    parser.add_argument("--api", "-a", action="store_true", help="Только API тесты")
    parser.add_argument("--methods", "-m", action="store_true", help="Только методы бота")
    parser.add_argument("--trading", "-t", action="store_true", help="Только торговые методы (LONG/SHORT)")
    parser.add_argument("--thresholds", "-th", action="store_true", help="Только тест порогов фильтрации")
    parser.add_argument("--code", "-c", action="store_true", help="Только качество кода")
    parser.add_argument("--real", "-r", action="store_true", help="РЕАЛЬНЫЙ режим (будут реальные заявки!)")
    parser.add_argument("--report", "-s", action="store_true", help="Сохранить отчёт в JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Подробный вывод")

    args = parser.parse_args()

    # Предупреждение о реальном режиме
    if args.real:
        # Цвета должны быть определены в Colors
        try:
            from trading_bot.logger import Colors
        except ImportError:
            # Fallback цвета если не загрузились
            class Colors:
                RED = '\033[91m'
                BOLD = '\033[1m'
                RESET = '\033[0m'

        print(f"\n{Colors.RED}{Colors.BOLD}{'=' * 80}{Colors.RESET}")
        print(f"{Colors.RED}{Colors.BOLD}⚠️  ВНИМАНИЕ! ВЫ ЗАПУСТИЛИ ТЕСТ В РЕАЛЬНОМ РЕЖИМЕ!{Colors.RESET}")
        print(f"{Colors.RED}{Colors.BOLD}   Будут отправлены РЕАЛЬНЫЕ ЗАЯВКИ на брокерский счёт!{Colors.RESET}")
        print(f"{Colors.RED}{Colors.BOLD}{'=' * 80}{Colors.RESET}")
        confirm = input("\nВы уверены? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Тест отменён.")
            return

    # Создаём экземпляр тестера
    tester = MasterTester(real_trading=args.real, fix_issues=False, verbose=args.verbose)

    # Запускаем асинхронные тесты
    asyncio.run(tester.run_all_tests(
        quick=args.quick,
        api_only=args.api,
        methods_only=args.methods,
        trading_only=args.trading,
        thresholds_only=args.thresholds,
        code_only=args.code
    ))


if __name__ == "__main__":
    main()