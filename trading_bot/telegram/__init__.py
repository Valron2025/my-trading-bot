"""Telegram module - уведомления и управление через Telegram"""

from .telegram_notifier import TelegramNotifier, get_telegram_notifier, telegram
from .telegram_polling import start_polling_in_background
from .telegram_proxy import TelegramProxyDetector, get_telegram_detector

# Настройка логгера
import logging
logger = logging.getLogger(__name__)

__all__ = [
    "TelegramNotifier",
    "get_telegram_notifier",
    "telegram",
    "start_polling_in_background",
    "TelegramProxyDetector",
    "get_telegram_detector",
]

logger.info("📱 Telegram module initialized")
