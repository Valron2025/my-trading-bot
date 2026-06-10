"""Надзиратель за торговым ботом"""

import threading
import time
from typing import Dict, Any, Optional

from trading_bot.logger import info, warning, error, debug


class Watchdog:
    """Надзиратель за ботом - мониторит состояние и перезапускает при проблемах"""

    def __init__(
        self,
        bot,
        check_interval: int = 60,
        max_idle_cycles: int = 30,
        max_memory_mb: int = 2000,
        max_cpu_percent: int = 90
    ) -> None:
        self.bot = bot
        self.check_interval = check_interval
        self.max_idle_cycles = max_idle_cycles
        self.max_memory_mb = max_memory_mb
        self.max_cpu_percent = max_cpu_percent

        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._last_cycle_count: int = 0
        self._idle_count: int = 0
        self._consecutive_failures: int = 0
        self._max_consecutive_failures: int = 3

        self._stats: Dict[str, Any] = {
            "restarts": 0,
            "last_restart_reason": None,
            "last_restart_time": None,
            "health_checks": 0,
            "health_failures": 0
        }

        self._restarting = False

        info("🔍 Watchdog инициализирован")

    def start_watchdog(self) -> None:
        """Запуск надзирателя (переименовано из start для устранения конфликта)"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._thread.start()
        info("🔍 Watchdog запущен")

    def stop_watchdog(self) -> None:
        """Остановка надзирателя (переименовано из stop для устранения конфликта)"""
        if not self._running:
            return
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
        info("🔍 Watchdog остановлен")

    def _watchdog_loop(self) -> None:
        """Основной цикл надзирателя"""
        while self._running:
            time.sleep(self.check_interval)

            try:
                self._stats["health_checks"] = self._stats.get("health_checks", 0) + 1
                is_healthy = self._check_bot_health()

                if not is_healthy:
                    self._stats["health_failures"] = self._stats.get("health_failures", 0) + 1
                    self._consecutive_failures += 1

                    if self._consecutive_failures >= self._max_consecutive_failures:
                        warning(f"🚨 Watchdog: {self._consecutive_failures} последовательных сбоев!")
                        self._restart_bot("multiple health check failures")
                else:
                    self._consecutive_failures = 0

            except Exception as e:
                error(f"Watchdog ошибка: {e}")
                self._consecutive_failures += 1

    def _check_bot_health(self) -> bool:
        """Проверка здоровья бота"""
        try:
            # 1. Проверка циклов
            if hasattr(self.bot, '_cycle_count'):
                current = getattr(self.bot, '_cycle_count', 0)
                if current == self._last_cycle_count:
                    self._idle_count += 1
                    if self._idle_count >= self.max_idle_cycles:
                        warning(f"⚠️ Watchdog: бот не обновляет циклы ({self._idle_count} проверок)")
                        self._restart_bot(f"idle for {self._idle_count} cycles")
                        return False
                else:
                    self._idle_count = 0
                self._last_cycle_count = current

            # 2. Проверка памяти
            try:
                import psutil
                process = psutil.Process()
                memory_mb = process.memory_info().rss / 1024 / 1024
                cpu_percent = process.cpu_percent()

                if memory_mb > self.max_memory_mb:
                    warning(f"⚠️ Watchdog: превышена память ({memory_mb:.0f} MB)")
                    self._restart_bot(f"memory limit exceeded: {memory_mb:.0f} MB")
                    return False

                if cpu_percent > self.max_cpu_percent:
                    warning(f"⚠️ Watchdog: превышен CPU ({cpu_percent:.0f}%)")
                    self._restart_bot(f"cpu limit exceeded: {cpu_percent:.0f}%")
                    return False

            except ImportError:
                pass

            # 3. Проверка маржи
            if hasattr(self.bot, 'get_margin_status'):
                try:
                    margin = self.bot.get_margin_status()
                    if margin.get('critical', False):
                        warning("⚠️ Watchdog: критическая маржа")
                        self._restart_bot("critical margin")
                        return False
                except Exception as e:
                    debug(f"Margin check error: {e}")

            # 4. Проверка основного потока
            if hasattr(self.bot, '_running') and not getattr(self.bot, '_running', False):
                warning("⚠️ Watchdog: бот не в состоянии running")
                self._restart_bot("bot not running")
                return False

            return True

        except Exception as e:
            error(f"Watchdog health check error: {e}")
            return False

    def _restart_bot(self, reason: str) -> None:
        """Принудительный перезапуск бота"""
        from datetime import datetime
        import sys
        import os

        if self._restarting:
            return
        self._restarting = True

        warning(f"🔄 Watchdog перезапуск бота: {reason}")

        self._stats["restarts"] = self._stats.get("restarts", 0) + 1
        self._stats["last_restart_reason"] = reason
        self._stats["last_restart_time"] = datetime.now().isoformat()

        def do_restart() -> None:
            try:
                self._restarting = False

                if hasattr(self.bot, '_running'):
                    self.bot._running = False

                time.sleep(2)

                if hasattr(self.bot, 'start'):
                    restart_thread = threading.Thread(target=self.bot.start, daemon=True)
                    restart_thread.start()

                info(f"✅ Watchdog: бот перезапущен после: {reason}")

            except Exception as e:
                error(f"Watchdog не смог перезапустить бота: {e}")
                os.execv(sys.executable, [sys.executable] + sys.argv)

        restart_timer = threading.Timer(1, do_restart)
        restart_timer.daemon = True
        restart_timer.start()

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики надзирателя"""
        return {
            "restarts": self._stats.get("restarts", 0),
            "last_restart_reason": self._stats.get("last_restart_reason"),
            "last_restart_time": self._stats.get("last_restart_time"),
            "health_checks": self._stats.get("health_checks", 0),
            "health_failures": self._stats.get("health_failures", 0),
            "check_interval": self.check_interval,
            "max_idle_cycles": self.max_idle_cycles,
            "running": self._running,
            "current_idle": self._idle_count,
            "consecutive_failures": self._consecutive_failures
        }