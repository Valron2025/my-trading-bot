# logger.py - БОМБА-ВЕРСИЯ С РОТАЦИЕЙ, АВТООЧИСТКОЙ И ПРАВИЛЬНЫМ ВРЕМЕНЕМ
# -*- coding: utf-8 -*-
"""МЕГА-ЛОГГЕР для торгового бота — красиво, понятно, без спама, с ротацией и автоочисткой"""

import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, Any
import logging.handlers
import threading
from datetime import datetime, timezone, timedelta

# ========== НАСТРОЙКИ ЧАСОВОГО ПОЯСА ==========
# Московское время (UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))

# Выберите нужный часовой пояс
TZ = MOSCOW_TZ

_root_dir = Path(__file__).parent.parent
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))


class Logger:
    """Базовый класс логгера для обратной совместимости"""

    @staticmethod
    def info(msg):
        print(f"ℹ️ {msg}")

    @staticmethod
    def error(msg):
        print(f"❌ {msg}")

    @staticmethod
    def warning(msg):
        print(f"⚠️ {msg}")

    @staticmethod
    def debug(msg):
        print(f"🐛 {msg}")

    @staticmethod
    def success(msg):
        print(f"✅ {msg}")


# Создаем экземпляр для обратной совместимости
logger = Logger()


# ========== ЦВЕТА ДЛЯ КОНСОЛИ ==========
class Colors:
    RESET = '\033[0m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


# ========== ЭМОДЗИ ==========
EMOJI = {
    'start': '🚀', 'stop': '🛑', 'success': '✅', 'error': '❌',
    'warning': '⚠️', 'info': 'ℹ️', 'money': '💰', 'chart': '📊',
    'robot': '🤖', 'brain': '🧠', 'fire': '🔥', 'lock': '🔒',
    'key': '🔑', 'bell': '🔔', 'gear': '⚙️', 'star': '⭐',
    'bolt': '⚡', 'clock': '⏱', 'calendar': '📅', 'folder': '📁',
    'file': '📄', 'save': '💾', 'load': '📂', 'connect': '🔌',
    'disconnect': '🔌', 'network': '🌐', 'database': '🗄️', 'trade': '💹',
    'buy': '🟢', 'sell': '🔴', 'hold': '🟡', 'up': '📈',
    'down': '📉', 'neutral': '➡️', 'ai': '🧠', 'ml': '🤖',
    'bert': '📖', 'ollama': '🦙', 'deepseek': '🔍', 'gpu': '🎮',
    'cpu': '💻', 'ram': '💾', 'time': '⏰', 'warning_signal': '⚠️',
    'critical': '🔥', 'debug': '🔍', 'cleanup': '🧹', 'profit': '💵',
    'loss': '💸', 'target': '🎯', 'stop': '🛑',
}


# ========== ОСНОВНОЙ ЛОГГЕР ==========
class BombLogger:
    _instance = None
    _last_logs: Dict[str, tuple] = {}
    _log_cooldown = 5

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._setup_logger()
        self._cleanup_old_logs(days=30)
        self._start_cleanup_scheduler()

        # Уровень логирования из окружения
        import os
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL,
        }
        self.logger.setLevel(level_map.get(log_level, logging.INFO))

    def _filter_noise(self, message: str) -> bool:
        """Фильтрация шумных сообщений"""
        noise_patterns = [
            'GetCandles INVALID_ARGUMENT 30014',
            'No columns/rows for',
            'MOEX: запрошен',
            'GetCandles$',
            'GetMarginAttributes$',
            'GetLastPrices$',
            'GetPortfolio$',
            'Shares$',
        ]
        for pattern in noise_patterns:
            if pattern in message:
                return True
        return False

    def _setup_logger(self):
        """Настройка логгера с консольным и файловым выводом"""
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger("Bomb")

        # Уровень из окружения
        import os
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL,
        }
        self.logger.setLevel(level_map.get(log_level, logging.INFO))

        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)

        # Консоль (только INFO и выше)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(self._ConsoleFormatter())
        console_handler.setLevel(logging.INFO)
        self.logger.addHandler(console_handler)

        # Файл с ротацией
        self.log_file = self.log_dir / "trading_bot.log"
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(self.log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=10,
            encoding='utf-8'
        )
        file_handler.setFormatter(self._FileFormatter())
        file_handler.setLevel(logging.DEBUG)
        self.logger.addHandler(file_handler)

        # Файл для ошибок
        self.error_file = self.log_dir / "errors.log"
        error_handler = logging.handlers.RotatingFileHandler(
            filename=str(self.error_file),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        error_handler.setFormatter(self._FileFormatter())
        error_handler.setLevel(logging.ERROR)
        self.logger.addHandler(error_handler)

        # Отключаем шумные логгеры
        for noisy in ['httpx', 'httpcore', 'urllib3', 'websockets', 'asyncio',
                      'werkzeug', 'telegram', 't_tech.invest', 'aiohttp',
                      'grpc', 'grpc.aio', 'google.auth']:
            logging.getLogger(noisy).setLevel(logging.WARNING)
            logging.getLogger(noisy).propagate = False

    def _cleanup_old_logs(self, days: int = 30):
        """Очистка старых лог-файлов"""
        if not self.log_dir.exists():
            return
        now = time.time()
        age_seconds = days * 86400
        deleted = 0
        for log_file in list(self.log_dir.glob("*.log*")):
            if log_file.is_file() and (now - log_file.stat().st_mtime) > age_seconds:
                try:
                    log_file.unlink()
                    deleted += 1
                except Exception:
                    pass
        if deleted > 0:
            self.logger.info(f"🧹 Удалено {deleted} старых лог-файлов (старше {days} дней)")

    def _start_cleanup_scheduler(self):
        """Запуск фоновой очистки раз в неделю"""
        def cleanup_loop():
            while True:
                time.sleep(7 * 86400)  # 7 дней
                try:
                    self._cleanup_old_logs(days=30)
                except Exception:
                    pass

        threading.Thread(target=cleanup_loop, daemon=True).start()

    class _ConsoleFormatter(logging.Formatter):
        """Форматтер для консоли с цветами и эмодзи"""
        LEVEL_COLORS = {
            logging.DEBUG: Colors.DIM,
            logging.INFO: Colors.CYAN,
            logging.WARNING: Colors.YELLOW,
            logging.ERROR: Colors.RED,
            logging.CRITICAL: Colors.RED + Colors.BOLD,
        }
        LEVEL_EMOJI = {
            logging.DEBUG: "🔍",
            logging.INFO: "ℹ️",
            logging.WARNING: "⚠️",
            logging.ERROR: "❌",
            logging.CRITICAL: "🔥",
        }

        def format(self, record):
            emoji = self.LEVEL_EMOJI.get(record.levelno, "")
            color = self.LEVEL_COLORS.get(record.levelno, Colors.WHITE)
            now_tz = datetime.now(TZ)
            time_str = now_tz.strftime("%H:%M:%S")
            name = record.name.split('.')[-1][:12]
            msg = record.getMessage()
            return f"{color}{emoji} {time_str} | {name:<12} | {record.levelname:<7} | {msg}{Colors.RESET}"

    class _FileFormatter(logging.Formatter):
        """Форматтер для файлов с полной датой"""
        def format(self, record):
            now_tz = datetime.now(TZ)
            time_str = now_tz.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            name = record.name.split('.')[-1][:20]
            msg = record.getMessage()
            return f"{time_str} | {name:<20} | {record.levelname:<7} | {msg}"

    def _should_log(self, key: str) -> bool:
        """Проверка, нужно ли логировать (anti-spam)"""
        now = time.time()
        if key in self._last_logs:
            last_time, count = self._last_logs[key]
            if now - last_time < self._log_cooldown:
                return False
        self._last_logs[key] = (now, 1)
        return True

    # ========== ОСНОВНЫЕ МЕТОДЫ ЛОГГИРОВАНИЯ ==========

    def debug(self, message: str, key: str = None):
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self.logger.debug(message)

    def info(self, message: str, key: str = None):
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self.logger.info(message)

    def success(self, message: str, key: str = None):
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self.logger.info(f"{EMOJI['success']} {message}")

    def warning(self, message: str, key: str = None):
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self.logger.warning(f"{EMOJI['warning']} {message}")

    def error(self, message: str, key: str = None, exc_info: bool = False):
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        if exc_info:
            tb = traceback.format_exc()
            if tb and tb != "NoneType: None\n":
                self.logger.error(f"{EMOJI['error']} {message}\n{tb}")
            else:
                self.logger.error(f"{EMOJI['error']} {message}")
        else:
            self.logger.error(f"{EMOJI['error']} {message}")

    def exception(self, message: str, key: str = None):
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self.logger.exception(f"{EMOJI['error']} {message}")

    def critical(self, message: str, key: str = None):
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self.logger.critical(f"{EMOJI['critical']} {message}")

    # ========== ТОРГОВЫЕ МЕТОДЫ ЛОГГИРОВАНИЯ ==========

    def trade(self, ticker: str, direction: str, quantity: int, price: float):
        emoji = EMOJI['buy'] if direction == 'BUY' else EMOJI['sell']
        self.logger.info(f"{emoji} {direction} {quantity} {ticker} @ {price:.2f} ₽")

    def trade_profit(self, ticker: str, profit_pct: float, profit_amount: float):
        emoji = EMOJI['profit'] if profit_amount > 0 else EMOJI['loss']
        self.logger.info(f"{emoji} {ticker}: {profit_pct:+.2f}% ({profit_amount:+.2f} ₽)")

    def balance(self, amount: float, key: str = "balance"):
        if not self._should_log(key):
            return
        self.logger.info(f"{EMOJI['money']} Баланс: {amount:,.2f} ₽")

    def position(self, ticker: str, quantity: float, price: float, pnl: float = None):
        pnl_str = f" | P&L: {pnl:+.2f} ₽" if pnl is not None else ""
        self.logger.info(f"{EMOJI['chart']} {ticker}: {quantity} шт @ {price:.2f} ₽{pnl_str}")

    def margin_status(self, margin_rate: float, used_margin: float, available_margin: float):
        emoji = "🔴" if margin_rate >= 85 else "🟡" if margin_rate >= 70 else "🟢"
        self.logger.info(f"{emoji} Маржа: {margin_rate:.1f}% | Использовано: {used_margin:.2f}₽ | Доступно: {available_margin:.2f}₽")

    def separator(self, title: str = None):
        if title:
            self.logger.info(f"{Colors.CYAN}{'=' * 60}{Colors.RESET}")
            self.logger.info(f"{Colors.BOLD}{title}{Colors.RESET}")
            self.logger.info(f"{Colors.CYAN}{'=' * 60}{Colors.RESET}")
        else:
            self.logger.info(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

    # ========== УТИЛИТЫ ==========

    def get_log_size(self) -> int:
        return self.log_file.stat().st_size if self.log_file.exists() else 0

    def get_log_size_mb(self) -> float:
        return self.get_log_size() / (1024 * 1024)

    def get_stats(self) -> Dict[str, Any]:
        return {
            'log_size_mb': self.get_log_size_mb(),
            'error_file_size_mb': self.error_file.stat().st_size / (1024*1024) if self.error_file.exists() else 0,
            'handlers_count': len(self.logger.handlers),
            'level': logging.getLevelName(self.logger.level)
        }


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
bomb = BombLogger()


# ========== УТИЛИТЫ ДЛЯ ПРОСТОГО ИСПОЛЬЗОВАНИЯ ==========
def info(msg: str): bomb.info(msg)
def success(msg: str): bomb.success(msg)
def error(msg: str, exc_info: bool = False): bomb.error(msg, exc_info=exc_info)
def warning(msg: str): bomb.warning(msg)
def debug(msg: str): bomb.debug(msg)
def exception(msg: str): bomb.exception(msg)
def critical(msg: str): bomb.critical(msg)
def trade(ticker: str, direction: str, qty: int, price: float): bomb.trade(ticker, direction, qty, price)
def trade_profit(ticker: str, profit_pct: float, profit_amount: float): bomb.trade_profit(ticker, profit_pct, profit_amount)
def balance(amount: float): bomb.balance(amount)
def margin_status(margin_rate: float, used_margin: float, available_margin: float): bomb.margin_status(margin_rate, used_margin, available_margin)
def sep(title: str = None): bomb.separator(title)


# ========== СОВМЕСТИМОСТЬ ==========
class AdvancedLogger:
    def __init__(self, config):
        self.config = config
        self.logger = bomb.logger

    def get_logger(self):
        return self.logger


def setup_logger(name: str = "TradingBot", log_file: str = "logs/bot.log", level: str = "INFO") -> logging.Logger:
    return bomb.logger


def setup_logging_prod(log_level: str = "INFO", enable_rich: bool = True) -> logging.Logger:
    return bomb.logger


def setup_quiet_logging(log_level: str = "INFO") -> logging.Logger:
    return bomb.logger


def get_logger(name: str) -> logging.Logger:
    return bomb.logger


def print_success(message: str): bomb.success(message)
def print_error(message: str): bomb.error(message)
def print_warning(message: str): bomb.warning(message)
def print_info(message: str): bomb.info(message)


def build_portfolio_table(portfolio_data: Dict[str, Any]) -> Any:
    return None


def build_performance_table(metrics: Dict[str, Any]) -> Any:
    return None


# ========== ОЧИСТКА ДУБЛИРУЮЩИХСЯ ЛОГГЕРОВ ==========
def _cleanup_duplicate_loggers():
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    for name in list(logging.root.manager.loggerDict.keys()):
        logging.getLogger(name).propagate = False
    logging.getLogger("Bomb").propagate = False
    for noisy in ['httpx', 'httpcore', 'urllib3', 'websockets', 'asyncio', 'werkzeug', 'telegram']:
        logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger(noisy).propagate = False


_cleanup_duplicate_loggers()


# ========== ЭКСПОРТ ==========
__all__ = [
    'AdvancedLogger', 'setup_logger', 'setup_logging_prod', 'setup_quiet_logging',
    'get_logger', 'build_portfolio_table', 'build_performance_table',
    'print_success', 'print_error', 'print_warning', 'print_info',
    'bomb', 'info', 'success', 'error', 'warning', 'debug', 'exception', 'critical',
    'trade', 'trade_profit', 'balance', 'margin_status', 'sep', 'Colors', 'EMOJI'
]