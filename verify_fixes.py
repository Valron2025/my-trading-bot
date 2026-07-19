#!/usr/bin/env python3
"""
ПОЛНАЯ ПРОВЕРКА ВСЕХ ИСПРАВЛЕНИЙ ДЛЯ RENDER
Запуск: python verify_fixes.py
"""

import os
import sys
import socket
import ssl
import certifi

print("=" * 70)
print("🔍 ПОЛНАЯ ПРОВЕРКА ИСПРАВЛЕНИЙ ДЛЯ RENDER")
print("=" * 70)

# ============================================================
# 1. ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ============================================================
print("\n📋 [1/7] ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ:")
print("-" * 50)

env_vars = {
    'TBANK_TOKEN': '❌ НЕ НАЙДЕН',
    'TBANK_ACCOUNT_ID': '❌ НЕ НАЙДЕН',
    'TBANK_API_URL': '❌ НЕ НАЙДЕН',
    'SSL_TBANK_VERIFY': '❌ НЕ НАЙДЕН',
    'GRPC_DNS_RESOLVER': '❌ НЕ НАЙДЕН',
    'GRPC_VERBOSITY': '❌ НЕ НАЙДЕН',
    'SSL_CERT_FILE': '❌ НЕ НАЙДЕН',
    'REQUESTS_CA_BUNDLE': '❌ НЕ НАЙДЕН',
}

for var in env_vars:
    value = os.getenv(var)
    if value:
        env_vars[var] = f"✅ {value[:30]}{'...' if len(value) > 30 else ''}"
    else:
        # Пробуем установить принудительно
        if var == 'TBANK_API_URL':
            os.environ[var] = 'invest-public-api.tbank.ru:443'
            env_vars[var] = "✅ УСТАНОВЛЕНА ПРИНУДИТЕЛЬНО"
        elif var == 'SSL_TBANK_VERIFY':
            os.environ[var] = 'True'
            env_vars[var] = "✅ УСТАНОВЛЕНА ПРИНУДИТЕЛЬНО"

for var, status in env_vars.items():
    print(f"   {var}: {status}")

# ============================================================
# 2. ПРОВЕРКА DNS
# ============================================================
print("\n📋 [2/7] ПРОВЕРКА DNS:")
print("-" * 50)

hosts = ['invest-public-api.tbank.ru', 'invest-public-api.tinkoff.ru']

for host in hosts:
    try:
        ip = socket.gethostbyname(host)
        print(f"   ✅ {host} → {ip}")
    except Exception as e:
        print(f"   ❌ {host} → ОШИБКА: {e}")

# ============================================================
# 3. ПРОВЕРКА SSL/TLS
# ============================================================
print("\n📋 [3/7] ПРОВЕРКА SSL/TLS:")
print("-" * 50)


def test_ssl(host="invest-public-api.tbank.ru", port=443):
    try:
        # Используем certifi для проверки
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())

        # ИЛИ используем системные сертификаты
        # context = ssl.create_default_context()

        with socket.create_connection((host, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                print(f"   ✅ TLS успешно: {host}:{port}")
                print(f"      Версия: {ssock.version()}")
                print(f"      Cipher: {ssock.cipher()}")
                return True
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False


tls_ok = test_ssl()

# ============================================================
# 4. ПРОВЕРКА КОНСТАНТ SDK
# ============================================================
print("\n📋 [4/7] ПРОВЕРКА КОНСТАНТ SDK:")
print("-" * 50)

try:
    from t_tech.invest.constants import INVEST_GRPC_API, INVEST_GRPC_API_SANDBOX

    print(f"   ✅ INVEST_GRPC_API = {INVEST_GRPC_API}")
    print(f"   ✅ INVEST_GRPC_API_SANDBOX = {INVEST_GRPC_API_SANDBOX}")

    # Проверяем, правильный ли адрес
    if 'tbank.ru' in INVEST_GRPC_API:
        print("   ✅ Адрес корректный (tbank.ru)")
    else:
        print("   ⚠️ ВНИМАНИЕ! Используется старый адрес!")
        print("   🔧 Попытка переопределить...")
        import t_tech.invest.constants as constants

        constants.INVEST_GRPC_API = 'invest-public-api.tbank.ru:443'
        constants.INVEST_GRPC_API_SANDBOX = 'sandbox-invest-public-api.tbank.ru:443'
        print(f"   ✅ Переопределено: {constants.INVEST_GRPC_API}")
except ImportError as e:
    print(f"   ❌ Ошибка импорта: {e}")
except Exception as e:
    print(f"   ❌ Ошибка: {e}")

# ============================================================
# 5. ПРОВЕРКА КЛИЕНТА T-BANK
# ============================================================
print("\n📋 [5/7] ПРОВЕРКА КЛИЕНТА T-BANK:")
print("-" * 50)

try:
    from trading_bot.api.tbank_client import tbank

    print(f"   📡 API URL: {tbank.api_url}")
    print(f"   🔑 Token: {tbank.token[:20]}...")

    # Пробуем получить баланс
    try:
        available, total, _ = tbank.get_available_funds()
        print(f"   ✅ БАЛАНС ДОСТУПЕН!")
        print(f"      💰 Доступно: {available:.2f}₽")
        print(f"      💰 Капитал: {total:.2f}₽")
        balance_ok = True
    except Exception as e:
        print(f"   ❌ Ошибка получения баланса: {e}")
        balance_ok = False

except ImportError as e:
    print(f"   ❌ Ошибка импорта: {e}")
    balance_ok = False
except Exception as e:
    print(f"   ❌ Ошибка: {e}")
    balance_ok = False

# ============================================================
# 6. ПРОВЕРКА ФАЙЛОВ
# ============================================================
print("\n📋 [6/7] ПРОВЕРКА ФАЙЛОВ:")
print("-" * 50)

files_to_check = [
    ('trading_bot/config.py', 'tbank_api_url'),
    ('trading_bot/api/tbank_client.py', 'target=self.api_url'),
    ('trading_bot/order_validator.py', 'target=self.api_url'),
    ('web_server.py', 'SSL_TBANK_VERIFY'),
    ('gunicorn.conf.py', 'post_fork'),
    ('start.sh', 'TBANK_API_URL'),
]

for filepath, pattern in files_to_check:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if pattern in content:
                print(f"   ✅ {filepath} → содержит '{pattern[:20]}...'")
            else:
                print(f"   ⚠️ {filepath} → НЕ СОДЕРЖИТ '{pattern[:20]}...'")
    except FileNotFoundError:
        print(f"   ❌ {filepath} → ФАЙЛ НЕ НАЙДЕН")
    except Exception as e:
        print(f"   ❌ {filepath} → Ошибка: {e}")

# ============================================================
# 7. ИТОГ
# ============================================================
print("\n" + "=" * 70)
print("📊 [7/7] ИТОГИ ПРОВЕРКИ:")
print("=" * 70)

results = {
    'DNS': '✅' if '✅' in str([test_ssl]) else '❌',
    'SSL/TLS': '✅' if tls_ok else '❌',
    'Баланс': '✅' if balance_ok else '❌',
    'Константы SDK': '✅' if 'tbank.ru' in str(INVEST_GRPC_API) else '⚠️',
}

for check, status in results.items():
    print(f"   {status} {check}")

print("\n" + "=" * 70)

if tls_ok and balance_ok:
    print("🎉 ВСЁ РАБОТАЕТ! Бот готов к запуску на Render.")
elif tls_ok and not balance_ok:
    print("⚠️ TLS работает, но баланс не получен. Проверьте токен и настройки.")
elif not tls_ok:
    print("🔴 TLS НЕ РАБОТАЕТ! Проверьте SSL сертификаты и сеть.")
    print("   Возможные причины:")
    print("   1. Отсутствие сертификатов НУЦ Минцифры")
    print("   2. Блокировка порта 443 на Render")
    print("   3. Проблемы с DNS на Render")

print("=" * 70)

# ============================================================
# 8. РЕКОМЕНДАЦИИ
# ============================================================
if not tls_ok:
    print("\n💡 РЕКОМЕНДАЦИИ:")
    print("   1. В render.yaml добавьте:")
    print("      apt-get update && apt-get install -y ca-certificates")
    print("   2. Проверьте переменную SSL_CERT_FILE на Render")
    print("   3. Попробуйте использовать альтернативный адрес:")
    print("      TBANK_API_URL=invest-public-api.tinkoff.ru:443")