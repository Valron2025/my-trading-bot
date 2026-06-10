#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для поиска всех методов закрытия позиций в проекте
Запуск: python find_close_methods.py
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple


# Цвета для вывода
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str, color: str = Colors.CYAN):
    print(f"\n{color}{'=' * 70}{Colors.RESET}")
    print(f"{color}{text}{Colors.RESET}")
    print(f"{color}{'=' * 70}{Colors.RESET}")


def find_methods_in_file(file_path: Path, patterns: List[str]) -> List[Tuple[str, int, str]]:
    """Поиск методов в одном файле"""
    results = []
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        for i, line in enumerate(lines, 1):
            line_stripped = line.strip()
            for pattern in patterns:
                if re.search(pattern, line_stripped):
                    # Определяем тип метода
                    method_type = "UNKNOWN"
                    if "def " in line_stripped:
                        method_name = re.search(r'def\s+([a-zA-Z_][a-zA-Z0-9_]*)', line_stripped)
                        if method_name:
                            method_name = method_name.group(1)
                            if "close" in method_name.lower():
                                method_type = "CLOSE"
                            elif "emergency" in method_name.lower():
                                method_type = "EMERGENCY"
                            elif "force" in method_name.lower():
                                method_type = "FORCE"
                            elif "_close" in method_name.lower():
                                method_type = "PRIVATE"
                            else:
                                method_type = "OTHER"

                            results.append((str(file_path), i, method_name, line_stripped[:100], method_type))
                    break
    except Exception as e:
        print(f"   ⚠️ Ошибка чтения {file_path}: {e}")
    return results


def main():
    print_header("🔍 ПОИСК ВСЕХ МЕТОДОВ ЗАКРЫТИЯ ПОЗИЦИЙ", Colors.BOLD + Colors.CYAN)

    # Паттерны для поиска
    patterns = [
        r'def\s+.*close.*\(',  # любые close методы
        r'def\s+.*emergency.*\(',  # emergency методы
        r'def\s+.*force.*close.*\(',  # force close методы
        r'def\s+_close.*\(',  # приватные _close методы
        r'def\s+cancel.*\(',  # cancel методы
        r'def\s+.*sell.*\(',  # sell методы (для закрытия LONG)
        r'def\s+.*buy.*\(',  # buy методы (для закрытия SHORT)
    ]

    # Директории для сканирования
    scan_dirs = [
        Path("trading_bot/api"),
        Path("trading_bot/risk"),
        Path("trading_bot/trading"),
        Path("trading_bot/core"),
        Path("trading_bot/bot.py"),
    ]

    all_methods = []

    for scan_path in scan_dirs:
        if scan_path.is_file():
            files = [scan_path]
        elif scan_path.is_dir():
            files = list(scan_path.rglob("*.py"))
        else:
            continue

        for file_path in files:
            if "__pycache__" in str(file_path):
                continue
            methods = find_methods_in_file(file_path, patterns)
            if methods:
                all_methods.extend(methods)

    # Группировка по типу
    close_methods = []
    emergency_methods = []
    force_methods = []
    private_methods = []
    other_methods = []

    for file_path, line, method_name, signature, method_type in all_methods:
        rel_path = file_path.replace("trading_bot\\", "").replace("trading_bot/", "")

        item = {
            'file': rel_path,
            'line': line,
            'name': method_name,
            'signature': signature,
            'full_path': file_path
        }

        if method_type == "CLOSE":
            close_methods.append(item)
        elif method_type == "EMERGENCY":
            emergency_methods.append(item)
        elif method_type == "FORCE":
            force_methods.append(item)
        elif method_type == "PRIVATE":
            private_methods.append(item)
        else:
            other_methods.append(item)

    # ========== ВЫВОД РЕЗУЛЬТАТОВ ==========

    # 1. close_position методы (самые важные)
    print_header("📌 1. МЕТОДЫ 'close_position' (ОСНОВНЫЕ)", Colors.YELLOW)
    found = False
    for m in close_methods:
        if "close_position" in m['name']:
            print(f"   📁 {Colors.GREEN}{m['file']}{Colors.RESET}:{m['line']}")
            print(
                f"      🔧 {Colors.CYAN}def {m['name']}{Colors.RESET}{m['signature'].split('def')[1] if 'def' in m['signature'] else ''}")
            found = True
    if not found:
        print(f"   {Colors.RED}❌ Не найдены{Colors.RESET}")

    # 2. emergency методы
    print_header("📌 2. МЕТОДЫ 'emergency' (АВАРИЙНЫЕ)", Colors.YELLOW)
    for m in emergency_methods:
        print(f"   📁 {Colors.MAGENTA}{m['file']}{Colors.RESET}:{m['line']}")
        print(f"      🔧 def {m['name']}{m['signature'].split('def')[1] if 'def' in m['signature'] else ''}")

    # 3. force методы
    print_header("📌 3. МЕТОДЫ 'force' (ПРИНУДИТЕЛЬНЫЕ)", Colors.YELLOW)
    for m in force_methods:
        print(f"   📁 {Colors.MAGENTA}{m['file']}{Colors.RESET}:{m['line']}")
        print(f"      🔧 def {m['name']}{m['signature'].split('def')[1] if 'def' in m['signature'] else ''}")

    # 4. Приватные _close методы
    print_header("📌 4. ПРИВАТНЫЕ МЕТОДЫ '_close'", Colors.YELLOW)
    for m in private_methods:
        print(f"   📁 {Colors.MAGENTA}{m['file']}{Colors.RESET}:{m['line']}")
        print(f"      🔧 def {m['name']}{m['signature'].split('def')[1] if 'def' in m['signature'] else ''}")

    # 5. Другие close методы
    print_header("📌 5. ДРУГИЕ МЕТОДЫ C 'close'", Colors.YELLOW)
    for m in other_methods:
        if "close" in m['name'].lower() and "close_position" not in m['name']:
            print(f"   📁 {Colors.MAGENTA}{m['file']}{Colors.RESET}:{m['line']}")
            print(f"      🔧 def {m['name']}{m['signature'].split('def')[1] if 'def' in m['signature'] else ''}")

    # ========== ИТОГОВАЯ ТАБЛИЦА ==========
    print_header("📊 ИТОГОВАЯ СТАТИСТИКА", Colors.BOLD + Colors.GREEN)
    print(f"""
   ├── close_position методы:  {len([m for m in close_methods if 'close_position' in m['name']])}
   ├── emergency методы:       {len(emergency_methods)}
   ├── force методы:           {len(force_methods)}
   ├── приватные _close:       {len(private_methods)}
   └── другие close методы:    {len(other_methods)}

   📊 ВСЕГО МЕТОДОВ:           {len(all_methods)}
    """)

    # ========== РЕКОМЕНДАЦИИ ==========
    print_header("💡 РЕКОМЕНДАЦИИ ПО ОПТИМИЗАЦИИ", Colors.BOLD + Colors.GREEN)

    print(f"""
   {Colors.GREEN}✅ ОСТАВИТЬ (РАБОЧИЕ):{Colors.RESET}
       • close_position_with_retry()     - ОСНОВНОЙ (tbank_client.py)
       • close_position_smart()          - ВЫСОКОУРОВНЕВЫЙ (position_manager.py)
       • _close_worst_positions()        - АВАРИЙНЫЙ (position_manager.py)
       • emergency_close_by_ticker()     - РУЧНОЙ (position_closer.py)

   {Colors.RED}❌ УДАЛИТЬ (ДУБЛИКАТЫ):{Colors.RESET}
       • _emergency_close_all_old()      - устарел
       • emergency_close_shorts()        - дублирует логику
       • force_close_stuck_positions()   - не сработал
       • emergency_close_worst_positions() - дублирует _close_worst_positions
       • _force_close_position_smart()   - внутренний дубликат
       • close_all_positions_smart()     - редко используется
       • cancel_stop_order()             - если не используется
    """)

    # Сохраняем результат в файл
    output_file = Path("close_methods_report.txt")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("ОТЧЁТ ПО МЕТОДАМ ЗАКРЫТИЯ ПОЗИЦИЙ\n")
        f.write("=" * 80 + "\n\n")

        f.write("1. close_position методы:\n")
        for m in close_methods:
            if "close_position" in m['name']:
                f.write(f"   {m['file']}:{m['line']} - {m['name']}\n")

        f.write("\n2. emergency методы:\n")
        for m in emergency_methods:
            f.write(f"   {m['file']}:{m['line']} - {m['name']}\n")

        f.write("\n3. force методы:\n")
        for m in force_methods:
            f.write(f"   {m['file']}:{m['line']} - {m['name']}\n")

        f.write("\n4. Приватные _close методы:\n")
        for m in private_methods:
            f.write(f"   {m['file']}:{m['line']} - {m['name']}\n")

        f.write("\n5. Другие close методы:\n")
        for m in other_methods:
            f.write(f"   {m['file']}:{m['line']} - {m['name']}\n")

        f.write(f"\n\nВСЕГО НАЙДЕНО: {len(all_methods)} методов\n")

    print(f"\n{Colors.GREEN}📄 Полный отчёт сохранён в: {output_file}{Colors.RESET}")
    print(f"{Colors.CYAN}🔍 Запустите анализ: python find_close_methods.py{Colors.RESET}\n")


if __name__ == "__main__":
    main()