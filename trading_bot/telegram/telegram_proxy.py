# telegram_proxy.py
# telegram_proxy.py - УПРОЩЁННАЯ ВЕРСИЯ
"""Простой детектор доступности Telegram API (без прокси)"""

import requests
import time
from typing import Optional

from ..logger import debug


class TelegramProxyDetector:
    """Простой детектор для Telegram API - без прокси, без лишних запросов"""

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self._session: Optional[requests.Session] = None
        self._available = None
        self._last_check = 0
        self._check_interval = 60  # Проверяем раз в минуту

    def _check_api(self) -> bool:
        """Быстрая проверка доступности API"""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            response = requests.get(url, timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def should_use_telegram(self) -> bool:
        """Проверка, доступен ли Telegram API"""
        now = time.time()

        # Используем кэш
        if self._available is not None and (now - self._last_check) < self._check_interval:
            return self._available

        self._available = self._check_api()
        self._last_check = now

        if not self._available:
            print("⚠️ Telegram API недоступен, уведомления отключены")

        return self._available

    def get_session(self) -> requests.Session:
        """Получение сессии"""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                'User-Agent': 'TradingBot/1.0'
            })
        return self._session


# Глобальный детектор
_telegram_detector = None


def get_telegram_detector(bot_token: str) -> TelegramProxyDetector:
    """Получение глобального детектора"""
    global _telegram_detector
    if _telegram_detector is None or _telegram_detector.bot_token != bot_token:
        _telegram_detector = TelegramProxyDetector(bot_token)
    return _telegram_detector