"""Утилиты для управления памятью"""

import gc
import sys
from typing import Dict, Any


def get_memory_usage_mb() -> float:
    """Получить использование памяти в MB"""
    try:
        import psutil
        import os
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / 1024 / 1024
    except ImportError:
        # Если psutil не установлен, используем другой метод
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def trim_caches(bot) -> Dict[str, Any]:
    """Очистка всех кэшей бота"""
    before = get_memory_usage_mb()

    # ✅ ИСПРАВЛЕНО: очищаем TTLCache вместо прямых словарей
    from trading_bot.cache import (
        price_cache, positions_cache, candles_cache,
        margin_cache, instruments_cache, analysis_cache, news_cache
    )

    # Очищаем TTLCache
    price_cache.clear()
    positions_cache.clear()
    candles_cache.clear()
    margin_cache.clear()
    instruments_cache.clear()
    analysis_cache.clear()
    news_cache.clear()

    # Очищаем кэш позиций в position_manager
    if hasattr(bot, 'position_manager') and bot.position_manager:
        if hasattr(bot.position_manager, '_figi_to_ticker_cache'):
            bot.position_manager._figi_to_ticker_cache.clear()
        if hasattr(bot.position_manager, '_temp_skip_until'):
            bot.position_manager._temp_skip_until.clear()
        if hasattr(bot.position_manager, '_temp_blacklist'):
            bot.position_manager._temp_blacklist.clear()

    # Очищаем кэш в фундаментальном анализаторе
    if hasattr(bot, 'fundamental_analyzer') and bot.fundamental_analyzer:
        bot.fundamental_analyzer.clear_cache()

    # Очищаем кэш в новостном анализаторе
    if hasattr(bot, 'news_analyzer') and bot.news_analyzer:
        if hasattr(bot.news_analyzer, 'news_cache'):
            bot.news_analyzer.news_cache.clear()

    # Очищаем кэш в техническом анализаторе
    if hasattr(bot, 'technical_analyzer') and bot.technical_analyzer:
        if hasattr(bot.technical_analyzer, '_analysis_cache'):
            bot.technical_analyzer._analysis_cache.clear()

    # Принудительная сборка мусора
    gc.collect()

    after = get_memory_usage_mb()

    return {
        'before_mb': round(before, 1),
        'after_mb': round(after, 1),
        'freed_mb': round(before - after, 1)
    }


def get_cache_stats() -> Dict[str, Any]:
    """Получение статистики всех кэшей"""
    from trading_bot.cache import (
        price_cache, positions_cache, candles_cache,
        margin_cache, instruments_cache, analysis_cache, news_cache
    )

    return {
        'price_cache': price_cache.get_stats(),
        'positions_cache': positions_cache.get_stats(),
        'candles_cache': candles_cache.get_stats(),
        'margin_cache': margin_cache.get_stats(),
        'instruments_cache': instruments_cache.get_stats(),
        'analysis_cache': analysis_cache.get_stats(),
        'news_cache': news_cache.get_stats(),
    }