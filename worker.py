#!/usr/bin/env python3
"""Background worker для торгового бота - с Telegram"""

import os
import sys
import time
import signal
import threading
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def ping_health_check():
    """Пинг health check каждые 30 секунд"""
    while True:
        time.sleep(30)
        try:
            requests.get("http://localhost:8000/health", timeout=2)
        except:
            pass


# ЗАПУСКАЕМ ПИНГ ДЛЯ HEALTH CHECK
threading.Thread(target=ping_health_check, daemon=True).start()


print("=" * 60)
print("🚀 TRADING BOT WORKER STARTING")
print(f"   PID: {os.getpid()}")
print("=" * 60)

# Диагностика времени
from trading_bot.utils.time_utils import get_moscow_time

now = get_moscow_time()
print(f"🕐 Текущее время МСК: {now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"🕐 Текущее время UTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")

if not os.getenv("TBANK_TOKEN"):
    print("❌ TBANK_TOKEN not found!")
    sys.exit(1)

os.environ['BOT_ALREADY_STARTED'] = 'true'
os.environ['WORKER_MODE'] = 'true'

_shutting_down = False
#  _telegram_bot_instance = None


def signal_handler(signum, frame):
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    print(f"\n⚠️ Received signal {signum}, shutting down worker...")
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# def init_telegram(bot):
#     """Инициализация Telegram бота"""
#     global _telegram_bot_instance
#
#     if not os.getenv("TELEGRAM_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
#         print("⚠️ Telegram не настроен (нет TOKEN или CHAT_ID)")
#         return None
#
#     try:
#         from trading_bot.telegram.telegram_bot import init_telegram_bot
#         from app import set_telegram_bot
#
#         print("📱 Инициализация Telegram бота...")
#         _telegram_bot_instance = init_telegram_bot(bot)
#
#         if _telegram_bot_instance:
#             _telegram_bot_instance.start()
#             set_telegram_bot(_telegram_bot_instance)
#             print("✅ Telegram бот запущен")
#
#             # Отправляем приветственное сообщение
#             try:
#                 _telegram_bot_instance._send_message("🤖 Торговый бот запущен на Render!")
#             except Exception as e:
#                 print(f"⚠️ Не удалось отправить приветствие: {e}")
#
#             return _telegram_bot_instance
#         else:
#             print("⚠️ Telegram бот не инициализирован")
#             return None
#
#     except Exception as e:
#         print(f"⚠️ Ошибка инициализации Telegram: {e}")
#         return None


def main():
    print("📦 Importing trading bot...")
    from trading_bot import get_trading_bot

    print("🚀 Creating bot instance...")
    bot = get_trading_bot()

    print("🚀 Initializing advanced managers...")
    bot.init_advanced_managers()

    # # ✅ ЗАПУСКАЕМ ФУНДАМЕНТАЛЬНЫЙ АПДЕЙТЕР
    # print("📊 Starting fundamental updater...")
    # import asyncio
    # import threading
    #
    # def run_fundamental_updater():
    #     loop = asyncio.new_event_loop()
    #     asyncio.set_event_loop(loop)
    #     try:
    #         if hasattr(bot, 'fundamental_updater') and bot.fundamental_updater:
    #             # Не ждём завершения, запускаем в фоне
    #             loop.create_task(bot.start_fundamental_updater())
    #             loop.run_until_complete(asyncio.sleep(1))
    #             print("✅ Фундаментальный апдейтер запущен")
    #     except Exception as e:
    #         print(f"⚠️ Ошибка фундаментального апдейтера: {e}")
    #     finally:
    #         loop.close()
    #
    # thread = threading.Thread(target=run_fundamental_updater, daemon=True)
    # thread.start()
    # print("✅ Fundamental updater started")

    # ✅ ЗАПУСКАЕМ TELEGRAM POLLING
    print("📱 Starting Telegram polling...")
    from trading_bot.telegram.telegram_polling import start_polling_in_background
    start_polling_in_background()
    print("✅ Telegram polling started")

    print("🚀 Starting trading bot...")
    bot.start()

    print("✅ Trading bot is running")
    print("=" * 60)

    # Держим процесс живым
    last_status = time.time()
    last_time_check = time.time()

    while not _shutting_down:
        time.sleep(10)

        if time.time() - last_status > 30:
            last_status = time.time()
            cycle_count = getattr(bot, '_cycle_count', 0)
            print(f"💓 Bot status: cycle_count={cycle_count}, running={bot._running}")

        if time.time() - last_time_check > 300:
            last_time_check = time.time()
            now = get_moscow_time()
            print(f"🕐 Текущее время МСК: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    print("🛑 Worker stopped")


if __name__ == "__main__":
    main()