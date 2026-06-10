# fix_all_config_imports.py
import os
import re


def fix_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Заменяем неправильные импорты
        new_content = re.sub(
            r'from trading_bot\.config import TradingConfig(?: as \w+)?',
            'from trading_bot.config import config',
            content
        )

        if new_content != content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True
    except Exception as e:
        print(f"Error in {filepath}: {e}")
    return False


# Исправляем все файлы
fixed = []
for root, dirs, files in os.walk('trading_bot'):
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            if fix_file(filepath):
                fixed.append(filepath)

print(f"\n✅ Исправлено файлов: {len(fixed)}")
for f in fixed:
    print(f"   - {f}")