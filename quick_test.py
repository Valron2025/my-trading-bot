# quick_test.py без проблемных импортов
# -*- coding: utf-8 -*-
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

print("🔍 Тестируем импорты...")

# Проверяем только config (он работает)
try:
    from trading_bot.config import TradingConfig as Config
    print("✅ config.py - OK")
except Exception as e:
    print(f"❌ config.py: {e}")

# Проверяем другие модули, которые не зависят от models.py
try:
    from trading_bot.logger import bomb  # Прямой импорт без logger.py
    print("✅ bomb logger - OK")
except Exception as e:
    print(f"❌ bomb logger: {e}")

print("\n📦 Проверка критических модулей:")

modules_to_test = [
    'trading_bot.config',
    'trading_bot.utils',
    'trading_bot.cache',
]

for module in modules_to_test:
    try:
        __import__(module)
        print(f"   ✅ {module}")
    except Exception as e:
        print(f"   ❌ {module}: {e}")

print("\n🔧 НУЖНО ИСПРАВИТЬ models.py строка 628")