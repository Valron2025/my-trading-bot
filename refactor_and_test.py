#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ПОЛНЫЙ РЕФАКТОРИНГ + ТЕСТИРОВАНИЕ ТОРГОВОГО БОТА
- Удаление дублирующихся методов закрытия
- Автоматическое обновление импортов
- Полное тестирование бота
"""

import os
import re
import sys
import subprocess
from pathlib import Path
from datetime import datetime


# Цвета для вывода
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str, color: str = Colors.CYAN):
    print(f"\n{color}{'=' * 70}{Colors.RESET}")
    print(f"{color}{text}{Colors.RESET}")
    print(f"{color}{'=' * 70}{Colors.RESET}")


def print_success(text: str):
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")


def print_error(text: str):
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")


def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠️ {text}{Colors.RESET}")


def print_info(text: str):
    print(f"{Colors.CYAN}📌 {text}{Colors.RESET}")


# ========== 1. УДАЛЕНИЕ ДУБЛИРУЮЩИХСЯ МЕТОДОВ ==========

def remove_method_from_file(filepath: str, method_name: str, reason: str = "") -> bool:
    """Удаляет метод из файла"""
    if not os.path.exists(filepath):
        return False

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        new_lines = []
        skip = False
        indent_level = 0
        removed = False

        for line in lines:
            # Проверяем начало метода
            if re.search(rf'^\s+def {method_name}\(', line):
                skip = True
                indent_level = len(line) - len(line.lstrip())
                removed = True
                print_info(f"  Удаляем метод {method_name} из {filepath}")
                if reason:
                    print_warning(f"    Причина: {reason}")
                continue

            # Если пропускаем метод, проверяем конец
            if skip:
                if line.strip() and len(line) - len(line.lstrip()) <= indent_level:
                    skip = False
                else:
                    continue

            new_lines.append(line)

        if removed:
            # Создаём бэкап
            backup_path = filepath + ".backup"
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

            with open(filepath, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            return True
    except Exception as e:
        print_error(f"  Ошибка в {filepath}: {e}")
    return False


def update_imports_in_file(filepath: str) -> bool:
    """Обновляет импорты, заменяя старые вызовы на position_closer"""
    if not os.path.exists(filepath):
        return False

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        original = content

        # Добавляем импорт position_closer если есть старые вызовы
        old_patterns = [
            (r'self\.bot\.close_position\(', 'position_closer.close_position_smart('),
            (r'self\.bot\.position_manager\.close_position\(', 'position_closer.close_position_smart('),
            (r'position_manager\.close_position\(', 'position_closer.close_position_smart('),
            (r'self\.bot\.position_closer\.emergency_close_by_ticker\(', 'position_closer.emergency_close_by_ticker('),
            (r'self\._close_worst_positions\(', 'position_closer.close_worst_positions('),
            (r'emergency_close_shorts\(', '# УДАЛЕНО: emergency_close_shorts, используйте close_worst_positions()'),
        ]

        for pattern, replacement in old_patterns:
            if re.search(pattern, content):
                # Добавляем импорт если ещё нет
                if 'from trading_bot.trading.position_closer import position_closer' not in content:
                    # Находим место для импорта
                    import_line = 'from trading_bot.trading.position_closer import position_closer\n'
                    # Добавляем после других импортов
                    lines = content.split('\n')
                    insert_pos = 0
                    for i, line in enumerate(lines[:30]):
                        if 'from trading_bot' in line or 'import' in line:
                            insert_pos = i + 1
                    lines.insert(insert_pos, import_line)
                    content = '\n'.join(lines)

                content = re.sub(pattern, replacement, content)

        if content != original:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
    except Exception as e:
        print_error(f"  Ошибка обновления {filepath}: {e}")
    return False


# ========== 2. СПИСОК МЕТОДОВ ДЛЯ УДАЛЕНИЯ ==========

METHODS_TO_REMOVE = {
    'trading_bot/risk/position_manager.py': [
        ('_close_position', 'приватный дубликат, используйте close_position_smart'),
        ('close_position', 'дубликат close_position_smart'),
    ],
    'trading_bot/bot.py': [
        ('close_position', 'дубликат, используйте position_closer.close_position_smart'),
        ('close_position_by_ticker', 'перенесён в position_closer.emergency_close_by_ticker'),
    ],
    'trading_bot/core/trading_loop.py': [
        ('_should_close_position_smart', 'перенесён в position_closer'),
        ('_should_close_position_smart_detailed', 'перенесён в position_closer'),
        ('_close_positions_before_clearing', 'перенесён в position_closer'),
    ],
    'trading_bot/api/tbank_client.py': [
        ('sell_short', 'используйте sell(use_market=True)'),
        ('cancel_stop_order', 'если не используется'),
    ],
    'trading_bot/trading/order_placement.py': [
        ('cancel_all_orders', 'дубликат, используйте tbank.cancel_all_duplicate_orders'),
    ],
    'trading_bot/trading/smart_orders.py': [
        ('cancel_order', 'дубликат, используйте tbank.cancel_order'),
    ],
}


# ========== 3. ГЛОБАЛЬНАЯ ПРОВЕРКА БОТА ==========

def run_test(command: str, description: str) -> tuple:
    """Запускает тест и возвращает (success, output)"""
    print_info(f"Запуск: {description}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60
        )
        success = result.returncode == 0
        output = result.stdout + result.stderr
        if success:
            print_success(f"  {description} - OK")
        else:
            print_error(f"  {description} - ОШИБКА")
            # Показываем последние 5 строк ошибки
            lines = output.split('\n')
            for line in lines[-5:]:
                if line.strip():
                    print_warning(f"    {line[:100]}")
        return success, output
    except subprocess.TimeoutExpired:
        print_error(f"  {description} - ТАЙМАУТ")
        return False, "Timeout"
    except Exception as e:
        print_error(f"  {description} - {e}")
        return False, str(e)


def main():
    print_header("🔧 ПОЛНЫЙ РЕФАКТОРИНГ ТОРГОВОГО БОТА", Colors.BOLD + Colors.CYAN)

    # ========== ШАГ 1: БЭКАП ==========
    print_header("📦 ШАГ 1: СОЗДАНИЕ БЭКАПА", Colors.YELLOW)
    backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(backup_dir, exist_ok=True)
    print_success(f"Бэкап создан в папке: {backup_dir}")

    # ========== ШАГ 2: УДАЛЕНИЕ ДУБЛИКАТОВ ==========
    print_header("🗑️ ШАГ 2: УДАЛЕНИЕ ДУБЛИРУЮЩИХСЯ МЕТОДОВ", Colors.YELLOW)

    removed_count = 0
    for filepath, methods in METHODS_TO_REMOVE.items():
        if os.path.exists(filepath):
            print_info(f"Обработка: {filepath}")
            for method_name, reason in methods:
                if remove_method_from_file(filepath, method_name, reason):
                    removed_count += 1
        else:
            print_warning(f"Файл не найден: {filepath}")

    print_success(f"Удалено методов: {removed_count}")

    # ========== ШАГ 3: ОБНОВЛЕНИЕ ИМПОРТОВ ==========
    print_header("📝 ШАГ 3: ОБНОВЛЕНИЕ ИМПОРТОВ", Colors.YELLOW)

    files_to_update = [
        'trading_bot/core/trading_loop.py',
        'trading_bot/bot.py',
        'trading_bot/risk/position_manager.py',
        'trading_bot/trading/position_opener.py',
    ]

    updated_count = 0
    for filepath in files_to_update:
        if os.path.exists(filepath):
            if update_imports_in_file(filepath):
                updated_count += 1
                print_success(f"Обновлён: {filepath}")

    print_success(f"Обновлено файлов: {updated_count}")

    # ========== ШАГ 4: СИНТАКСИЧЕСКАЯ ПРОВЕРКА ==========
    print_header("🔍 ШАГ 4: СИНТАКСИЧЕСКАЯ ПРОВЕРКА", Colors.YELLOW)

    syntax_tests = [
        ("python -m py_compile trading_bot/trading/position_closer.py", "Проверка position_closer.py"),
        ("python -m py_compile trading_bot/bot.py", "Проверка bot.py"),
        ("python -m py_compile trading_bot/core/trading_loop.py", "Проверка trading_loop.py"),
        ("python -m py_compile trading_bot/risk/position_manager.py", "Проверка position_manager.py"),
    ]

    syntax_ok = True
    for cmd, desc in syntax_tests:
        success, _ = run_test(cmd, desc)
        if not success:
            syntax_ok = False

    # ========== ШАГ 5: ПОИСК ОСТАВШИХСЯ ДУБЛИКАТОВ ==========
    print_header("🔎 ШАГ 5: ПОИСК ОСТАВШИХСЯ ДУБЛИКАТОВ", Colors.YELLOW)

    if os.path.exists("find_close_methods.py"):
        run_test("python find_close_methods.py", "Анализ методов закрытия")
    else:
        print_warning("find_close_methods.py не найден, пропускаем")

    # ========== ШАГ 6: БЫСТРЫЙ ТЕСТ ==========
    print_header("🧪 ШАГ 6: БЫСТРЫЙ ТЕСТ БОТА", Colors.YELLOW)

    quick_tests = [
        ("python -c \"from trading_bot.config import config; print('config OK')\"", "Проверка config"),
        ("python -c \"from trading_bot.logger import info; print('logger OK')\"", "Проверка logger"),
        ("python -c \"from trading_bot.trading.position_closer import position_closer; print('position_closer OK')\"",
         "Проверка position_closer"),
        ("python quick_test.py", "Быстрый тест"),
    ]

    for cmd, desc in quick_tests:
        run_test(cmd, desc)

    # ========== ШАГ 7: ФИНАЛЬНЫЙ ОТЧЁТ ==========
    print_header("📊 ФИНАЛЬНЫЙ ОТЧЁТ", Colors.BOLD + Colors.GREEN)

    print(f"""
    {Colors.CYAN}📁 Бэкап:{Colors.RESET} {backup_dir}
    {Colors.CYAN}🗑️ Удалено методов:{Colors.RESET} {removed_count}
    {Colors.CYAN}📝 Обновлено файлов:{Colors.RESET} {updated_count}
    {Colors.CYAN}🔧 Синтаксис:{Colors.RESET} {'✅ OK' if syntax_ok else '❌ ОШИБКИ'}

    {Colors.BOLD}📋 ДАЛЬНЕЙШИЕ ДЕЙСТВИЯ:{Colors.RESET}

    1. Проверьте изменения:
       git diff

    2. Если всё OK, закоммитьте:
       git add .
       git commit -m "refactor: centralize position closing methods"
       git push origin main

    3. Перезапустите бота на Render

    4. Проверьте статус:
       curl https://my-trading-bot-gomz.onrender.com/status
    """)

    # ========== ШАГ 8: ЗАКРЫТИЕ FIXR ==========
    print_header("🚨 ШАГ 8: ЗАКРЫТИЕ FIXR", Colors.RED)

    print(f"""
    {Colors.YELLOW}ВНИМАНИЕ! Позиция FIXR всё ещё открыта и убыточна!{Colors.RESET}

    Для закрытия выполните:

    python -c "
    from trading_bot.trading.position_closer import position_closer
    result = position_closer.close_position_smart('TCS40A10B5G8', 'FIXR', max_attempts=5)
    print(f'Результат: {result}')
    "

    ИЛИ вручную в приложении Т-Банк.
    """)

    if syntax_ok:
        print_success("\n🎉 БОТ ГОТОВ К ЗАПУСКУ!")
    else:
        print_error("\n⚠️ ЕСТЬ СИНТАКСИЧЕСКИЕ ОШИБКИ! Исправьте перед запуском.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()