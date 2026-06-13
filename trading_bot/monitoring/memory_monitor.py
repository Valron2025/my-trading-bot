"""Мониторинг использования памяти"""

import psutil
from ..logger import info, warning


class MemoryMonitor:
    """Мониторинг памяти процесса"""

    def __init__(self, warning_threshold_mb: int = 500):
        self.warning_threshold_mb = warning_threshold_mb

    def get_usage_mb(self) -> float:
        """Получение использования памяти в MB"""
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024

    def log_usage(self):
        """Логирование использования памяти"""
        memory_mb = self.get_usage_mb()
        info(f"💾 Использование памяти: {memory_mb:.1f} MB")

        if memory_mb > self.warning_threshold_mb:
            warning(f"⚠️ Высокое использование памяти: {memory_mb:.1f} MB > {self.warning_threshold_mb} MB")