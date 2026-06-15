"""Мониторинг использования памяти и производительности"""

import psutil
import time
import functools
from collections import defaultdict
from datetime import datetime
from ..logger import info, warning, debug


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


class PerformanceMonitor:
    """МОНИТОРИНГ ПРОИЗВОДИТЕЛЬНОСТИ - показывает где тормозит"""

    def __init__(self):
        self.timings = defaultdict(list)
        self.slow_threshold_ms = 100  # 100ms считаем медленным
        self.enabled = True

    def measure(self, name: str):
        """Декоратор для замера времени выполнения функции"""
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not self.enabled:
                    return func(*args, **kwargs)

                start = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    self._add_measurement(name, elapsed_ms)

                    # Если медленно - сразу предупреждение
                    if elapsed_ms > self.slow_threshold_ms:
                        warning(f"🐌 МЕДЛЕННАЯ ФУНКЦИЯ: {name} = {elapsed_ms:.0f}ms")

                    return result
                except Exception as e:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    self._add_measurement(name, elapsed_ms, error=True)
                    raise
            return wrapper
        return decorator

    def _add_measurement(self, name: str, elapsed_ms: float, error: bool = False):
        """Добавить замер"""
        self.timings[name].append({
            'time_ms': elapsed_ms,
            'timestamp': datetime.now(),
            'error': error
        })
        # Оставляем последние 1000 замеров
        if len(self.timings[name]) > 1000:
            self.timings[name] = self.timings[name][-1000:]

    def get_stats(self):
        """Получить статистику по всем замерам"""
        stats = {}
        for name, measurements in self.timings.items():
            if not measurements:
                continue
            times = [m['time_ms'] for m in measurements]
            stats[name] = {
                'count': len(times),
                'avg_ms': sum(times) / len(times),
                'min_ms': min(times),
                'max_ms': max(times),
                'total_seconds': sum(times) / 1000,
                'slow_count': sum(1 for t in times if t > self.slow_threshold_ms)
            }
        return stats

    def print_stats(self):
        """Вывести статистику в консоль"""
        stats = self.get_stats()
        if not stats:
            info("📊 Нет данных о производительности")
            return

        info("\n" + "="*80)
        info("📊 СТАТИСТИКА ПРОИЗВОДИТЕЛЬНОСТИ (где тратится время)")
        info("="*80)

        # Сортируем по общему времени (самые затратные сверху)
        sorted_stats = sorted(stats.items(), key=lambda x: x[1]['total_seconds'], reverse=True)

        for name, data in sorted_stats[:15]:  # Топ-15
            slow_mark = "⚠️" if data['slow_count'] > 0 else "✅"
            info(f"{slow_mark} {name:<45} | "
                 f"ср:{data['avg_ms']:>6.1f}ms | "
                 f"макс:{data['max_ms']:>6.1f}ms | "
                 f"вызовов:{data['count']:>6} | "
                 f"всего:{data['total_seconds']:>6.1f}с")

        info("="*80 + "\n")

    def reset(self):
        """Сбросить статистику"""
        self.timings.clear()
        info("🧹 Статистика производительности сброшена")


class APILatencyMonitor:
    """Специальный монитор для задержек API Т-Банка"""

    def __init__(self):
        self.latencies = defaultdict(list)

    def measure(self, method_name: str):
        """Декоратор для замера API запросов"""
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    latency_ms = (time.perf_counter() - start) * 1000
                    self.latencies[method_name].append(latency_ms)

                    # API медленнее 500ms - проблема
                    if latency_ms > 500:
                        warning(f"🌐 API {method_name} МЕДЛЕННЫЙ: {latency_ms:.0f}ms")
                    elif latency_ms > 200:
                        debug(f"🌐 API {method_name}: {latency_ms:.0f}ms")

                    return result
                except Exception as e:
                    latency_ms = (time.perf_counter() - start) * 1000
                    error(f"❌ API {method_name} ошибка после {latency_ms:.0f}ms: {e}")
                    raise
            return wrapper
        return decorator

    def get_stats(self):
        """Статистика по API"""
        stats = {}
        for name, latencies in self.latencies.items():
            if not latencies:
                continue
            stats[name] = {
                'avg_ms': sum(latencies) / len(latencies),
                'max_ms': max(latencies),
                'min_ms': min(latencies),
                'count': len(latencies)
            }
        return stats

    def print_stats(self):
        """Вывести статистику API"""
        stats = self.get_stats()
        if not stats:
            return

        info("\n" + "="*60)
        info("🌐 СТАТИСТИКА API Т-БАНКА")
        info("="*60)

        for name, data in stats.items():
            status = "✅" if data['avg_ms'] < 200 else "⚠️" if data['avg_ms'] < 500 else "❌"
            info(f"{status} {name:<30} | ср:{data['avg_ms']:>6.1f}ms | макс:{data['max_ms']:>6.1f}ms | n={data['count']}")

        info("="*60 + "\n")


# Глобальные экземпляры
perf_monitor = PerformanceMonitor()
api_monitor = APILatencyMonitor()