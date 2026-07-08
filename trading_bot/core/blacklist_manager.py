"""Менеджер чёрного списка с автоматической блокировкой"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from threading import Lock

from trading_bot.logger import info, warning, error, debug
from trading_bot.utils.time_utils import get_moscow_time


class BlacklistManager:
    """Автоматическое управление чёрным списком тикеров"""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self._permanent_blacklist: set = set()
        self._temporary_blacklist: Dict[str, datetime] = {}
        self._error_counts: Dict[str, int] = {}

        self._error_threshold = 3
        self._block_ttl_hours = 24

        info("✅ BlacklistManager инициализирован")

    def add_permanent(self, ticker: str):
        ticker = ticker.upper()
        self._permanent_blacklist.add(ticker)
        info(f"⛔ {ticker} добавлен в ПЕРМАНЕНТНЫЙ чёрный список")

    def add_temporary(self, ticker: str, ttl_minutes: int = 60):
        ticker = ticker.upper()
        expires_at = get_moscow_time() + timedelta(minutes=ttl_minutes)
        self._temporary_blacklist[ticker] = expires_at
        warning(f"⛔ {ticker} добавлен во временный чёрный список на {ttl_minutes} минут")

    def report_error(self, ticker: str, error_code: str = ""):
        ticker = ticker.upper()

        # ✅ НЕ БЛОКИРУЕМ ПРИ ОПРЕДЕЛЁННЫХ ОШИБКАХ
        if error_code in ["30079", "30049", "30014"]:
            return

        self._error_counts[ticker] = self._error_counts.get(ticker, 0) + 1
        warning(f"📊 {ticker}: ошибка #{self._error_counts[ticker]} (код: {error_code})")

        # ✅ БЛОКИРУЕМ ТОЛЬКО ПОСЛЕ 3 ОШИБОК ПОДРЯД
        if self._error_counts[ticker] >= self._error_threshold:
            warning(f"🚨 {ticker}: превышен порог ошибок - АВТОБЛОКИРОВКА!")
            self.add_temporary(ticker, ttl_minutes=self._block_ttl_hours * 60)
            self._error_counts[ticker] = 0

    def report_success(self, ticker: str):
        """Сбросить счётчик ошибок при успешной операции"""
        ticker = ticker.upper()
        if ticker in self._error_counts:
            del self._error_counts[ticker]
            debug(f"✅ {ticker}: счётчик ошибок сброшен")

    def is_blocked(self, ticker: str) -> Tuple[bool, str]:
        ticker = ticker.upper()

        if ticker in self._permanent_blacklist:
            return True, "в перманентном чёрном списке"

        if ticker in self._temporary_blacklist:
            expires_at = self._temporary_blacklist[ticker]
            if get_moscow_time() < expires_at:
                minutes_left = int((expires_at - get_moscow_time()).total_seconds() / 60)
                return True, f"во временном чёрном списке (ещё {minutes_left} мин)"
            else:
                del self._temporary_blacklist[ticker]

        return False, ""

    def clear_temporary(self, ticker: str = None):
        if ticker:
            ticker = ticker.upper()
            if ticker in self._temporary_blacklist:
                del self._temporary_blacklist[ticker]
                info(f"✅ {ticker} удалён из временного чёрного списка")
        else:
            count = len(self._temporary_blacklist)
            self._temporary_blacklist.clear()
            info(f"✅ Временный чёрный список очищен (удалено {count} тикеров)")

    def get_blocked_list(self) -> List[str]:
        now = get_moscow_time()
        expired = [t for t, exp in self._temporary_blacklist.items() if now >= exp]
        for t in expired:
            del self._temporary_blacklist[t]

        return list(self._permanent_blacklist) + list(self._temporary_blacklist.keys())

    def get_status(self) -> Dict:
        return {
            'permanent_blocked': len(self._permanent_blacklist),
            'temporary_blocked': len(self._temporary_blacklist),
            'blocked_list': self.get_blocked_list(),
            'error_counts': dict(self._error_counts)
        }


blacklist_manager = BlacklistManager()