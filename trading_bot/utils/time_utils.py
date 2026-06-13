"""Работа с временем и часовыми поясами"""

from datetime import datetime, timedelta, timezone

# Часовой пояс МСК (UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))


def get_moscow_time() -> datetime:
    """Возвращает текущее время в часовом поясе МСК"""
    return datetime.now(MOSCOW_TZ)


def get_moscow_time_iso() -> str:
    """Возвращает текущее время в ISO формате"""
    return get_moscow_time().isoformat()


def format_time_for_log() -> str:
    """Форматирует время для логов"""
    return get_moscow_time().strftime('%Y-%m-%d %H:%M:%S')