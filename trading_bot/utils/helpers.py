"""Вспомогательные функции"""

import time
from functools import wraps


def safe_float(value, default: float = 0.0) -> float:
    """Безопасное преобразование в float"""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def safe_divide(a, b, default: float = 0.0) -> float:
    """Безопасное деление с защитой от деления на ноль"""
    try:
        a_f, b_f = float(a), float(b)
        return a_f / b_f if b_f != 0 else default
    except (TypeError, ValueError):
        return default


def retry_on_fail(max_retries: int = 3, delay: float = 1):
    """Декоратор для повторных попыток при ошибках"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    # Ленивый импорт для избежания циклических зависимостей
                    from ..logger import warning
                    warning(f"⚠️ Ошибка {func.__name__}, попытка {attempt + 2}/{max_retries}: {e}")
                    time.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator