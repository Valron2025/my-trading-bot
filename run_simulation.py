# run_simulation.py
# -*- coding: utf-8 -*-
import os
os.environ['SIMULATION_MODE'] = 'true'

from trading_bot.bot import TradingBot
from trading_bot.logger import success, error

try:
    bot = TradingBot()
    success("🚀 Запуск бота в симуляционном режиме")
    bot.start()
except KeyboardInterrupt:
    success("\n👋 Остановка бота")
except Exception as e:
    error(f"❌ Ошибка: {e}")