#!/usr/bin/env python3
"""Точка входа для торгового бота (альтернативная)"""

import sys
import os
import atexit

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def shutdown_candle_builder():
    """Корректное завершение CandleBuilder"""
    try:
        from trading_bot.core.candle_sync_wrapper import shutdown_candle_builder
        shutdown_candle_builder()
        print("✅ CandleBuilder stopped")
    except ImportError:
        pass
    except Exception as e:
        print(f"⚠️ CandleBuilder shutdown error: {e}")


def main():
    """Запуск торгового бота"""
    print("=" * 60)
    print("🚀 T-Bank Trading Bot")
    print("=" * 60)

    from trading_bot import get_trading_bot
    from trading_bot.config import config
    from trading_bot.logger import error

    # Проверка токена
    if not config.tbank_token:
        error("❌ TBANK_TOKEN не найден в .env файле!")
        print("\nПожалуйста, создайте файл .env с переменной:")
        print("TBANK_TOKEN=ваш_токен")
        return

    # Регистрируем завершение
    atexit.register(shutdown_candle_builder)

    # Создание и запуск бота
    try:
        bot = get_trading_bot()
        bot.start()

        # Держим бота запущенным
        import time
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n⏹️ Бот остановлен пользователем")
    except Exception as e:
        error(f"❌ Критическая ошибка: {e}")
        raise


import signal


def signal_handler(sig, frame):
    """Обработчик сигналов для корректного завершения"""
    print(f"\n🛑 Получен сигнал {sig}, завершаем работу...")
    shutdown_candle_builder()
    sys.exit(0)


if __name__ == "__main__":
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    main()