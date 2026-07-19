#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHECKLIST_FIXES.py - ПОЛНЫЙ СПИСОК ИСПРАВЛЕНИЙ ДЛЯ ТОРГОВОГО БОТА
Версия: 2.0 (с прямым чтением файлов)
"""

import sys
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass

# ============================================================================
# КОНФИГУРАЦИЯ ТЕСТОВ
# ============================================================================

@dataclass
class FixTest:
    id: str
    file_path: str
    description: str
    expected_pattern: str  # регулярное выражение для поиска
    check_type: str = "file_contains"

@dataclass
class TestResult:
    fix_id: str
    passed: bool
    message: str
    details: str = ""

# ============================================================================
# СПИСОК ВСЕХ ИСПРАВЛЕНИЙ (С РЕГУЛЯРНЫМИ ВЫРАЖЕНИЯМИ)
# ============================================================================

FIXES: List[FixTest] = [
    FixTest(
        id="FIX-001",
        file_path="trading_bot/api/tbank_client.py",
        description="Использовать единый кэш (instruments_cache)",
        expected_pattern=r"instruments_cache\.get\(cache_key\)"
    ),
    FixTest(
        id="FIX-002",
        file_path="trading_bot/api/tbank_client.py",
        description="Упростить кэширование OTC",
        expected_pattern=r"instruments_cache\.set\(cache_key"
    ),
    FixTest(
        id="FIX-003",
        file_path="trading_bot/api/tbank_client.py",
        description="Добавить clear_all_caches",
        expected_pattern=r"def clear_all_caches"
    ),
    FixTest(
        id="FIX-004",
        file_path="trading_bot/cache/cache_manager.py",
        description="ValidationCache использует TTLCache",
        expected_pattern=r"TTLCache\("
    ),
    FixTest(
        id="FIX-005",
        file_path="trading_bot/cache/cache_manager.py",
        description="save_to_disk",
        expected_pattern=r"def save_to_disk"
    ),
    FixTest(
        id="FIX-006",
        file_path="trading_bot/cache/cache_manager.py",
        description="load_from_disk",
        expected_pattern=r"def load_from_disk"
    ),
    FixTest(
        id="FIX-007",
        file_path="trading_bot/cache/cache_manager.py",
        description="trading_status_cache в статистике",
        expected_pattern=r"trading_status_cache\.get_stats\(\)"
    ),
    FixTest(
        id="FIX-008",
        file_path="trading_bot/cache/cache_manager.py",
        description="trading_status_cache в очистке",
        expected_pattern=r"trading_status_cache\.clear\(\)"
    ),
    FixTest(
        id="FIX-009",
        file_path="trading_bot/backtest/backtest.py",
        description="Учёт комиссии при входе",
        expected_pattern=r"entry_commission = entry_value"
    ),
    FixTest(
        id="FIX-010",
        file_path="trading_bot/backtest/backtest.py",
        description="clear_cache метод",
        expected_pattern=r"def clear_cache"
    ),
    FixTest(
        id="FIX-011",
        file_path="trading_bot/backtest/backtest.py",
        description="Повторные попытки MOEX",
        expected_pattern=r"max_retries = 3"
    ),
    FixTest(
        id="FIX-012",
        file_path="trading_bot/backtest/parameter_optimizer.py",
        description="_save_optimization_history",
        expected_pattern=r"def _save_optimization_history"
    ),
    FixTest(
        id="FIX-013",
        file_path="trading_bot/core/trading_loop.py",
        description="Базовые тикеры при ошибке",
        expected_pattern=r'fallback_tickers = \["SBER"'
    ),
    FixTest(
        id="FIX-014",
        file_path="trading_bot/core/trading_loop.py",
        description="Кэш с учётом цены",
        expected_pattern=r"abs\(current_price - old_price\)"
    ),
    FixTest(
        id="FIX-015",
        file_path="trading_bot/core/trading_loop.py",
        description="max_profit_pct инициализация",
        expected_pattern=r"position\.max_profit_pct = profit_pct"
    ),
    FixTest(
        id="FIX-016",
        file_path="trading_bot/monitoring/__init__.py",
        description="stop_watchdog",
        expected_pattern=r"_watchdog\.stop_watchdog\(\)"
    ),
    FixTest(
        id="FIX-017",
        file_path="trading_bot/risk/position_manager.py",
        description="Проверка наличия позиции",
        expected_pattern=r"exists = False"
    ),
    FixTest(
        id="FIX-018",
        file_path="trading_bot/risk/position_manager.py",
        description="Удалять нулевые позиции",
        expected_pattern=r"abs\(p\.get\('quantity', 0\)\) > 0"
    ),
    FixTest(
        id="FIX-019",
        file_path="trading_bot/trading/position_closer.py",
        description="OTC с кэшированием",
        expected_pattern=r"self\._is_otc_cached\(figi\)"
    ),
    FixTest(
        id="FIX-020",
        file_path="trading_bot/trading/position_opener.py",
        description="Проверка позиции перед SHORT",
        expected_pattern=r"existing = position_manager\.get_position\(stock\.figi\)"
    ),
    FixTest(
        id="FIX-021",
        file_path="trading_bot/trading/position_opener.py",
        description="Проверка дублирования",
        expected_pattern=r"existing = position_manager\.get_position\(stock\.figi\)"
    ),
    FixTest(
        id="FIX-022",
        file_path="trading_bot/trading/position_opener.py",
        description="Учёт комиссии",
        expected_pattern=r"total_with_commission = total_cost \+ commission"
    ),
    FixTest(
        id="FIX-023",
        file_path="trading_bot/utils/time_utils.py",
        description="Проверка пустого тикера",
        expected_pattern=r'if not ticker:'
    ),
    FixTest(
        id="FIX-024",
        file_path="trading_bot/utils/time_utils.py",
        description="_is_holiday_date",
        expected_pattern=r"def _is_holiday_date"
    ),
    FixTest(
        id="FIX-025",
        file_path="trading_bot/utils/time_utils.py",
        description="is_dsvd_trading_time использует _is_holiday_date",
        expected_pattern=r"if _is_holiday_date\(now\):"
    ),
    FixTest(
        id="FIX-026",
        file_path="trading_bot/utils/time_utils.py",
        description="is_otc_trading_time использует _is_holiday_date",
        expected_pattern=r"if _is_holiday_date\(now\):"
    ),
    FixTest(
        id="FIX-027",
        file_path="web_server.py",
        description="Проверка _trading_bot",
        expected_pattern=r"if hasattr\(_trading_bot, 'trading_loop'\)"
    ),
    FixTest(
        id="FIX-028",
        file_path="gunicorn.conf.py",
        description="gRPC безопасная обработка",
        expected_pattern=r"if hasattr\(grpc, '_cython'\)"
    ),
    FixTest(
        id="FIX-029",
        file_path="start.sh",
        description="Проверка gunicorn",
        expected_pattern=r"command -v gunicorn"
    ),
    FixTest(
        id="FIX-030",
        file_path="main.py",
        description="Обработка сигналов",
        expected_pattern=r"signal\.signal\(signal\.SIGINT, signal_handler\)"
    ),
    FixTest(
        id="FIX-031",
        file_path="trading_bot/order_validator.py",
        description="Импорт os",
        expected_pattern=r"self\.api_url = os\.getenv"
    ),
    FixTest(
        id="FIX-032",
        file_path="trading_bot/models.py",
        description="Защита от деления на ноль",
        expected_pattern=r"if self\.avg_price == 0:"
    ),
    FixTest(
        id="FIX-033",
        file_path="trading_bot/logger.py",
        description="Очистка логов",
        expected_pattern=r"_cleanup_old_logs\(days=30\)"
    ),
    FixTest(
        id="FIX-034",
        file_path="trading_bot/config.py",
        description="Проверка tbank на None",
        expected_pattern=r"if tbank is None:"
    ),
    FixTest(
        id="FIX-035",
        file_path="render.yaml",
        description="GRPC_VERBOSITY",
        expected_pattern=r"GRPC_VERBOSITY"
    ),
]

# ============================================================================
# ПРОВЕРКА
# ============================================================================

class FixChecker:
    def __init__(self):
        self.results: List[TestResult] = []
        self.project_root = Path(__file__).parent.parent

    def check_file_contains(self, fix: FixTest) -> TestResult:
        """Проверка, что файл содержит паттерн"""
        file_path = self.project_root / fix.file_path

        if not file_path.exists():
            return TestResult(
                fix_id=fix.id,
                passed=False,
                message=f"❌ Файл не найден: {fix.file_path}",
                details=""
            )

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            if re.search(fix.expected_pattern, content, re.IGNORECASE):
                return TestResult(
                    fix_id=fix.id,
                    passed=True,
                    message=f"✅ Найден паттерн: {fix.expected_pattern[:30]}...",
                    details=""
                )
            else:
                return TestResult(
                    fix_id=fix.id,
                    passed=False,
                    message=f"❌ Паттерн НЕ найден: {fix.expected_pattern[:30]}...",
                    details=f"Файл: {fix.file_path}"
                )
        except Exception as e:
            return TestResult(
                fix_id=fix.id,
                passed=False,
                message=f"❌ Ошибка: {e}",
                details=""
            )

    def run(self) -> None:
        print("\n" + "=" * 80)
        print("🔧 ПРОВЕРКА ИСПРАВЛЕНИЙ ТОРГОВОГО БОТА (v2)")
        print("=" * 80)
        print(f"Всего исправлений: {len(FIXES)}")
        print(f"Проект: {self.project_root}")
        print("=" * 80 + "\n")

        for fix in FIXES:
            print(f"[{fix.id}] {fix.description}")
            print(f"    Файл: {fix.file_path}")

            if fix.check_type == "file_contains":
                result = self.check_file_contains(fix)
            else:
                result = TestResult(
                    fix_id=fix.id,
                    passed=False,
                    message=f"❌ Неизвестный тип: {fix.check_type}",
                    details=""
                )

            self.results.append(result)
            status = "✅ ПРОЙДЕН" if result.passed else "❌ НЕ ПРОЙДЕН"
            print(f"    Статус: {status}")
            print(f"    Сообщение: {result.message}")
            if result.details:
                print(f"    Детали: {result.details}")
            print()

        self.print_summary()

    def print_summary(self) -> None:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed

        print("=" * 80)
        print("📊 СТАТИСТИКА ПРОВЕРКИ")
        print("=" * 80)
        print(f"   ✅ ПРОЙДЕНО: {passed}/{total} ({passed/total*100:.1f}%)")
        print(f"   ❌ НЕ ПРОЙДЕНО: {failed}/{total} ({failed/total*100:.1f}%)")
        print("=" * 80)

        if failed > 0:
            print("\n❌ СПИСОК НЕ ПРОЙДЕННЫХ ИСПРАВЛЕНИЙ:")
            for r in self.results:
                if not r.passed:
                    print(f"   [{r.fix_id}] {r.message}")
        else:
            print("\n🎉 ВСЕ ИСПРАВЛЕНИЯ ПРОЙДЕНЫ!")

        print("\n" + "=" * 80)
        print("📋 ПРИМЕЧАНИЯ:")
        print("   • Проверка выполняется прямым чтением файлов")
        print("   • Если файл не найден, проверка считается НЕ ПРОЙДЕННОЙ")
        print("   • FIX-028 (gunicorn.conf.py) можно игнорировать")
        print("=" * 80 + "\n")


def main():
    try:
        checker = FixChecker()
        checker.run()
    except KeyboardInterrupt:
        print("\n🛑 Проверка прервана")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()