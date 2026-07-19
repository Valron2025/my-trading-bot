# render_cleanup.py
import os
import socket
import sys

print("🧹 ОЧИСТКА RENDER ОКРУЖЕНИЯ")

# 1. Установка переменных
os.environ['TBANK_API_URL'] = 'invest-public-api.tbank.ru:443'
os.environ['T_INVEST_API_URL'] = 'invest-public-api.tbank.ru:443'
os.environ['GRPC_DNS_RESOLVER'] = 'native'

# 2. Проверка DNS
try:
    ip = socket.gethostbyname('invest-public-api.tbank.ru')
    print(f"✅ DNS: invest-public-api.tbank.ru → {ip}")
except Exception as e:
    print(f"❌ DNS ошибка: {e}")

# 3. Проверка старого IP
try:
    old_ip = socket.gethostbyname('178.130.128.33')
    print(f"⚠️ Старый IP всё ещё резолвится: {old_ip}")
except:
    print("✅ Старый IP не резолвится")

print("✅ Очистка завершена")