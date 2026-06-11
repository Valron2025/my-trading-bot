# quick_fixes.py
import os
import re

# Текущая директория
base_dir = os.getcwd()
print(f"📁 Текущая директория: {base_dir}")

# 1. Исправляем available в position_sizer.py
position_sizer_path = os.path.join('trading_bot', 'risk', 'position_sizer.py')
if os.path.exists(position_sizer_path):
    with open(position_sizer_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Заменяем available на available_funds (только в определённых местах)
    content = content.replace('available * 0.8', 'available_funds * 0.8')
    content = content.replace('available * 0.9', 'available_funds * 0.9')
    content = content.replace('available_funds_funds', 'available_funds')

    with open(position_sizer_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"✅ PositionSizer исправлен: {position_sizer_path}")
else:
    print(f"❌ Файл не найден: {position_sizer_path}")

# 2. Увеличиваем лимит PreMarketTrader
pre_market_path = os.path.join('trading_bot', 'trading', 'pre_market_trader.py')
if os.path.exists(pre_market_path):
    with open(pre_market_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Заменяем жёсткий лимит на динамический
    content = content.replace(
        'self.max_capital_per_order = 1000  # Будет перезаписано в _update_dynamic_params',
        'self.max_capital_per_order = max(1000, 20000)  # Увеличен лимит до 20,000₽'
    )

    # Также исправляем в _update_dynamic_params
    content = content.replace(
        'self.max_capital_per_order = max(1000, total_capital * 0.05)',
        'self.max_capital_per_order = max(2000, total_capital * 0.1)'
    )

    with open(pre_market_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"✅ PreMarketTrader лимит увеличен: {pre_market_path}")
else:
    print(f"❌ Файл не найден: {pre_market_path}")

# 3. Добавляем проверку времени торгов в position_sizer.py
if os.path.exists(position_sizer_path):
    with open(position_sizer_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Находим метод _calculate_long и добавляем проверку времени
    new_lines = []
    in_calculate_long = False
    added_time_check = False

    for i, line in enumerate(lines):
        new_lines.append(line)

        # После строки def _calculate_long
        if 'def _calculate_long' in line and not added_time_check:
            in_calculate_long = True

        # Добавляем проверку времени после получения параметров
        if in_calculate_long and 'info(f"   💰 Цена:' in line and not added_time_check:
            indent = '        '
            time_check = f'''{indent}# ========== 0. ПРОВЕРКА ВРЕМЕНИ ТОРГОВ ==========
{indent}from trading_bot.utils.time_utils import is_trading_time, is_weekend_trading_time, is_otc_trading_time, get_moscow_time
{indent}
{indent}now = get_moscow_time()
{indent}if not (is_trading_time() or is_weekend_trading_time() or is_otc_trading_time()):
{indent}    debug(f"   ⏸️ {{ticker}}: торги закрыты ({{now.strftime('%H:%M')}})")
{indent}    return 0
{indent}
'''
            new_lines.append(time_check)
            added_time_check = True
            in_calculate_long = False
            print(f"   ✅ Добавлена проверка времени в _calculate_long")

    if added_time_check:
        with open(position_sizer_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f"✅ Проверка времени торгов добавлена")
    else:
        print(f"⚠️ Не удалось добавить проверку времени (возможно, уже есть)")

print("\n" + "=" * 50)
print("🎯 ГОТОВО! Запустите бота снова:")
print("   python master_test.py --quick")
print("=" * 50)