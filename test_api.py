"""Простой тест подключения к T-Bank API"""
import os
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TBANK_TOKEN")
print(f"Token: {token[:20]}..." if token else "Token not found!")

if token:
    try:
        from t_tech.invest import Client

        with Client(token) as client:
            # Пробуем получить информацию о пользователе
            info = client.users.get_info()
            print(f"✅ Подключение успешно!")
            print(f"   Тариф: {info.tariff}")
            print(f"   Статус: {info.qual_status}")

            # Пробуем получить портфель
            accounts = client.users.get_accounts()
            if accounts.accounts:
                print(f"   Счет: {accounts.accounts[0].id}")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
else:
    print("❌ Токен не найден в .env файле!")