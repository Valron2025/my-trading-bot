# close_fixr.py
import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.getcwd())

from trading_bot.api.tbank_client import tbank
from trading_bot.logger import info, success, error


def main():
    print("=" * 50)
    print("🔍 ЗАКРЫТИЕ ПОЗИЦИИ FIXR")
    print("=" * 50)

    figi = "TCS40A10B5G8"
    quantity = 117000

    # 1. Проверим текущие позиции
    print("\n📊 Проверка текущих позиций...")
    positions = tbank.get_positions(force_refresh=True)
    print(f"   Всего позиций: {len(positions)}")

    for pos in positions:
        print(f"   - {pos.get('ticker', 'unknown')}: {pos.get('quantity', 0)} шт")

    # 2. Закрываем FIXR
    print(f"\n🔒 Закрываем FIXR ({quantity} шт)...")
    try:
        result = tbank.sell(figi, quantity, use_market=True)
        if result:
            success("✅ FIXR успешно закрыт!")
        else:
            error("❌ Не удалось закрыть FIXR")
    except Exception as e:
        error(f"❌ Ошибка: {e}")

    # 3. Проверим результат
    print("\n📊 Проверка после закрытия...")
    positions = tbank.get_positions(force_refresh=True)
    print(f"   Осталось позиций: {len(positions)}")

    for pos in positions:
        ticker = pos.get('ticker', 'unknown')
        qty = pos.get('quantity', 0)
        print(f"   - {ticker}: {qty} шт")


if __name__ == "__main__":
    main()