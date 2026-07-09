#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
🏆 МАСТЕР-ДИАГНОСТИКА + ИСПРАВЛЕНИЕ ТОРГОВОГО БОТА v4.0
================================================================================

ОБЪЕДИНЯЕТ:
- Полную диагностику всех компонентов
- Проверку всех импортов
- Проверку API Т-Банка
- Проверку торговых методов
- Проверку качества кода
- АВТОМАТИЧЕСКОЕ ИСПРАВЛЕНИЕ найденных проблем

ЗАПУСК:
    python fix_bot.py              # Диагностика + исправление
    python fix_bot.py --check      # Только диагностика (без исправлений)
    python fix_bot.py --fix        # Только исправление (без диагностики)
    python fix_bot.py --verbose    # Подробный вывод

РЕЖИМЫ:
    --check   - Только проверка, ничего не меняет
    --fix     - Только исправление (без проверки)
    --verbose - Подробный вывод всех действий
================================================================================
"""

import os
import sys
import re
import ast
import json
import time
import shutil
import sqlite3
import asyncio
import argparse
import importlib
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor


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


def print_warn(text: str): print(f"{Colors.YELLOW}⚠️ {text}{Colors.RESET}")


def print_error(text: str): print(f"{Colors.RED}❌ {text}{Colors.RESET}")


def print_info(text: str): print(f"{Colors.CYAN}ℹ️ {text}{Colors.RESET}")


def print_debug(text: str): print(f"{Colors.DIM}🔍 {text}{Colors.RESET}")


def print_header(text: str, char: str = "="):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{char * 80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.MAGENTA}🔍 {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{char * 80}{Colors.RESET}")


def print_sep(): print(f"{Colors.DIM}{'─' * 80}{Colors.RESET}")


# ============================================================================
# КОНСТАНТЫ
# ============================================================================

PROJECT_ROOT = Path(__file__).parent
LOG_FILE = "fix_bot.log"
BACKUP_DIR = PROJECT_ROOT / ".backup_fix"
TIMEOUT_API = 10

EXCLUDE_DIRS = {
    '.venv', 'venv', '__pycache__', '.git', 'logs', 'data', '.backup',
    'node_modules', 'backtest_results', 'optimization_results', 'models',
    'bert_training_data', '.idea', '.vscode', 'dist', 'build', '__pycache__',
    '.backup_fix'
}

# Критические методы, которые должны быть уникальными
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
    'validate_before_send': 'Валидация заявки',
    'send_order_with_confirmation': 'Отправка заявки',
    'close_position_with_retry': 'Закрытие позиции',
    '_place_market_order_impl': 'Реализация рыночной заявки',
    '_place_limit_order_with_fallback': 'Лимитная заявка с fallback',
}


# ============================================================================
# ДАТАКЛАССЫ
# ============================================================================

class TestSeverity(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class Issue:
    file: str
    line: int
    severity: TestSeverity
    type: str
    message: str
    suggestion: str = ""
    code_snippet: str = ""
    fixed: bool = False


@dataclass
class TestResult:
    name: str
    passed: bool
    duration: float
    message: str = ""
    issues: List[Issue] = field(default_factory=list)


# ============================================================================
# ОСНОВНОЙ КЛАСС
# ============================================================================

class BotFixer:
    """Мастер-диагностика и исправление бота"""

    def __init__(self, root_path: Path = None, auto_fix: bool = True, verbose: bool = False):
        self.root_path = root_path or PROJECT_ROOT
        self.auto_fix = auto_fix
        self.verbose = verbose
        self.start_time = datetime.now()
        self.results: List[TestResult] = []
        self.issues: List[Issue] = []
        self.fixed_files: List[str] = []
        self.stats = {
            'files_scanned': 0,
            'total_lines': 0,
            'issues_found': 0,
            'issues_fixed': 0,
            'imports_total': 0,
            'imports_failed': 0,
        }

        # Создаём бэкап директорию
        BACKUP_DIR.mkdir(exist_ok=True)

        # Очищаем лог
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(f"=== МАСТЕР-ДИАГНОСТИКА + ИСПРАВЛЕНИЕ v4.0 ===\n")
            f.write(f"Время: {self.start_time}\n")
            f.write(f"Автоисправление: {'ВКЛ' if auto_fix else 'ВЫКЛ'}\n")
            f.write("=" * 80 + "\n\n")

        self._log_start()

    def _log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] [{level}] {message}\n")

    def _log_start(self):
        print_header("МАСТЕР-ДИАГНОСТИКА + ИСПРАВЛЕНИЕ v4.0")
        print_info(f"Директория: {self.root_path}")
        print_info(f"Время: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print_info(f"Автоисправление: {'✅ ВКЛЮЧЕНО' if self.auto_fix else '❌ ВЫКЛЮЧЕНО'}")
        print_info(f"Подробный вывод: {'✅ ДА' if self.verbose else '❌ НЕТ'}")
        self._log(f"Запуск. Автоисправление: {self.auto_fix}", "INFO")

    def _add_issue(self, issue: Issue):
        self.issues.append(issue)
        self.stats['issues_found'] += 1
        self._log(f"[{issue.severity.value}] {issue.file}:{issue.line} - {issue.message}", "WARNING")
        if self.verbose:
            print_warn(f"{issue.file}:{issue.line} - {issue.message[:80]}")

    def _add_result(self, name: str, passed: bool, duration: float, message: str = ""):
        self.results.append(TestResult(name=name, passed=passed, duration=duration, message=message))
        if passed:
            print_ok(f"{name} ({duration:.2f}с)")
        else:
            print_error(f"{name} ({duration:.2f}с): {message}")
        self._log(f"{'✅' if passed else '❌'} {name}: {duration:.2f}с - {message}", "INFO" if passed else "ERROR")

    def _backup_file(self, file_path: Path) -> Path:
        """Создание бэкапа файла"""
        backup_path = BACKUP_DIR / file_path.relative_to(self.root_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, backup_path)
        if self.verbose:
            print_debug(f"Бэкап: {backup_path}")
        return backup_path

    # ========================================================================
    # 1. ПРОВЕРКА ИМПОРТОВ
    # ========================================================================

    def check_imports(self) -> bool:
        print_header("ПРОВЕРКА ИМПОРТОВ")
        start = time.time()

        modules = [
            ("trading_bot.config", "config"),
            ("trading_bot.logger", "bomb"),
            ("trading_bot.models", "StockAnalysis"),
            ("trading_bot.models", "OrderSide"),
            ("trading_bot.api.tbank_client", "tbank"),
            ("trading_bot.bot", "trading_bot"),
            ("trading_bot", "get_trading_bot"),
            ("trading_bot.risk.position_manager", "position_manager"),
            ("trading_bot.analysis.technical_analyzer", "analyzer"),
            ("trading_bot.analysis.market_analyzer", "market_analyzer"),
            ("trading_bot.analysis.strategy_engine", "StrategyEngine"),
            ("trading_bot.core.market_checker", "MarketChecker"),
            ("trading_bot.core.settings_manager", "settings_manager"),
            ("trading_bot.trading.position_sizer", "PositionSizer"),
            ("trading_bot.trading.position_opener", "PositionOpener"),
            ("trading_bot.trading.position_closer", "PositionCloser"),
            ("trading_bot.cache.cache_manager", "TTLCache"),
            ("trading_bot.utils.time_utils", "get_moscow_time"),
            ("trading_bot.utils.figi_resolver", "FigiResolver"),
        ]

        passed_count = 0
        failed = []

        for module_name, import_name in modules:
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, import_name):
                    if self.verbose:
                        print_ok(f"  {module_name}.{import_name}")
                    passed_count += 1
                else:
                    print_error(f"  {module_name}.{import_name} - атрибут не найден")
                    failed.append(f"{module_name}.{import_name}")
            except ImportError as e:
                print_error(f"  {module_name}: {e}")
                failed.append(f"{module_name}")
            except Exception as e:
                print_error(f"  {module_name}: {e}")
                failed.append(f"{module_name}")

        duration = time.time() - start
        passed = len(failed) == 0
        self.stats['imports_total'] = len(modules)
        self.stats['imports_failed'] = len(failed)
        self._add_result("Импорты модулей", passed, duration, f"Успешно: {passed_count}/{len(modules)}")
        return passed

    # ========================================================================
    # 2. ПРОВЕРКА API Т-БАНКА
    # ========================================================================

    async def check_tbank_api(self) -> bool:
        """Тест 3: Полная проверка T-Bank API"""
        print_header("ПРОВЕРКА API Т-БАНКА")
        start = time.time()

        try:
            from trading_bot.api.tbank_client import tbank
        except ImportError as e:
            self._add_result("API Т-Банка", False, time.time() - start, f"Не удалось импортировать: {e}")
            return False

        tests = []
        figi_sber = None

        # 3.1 Получение баланса
        print_info("1. get_available_funds()...")
        try:
            available, total, _ = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, tbank.get_available_funds),
                timeout=TIMEOUT_API
            )
            print_ok(f"  Доступно: {available:.2f}₽, Капитал: {total:.2f}₽")
            tests.append(True)
        except Exception as e:
            print_error(f"  Ошибка: {e}")
            tests.append(False)

        print_info("2. get_all_shares()...")
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

        if figi_sber:
            print_info("3. get_current_price()...")
            try:
                price = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, tbank.get_current_price, figi_sber),
                    timeout=TIMEOUT_API
                )
                if price and price > 0:
                    print_ok(f"  Цена SBER: {price:.2f}₽")
                    tests.append(True)
                else:
                    print_warn("  Цена не получена (возможно рынок закрыт)")
                    tests.append(True)
            except Exception as e:
                print_error(f"  Ошибка: {e}")
                tests.append(False)

        duration = time.time() - start
        passed = all(tests)
        self._add_result("API Т-Банка", passed, duration, f"Успешно: {sum(tests)}/{len(tests)}")
        return passed

    # ========================================================================
    # 3. ПРОВЕРКА ТОРГОВЫХ МЕТОДОВ
    # ========================================================================

    async def check_trading_methods(self) -> bool:
        """Тест 9: Полная проверка всех торговых методов"""
        print_header("ПРОВЕРКА ТОРГОВЫХ МЕТОДОВ")
        start = time.time()

        try:
            from trading_bot.api.tbank_client import tbank
            from trading_bot.config import config
            from trading_bot.risk.position_manager import position_manager

            methods = [
                'buy', 'sell', 'sell_short', 'place_limit_order',
                'get_positions', 'get_available_funds', 'get_current_price',
                'get_margin_info', 'get_trading_status', 'is_confirmation_required'
            ]

            available = []
            missing = []

            for method in methods:
                if hasattr(tbank, method):
                    available.append(method)
                    if self.verbose:
                        print_ok(f"  {method}()")
                else:
                    missing.append(method)
                    print_warn(f"  {method}() - отсутствует")

            print_ok(f"Доступно: {len(available)}/{len(methods)}")

            # Проверка SHORT
            margin_allowed, _ = tbank.check_margin_trading_allowed()
            print_ok(f"Маржинальная торговля: {'ДОСТУПНА' if margin_allowed else 'НЕ ДОСТУПНА'}")

            # Проверка позиций
            positions = position_manager.get_all_positions()
            print_ok(f"Открытых позиций: {len(positions)}")

            duration = time.time() - start
            passed = len(missing) == 0
            self._add_result("Торговые методы", passed, duration, f"Доступно: {len(available)}/{len(methods)}")
            return passed

        except Exception as e:
            print_error(f"Ошибка: {e}")
            duration = time.time() - start
            self._add_result("Торговые методы", False, duration, str(e))
            return False

    # ========================================================================
    # 4. ПРОВЕРКА КАЧЕСТВА КОДА
    # ========================================================================

    def check_code_quality(self) -> bool:
        print_header("ПРОВЕРКА КАЧЕСТВА КОДА")
        start = time.time()

        issues = []
        python_files = []

        for py_file in self.root_path.rglob("*.py"):
            if not any(exclude in py_file.parts for exclude in EXCLUDE_DIRS):
                python_files.append(py_file)

        print_info(f"Найдено Python файлов: {len(python_files)}")

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
                    issues.append(Issue(
                        file=str(file_path.relative_to(self.root_path)),
                        line=e.lineno or 0,
                        severity=TestSeverity.CRITICAL,
                        type="SYNTAX_ERROR",
                        message=str(e),
                        suggestion="Исправьте синтаксическую ошибку"
                    ))

                # Проверка длины строк
                for i, line in enumerate(lines, 1):
                    if len(line) > 120 and not line.strip().startswith('#'):
                        issues.append(Issue(
                            file=str(file_path.relative_to(self.root_path)),
                            line=i,
                            severity=TestSeverity.MEDIUM,
                            type="LONG_LINE",
                            message=f"Строка слишком длинная: {len(line)} символов",
                            suggestion="Разбейте строку на несколько"
                        ))
                        break

                # Проверка bare except
                if re.search(r'except\s*:(?![^\n]*Exception)', content):
                    issues.append(Issue(
                        file=str(file_path.relative_to(self.root_path)),
                        line=0,
                        severity=TestSeverity.MEDIUM,
                        type="BARE_EXCEPT",
                        message="Найден 'except Exception as e:' без указания исключения",
                        suggestion="Используйте 'except Exception as e:'"
                    ))

            except Exception as e:
                if self.verbose:
                    print_warn(f"  Ошибка анализа {file_path.name}: {e}")

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len([i for i in issues if i.severity == TestSeverity.CRITICAL]) == 0
        self._add_result("Качество кода", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 5. ПРОВЕРКА ДУБЛИРОВАНИЯ МЕТОДОВ
    # ========================================================================

    def check_duplicate_methods(self) -> bool:
        print_header("ПРОВЕРКА ДУБЛИРОВАНИЯ МЕТОДОВ")
        start = time.time()

        method_locations = defaultdict(list)
        issues = []

        python_files = []
        for py_file in self.root_path.rglob("*.py"):
            if not any(exclude in py_file.parts for exclude in EXCLUDE_DIRS):
                python_files.append(py_file)

        print_info(f"Сканирование {len(python_files)} файлов...")

        method_pattern = re.compile(r'^\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(')

        for file_path in python_files:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()

                for i, line in enumerate(lines, 1):
                    match = method_pattern.match(line)
                    if match:
                        method_name = match.group(1)
                        if method_name.startswith('_') and method_name not in CRITICAL_METHODS:
                            continue
                        location = f"{file_path.relative_to(self.root_path)}:{i}"
                        method_locations[method_name].append(location)

            except Exception as e:
                if self.verbose:
                    print_warn(f"  Ошибка анализа {file_path.name}: {e}")

        for method_name, locations in method_locations.items():
            if method_name in CRITICAL_METHODS and len(locations) > 1:
                issues.append(Issue(
                    file=locations[0].split(':')[0],
                    line=int(locations[0].split(':')[1]),
                    severity=TestSeverity.CRITICAL,
                    type="DUPLICATE_METHOD",
                    message=f"Метод '{method_name}' найден в {len(locations)} местах",
                    suggestion="Должен быть только в одном месте! Удалите дубликаты."
                ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Дублирование методов", passed, duration, f"Конфликтов: {len(issues)}")
        return passed

    # ========================================================================
    # 6. ПРОВЕРКА ПОЗИЦИЙ (BATCH ЗАПРОСЫ)
    # ========================================================================

    def check_batch_requests(self) -> bool:
        print_header("ПРОВЕРКА BATCH-ЗАПРОСОВ")
        start = time.time()

        issues = []

        # Проверяем наличие метода get_last_prices_batch
        try:
            from trading_bot.api.tbank_client import tbank
            if not hasattr(tbank, 'get_last_prices_batch'):
                issues.append(Issue(
                    file="tbank_client.py",
                    line=0,
                    severity=TestSeverity.HIGH,
                    type="MISSING_BATCH",
                    message="Отсутствует метод get_last_prices_batch()",
                    suggestion="Добавьте метод для batch-получения цен"
                ))
            else:
                print_ok("  get_last_prices_batch() найден")
        except Exception as e:
            print_error(f"  Ошибка: {e}")

        # Проверяем использование batch в trading_loop.py
        trading_loop_file = self.root_path / "trading_bot" / "core" / "trading_loop.py"
        if trading_loop_file.exists():
            with open(trading_loop_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if 'get_last_prices_batch' not in content:
                    issues.append(Issue(
                        file="trading_loop.py",
                        line=0,
                        severity=TestSeverity.HIGH,
                        type="NO_BATCH_USAGE",
                        message="trading_loop.py не использует get_last_prices_batch()",
                        suggestion="Замените последовательные запросы на batch"
                    ))
                else:
                    print_ok("  trading_loop.py использует batch-запросы")
        else:
            print_warn("  trading_loop.py не найден")

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Batch-запросы", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 7. ПРОВЕРКА ОБРАБОТКИ ОШИБКИ 30240
    # ========================================================================

    def check_error_30240_handling(self) -> bool:
        print_header("ПРОВЕРКА ОБРАБОТКИ ОШИБКИ 30240")
        start = time.time()

        issues = []
        files_to_check = [
            "trading_bot/api/tbank_client.py",
            "trading_bot/order_validator.py",
        ]

        for file_path in files_to_check:
            full_path = self.root_path / file_path
            if not full_path.exists():
                print_warn(f"  {file_path} не найден")
                continue

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if "30240" in content:
                print_ok(f"  {file_path}: обработка 30240 найдена")
            else:
                issues.append(Issue(
                    file=file_path,
                    line=0,
                    severity=TestSeverity.HIGH,
                    type="MISSING_30240",
                    message=f"В {file_path} отсутствует обработка ошибки 30240",
                    suggestion="Добавьте обработку '30240 - требуется подтверждение сделок'"
                ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Обработка 30240", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 8. ПРОВЕРКА КЭШИРОВАНИЯ
    # ========================================================================

    def check_caching(self) -> bool:
        print_header("ПРОВЕРКА КЭШИРОВАНИЯ")
        start = time.time()

        issues = []

        try:
            from trading_bot.cache.cache_manager import TTLCache, candles_cache

            cache = TTLCache(default_ttl=2)
            cache.set("test", "value")
            value = cache.get("test")
            if value == "value":
                print_ok("  TTLCache работает")
            else:
                issues.append(Issue(
                    file="cache_manager.py",
                    line=0,
                    severity=TestSeverity.HIGH,
                    type="CACHE_BROKEN",
                    message="TTLCache не работает корректно",
                    suggestion="Проверьте реализацию TTLCache"
                ))

            # Проверка TTL свечей
            ttl = candles_cache.default_ttl if hasattr(candles_cache, 'default_ttl') else 0
            if ttl >= 120:
                print_ok(f"  candles_cache TTL: {ttl}с")
            else:
                issues.append(Issue(
                    file="cache_manager.py",
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="LOW_CACHE_TTL",
                    message=f"TTL свечей слишком мал: {ttl}с (рекомендуется 120с)",
                    suggestion="Увеличьте TTL candles_cache до 120 секунд"
                ))

        except Exception as e:
            print_error(f"  Ошибка: {e}")
            issues.append(Issue(
                file="cache_manager.py",
                line=0,
                severity=TestSeverity.HIGH,
                type="CACHE_ERROR",
                message=str(e),
                suggestion="Исправьте ошибку в cache_manager.py"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Кэширование", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 9. ПРОВЕРКА POSITION MONITOR
    # ========================================================================

    def check_position_monitor(self) -> bool:
        print_header("ПРОВЕРКА POSITION MONITOR")
        start = time.time()

        issues = []
        trading_loop_file = self.root_path / "trading_bot" / "core" / "trading_loop.py"

        if trading_loop_file.exists():
            with open(trading_loop_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Проверка check_interval
            if 'check_interval = 5' in content:
                print_ok("  check_interval = 5 секунд")
            elif 'check_interval = 2' in content or 'check_interval = 3' in content:
                issues.append(Issue(
                    file="trading_loop.py",
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="LOW_CHECK_INTERVAL",
                    message="check_interval слишком мал (2-3 секунды)",
                    suggestion="Установите check_interval = 5"
                ))
            else:
                print_warn("  check_interval не найден в коде")

            # Проверка _check_positions
            if '_check_positions' in content:
                # Проверка интервала 60 секунд
                if 'if self._last_check_time > 0 and elapsed < 60' in content:
                    print_ok("  _check_positions имеет защиту от частых вызовов (60с)")
                else:
                    issues.append(Issue(
                        file="trading_loop.py",
                        line=0,
                        severity=TestSeverity.MEDIUM,
                        type="NO_CHECK_INTERVAL",
                        message="_check_positions не имеет защиты от частых вызовов",
                        suggestion="Добавьте проверку интервала (60 секунд)"
                    ))
            else:
                issues.append(Issue(
                    file="trading_loop.py",
                    line=0,
                    severity=TestSeverity.HIGH,
                    type="MISSING_CHECK_POSITIONS",
                    message="Метод _check_positions отсутствует",
                    suggestion="Добавьте метод _check_positions для проверки позиций"
                ))

        else:
            issues.append(Issue(
                file="trading_loop.py",
                line=0,
                severity=TestSeverity.HIGH,
                type="FILE_NOT_FOUND",
                message="trading_loop.py не найден",
                suggestion="Проверьте структуру проекта"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Position Monitor", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 10. ПРОВЕРКА ТАЙМАУТОВ API
    # ========================================================================

    def check_api_timeouts(self) -> bool:
        print_header("ПРОВЕРКА ТАЙМАУТОВ API")
        start = time.time()

        issues = []
        tbank_file = self.root_path / "trading_bot" / "api" / "tbank_client.py"

        if tbank_file.exists():
            with open(tbank_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Проверка timeout_config
            if 'timeout_config' in content:
                print_ok("  timeout_config найден")
                # Проверка значений
                if "get_candles': 5.0" in content:
                    print_ok("  get_candles timeout: 5.0с")
                else:
                    issues.append(Issue(
                        file="tbank_client.py",
                        line=0,
                        severity=TestSeverity.MEDIUM,
                        type="LOW_TIMEOUT",
                        message="Таймаут get_candles слишком мал",
                        suggestion="Установите get_candles timeout = 5.0"
                    ))

                if "post_order': 5.0" in content:
                    print_ok("  post_order timeout: 5.0с")
                else:
                    issues.append(Issue(
                        file="tbank_client.py",
                        line=0,
                        severity=TestSeverity.MEDIUM,
                        type="LOW_TIMEOUT",
                        message="Таймаут post_order слишком мал",
                        suggestion="Установите post_order timeout = 5.0"
                    ))

                # Проверка _min_interval
                if "_min_interval = 2.0" in content:
                    print_ok("  _min_interval: 2.0с")
                elif "_min_interval = 1.0" in content:
                    print_warn("  _min_interval: 1.0с (рекомендуется 2.0)")
                else:
                    print_warn("  _min_interval не найден или имеет нестандартное значение")

            else:
                issues.append(Issue(
                    file="tbank_client.py",
                    line=0,
                    severity=TestSeverity.HIGH,
                    type="MISSING_TIMEOUT_CONFIG",
                    message="timeout_config отсутствует",
                    suggestion="Добавьте настройку таймаутов в __init__"
                ))

        else:
            issues.append(Issue(
                file="tbank_client.py",
                line=0,
                severity=TestSeverity.HIGH,
                type="FILE_NOT_FOUND",
                message="tbank_client.py не найден",
                suggestion="Проверьте структуру проекта"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Таймауты API", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 11. ПРОВЕРКА ОБРАБОТКИ 30042
    # ========================================================================

    def check_error_30042_handling(self) -> bool:
        print_header("ПРОВЕРКА ОБРАБОТКИ 30042")
        start = time.time()

        issues = []
        tbank_file = self.root_path / "trading_bot" / "api" / "tbank_client.py"

        if tbank_file.exists():
            with open(tbank_file, 'r', encoding='utf-8') as f:
                content = f.read()

            if "30042" in content:
                print_ok("  Обработка 30042 найдена")
                # Проверка наличия проверки мёртвых позиций
                if "real_figi_set" in content:
                    print_ok("  Проверка мёртвых позиций есть")
                else:
                    issues.append(Issue(
                        file="tbank_client.py",
                        line=0,
                        severity=TestSeverity.MEDIUM,
                        type="NO_DEAD_POSITION_CHECK",
                        message="Нет проверки мёртвых позиций при ошибке 30042",
                        suggestion="Добавьте проверку наличия позиции у брокера"
                    ))
            else:
                issues.append(Issue(
                    file="tbank_client.py",
                    line=0,
                    severity=TestSeverity.HIGH,
                    type="MISSING_30042",
                    message="Отсутствует обработка ошибки 30042",
                    suggestion="Добавьте обработку '30042 - недостаточно средств'"
                ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Обработка 30042", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 12. ПРОВЕРКА CLEANUP_STUCK_ORDERS
    # ========================================================================

    def check_cleanup_orders(self) -> bool:
        print_header("ПРОВЕРКА ОЧИСТКИ ЗАЯВОК")
        start = time.time()

        issues = []
        files_to_check = {
            "trading_bot/api/tbank_client.py": "cleanup_stuck_orders_auto",
            "trading_bot/core/trading_loop.py": "_cleanup_stale_limit_orders"
        }

        for file_path, method_name in files_to_check.items():
            full_path = self.root_path / file_path
            if not full_path.exists():
                print_warn(f"  {file_path} не найден")
                continue

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if method_name in content:
                print_ok(f"  {method_name} найден в {file_path}")
            else:
                issues.append(Issue(
                    file=file_path,
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type=f"MISSING_{method_name.upper()}",
                    message=f"Метод {method_name} отсутствует в {file_path}",
                    suggestion=f"Добавьте метод {method_name} для очистки заявок"
                ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Очистка заявок", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 13. ПРОВЕРКА ЛОГГЕРА
    # ========================================================================

    def check_logger(self) -> bool:
        print_header("ПРОВЕРКА ЛОГГЕРА")
        start = time.time()

        issues = []
        logger_file = self.root_path / "trading_bot" / "logger.py"

        if logger_file.exists():
            with open(logger_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Проверка основных методов
            methods = ['info', 'success', 'error', 'warning', 'debug']
            for method in methods:
                if f'def {method}' in content or f'def {method}(' in content:
                    if self.verbose:
                        print_ok(f"  {method}() найден")
                else:
                    issues.append(Issue(
                        file="logger.py",
                        line=0,
                        severity=TestSeverity.HIGH,
                        type=f"MISSING_LOGGER_{method.upper()}",
                        message=f"Метод {method} отсутствует",
                        suggestion=f"Добавьте метод {method} в логгер"
                    ))

            # Проверка ротации
            if "RotatingFileHandler" in content:
                print_ok("  Ротация логов есть")
            else:
                issues.append(Issue(
                    file="logger.py",
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="NO_LOG_ROTATION",
                    message="Отсутствует ротация логов",
                    suggestion="Добавьте RotatingFileHandler"
                ))

        else:
            issues.append(Issue(
                file="logger.py",
                line=0,
                severity=TestSeverity.CRITICAL,
                type="FILE_NOT_FOUND",
                message="logger.py не найден",
                suggestion="Создайте файл logger.py"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Логгер", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 14. ПРОВЕРКА CONFIG
    # ========================================================================

    def check_config(self) -> bool:
        print_header("ПРОВЕРКА CONFIG")
        start = time.time()

        issues = []
        config_file = self.root_path / "trading_bot" / "config.py"

        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Проверка основных параметров
            params = [
                'tbank_token', 'take_profit_pct', 'stop_loss_pct',
                'max_positions', 'use_short', 'min_trade_amount'
            ]

            for param in params:
                if param in content:
                    if self.verbose:
                        print_ok(f"  {param} найден")
                else:
                    issues.append(Issue(
                        file="config.py",
                        line=0,
                        severity=TestSeverity.HIGH,
                        type=f"MISSING_CONFIG_{param.upper()}",
                        message=f"Параметр {param} отсутствует",
                        suggestion=f"Добавьте {param} в TradingConfig"
                    ))

        else:
            issues.append(Issue(
                file="config.py",
                line=0,
                severity=TestSeverity.CRITICAL,
                type="FILE_NOT_FOUND",
                message="config.py не найден",
                suggestion="Создайте файл config.py"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Config", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 15. ПРОВЕРКА WEB_SERVER
    # ========================================================================

    def check_web_server(self) -> bool:
        print_header("ПРОВЕРКА WEB_SERVER")
        start = time.time()

        issues = []
        web_file = self.root_path / "web_server.py"

        if web_file.exists():
            with open(web_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Проверка endpoints
            endpoints = ['/health', '/status', '/ping']
            for endpoint in endpoints:
                if f"@app.route('{endpoint}')" in content or f"@app.route(\"{endpoint}\")" in content:
                    if self.verbose:
                        print_ok(f"  {endpoint} найден")
                else:
                    issues.append(Issue(
                        file="web_server.py",
                        line=0,
                        severity=TestSeverity.MEDIUM,
                        type=f"MISSING_ENDPOINT_{endpoint.replace('/', '')}",
                        message=f"Endpoint {endpoint} отсутствует",
                        suggestion=f"Добавьте маршрут {endpoint}"
                    ))

            # Проверка порта
            if 'port = int(os.environ.get("PORT", 10000))' in content:
                print_ok("  Порт 10000 (Render) настроен")
            else:
                issues.append(Issue(
                    file="web_server.py",
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="WRONG_PORT",
                    message="Порт не настроен для Render (10000)",
                    suggestion="Используйте port = int(os.environ.get('PORT', 10000))"
                ))

        else:
            issues.append(Issue(
                file="web_server.py",
                line=0,
                severity=TestSeverity.CRITICAL,
                type="FILE_NOT_FOUND",
                message="web_server.py не найден",
                suggestion="Создайте файл web_server.py"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Web Server", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 16. ПРОВЕРКА MAIN.PY
    # ========================================================================

    def check_main(self) -> bool:
        print_header("ПРОВЕРКА MAIN.PY")
        start = time.time()

        issues = []
        main_file = self.root_path / "main.py"

        if main_file.exists():
            with open(main_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Проверка запуска
            if 'bot.start()' in content:
                print_ok("  bot.start() найден")
            else:
                issues.append(Issue(
                    file="main.py",
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="MISSING_BOT_START",
                    message="bot.start() отсутствует",
                    suggestion="Добавьте вызов bot.start() в main()"
                ))

            # Проверка токена
            if 'if not config.tbank_token' in content:
                print_ok("  Проверка токена есть")
            else:
                issues.append(Issue(
                    file="main.py",
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="NO_TOKEN_CHECK",
                    message="Нет проверки токена",
                    suggestion="Добавьте проверку config.tbank_token"
                ))

        else:
            issues.append(Issue(
                file="main.py",
                line=0,
                severity=TestSeverity.MEDIUM,
                type="FILE_NOT_FOUND",
                message="main.py не найден",
                suggestion="Создайте файл main.py"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Main.py", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 17. ПРОВЕРКА .ENV
    # ========================================================================

    def check_env(self) -> bool:
        print_header("ПРОВЕРКА .ENV")
        start = time.time()

        issues = []
        env_file = self.root_path / ".env"

        if env_file.exists():
            with open(env_file, 'r', encoding='utf-8') as f:
                content = f.read()

            required_vars = ['TBANK_TOKEN', 'TBANK_ACCOUNT_ID']
            for var in required_vars:
                if f"{var}=" in content:
                    print_ok(f"  {var} найден")
                else:
                    issues.append(Issue(
                        file=".env",
                        line=0,
                        severity=TestSeverity.CRITICAL,
                        type=f"MISSING_ENV_{var}",
                        message=f"Переменная {var} отсутствует",
                        suggestion=f"Добавьте {var}=ваше_значение в .env"
                    ))

        else:
            issues.append(Issue(
                file=".env",
                line=0,
                severity=TestSeverity.CRITICAL,
                type="FILE_NOT_FOUND",
                message=".env не найден",
                suggestion="Создайте файл .env с переменной TBANK_TOKEN"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result(".env", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 18. ПРОВЕРКА REQUIREMENTS
    # ========================================================================

    def check_requirements(self) -> bool:
        print_header("ПРОВЕРКА REQUIREMENTS")
        start = time.time()

        issues = []
        req_file = self.root_path / "requirements.txt"

        if req_file.exists():
            with open(req_file, 'r', encoding='utf-8') as f:
                content = f.read()

            required_packages = [
                't_tech.invest', 'flask', 'python-dotenv', 'psutil',
                'aiohttp', 'websockets', 'requests', 'numpy', 'pandas'
            ]

            for pkg in required_packages:
                if pkg in content:
                    if self.verbose:
                        print_ok(f"  {pkg} найден")
                else:
                    issues.append(Issue(
                        file="requirements.txt",
                        line=0,
                        severity=TestSeverity.HIGH,
                        type=f"MISSING_PACKAGE_{pkg}",
                        message=f"Пакет {pkg} отсутствует",
                        suggestion=f"Добавьте {pkg} в requirements.txt"
                    ))

        else:
            issues.append(Issue(
                file="requirements.txt",
                line=0,
                severity=TestSeverity.CRITICAL,
                type="FILE_NOT_FOUND",
                message="requirements.txt не найден",
                suggestion="Создайте requirements.txt со списком зависимостей"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Requirements", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 19. ПРОВЕРКА STRUCTURE
    # ========================================================================

    def check_structure(self) -> bool:
        print_header("ПРОВЕРКА СТРУКТУРЫ ПРОЕКТА")
        start = time.time()

        required_dirs = [
            "trading_bot",
            "trading_bot/api",
            "trading_bot/core",
            "trading_bot/analysis",
            "trading_bot/risk",
            "trading_bot/trading",
            "trading_bot/cache",
            "trading_bot/utils",
            "trading_bot/data",
            "trading_bot/monitoring",
            "trading_bot/telegram",
            "trading_bot/models",
            "logs",
            "backtest_results"
        ]

        issues = []

        for dir_path in required_dirs:
            full_path = self.root_path / dir_path
            if full_path.exists() and full_path.is_dir():
                if self.verbose:
                    print_ok(f"  {dir_path}/")
            else:
                if self.auto_fix:
                    full_path.mkdir(parents=True, exist_ok=True)
                    print_ok(f"  {dir_path}/ - СОЗДАН")
                else:
                    issues.append(Issue(
                        file=dir_path,
                        line=0,
                        severity=TestSeverity.HIGH,
                        type="MISSING_DIR",
                        message=f"Директория {dir_path} отсутствует",
                        suggestion=f"Создайте директорию {dir_path}"
                    ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Структура проекта", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 20. ПРОВЕРКА INIT.PY ФАЙЛОВ
    # ========================================================================

    def check_init_files(self) -> bool:
        print_header("ПРОВЕРКА INIT.PY ФАЙЛОВ")
        start = time.time()

        issues = []

        for py_dir in self.root_path.rglob("trading_bot/**/"):
            if not any(exclude in py_dir.parts for exclude in EXCLUDE_DIRS):
                init_file = py_dir / "__init__.py"
                if not init_file.exists():
                    if self.auto_fix:
                        init_file.touch()
                        print_ok(f"  {init_file.relative_to(self.root_path)} - СОЗДАН")
                    else:
                        issues.append(Issue(
                            file=str(init_file.relative_to(self.root_path)),
                            line=0,
                            severity=TestSeverity.MEDIUM,
                            type="MISSING_INIT",
                            message="__init__.py отсутствует",
                            suggestion="Создайте __init__.py для импорта модулей"
                        ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Init файлы", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 21. ПРОВЕРКА ПОЗИЦИЙ (МЁРТВЫЕ ПОЗИЦИИ)
    # ========================================================================

    def check_dead_positions(self) -> bool:
        print_header("ПРОВЕРКА МЁРТВЫХ ПОЗИЦИЙ")
        start = time.time()

        issues = []

        files_to_check = [
            "trading_bot/api/tbank_client.py",
            "trading_bot/core/trading_loop.py",
        ]

        for file_path in files_to_check:
            full_path = self.root_path / file_path
            if not full_path.exists():
                continue

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if "position_manager.remove_position" in content or "position_manager.remove_position(" in content:
                print_ok(f"  {file_path}: удаление мёртвых позиций есть")
            else:
                issues.append(Issue(
                    file=file_path,
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="NO_DEAD_POSITION_REMOVAL",
                    message=f"В {file_path} нет удаления мёртвых позиций",
                    suggestion="Добавьте вызов position_manager.remove_position(figi) при ошибке 30042/30240"
                ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Мёртвые позиции", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 22. ПРОВЕРКА TELEGRAM
    # ========================================================================

    def check_telegram(self) -> bool:
        print_header("ПРОВЕРКА TELEGRAM")
        start = time.time()

        issues = []
        tg_file = self.root_path / "trading_bot" / "telegram" / "telegram_notifier.py"

        if tg_file.exists():
            with open(tg_file, 'r', encoding='utf-8') as f:
                content = f.read()

            methods = ['send_message', 'send_error', 'send_info', 'send_shutdown']
            for method in methods:
                if f'def {method}' in content:
                    if self.verbose:
                        print_ok(f"  {method}() найден")
                else:
                    issues.append(Issue(
                        file="telegram_notifier.py",
                        line=0,
                        severity=TestSeverity.MEDIUM,
                        type=f"MISSING_TG_{method.upper()}",
                        message=f"Метод {method} отсутствует",
                        suggestion=f"Добавьте метод {method} в TelegramNotifier"
                    ))

            # Проверка polling
            if "telegram_polling" in content or "start_polling" in content:
                print_ok("  Telegram polling найден")
            else:
                issues.append(Issue(
                    file="telegram_notifier.py",
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="MISSING_TG_POLLING",
                    message="Telegram polling отсутствует",
                    suggestion="Добавьте start_polling_in_background()"
                ))

        else:
            issues.append(Issue(
                file="telegram_notifier.py",
                line=0,
                severity=TestSeverity.MEDIUM,
                type="FILE_NOT_FOUND",
                message="telegram_notifier.py не найден",
                suggestion="Создайте модуль Telegram"
            ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Telegram", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 23. ПРОВЕРКА ОБРАБОТКИ ОШИБОК (ОБЩАЯ)
    # ========================================================================

    def check_error_handling(self) -> bool:
        print_header("ПРОВЕРКА ОБРАБОТКИ ОШИБОК")
        start = time.time()

        issues = []

        files_to_check = {
            "trading_bot/api/tbank_client.py": ["30042", "30240", "30068", "30083", "30100", "30099"],
            "trading_bot/order_validator.py": ["30240", "30042"],
            "trading_bot/core/trading_loop.py": ["30042", "30240"],
        }

        for file_path, error_codes in files_to_check.items():
            full_path = self.root_path / file_path
            if not full_path.exists():
                continue

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            for code in error_codes:
                if code in content:
                    if self.verbose:
                        print_ok(f"  {file_path}: {code} обрабатывается")
                else:
                    issues.append(Issue(
                        file=file_path,
                        line=0,
                        severity=TestSeverity.HIGH,
                        type=f"MISSING_ERROR_{code}",
                        message=f"В {file_path} отсутствует обработка ошибки {code}",
                        suggestion=f"Добавьте обработку ошибки {code} в {file_path}"
                    ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Обработка ошибок", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 24. ПРОВЕРКА PROMETHEUS
    # ========================================================================

    def check_prometheus(self) -> bool:
        print_header("ПРОВЕРКА PROMETHEUS")
        start = time.time()

        issues = []
        prom_file = self.root_path / "trading_bot" / "monitoring" / "prometheus_metrics.py"

        if prom_file.exists():
            with open(prom_file, 'r', encoding='utf-8') as f:
                content = f.read()

            if "start_http_server" in content or "start_server" in content:
                print_ok("  Prometheus сервер найден")
            else:
                issues.append(Issue(
                    file="prometheus_metrics.py",
                    line=0,
                    severity=TestSeverity.LOW,
                    type="MISSING_PROMETHEUS_SERVER",
                    message="Prometheus сервер отсутствует",
                    suggestion="Добавьте start_http_server(8000) для метрик"
                ))

            metrics = ['bot_status', 'portfolio_value', 'margin_rate', 'positions_count']
            for metric in metrics:
                if metric in content:
                    if self.verbose:
                        print_ok(f"  {metric} найден")
                else:
                    issues.append(Issue(
                        file="prometheus_metrics.py",
                        line=0,
                        severity=TestSeverity.LOW,
                        type=f"MISSING_METRIC_{metric}",
                        message=f"Метрика {metric} отсутствует",
                        suggestion=f"Добавьте метрику {metric}"
                    ))

        else:
            print_warn("  prometheus_metrics.py не найден")

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Prometheus", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # 25. ПРОВЕРКА КЭШИРОВАНИЯ ВАЛИДАЦИИ
    # ========================================================================

    def check_validation_cache(self) -> bool:
        print_header("ПРОВЕРКА КЭШИРОВАНИЯ ВАЛИДАЦИИ")
        start = time.time()

        issues = []

        # Проверка валидации в order_validator.py
        ov_file = self.root_path / "trading_bot" / "order_validator.py"
        if ov_file.exists():
            with open(ov_file, 'r', encoding='utf-8') as f:
                content = f.read()

            if "_confirmation_cache" in content or "confirmation_cache" in content:
                print_ok("  Кэш подтверждения найден")
            else:
                issues.append(Issue(
                    file="order_validator.py",
                    line=0,
                    severity=TestSeverity.MEDIUM,
                    type="MISSING_CONFIRMATION_CACHE",
                    message="Отсутствует кэш подтверждения сделок",
                    suggestion="Добавьте _confirmation_cache для OTC инструментов"
                ))

        # Проверка в tbank_client.py
        tbank_file = self.root_path / "trading_bot" / "api" / "tbank_client.py"
        if tbank_file.exists():
            with open(tbank_file, 'r', encoding='utf-8') as f:
                content = f.read()

            if "is_confirmation_required" in content:
                print_ok("  is_confirmation_required() найден")
                if "_confirmation_cache" in content:
                    print_ok("  _confirmation_cache найден")
                else:
                    issues.append(Issue(
                        file="tbank_client.py",
                        line=0,
                        severity=TestSeverity.MEDIUM,
                        type="MISSING_CONFIRMATION_CACHE_TBANK",
                        message="Отсутствует _confirmation_cache в tbank_client.py",
                        suggestion="Добавьте кэш для is_confirmation_required()"
                    ))
            else:
                issues.append(Issue(
                    file="tbank_client.py",
                    line=0,
                    severity=TestSeverity.HIGH,
                    type="MISSING_CONFIRMATION_CHECK",
                    message="Метод is_confirmation_required() отсутствует",
                    suggestion="Добавьте проверку OTC инструментов"
                ))

        for issue in issues:
            self._add_issue(issue)

        duration = time.time() - start
        passed = len(issues) == 0
        self._add_result("Кэширование валидации", passed, duration, f"Проблем: {len(issues)}")
        return passed

    # ========================================================================
    # АВТОМАТИЧЕСКОЕ ИСПРАВЛЕНИЕ
    # ========================================================================

    def fix_issues(self) -> int:
        """Автоматическое исправление найденных проблем"""
        print_header("АВТОМАТИЧЕСКОЕ ИСПРАВЛЕНИЕ")
        fixed_count = 0

        critical_issues = [i for i in self.issues if i.severity == TestSeverity.CRITICAL]
        high_issues = [i for i in self.issues if i.severity == TestSeverity.HIGH]

        if critical_issues:
            print_error(f"Найдено {len(critical_issues)} критических проблем! Требуется ручное исправление:")
            for issue in critical_issues[:5]:
                print(f"  - {issue.file}:{issue.line} - {issue.message}")
            if self.auto_fix:
                print_warn("Критические проблемы не могут быть автоматически исправлены!")
                print_info("  Создан бэкап всех файлов в .backup_fix/")

        for issue in high_issues:
            if "MISSING_BATCH" in issue.type:
                fixed_count += self._fix_missing_batch(issue)
            elif "MISSING_30240" in issue.type:
                fixed_count += self._fix_missing_30240(issue)
            elif "MISSING_30042" in issue.type:
                fixed_count += self._fix_missing_30042(issue)

        print_ok(f"Автоматически исправлено: {fixed_count} проблем")
        return fixed_count

    def _fix_missing_batch(self, issue: Issue) -> int:
        """Добавление метода get_last_prices_batch"""
        file_path = self.root_path / issue.file
        if not file_path.exists():
            return 0

        self._backup_file(file_path)

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Поиск места для вставки
        if "get_last_prices_batch" in content:
            return 0

        # Вставка метода перед def get_current_price
        batch_method = '''
    @api_monitor.measure("get_last_prices_batch")
    def get_last_prices_batch(self, figis: List[str]) -> Dict[str, float]:
        """ПОЛУЧЕНИЕ ЦЕН СРАЗУ ДЛЯ НЕСКОЛЬКИХ ИНСТРУМЕНТОВ (BATCH)"""
        from t_tech.invest.utils import quotation_to_decimal

        if not figis:
            return {}

        # Проверка кэша
        result = {}
        uncached_figis = []

        for figi in figis:
            cached = price_cache.get(figi)
            if cached is not None:
                result[figi] = cached
            else:
                uncached_figis.append(figi)

        if not uncached_figis:
            return result

        # Ограничение количества FIGI
        MAX_FIGI_PER_REQUEST = 50
        if len(uncached_figis) > MAX_FIGI_PER_REQUEST:
            chunks = [uncached_figis[i:i + MAX_FIGI_PER_REQUEST] for i in range(0,
                len(uncached_figis)
                MAX_FIGI_PER_REQUEST)]
            for chunk in chunks:
                chunk_result = self._get_last_prices_batch_chunk(chunk)
                result.update(chunk_result)
            return result

        # Batch запрос
        try:
            with Client(self.token) as client:
                last_prices_response = client.market_data.get_last_prices(figi=uncached_figis)

                for price_data in last_prices_response.last_prices:
                    figi = price_data.figi
                    price = float(quotation_to_decimal(price_data.price))
                    result[figi] = price
                    price_cache.set(figi, price, ttl=10)

                return result

        except Exception as e:
            error(f"❌ Ошибка batch получения цен: {e}")
            return result

    def _get_last_prices_batch_chunk(self, figis: List[str]) -> Dict[str, float]:
        """Вспомогательный метод для получения цен по чанку"""
        from t_tech.invest.utils import quotation_to_decimal

        try:
            with Client(self.token) as client:
                last_prices_response = client.market_data.get_last_prices(figi=figis)
                result = {}
                for price_data in last_prices_response.last_prices:
                    figi = price_data.figi
                    price = float(quotation_to_decimal(price_data.price))
                    result[figi] = price
                    price_cache.set(figi, price, ttl=10)
                return result
        except Exception as e:
            warning(f"❌ Ошибка batch запроса для чанка: {e}")
            return {}
'''

        # Поиск места для вставки
        if "def get_current_price" in content:
            content = content.replace("def get_current_price", batch_method + "\n    def get_current_price")
        else:
            content += "\n" + batch_method

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        print_ok(f"  ✅ Добавлен get_last_prices_batch() в {issue.file}")
        return 1

    def _fix_missing_30240(self, issue: Issue) -> int:
        """Добавление обработки ошибки 30240"""
        file_path = self.root_path / issue.file
        if not file_path.exists():
            return 0

        self._backup_file(file_path)

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if "30240" in content:
            return 0

        # Поиск блоков обработки ошибок
        if "elif '30240' in error_msg" not in content:
            # Добавляем обработку в _place_market_order_impl
            if "_place_market_order_impl" in content:
                # Ищем блок except
                content = content.replace(
                    'except Exception as e:',
                    '''except Exception as e:
            error_msg = str(e)

            # Обработка 30240 - требуется подтверждение сделок
            if "30240" in error_msg:
                warning(f"   🔐 {ticker}: ОШИБКА 30240 - требуется подтверждение сделок!")
                warning(f"   📱 Закройте позицию вручную в приложении Т-Банк")
                self._confirmation_cache[figi] = True
                self._confirmation_cache_time[figi] = time.time()
                return {
                    'success': False,
                    'order_id': None,
                    'quantity': 0,
                    'error': '30240 - требуется подтверждение сделок (OTC)',
                    'requires_manual': True,
                    'is_otc': True,
                    'block_ticker': False
                }

            # Другие ошибки
            print(f"   ❌ ОШИБКА: {error_msg[:150]}")'''
                )
                print_ok(f"  ✅ Добавлена обработка 30240 в {issue.file}")
                return 1

        return 0

    def _fix_missing_30042(self, issue: Issue) -> int:
        """Добавление обработки ошибки 30042 с проверкой мёртвых позиций"""
        file_path = self.root_path / issue.file
        if not file_path.exists():
            return 0

        self._backup_file(file_path)

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if "real_figi_set" in content:
            return 0

        # Добавляем проверку мёртвых позиций
        if "_place_market_order_impl" in content:
            # Ищем блок обработки 30042
            pattern = r'(if "30042" in error_msg:.*?)(?=elif|else:|$)'
            replacement = r'''\1
                # Проверяем наличие позиции у брокера (мёртвые позиции)
                try:
                    positions = self.get_positions()
                    real_figi_set = {p['figi'] for p in positions if abs(p.get('quantity', 0)) > 0}
                    if figi not in real_figi_set:
                        warning(f"   🧹 Позиции {ticker} нет у брокера! Удаляем из менеджера")
                        from trading_bot.risk.position_manager import position_manager
                        position_manager.remove_position(figi)
                        return {
                            'success': True,
                            'order_id': None,
                            'quantity': 0,
                            'note': 'Позиция уже закрыта у брокера'
                        }
                except Exception as e:
                    debug(f"   ⚠️ Ошибка проверки позиций: {e}")

                # Рекомендация для пользователя
                current_price = self.get_current_price(figi)
                if current_price:
                    info(f"   💡 РЕКОМЕНДАЦИЯ:")
                    info(f"      → Пополните счёт (нужно ~{quantity * current_price:.0f}₽)")
                    info(f"      → Или закройте часть позиций для освобождения маржи")'''

            content = re.sub(pattern, replacement, content, flags=re.DOTALL)
            print_ok(f"  ✅ Добавлена проверка мёртвых позиций в {issue.file}")
            return 1

        return 0

    # ========================================================================
    # ЗАПУСК ВСЕХ ПРОВЕРОК
    # ========================================================================

    async def run_all_checks(self, check_only: bool = False, fix_only: bool = False) -> Dict[str, Any]:
        print_info("\n🚀 Запуск мастер-диагностики...")

        if fix_only:
            return self.fix_issues()

        # Запуск всех проверок
        checks = [
            ("Структура проекта", self.check_structure),
            ("Init файлы", self.check_init_files),
            (".env", self.check_env),
            ("Requirements", self.check_requirements),
            ("Config", self.check_config),
            ("Логгер", self.check_logger),
            ("API Т-Банка", self.check_tbank_api),
            ("Импорты", self.check_imports),
            ("Торговые методы", self.check_trading_methods),
            ("Качество кода", self.check_code_quality),
            ("Дублирование методов", self.check_duplicate_methods),
            ("Batch-запросы", self.check_batch_requests),
            ("Обработка 30240", self.check_error_30240_handling),
            ("Обработка 30042", self.check_error_30042_handling),
            ("Кэширование", self.check_caching),
            ("Position Monitor", self.check_position_monitor),
            ("Таймауты API", self.check_api_timeouts),
            ("Очистка заявок", self.check_cleanup_orders),
            ("Мёртвые позиции", self.check_dead_positions),
            ("Telegram", self.check_telegram),
            ("Обработка ошибок", self.check_error_handling),
            ("Prometheus", self.check_prometheus),
            ("Кэширование валидации", self.check_validation_cache),
            ("Web Server", self.check_web_server),
            ("Main.py", self.check_main),
        ]

        for name, check_func in checks:
            try:
                # Проверяем, является ли функция асинхронной
                if asyncio.iscoroutinefunction(check_func):
                    result = await check_func()  # ← await для асинхронных
                else:
                    result = check_func()  # ← синхронный вызов

                if isinstance(result, bool):
                    self._add_result(name, result, 0.1, "")
            except Exception as e:
                print_error(f"  {name}: ОШИБКА - {e}")
                self._add_result(name, False, 0.1, str(e))

        if not check_only and self.auto_fix:
            fixed = self.fix_issues()
            print_ok(f"Всего исправлено: {fixed} проблем")
            self.stats['issues_fixed'] = fixed

        return self.get_summary()

    def get_summary(self) -> Dict[str, Any]:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)

        critical = sum(1 for i in self.issues if i.severity == TestSeverity.CRITICAL)
        high = sum(1 for i in self.issues if i.severity == TestSeverity.HIGH)
        medium = sum(1 for i in self.issues if i.severity == TestSeverity.MEDIUM)
        low = sum(1 for i in self.issues if i.severity == TestSeverity.LOW)

        score = 100 - critical * 20 - high * 10 - medium * 5 - low * 2
        score = max(0, min(100, score))

        elapsed = (datetime.now() - self.start_time).total_seconds()

        print_header("ИТОГИ МАСТЕР-ДИАГНОСТИКИ")

        print_info(f"⏱ Время: {elapsed:.2f}с")
        print_info(f"📁 Файлов: {self.stats['files_scanned']}")
        print_info(f"📄 Строк: {self.stats['total_lines']:,}")

        print_sep()
        print_info("📊 РЕЗУЛЬТАТЫ:")
        for result in self.results:
            status = "✅" if result.passed else "❌"
            print(f"   {status} {result.name}")

        print_sep()
        print_info("📊 СТАТИСТИКА:")
        print(f"   ✅ Тестов пройдено: {passed}/{total}")
        print(
            f"   ⚠️ Найдено проблем: {len(self.issues)} (CRITICAL: {critical}, HIGH: {high}, MEDIUM: {medium}, LOW: {low})")
        print(f"   🔧 Исправлено: {self.stats['issues_fixed']}")

        print_sep()
        print_info(f"⭐ ИТОГОВАЯ ОЦЕНКА: {score:.0f}/100")

        if score >= 90:
            print_ok("🎉 ОТЛИЧНО! Бот готов к эксплуатации!")
        elif score >= 70:
            print_ok("✅ ХОРОШО! Небольшие замечания, можно запускать.")
        elif score >= 50:
            print_warn("⚠️ УДОВЛЕТВОРИТЕЛЬНО! Рекомендуется доработка.")
        else:
            print_error("🔴 ПЛОХО! Требуется серьёзная доработка!")

        report = {
            "timestamp": self.start_time.isoformat(),
            "duration": elapsed,
            "auto_fix": self.auto_fix,
            "stats": self.stats,
            "score": score,
            "results": [{"name": r.name, "passed": r.passed} for r in self.results],
            "issues": [{"file": i.file, "severity": i.severity.value, "message": i.message, "fixed": i.fixed}
                       for i in self.issues[:20]]
        }

        with open("fix_bot_report.json", 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        print_info(f"\n📄 Отчёт: fix_bot_report.json")
        print_info(f"📄 Лог: {LOG_FILE}")

        return report


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="МАСТЕР-ДИАГНОСТИКА + ИСПРАВЛЕНИЕ ТОРГОВОГО БОТА v4.0",
        epilog="""
Примеры:
  python fix_bot.py              # Диагностика + исправление
  python fix_bot.py --check      # Только диагностика (без исправлений)
  python fix_bot.py --fix        # Только исправление (без диагностики)
  python fix_bot.py --verbose    # Подробный вывод
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--check", "-c", action="store_true", help="Только диагностика (без исправлений)")
    parser.add_argument("--fix", "-f", action="store_true", help="Только исправление (без диагностики)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Подробный вывод")

    args = parser.parse_args()

    # Создаём экземпляр
    fixer = BotFixer(auto_fix=not args.check, verbose=args.verbose)

    # Запуск
    asyncio.run(fixer.run_all_checks(check_only=args.check, fix_only=args.fix))


if __name__ == "__main__":
    main()