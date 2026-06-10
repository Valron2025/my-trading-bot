"""Асинхронные утилиты"""

import asyncio
from typing import Any, Optional


def run_async(coro, timeout: Optional[float] = None) -> Any:
    """
    Безопасный запуск асинхронной корутины из синхронного кода

    Args:
        coro: Корутина для выполнения
        timeout: Таймаут в секундах (опционально)

    Returns:
        Результат корутины или None при ошибке
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if timeout:
            return loop.run_until_complete(asyncio.wait_for(coro, timeout))
        else:
            return loop.run_until_complete(coro)
    except asyncio.TimeoutError:
        from trading_bot.logger import warning
        warning(f"⏰ Таймаут выполнения асинхронной задачи ({timeout}с)")
        return None
    except asyncio.CancelledError:
        return None
    finally:
        # Закрываем все ожидающие задачи
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


async def safe_gather(*coros, return_exceptions: bool = True, timeout: Optional[float] = None):
    """
    Безопасный сбор результатов нескольких корутин

    Args:
        *coros: Корутины для выполнения
        return_exceptions: Возвращать исключения или поднимать их
        timeout: Таймаут выполнения

    Returns:
        Список результатов
    """
    if timeout:
        try:
            return await asyncio.wait_for(
                asyncio.gather(*coros, return_exceptions=return_exceptions),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            from trading_bot.logger import warning
            warning(f"⏰ Таймаут выполнения safe_gather ({timeout}с)")
            return [None] * len(coros)
    else:
        return await asyncio.gather(*coros, return_exceptions=return_exceptions)