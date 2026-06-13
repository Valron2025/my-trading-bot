#!/usr/bin/env python3
"""Единая точка входа для Render - упрощённая версия"""

import os
import sys
import threading
import time
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("🚀 TRADING BOT STARTING ON RENDER")
print(f"   PID: {os.getpid()}")
print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

logging.basicConfig(level=logging.INFO)

if not os.getenv("TBANK_TOKEN"):
    print("❌ TBANK_TOKEN not found!")
    sys.exit(1)

os.environ['BOT_ALREADY_STARTED'] = 'true'

_trading_bot_instance = None


def init_trading_bot():
    global _trading_bot_instance
    try:
        print("🔄 Initializing trading bot...")
        from trading_bot import get_trading_bot
        _trading_bot_instance = get_trading_bot()

        # Запускаем в отдельном потоке
        def run_bot():
            try:
                _trading_bot_instance.start()
            except Exception as e:
                print(f"❌ Bot crashed: {e}")

        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        print("✅ Trading bot started")
        return _trading_bot_instance
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def init_telegram():
    try:
        from trading_bot.telegram.telegram_bot import init_telegram_bot
        from app import set_telegram_bot

        time.sleep(2)
        if _trading_bot_instance:
            bot = init_telegram_bot(_trading_bot_instance)
            if bot:
                bot.start()
                set_telegram_bot(bot)
                print("✅ Telegram bot started")
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("📦 INITIALIZING COMPONENTS")
    print("=" * 60)

    trading_bot = init_trading_bot()
    time.sleep(2)
    init_telegram()

    print("\n" + "=" * 60)
    print("🚀 Trading bot is running")
    print("✅ Flask server will be started by gunicorn")
    print("=" * 60 + "\n")

    # Не запускаем Flask здесь — это делает gunicorn
    # Просто держим поток живым
    while True:
        time.sleep(60)
        print("💓 Bot is alive...")