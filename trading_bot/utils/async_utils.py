"""Асинхронные утилиты"""

import asyncio
from typing import Any


def run_async(coro) -> Any:
    """
    Безопасный запуск асинхронной корутины из синхронного кода

    Всегда создаёт новый event loop, выполняет корутину и закрывает loop.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()