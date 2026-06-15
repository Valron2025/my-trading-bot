# logger.py - БОМБА-ВЕРСИЯ С РОТАЦИЕЙ, АВТООЧИСТКОЙ И ОПТИМИЗАЦИЕЙ ДЛЯ СЕРВЕРА
# -*- coding: utf-8 -*-
"""МЕГА-ЛОГГЕР для торгового бота — оптимизирован для сервера, но с полной функциональностью"""

import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, Any
import logging.handlers
import threading
from datetime import datetime, timezone, timedelta
import os

# ========== НАСТРОЙКИ ЧАСОВОГО ПОЯСА ==========
# Московское время (UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))

# Выберите нужный часовой пояс
TZ = MOSCOW_TZ

_root_dir = Path(__file__).parent.parent
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))

# ========== ОПТИМИЗАЦИИ ДЛЯ СЕРВЕРА ==========
# Проверяем окружение для оптимизаций (без изменения .env)
_ENV = os.getenv('ENV', 'development')
_LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

# В production используем оптимизации
_IS_PRODUCTION = _ENV == 'production'

# Кэш для шумных паттернов (tuple быстрее list)
_NOISE_PATTERNS = (
    'GetCandles INVALID_ARGUMENT 30014',
    'No columns/rows for',
    'MOEX: запрошен',
    'GetCandles$',
    'GetMarginAttributes$',
    'GetLastPrices$',
    'GetPortfolio$',
    'Shares$',
)


class Logger:
    """Базовый класс логгера для обратной совместимости"""

    @staticmethod
    def info(msg):
        """Вывод информационного сообщения в консоль."""
        print(f"ℹ️ {msg}")

    @staticmethod
    def error(msg):
        """Вывод сообщения об ошибке в консоль."""
        print(f"❌ {msg}")

    @staticmethod
    def warning(msg):
        """Вывод предупреждения в консоль."""
        print(f"⚠️ {msg}")

    @staticmethod
    def debug(msg):
        """Вывод отладочного сообщения в консоль."""
        print(f"🐛 {msg}")

    @staticmethod
    def success(msg):
        """Вывод сообщения об успехе в консоль."""
        print(f"✅ {msg}")


# Создаем экземпляр для обратной совместимости
logger = Logger()


# ========== ЦВЕТА ДЛЯ КОНСОЛИ ==========
class Colors:
    """Класс с ANSI-кодами цветов для консольного вывода"""
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
    """Основной класс логгера с оптимизациями для сервера"""
    _instance = None
    _last_logs: Dict[str, tuple] = {}
    _log_cooldown = 5

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Инициализация логгера с оптимизациями"""
        if self._initialized:
            return
        self._initialized = True
        self._setup_logger()

        # Отложенная очистка логов (запускаем в фоне)
        self._stop_event = threading.Event()
        self._cleanup_thread = None

        # Запускаем очистку только в production или по запросу
        if _IS_PRODUCTION:
            self._cleanup_old_logs(days=30)
            self._start_cleanup_scheduler()

        # Уровень логирования из окружения (уже есть в .env)
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL,
        }
        self.logger.setLevel(level_map.get(_LOG_LEVEL, logging.INFO))

    def _filter_noise(self, message: str) -> bool:
        """БЫСТРАЯ фильтрация шумных сообщений"""
        # Быстрая проверка через tuple (оптимизировано)
        for pattern in _NOISE_PATTERNS:
            if pattern in message:
                return True
        return False

    def _setup_logger(self):
        """ОПТИМИЗИРОВАННАЯ настройка логгера с ленивым созданием файлов"""
        self.log_dir = Path("logs")
        self._log_dir_created = False

        self.logger = logging.getLogger("Bomb")

        # Уровень из окружения
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL,
        }
        self.logger.setLevel(level_map.get(_LOG_LEVEL, logging.INFO))

        # Удаляем старые хендлеры
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)

        # Консольный хендлер (с уровнем из .env)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(self._ConsoleFormatter())

        # В production уменьшаем вывод в консоль для скорости
        if _IS_PRODUCTION and _LOG_LEVEL != 'DEBUG':
            console_handler.setLevel(logging.WARNING)
        else:
            console_handler.setLevel(logging.DEBUG if _LOG_LEVEL == 'DEBUG' else logging.INFO)

        self.logger.addHandler(console_handler)

        # Файловые хендлеры - создаём лениво (только при первой записи)
        self._file_handler = None
        self._error_handler = None
        self.log_file = None
        self.error_file = None

        # Отключаем шумные логгеры
        for noisy in ['httpx', 'httpcore', 'urllib3', 'websockets', 'asyncio',
                      'werkzeug', 'telegram', 't_tech.invest', 'aiohttp',
                      'grpc', 'grpc.aio', 'google.auth']:
            logging.getLogger(noisy).setLevel(logging.WARNING)
            logging.getLogger(noisy).propagate = False

    def _ensure_file_handlers(self):
        """Ленивое создание файловых хендлеров (только при первом логе)"""
        if self._file_handler is not None:
            return

        # Создаём директорию только сейчас
        if not self._log_dir_created:
            self.log_dir.mkdir(exist_ok=True)
            self._log_dir_created = True

        # Файл с ротацией
        self.log_file = self.log_dir / "trading_bot.log"
        self._file_handler = logging.handlers.RotatingFileHandler(
            filename=str(self.log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=10,
            encoding='utf-8'
        )
        self._file_handler.setFormatter(self._FileFormatter())
        self._file_handler.setLevel(logging.DEBUG)
        self.logger.addHandler(self._file_handler)

        # Файл для ошибок
        self.error_file = self.log_dir / "errors.log"
        self._error_handler = logging.handlers.RotatingFileHandler(
            filename=str(self.error_file),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        self._error_handler.setFormatter(self._FileFormatter())
        self._error_handler.setLevel(logging.ERROR)
        self.logger.addHandler(self._error_handler)

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
        if deleted > 0 and self._file_handler:
            self.logger.info(f"🧹 Удалено {deleted} старых лог-файлов (старше {days} дней)")

    def _start_cleanup_scheduler(self):
        """Запуск фоновой очистки логов"""

        def cleanup_loop():
            # Ждём 1 час перед первым запуском
            if self._stop_event.wait(3600):
                return

            while not self._stop_event.is_set():
                try:
                    self._cleanup_old_logs(days=30)
                    # Ждём 24 часа с возможностью прерывания
                    if self._stop_event.wait(86400):
                        break
                except Exception:
                    if self._stop_event.wait(3600):
                        break

        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def stop_cleanup_thread(self):
        """Остановка потока очистки"""
        self._stop_event.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)

    class _ConsoleFormatter(logging.Formatter):
        """ОПТИМИЗИРОВАННЫЙ форматтер для консоли"""
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
            # Минимум операций для скорости
            emoji = self.LEVEL_EMOJI.get(record.levelno, "")
            color = self.LEVEL_COLORS.get(record.levelno, Colors.WHITE)
            now_tz = datetime.now(TZ)
            # Быстрое форматирование времени
            time_str = f"{now_tz.hour:02d}:{now_tz.minute:02d}:{now_tz.second:02d}"
            name = record.name.split('.')[-1][:12]
            msg = record.getMessage()
            return f"{color}{emoji} {time_str} | {name:<12} | {record.levelname:<7} | {msg}{Colors.RESET}"

    class _FileFormatter(logging.Formatter):
        """ОПТИМИЗИРОВАННЫЙ форматтер для файлов"""

        def format(self, record):
            now_tz = datetime.now(TZ)
            time_str = now_tz.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            name = record.name.split('.')[-1][:20]
            msg = record.getMessage()
            return f"{time_str} | {name:<20} | {record.levelname:<7} | {msg}"

    def _should_log(self, key: str) -> bool:
        """БЫСТРАЯ проверка anti-spam"""
        now = time.time()
        last = self._last_logs.get(key)
        if last:
            if now - last[0] < self._log_cooldown:
                return False
        self._last_logs[key] = (now, 1)
        return True

    # ========== ОСНОВНЫЕ МЕТОДЫ ЛОГГИРОВАНИЯ ==========

    def debug(self, message: str, key: str = None, exc_info: bool = False):
        """Логирование отладочного сообщения"""
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self._ensure_file_handlers()
        self.logger.debug(message)

    def info(self, message: str, key: str = None, exc_info: bool = False):
        """Логирование информационного сообщения"""
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self._ensure_file_handlers()
        self.logger.info(message)

    def success(self, message: str, key: str = None, exc_info: bool = False):
        """Логирование сообщения об успешном действии"""
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self._ensure_file_handlers()
        self.logger.info(f"{EMOJI['success']} {message}")

    def warning(self, message: str, key: str = None, exc_info: bool = False):
        """Логирование предупреждения"""
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self._ensure_file_handlers()
        self.logger.warning(f"{EMOJI['warning']} {message}")

    def error(self, message: str, key: str = None, exc_info: bool = False):
        """Логирование сообщения об ошибке"""
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self._ensure_file_handlers()
        if exc_info:
            tb = traceback.format_exc()
            if tb and tb != "NoneType: None\n":
                self.logger.error(f"{EMOJI['error']} {message}\n{tb}")
            else:
                self.logger.error(f"{EMOJI['error']} {message}")
        else:
            self.logger.error(f"{EMOJI['error']} {message}")

    def exception(self, message: str, key: str = None):
        """Логирование исключения с полной трассировкой"""
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self._ensure_file_handlers()
        self.logger.exception(f"{EMOJI['error']} {message}")

    def critical(self, message: str, key: str = None):
        """Логирование критической ошибки"""
        if self._filter_noise(message):
            return
        if key and not self._should_log(key):
            return
        self._ensure_file_handlers()
        self.logger.critical(f"{EMOJI['critical']} {message}")

    # ========== ТОРГОВЫЕ МЕТОДЫ ЛОГГИРОВАНИЯ ==========

    def trade(self, ticker: str, direction: str, quantity: int, price: float):
        """Логирование торговой операции"""
        emoji = EMOJI['buy'] if direction == 'BUY' else EMOJI['sell']
        self._ensure_file_handlers()
        self.logger.info(f"{emoji} {direction} {quantity} {ticker} @ {price:.2f} ₽")

    def trade_profit(self, ticker: str, profit_pct: float, profit_amount: float):
        """Логирование прибыли/убытка по сделке"""
        emoji = EMOJI['profit'] if profit_amount > 0 else EMOJI['loss']
        self._ensure_file_handlers()
        self.logger.info(f"{emoji} {ticker}: {profit_pct:+.2f}% ({profit_amount:+.2f} ₽)")

    def balance(self, amount: float, key: str = "balance"):
        """Логирование текущего баланса"""
        if not self._should_log(key):
            return
        self._ensure_file_handlers()
        self.logger.info(f"{EMOJI['money']} Баланс: {amount:,.2f} ₽")

    def position(self, ticker: str, quantity: float, price: float, pnl: float = None):
        """Логирование информации о позиции"""
        pnl_str = f" | P&L: {pnl:+.2f} ₽" if pnl is not None else ""
        self._ensure_file_handlers()
        self.logger.info(f"{EMOJI['chart']} {ticker}: {quantity} шт @ {price:.2f} ₽{pnl_str}")

    def margin_status(self, margin_rate: float, used_margin: float, available_margin: float):
        """Логирование статуса маржи"""
        emoji = "🔴" if margin_rate >= 85 else "🟡" if margin_rate >= 70 else "🟢"
        self._ensure_file_handlers()
        self.logger.info(
            f"{emoji} Маржа: {margin_rate:.1f}% | Использовано: {used_margin:.2f}₽ | Доступно: {available_margin:.2f}₽")

    def separator(self, title: str = None):
        """Логирование разделительной линии"""
        self._ensure_file_handlers()
        if title:
            self.logger.info(f"{Colors.CYAN}{'=' * 60}{Colors.RESET}")
            self.logger.info(f"{Colors.BOLD}{title}{Colors.RESET}")
            self.logger.info(f"{Colors.CYAN}{'=' * 60}{Colors.RESET}")
        else:
            self.logger.info(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

    # ========== УТИЛИТЫ ==========

    def get_log_size(self) -> int:
        """Возвращает размер основного лог-файла в байтах"""
        return self.log_file.stat().st_size if self.log_file and self.log_file.exists() else 0

    def get_log_size_mb(self) -> float:
        """Возвращает размер основного лог-файла в мегабайтах"""
        return self.get_log_size() / (1024 * 1024)

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику работы логгера"""
        return {
            'log_size_mb': self.get_log_size_mb(),
            'error_file_size_mb': self.error_file.stat().st_size / (
                        1024 * 1024) if self.error_file and self.error_file.exists() else 0,
            'handlers_count': len(self.logger.handlers),
            'level': logging.getLevelName(self.logger.level)
        }


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
bomb = BombLogger()


# ========== УТИЛИТЫ ДЛЯ ПРОСТОГО ИСПОЛЬЗОВАНИЯ ==========
def info(msg: str):
    """Логирование информационного сообщения"""
    bomb.info(msg)


def success(msg: str):
    """Логирование сообщения об успешном действии"""
    bomb.success(msg)


def error(msg: str, exc_info: bool = False):
    """Логирование сообщения об ошибке"""
    bomb.error(msg, exc_info=exc_info)


def warning(msg: str):
    """Логирование предупреждения"""
    bomb.warning(msg)


def debug(msg: str):
    """Логирование отладочного сообщения"""
    bomb.debug(msg)


def exception(msg: str):
    """Логирование исключения с полной трассировкой"""
    bomb.exception(msg)


def critical(msg: str):
    """Логирование критической ошибки"""
    bomb.critical(msg)


def trade(ticker: str, direction: str, qty: int, price: float):
    """Логирование торговой операции"""
    bomb.trade(ticker, direction, qty, price)


def trade_profit(ticker: str, profit_pct: float, profit_amount: float):
    """Логирование прибыли/убытка по сделке"""
    bomb.trade_profit(ticker, profit_pct, profit_amount)


def balance(amount: float):
    """Логирование текущего баланса"""
    bomb.balance(amount)


def margin_status(margin_rate: float, used_margin: float, available_margin: float):
    """Логирование статуса маржи"""
    bomb.margin_status(margin_rate, used_margin, available_margin)


def sep(title: str = None):
    """Логирование разделительной линии"""
    bomb.separator(title)


# ========== СОВМЕСТИМОСТЬ ==========
class AdvancedLogger:
    """Класс для обратной совместимости со старым API"""

    def __init__(self, config):
        self.config = config
        self.logger = bomb.logger

    def get_logger(self):
        return self.logger


def setup_logger(name: str = "TradingBot", log_file: str = "logs/bot.log", level: str = "INFO") -> logging.Logger:
    """Настройка логгера для обратной совместимости"""
    return bomb.logger


def setup_logging_prod(log_level: str = "INFO", enable_rich: bool = True) -> logging.Logger:
    """Настройка продукционного логирования"""
    return bomb.logger


def setup_quiet_logging(log_level: str = "INFO") -> logging.Logger:
    """Настройка тихого логирования"""
    return bomb.logger


def get_logger(name: str) -> logging.Logger:
    """Получение логгера по имени"""
    return bomb.logger


def print_success(message: str, exc_info: bool = False):
    """Вывод сообщения об успехе (алиас success)"""
    bomb.success(message)


def print_error(message: str, exc_info: bool = False):
    """Вывод сообщения об ошибке (алиас error)"""
    bomb.error(message, exc_info=exc_info)


def print_warning(message: str, exc_info: bool = False):
    """Вывод предупреждения (алиас warning)"""
    bomb.warning(message)


def print_info(message: str, exc_info: bool = False):
    """Вывод информационного сообщения (алиас info)"""
    bomb.info(message)


def build_portfolio_table(portfolio_data: Dict[str, Any]) -> Any:
    """Построение таблицы портфеля (заглушка)"""
    return None


def build_performance_table(metrics: Dict[str, Any]) -> Any:
    """Построение таблицы производительности (заглушка)"""
    return None


def separator(title: str = None):
    """Алиас для sep() - логирование разделительной линии"""
    bomb.separator(title)


# ========== ОЧИСТКА ДУБЛИРУЮЩИХСЯ ЛОГГЕРОВ ==========
def _cleanup_duplicate_loggers():
    """Очистка дублирующихся логгеров для предотвращения дублирования записей"""
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

# ========== ОСНОВНОЙ ЭКЗЕМПЛЯР ДЛЯ СОВМЕСТИМОСТИ ==========
# Этот logger используется в tbank_client.py и других модулях
logger = bomb

# Обновляем __all__
__all__ = [
    'AdvancedLogger', 'setup_logger', 'setup_logging_prod', 'setup_quiet_logging',
    'get_logger', 'build_portfolio_table', 'build_performance_table',
    'print_success', 'print_error', 'print_warning', 'print_info',
    'bomb', 'logger', 'info', 'success', 'error', 'warning', 'debug', 'exception', 'critical',
    'trade', 'trade_profit', 'balance', 'margin_status', 'sep', 'separator',
    'Colors', 'EMOJI'
]