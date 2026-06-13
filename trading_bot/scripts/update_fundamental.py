#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для ручного обновления фундаментальных данных
Запуск: python scripts/update_fundamental.py
ИЛИ: python -m trading_bot.scripts.update_fundamental
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем корневую директорию проекта в PYTHONPATH
project_root = Path(__file__).parent.parent.parent  # F:/PROJECTS/my-trading-bot
sys.path.insert(0, str(project_root))

# Также добавляем текущую директорию
sys.path.insert(0, str(Path(__file__).parent.parent))

# Проверка, что путь добавлен
print(f"Project root: {project_root}")
print(f"Python path: {sys.path[:3]}")

from trading_bot.analysis.fundamental_updater import FundamentalUpdater
from trading_bot.logger import info, success, sep


async def main():
    sep("=")
    info("🔄 Ручное обновление фундаментальных данных")
    sep("=")

    updater = FundamentalUpdater()
    await updater.update_all()
    updater.print_report()

    sep("=")
    success("✅ Ручное обновление завершено")
    sep("=")


if __name__ == "__main__":
    asyncio.run(main())