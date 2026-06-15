#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ДИАГНОСТИЧЕСКИЙ СКРИПТ - ИСПРАВЛЕННАЯ ВЕРСИЯ
"""

import os
import sys
from pathlib import Path


# Цвета для вывода
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


def print_header(text):
    print(f"\n{Colors.CYAN}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{text}{Colors.RESET}")
    print(f"{Colors.CYAN}{'=' * 60}{Colors.RESET}")


def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")


def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")


def print_warning(text):
    print(f"{Colors.YELLOW}⚠️ {text}{Colors.RESET}")


def print_info(text):
    print(f"{Colors.BLUE}ℹ️ {text}{Colors.RESET}")


def print_value(key, value):
    print(f"   {Colors.CYAN}{key}:{Colors.RESET} {value}")


# ============================================================
# 1. ПРОВЕРКА ОКРУЖЕНИЯ
# ============================================================

print_header("🔍 ДИАГНОСТИКА ТОРГОВОГО БОТА")
print_header("1. ПРОВЕРКА ОКРУЖЕНИЯ")

print_info(f"Python версия: {sys.version}")
print_info(f"Текущая директория: {os.getcwd()}")

# Проверка файла .env
env_path = Path(".env")
if env_path.exists():
    print_success(f".env файл существует ({env_path.stat().st_size} байт)")
    with open(env_path, 'r', encoding='utf-8') as f:
        content = f.read()
        if "TBANK_TOKEN" in content:
            lines = content.split('\n')
            for line in lines:
                if line.startswith('TBANK_TOKEN'):
                    token_value = line.split('=')[1].strip()
                    if token_value and token_value != 'emulator':
                        masked = token_value[:15] + "..." + token_value[-5:] if len(token_value) > 20 else token_value
                        print_success(f"   TBANK_TOKEN найден: {masked}")
                    else:
                        print_error(f"   TBANK_TOKEN: {token_value} (некорректный токен)")
                elif line.startswith('TBANK_ACCOUNT_ID'):
                    print_info(f"   TBANK_ACCOUNT_ID: {line.split('=')[1].strip()}")
else:
    print_error(".env файл НЕ СУЩЕСТВУЕТ!")

# ============================================================
# 2. ПРОВЕРКА ФАЙЛА КОНФИГУРАЦИИ
# ============================================================

print_header("2. ПРОВЕРКА ФАЙЛА config.py")

config_path = Path("trading_bot/config.py")
if config_path.exists():
    print_success(f"config.py существует ({config_path.stat().st_size} байт)")

    # Читаем содержимое config.py
    with open(config_path, 'r', encoding='utf-8') as f:
        config_content = f.read()

    # Проверяем наличие атрибутов
    has_is_emulator = 'is_emulator' in config_content
    has_tbank_token = 'tbank_token' in config_content
    has_use_short = 'use_short' in config_content

    print_info("Найденные атрибуты в config.py:")
    print_value("is_emulator", "✅ ДА" if has_is_emulator else "❌ НЕТ")
    print_value("tbank_token", "✅ ДА" if has_tbank_token else "❌ НЕТ")
    print_value("use_short", "✅ ДА" if has_use_short else "❌ НЕТ")

    if not has_is_emulator:
        print_warning("⚠️ В config.py ОТСУТСТВУЕТ атрибут 'is_emulator'!")
        print_warning("   Это НОРМАЛЬНО, если вы не используете эмулятор.")
        print_warning("   Просто не обращайте внимания на эту ошибку.")

else:
    print_error("trading_bot/config.py НЕ НАЙДЕН!")

# ============================================================
# 3. ПРОВЕРКА ИНИЦИАЛИЗАЦИИ (без проблем с кодировкой)
# ============================================================

print_header("3. ПРОВЕРКА ФАЙЛА __init__.py")

init_path = Path("trading_bot/__init__.py")
if init_path.exists():
    print_success(f"trading_bot/__init__.py существует ({init_path.stat().st_size} байт)")

    # Читаем в бинарном режиме, чтобы избежать проблем с кодировкой
    with open(init_path, 'rb') as f:
        init_content_bytes = f.read()

    # Проверяем наличие эмулятора (в байтах)
    if b'emulator' in init_content_bytes.lower():
        print_error("⚠️ В trading_bot/__init__.py обнаружен код эмулятора!")

        # Пытаемся найти строки (в текстовом режиме)
        try:
            with open(init_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            for i, line in enumerate(lines, 1):
                if 'emulator' in line.lower():
                    print_warning(f"   Строка {i}: {line.strip()[:80]}")
        except:
            pass

        print_warning("\n   РЕШЕНИЕ: Удалите или закомментируйте блок с эмулятором!")
    else:
        print_success("Эмулятор не обнаружен")
else:
    print_error("trading_bot/__init__.py не найден!")

# ============================================================
# 4. ЗАГРУЗКА КОНФИГУРАЦИИ (без is_emulator)
# ============================================================

print_header("4. ЗАГРУЗКА КОНФИГУРАЦИИ")

try:
    # Импортируем config, но игнорируем is_emulator
    from trading_bot.config import config

    print_success("config загружен")

    # Проверяем основные параметры
    print_info("КЛЮЧЕВЫЕ ПАРАМЕТРЫ:")

    if hasattr(config, 'tbank_token'):
        token = config.tbank_token
        masked = token[:15] + "..." + token[-5:] if token and len(token) > 20 else str(token)
        print_value("tbank_token", masked)
    else:
        print_error("tbank_token ОТСУТСТВУЕТ!")

    if hasattr(config, 'use_short'):
        print_value("use_short", config.use_short)
    else:
        print_warning("use_short ОТСУТСТВУЕТ")

    if hasattr(config, 'take_profit_pct'):
        print_value("take_profit_pct", f"{config.take_profit_pct}%")

    if hasattr(config, 'stop_loss_pct'):
        print_value("stop_loss_pct", f"{config.stop_loss_pct}%")

    if hasattr(config, 'max_positions'):
        print_value("max_positions", config.max_positions)

    if hasattr(config, 'min_trade_amount'):
        print_value("min_trade_amount", f"{config.min_trade_amount}₽")

    if hasattr(config, 'total_capital'):
        print_value("total_capital", f"{config.total_capital:.2f}₽")

    # Проверяем режим работы
    if hasattr(config, 'is_emulator') and config.is_emulator:
        print_error("⚠️ КРИТИЧЕСКАЯ ОШИБКА: config.is_emulator = True!")
        print_error("   Бот работает в РЕЖИМЕ ЭМУЛЯТОРА, а не реальной торговли!")
    else:
        print_success("Режим: РЕАЛЬНАЯ ТОРГОВЛЯ (или is_emulator не задан)")

except Exception as e:
    print_error(f"Ошибка загрузки config: {e}")
    import traceback

    traceback.print_exc()

# ============================================================
# 5. ПРОВЕРКА API СОЕДИНЕНИЯ
# ============================================================

print_header("5. ПРОВЕРКА API СОЕДИНЕНИЯ")

try:
    from trading_bot.api.tbank_client import tbank

    print_info("Проверка API...")

    # Проверяем токен
    if hasattr(tbank, 'token') and tbank.token and tbank.token != "emulator":
        print_success(f"Токен установлен: {tbank.token[:15]}...")
    else:
        print_error(f"Токен НЕ УСТАНОВЛЕН или ЭМУЛЯТОР")

    # Пробуем получить баланс
    try:
        result = tbank.get_available_funds()
        if result and len(result) >= 2:
            available, total, blocked = result
            print_success(f"API ДОСТУПЕН! Баланс: {total:.2f}₽, Свободно: {available:.2f}₽")
        else:
            print_error("API вернул некорректный результат")
    except Exception as e:
        print_error(f"API НЕ ДОСТУПЕН: {str(e)[:100]}")

        if "invalid token" in str(e).lower():
            print_error("   → НЕВЕРНЫЙ ТОКЕН! Проверьте TBANK_TOKEN в .env")
        elif "connection" in str(e).lower():
            print_error("   → НЕТ СОЕДИНЕНИЯ! Проверьте интернет")
        elif "timeout" in str(e).lower():
            print_error("   → ТАЙМАУТ! API медленно отвечает")

except Exception as e:
    print_error(f"Ошибка импорта tbank: {e}")
    import traceback

    traceback.print_exc()

# ============================================================
# 6. ПРОВЕРКА ПОЗИЦИЙ
# ============================================================

print_header("6. ПРОВЕРКА ПОЗИЦИЙ")

try:
    from trading_bot.api.tbank_client import tbank

    positions = tbank.get_positions()

    if positions:
        print_info(f"Найдено позиций: {len(positions)}")
        for pos in positions[:5]:
            ticker = tbank._get_ticker_by_figi(pos['figi']) or pos['figi'][:8]
            qty = pos['quantity']
            side = "SHORT" if qty < 0 else "LONG"
            avg = pos['avg_price']
            print_value(f"   {ticker}", f"{side} {abs(qty)} шт по {avg:.2f}₽")
    else:
        print_info("Нет открытых позиций")

except Exception as e:
    print_error(f"Ошибка получения позиций: {e}")

# ============================================================
# 7. ПРОВЕРКА АКТИВНЫХ ЗАЯВОК
# ============================================================

print_header("7. ПРОВЕРКА АКТИВНЫХ ЗАЯВОК")

try:
    from trading_bot.api.tbank_client import tbank

    orders = tbank.get_active_orders()

    if orders:
        print_warning(f"Найдено активных заявок: {len(orders)}")
        for order in orders[:10]:
            ticker = order.get('ticker', 'unknown')
            direction = order.get('direction', '?')
            price = order.get('price', 0)
            qty = order.get('quantity', 0)
            print_value(f"   {ticker}", f"{direction} {qty} шт по {price:.2f}₽")

        if len(orders) > 1:
            print_warning("⚠️ Есть дублирующиеся заявки! Запустите очистку.")
    else:
        print_success("Нет активных заявок")

except Exception as e:
    print_error(f"Ошибка получения заявок: {e}")

# ============================================================
# 8. ИТОГОВЫЙ ОТЧЁТ
# ============================================================

print_header("📊 ИТОГОВЫЙ ДИАГНОСТИЧЕСКИЙ ОТЧЁТ")

# Собираем проблемы
issues = []

# Проверяем токен
try:
    from trading_bot.api.tbank_client import tbank

    if not hasattr(tbank, 'token') or not tbank.token or tbank.token == "emulator":
        issues.append("🔴 КРИТИЧЕСКАЯ: Некорректный токен! Проверьте .env")
    else:
        print_success("Токен установлен корректно")
except:
    pass

# Проверяем API доступ
try:
    from trading_bot.api.tbank_client import tbank

    available, total, _ = tbank.get_available_funds()
    print_success(f"API работает, капитал: {total:.2f}₽")
except Exception as e:
    issues.append(f"🟡 API НЕ ДОСТУПЕН: {str(e)[:80]}")

# Проверяем дублирующиеся заявки
try:
    from trading_bot.api.tbank_client import tbank

    orders = tbank.get_active_orders()
    if len(orders) > 1:
        issues.append(f"🟡 Есть дублирующиеся заявки ({len(orders)} шт)")
except:
    pass

# Вывод результатов
if issues:
    print_error(f"\nНАЙДЕНО ПРОБЛЕМ: {len(issues)}")
    for issue in issues:
        print_warning(f"   • {issue}")
else:
    print_success("\n✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ! Бот должен работать корректно.")

print_header("🏁 ДИАГНОСТИКА ЗАВЕРШЕНА")