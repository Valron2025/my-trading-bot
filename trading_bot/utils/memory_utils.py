"""Утилиты для управления памятью"""

import gc
import sys
from typing import Dict, Any


def get_memory_usage_mb() -> float:
    """Получить использование памяти в MB"""
    import psutil
    import os
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024


def trim_caches(bot) -> Dict[str, Any]:
    """Очистка всех кэшей бота"""
    import gc

    before = get_memory_usage_mb()

    # Очищаем кэш клиента
    if hasattr(bot, 'tbank'):
        bot.tbank._candles_cache.clear()
        bot.tbank._shares_cache = None
        bot.tbank._ticker_cache.clear()
        bot.tbank._margin_cache = None

    # Очищаем кэш позиций
    if hasattr(bot, 'position_manager'):
        bot.position_manager._figi_to_ticker_cache.clear()

    # GC
    gc.collect()

    after = get_memory_usage_mb()

    return {
        'before_mb': round(before, 1),
        'after_mb': round(after, 1),
        'freed_mb': round(before - after, 1)
    }