"""
System Optimizer - Автоматическая оптимизация бота под текущую систему
Автоматически определяет возможности системы и настраивает параметры
"""

import os
import sys
import time
import json
import psutil
import platform
import subprocess
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict

from trading_bot.logger import info, success, warning, error, debug
from trading_bot.config import config


@dataclass
class SystemProfile:
    """Профиль системы с оптимальными настройками"""
    # Системные параметры
    cpu_count: int
    cpu_freq_mhz: float
    total_ram_mb: float
    available_ram_mb: float
    is_vps: bool
    is_low_end: bool
    network_latency_ms: float

    # Оптимальные настройки бота
    max_workers: int
    max_tickers: int
    cache_ttl_seconds: int
    cycle_sleep_seconds: int
    use_parallel_scan: bool
    use_websocket: bool
    use_fundamental: bool
    use_news: bool
    scan_timeout_seconds: int

    # Дополнительные флаги
    notes: str = ""


class SystemOptimizer:
    """
    Автоматический оптимизатор бота под текущую систему
    """

    def __init__(self, bot=None):
        self.bot = bot
        self.profile: Optional[SystemProfile] = None
        self._optimization_log = []
        self._last_optimization = 0
        self._optimization_interval = 3600  # Раз в час

    def diagnose(self, force: bool = False) -> SystemProfile:
        """
        Полная диагностика системы и создание профиля

        Args:
            force: Принудительная диагностика (игнорировать кэш)
        """
        now = time.time()
        if not force and self.profile and (now - self._last_optimization) < self._optimization_interval:
            info(f"📊 Используем существующий профиль (возраст: {(now - self._last_optimization) / 60:.0f} мин)")
            return self.profile

        info("\n" + "=" * 60)
        info("🔍 ЗАПУСК АВТОМАТИЧЕСКОЙ ДИАГНОСТИКИ СИСТЕМЫ")
        info("=" * 60)

        # 1. Базовые системные параметры
        cpu_count = self._get_cpu_count()
        cpu_freq = self._get_cpu_frequency()
        total_ram, available_ram = self._get_ram_info()
        is_vps = self._is_vps()
        is_low_end = self._is_low_end_system(cpu_count, total_ram)

        info(f"💻 СИСТЕМА:")
        info(f"   🖥️  Платформа: {platform.system()} {platform.release()}")
        info(f"   🔢 CPU ядер: {cpu_count}")
        info(f"   ⚡ Частота CPU: {cpu_freq:.0f} MHz")
        info(f"   💾 RAM: {total_ram:.0f} MB (доступно: {available_ram:.0f} MB)")
        info(f"   🖧 VPS режим: {'✅ ДА' if is_vps else '❌ НЕТ'}")
        info(f"   ⚡ Низкопроизводительная: {'✅ ДА' if is_low_end else '❌ НЕТ'}")

        # 2. Сетевые параметры
        network_latency = self._measure_network_latency()
        info(f"\n🌐 СЕТЬ:")
        info(f"   ⏱️  Задержка до API: {network_latency:.0f} ms")

        # 3. Определяем оптимальные настройки
        settings = self._calculate_optimal_settings(
            cpu_count=cpu_count,
            total_ram_mb=total_ram,  # ← ИСПРАВЛЕНО
            is_vps=is_vps,
            is_low_end=is_low_end,
            network_latency=network_latency
        )

        info(f"\n⚙️ ОПТИМАЛЬНЫЕ НАСТРОЙКИ:")
        info(f"   👥 Параллельных потоков: {settings['max_workers']}")
        info(f"   📊 Макс. тикеров за раз: {settings['max_tickers']}")
        info(f"   💾 TTL кэша: {settings['cache_ttl']} сек")
        info(f"   ⏱️  Пауза между циклами: {settings['cycle_sleep']} сек")
        info(f"   🔄 Параллельное сканирование: {'✅' if settings['use_parallel'] else '❌'}")
        info(f"   📡 WebSocket: {'✅' if settings['use_websocket'] else '❌'}")
        info(f"   📊 Фундаментальный анализ: {'✅' if settings['use_fundamental'] else '❌'}")
        info(f"   📰 Новостной анализ: {'✅' if settings['use_news'] else '❌'}")

        # 4. Применяем настройки
        self.profile = SystemProfile(
            cpu_count=cpu_count,
            cpu_freq_mhz=cpu_freq,
            total_ram_mb=total_ram,
            available_ram_mb=available_ram,
            is_vps=is_vps,
            is_low_end=is_low_end,
            network_latency_ms=network_latency,
            max_workers=settings['max_workers'],
            max_tickers=settings['max_tickers'],
            cache_ttl_seconds=settings['cache_ttl'],
            cycle_sleep_seconds=settings['cycle_sleep'],
            use_parallel_scan=settings['use_parallel'],
            use_websocket=settings['use_websocket'],
            use_fundamental=settings['use_fundamental'],
            use_news=settings['use_news'],
            scan_timeout_seconds=settings['scan_timeout'],
            notes=settings.get('notes', '')
        )

        self._last_optimization = now
        self._log_optimization()

        info("\n" + "=" * 60)
        success("✅ ДИАГНОСТИКА ЗАВЕРШЕНА")
        info("=" * 60 + "\n")

        return self.profile

    def apply_optimizations(self, bot=None) -> Dict[str, Any]:
        """
        Применение оптимальных настроек к боту
        """
        if not self.profile:
            self.diagnose()

        info("\n" + "=" * 60)
        info("🔧 ПРИМЕНЕНИЕ ОПТИМАЛЬНЫХ НАСТРОЕК")
        info("=" * 60)

        applied = {}

        # 1. Настройки сканера
        if bot and hasattr(bot, 'stock_scanner'):
            scanner = bot.stock_scanner
            old_max = getattr(scanner, 'max_tickers_to_scan', 15)
            scanner.max_tickers_to_scan = self.profile.max_tickers
            applied['scanner_max_tickers'] = f"{old_max} → {self.profile.max_tickers}"

            old_workers = getattr(scanner, 'parallel_workers', 8)
            scanner.parallel_workers = self.profile.max_workers
            applied['scanner_workers'] = f"{old_workers} → {self.profile.max_workers}"

            info(f"   📊 StockScanner: max_tickers={self.profile.max_tickers}, workers={self.profile.max_workers}")

        # 2. Настройки кэша
        try:
            from trading_bot.cache import price_cache, candles_cache
            old_price_ttl = price_cache.default_ttl if hasattr(price_cache, 'default_ttl') else 5
            if hasattr(price_cache, 'set_ttl'):
                price_cache.set_ttl(self.profile.cache_ttl_seconds)
            applied['price_cache_ttl'] = f"{old_price_ttl} → {self.profile.cache_ttl_seconds} сек"
            info(f"   💾 price_cache TTL: {self.profile.cache_ttl_seconds} сек")
        except Exception as e:
            debug(f"   ⚠️ Не удалось настроить price_cache: {e}")

        # 3. Настройки анализаторов
        try:
            from trading_bot.core.settings_manager import settings_manager

            old_fund = settings_manager.get('fundamental_enabled', True)
            settings_manager.set('fundamental_enabled', self.profile.use_fundamental)
            applied['fundamental_enabled'] = f"{old_fund} → {self.profile.use_fundamental}"

            old_news = settings_manager.get('news_enabled', True)
            settings_manager.set('news_enabled', self.profile.use_news)
            applied['news_enabled'] = f"{old_news} → {self.profile.use_news}"

            info(f"   📊 Фундаментальный анализ: {'ВКЛ' if self.profile.use_fundamental else 'ВЫКЛ'}")
            info(f"   📰 Новостной анализ: {'ВКЛ' if self.profile.use_news else 'ВЫКЛ'}")
        except Exception as e:
            debug(f"   ⚠️ Не удалось настроить анализаторы: {e}")

        # 4. Настройки цикла
        if bot and hasattr(bot, 'trading_loop'):
            loop = bot.trading_loop
            if hasattr(loop, 'check_interval'):
                old_interval = loop.check_interval
                loop.check_interval = max(5, self.profile.cycle_sleep_seconds)
                applied['check_interval'] = f"{old_interval} → {loop.check_interval} сек"

            # Настройка PositionMonitor
            if hasattr(loop, '_position_monitor') and loop._position_monitor:
                loop._position_monitor.check_interval = max(2, self.profile.cycle_sleep_seconds // 2)
                applied['monitor_interval'] = f"→ {loop._position_monitor.check_interval} сек"

            info(f"   ⏱️  Цикл мониторинга: {loop.check_interval if hasattr(loop, 'check_interval') else 'N/A'} сек")

        # 5. Сохраняем профиль в файл
        self._save_profile()

        info("\n" + "=" * 60)
        success("✅ ОПТИМИЗАЦИЯ ПРИМЕНЕНА")
        info("=" * 60 + "\n")

        return applied

    def get_recommendations(self) -> str:
        """
        Получение рекомендаций по улучшению работы
        """
        if not self.profile:
            self.diagnose()

        recommendations = []

        if self.profile.is_low_end:
            recommendations.append("⚠️ СИСТЕМА С НИЗКОЙ ПРОИЗВОДИТЕЛЬНОСТЬЮ")
            recommendations.append("   → Используйте только рыночные заявки")
            recommendations.append("   → Отключите фундаментальный и новостной анализ")
            recommendations.append("   → Установите max_tickers=3-5")

        if self.profile.network_latency_ms > 500:
            recommendations.append(f"⚠️ ВЫСОКАЯ ЗАДЕРЖКА СЕТИ: {self.profile.network_latency_ms:.0f}ms")
            recommendations.append("   → Используйте лимитные заявки вместо рыночных")
            recommendations.append("   → Увеличьте TTL кэша до 30-60 секунд")

        if self.profile.total_ram_mb < 1024:
            recommendations.append("⚠️ МАЛО ОПЕРАТИВНОЙ ПАМЯТИ")
            recommendations.append("   → Уменьшите размер кэшей")
            recommendations.append("   → Отключите параллельное сканирование")

        if self.profile.is_vps:
            recommendations.append("✅ VPS ОБНАРУЖЕН")
            recommendations.append("   → Можно использовать агрессивные настройки")

        return "\n".join(recommendations) if recommendations else "✅ Система готова к работе"

    def optimize_on_the_fly(self, metric_name: str, value: float):
        """
        Адаптивная оптимизация на лету
        """
        if not self.profile:
            return

        # Адаптация по времени сканирования
        if metric_name == 'scan_time' and value > 60:
            warning(f"⏰ Сканирование заняло {value:.0f}с → уменьшаем тикеры")
            self.profile.max_tickers = max(3, self.profile.max_tickers - 2)
            self._apply_scanner_settings()

        # Адаптация по задержкам API
        elif metric_name == 'api_latency' and value > 3000:
            warning(f"🌐 API задержка {value:.0f}ms → увеличиваем кэш")
            self.profile.cache_ttl_seconds = min(120, self.profile.cache_ttl_seconds + 10)
            self._apply_cache_settings()

        # Адаптация по использованию памяти
        elif metric_name == 'memory_usage' and value > 80:
            warning(f"💾 Память: {value:.0f}% → очищаем кэши")
            self._clear_caches()

    # ========== ВНУТРЕННИЕ МЕТОДЫ ==========

    def _get_cpu_count(self) -> int:
        """Получение количества CPU ядер"""
        try:
            return os.cpu_count() or 2
        except:
            return 2

    def _get_cpu_frequency(self) -> float:
        """Получение частоты CPU"""
        try:
            freq = psutil.cpu_freq()
            return freq.current if freq else 2000.0
        except:
            return 2000.0

    def _get_ram_info(self) -> Tuple[float, float]:
        """Получение информации о RAM"""
        try:
            mem = psutil.virtual_memory()
            return mem.total / (1024 * 1024), mem.available / (1024 * 1024)
        except:
            return 2048.0, 1024.0

    def _is_vps(self) -> bool:
        """Определение, работает ли бот на VPS"""
        # Проверяем по характерным признакам
        vps_indicators = [
            'vps', 'vds', 'cloud', 'digitalocean', 'aws', 'gcp',
            'azure', 'linode', 'hetzner', 'ovh', 'scaleway'
        ]

        # Проверка hostname
        try:
            hostname = platform.node().lower()
            for indicator in vps_indicators:
                if indicator in hostname:
                    return True
        except:
            pass

        # Проверка окружения
        if os.environ.get('RENDER') or os.environ.get('HEROKU'):
            return True

        return False

    def _is_low_end_system(self, cpu_count: int, total_ram_mb: float) -> bool:
        """Определение низкопроизводительной системы"""
        return cpu_count <= 2 or total_ram_mb < 2048

    def _measure_network_latency(self) -> float:
        """Измерение задержки до API Т-Банка"""
        import socket
        import time

        try:
            # Пинг до API
            start = time.time()
            socket.create_connection(('invest-public-api.tinkoff.ru', 443), timeout=5)
            latency = (time.time() - start) * 1000
            return min(latency, 5000)  # Ограничиваем 5 секундами
        except:
            return 1000  # По умолчанию 1 секунда

    def _calculate_optimal_settings(self, cpu_count: int, total_ram_mb: float,
                                    is_vps: bool, is_low_end: bool,
                                    network_latency: float) -> Dict[str, Any]:
        """Расчёт оптимальных настроек на основе диагностики"""

        # Базовые настройки
        if is_vps and not is_low_end:
            # Мощный VPS/сервер
            settings = {
                'max_workers': min(8, cpu_count),
                'max_tickers': 15,
                'cache_ttl': 30,
                'cycle_sleep': 5,
                'use_parallel': True,
                'use_websocket': True,
                'use_fundamental': True,
                'use_news': True,
                'scan_timeout': 30,
                'notes': 'Мощный VPS режим'
            }
        elif is_low_end:
            # Слабый VPS или локальная машина
            settings = {
                'max_workers': max(2, cpu_count // 2),
                'max_tickers': 5,
                'cache_ttl': 60,
                'cycle_sleep': 10,
                'use_parallel': False,
                'use_websocket': False,
                'use_fundamental': False,
                'use_news': False,
                'scan_timeout': 20,
                'notes': 'Экономичный режим'
            }
        else:
            # Средняя система
            settings = {
                'max_workers': max(4, cpu_count // 2),
                'max_tickers': 10,
                'cache_ttl': 45,
                'cycle_sleep': 7,
                'use_parallel': True,
                'use_websocket': True,
                'use_fundamental': True,
                'use_news': True,
                'scan_timeout': 25,
                'notes': 'Сбалансированный режим'
            }

        # Корректировка по задержке сети
        if network_latency > 1000:
            settings['cache_ttl'] = min(120, settings['cache_ttl'] + 30)
            settings['cycle_sleep'] = min(15, settings['cycle_sleep'] + 3)
            settings['notes'] += ', адаптация к высокой задержке'

        # Корректировка по RAM
        if total_ram_mb < 1024:
            settings['max_tickers'] = min(3, settings['max_tickers'])
            settings['use_parallel'] = False
            settings['notes'] += ', ограничение по RAM'

        return settings

    def _apply_scanner_settings(self):
        """Применение настроек сканера"""
        if self.bot and hasattr(self.bot, 'stock_scanner'):
            self.bot.stock_scanner.max_tickers_to_scan = self.profile.max_tickers
            self.bot.stock_scanner.parallel_workers = self.profile.max_workers
            info(
                f"🔄 Обновлены настройки сканера: тикеров={self.profile.max_tickers}, воркеров={self.profile.max_workers}")

    def _apply_cache_settings(self):
        """Применение настроек кэша"""
        try:
            from trading_bot.cache import price_cache
            if hasattr(price_cache, 'set_ttl'):
                price_cache.set_ttl(self.profile.cache_ttl_seconds)
                info(f"🔄 Обновлён TTL кэша: {self.profile.cache_ttl_seconds} сек")
        except Exception as e:
            debug(f"Не удалось обновить TTL кэша: {e}")

    def _clear_caches(self):
        """Очистка кэшей при высокой нагрузке"""
        try:
            from trading_bot.cache import price_cache, candles_cache, positions_cache
            price_cache.clear()
            candles_cache.clear()
            positions_cache.clear()
            info("🧹 Кэши очищены из-за высокой нагрузки")
        except Exception as e:
            debug(f"Ошибка очистки кэшей: {e}")

    def _save_profile(self):
        """Сохранение профиля в файл"""
        try:
            profile_data = {
                'timestamp': datetime.now().isoformat(),
                'profile': asdict(self.profile)
            }
            with open('system_profile.json', 'w') as f:
                json.dump(profile_data, f, indent=2, default=str)
            debug("💾 Профиль системы сохранён в system_profile.json")
        except Exception as e:
            debug(f"Не удалось сохранить профиль: {e}")

    def _log_optimization(self):
        """Логирование оптимизации"""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'profile': asdict(self.profile) if self.profile else {}
        }
        self._optimization_log.append(entry)

        # Ограничиваем лог
        if len(self._optimization_log) > 100:
            self._optimization_log = self._optimization_log[-100:]

        try:
            with open('optimization_log.json', 'w') as f:
                json.dump(self._optimization_log, f, indent=2, default=str)
        except:
            pass


# ========== ИНТЕГРАЦИЯ С БОТОМ ==========

class AutoOptimizer:
    """
    Автоматический оптимизатор, интегрируемый в TradingLoop
    """

    def __init__(self, bot):
        self.bot = bot
        self.optimizer = SystemOptimizer(bot)
        self.is_running = False
        self._last_scan_time = 0

    def start(self):
        """Запуск автооптимизации"""
        self.is_running = True
        info("🚀 AutoOptimizer запущен")

        # Первичная диагностика
        profile = self.optimizer.diagnose()

        # Показываем рекомендации
        recommendations = self.optimizer.get_recommendations()
        if recommendations and "⚠️" in recommendations:
            warning("\n" + recommendations)

        # Применяем настройки
        self.optimizer.apply_optimizations(self.bot)

        return profile

    def stop(self):
        """Остановка автооптимизации"""
        self.is_running = False
        info("🛑 AutoOptimizer остановлен")

    def check_and_optimize(self, cycle_time: float, api_latency: float = None):
        """
        Проверка и оптимизация в процессе работы

        Args:
            cycle_time: Время выполнения цикла в секундах
            api_latency: Задержка API в миллисекундах
        """
        if not self.is_running:
            return

        now = time.time()

        # Проверяем каждые 10 циклов
        if now - self._last_scan_time < 300:  # 5 минут
            return

        self._last_scan_time = now

        # Оценка производительности
        if cycle_time > 60:
            warning(f"⏰ Цикл слишком долгий: {cycle_time:.0f}с → оптимизируем")
            self.optimizer.optimize_on_the_fly('scan_time', cycle_time)

        # Проверка API задержек
        if api_latency and api_latency > 3000:
            self.optimizer.optimize_on_the_fly('api_latency', api_latency)

        # Проверка памяти
        try:
            mem = psutil.virtual_memory()
            if mem.percent > 80:
                self.optimizer.optimize_on_the_fly('memory_usage', mem.percent)
        except:
            pass


# ========== ИСПОЛЬЗОВАНИЕ ==========

def setup_auto_optimization(bot):
    """
    Настройка автоматической оптимизации для бота

    Usage:
        from trading_bot.core.system_optimizer import setup_auto_optimization
        setup_auto_optimization(bot)
    """
    info("\n" + "=" * 60)
    info("🤖 АВТОМАТИЧЕСКАЯ ОПТИМИЗАЦИЯ БОТА")
    info("=" * 60)

    # Создаём оптимизатор
    auto_opt = AutoOptimizer(bot)

    # Запускаем
    profile = auto_opt.start()

    # Сохраняем в бот
    bot.auto_optimizer = auto_opt
    bot.system_profile = profile

    # Выводим сводку
    info("\n📊 СВОДКА ОПТИМИЗАЦИИ:")
    info(f"   👥 Потоков: {profile.max_workers}")
    info(f"   📊 Тикеров: {profile.max_tickers}")
    info(f"   💾 TTL кэша: {profile.cache_ttl_seconds}с")
    info(f"   ⏱️  Цикл: {profile.cycle_sleep_seconds}с")

    return auto_opt


# ========== ТЕСТОВЫЙ ЗАПУСК ==========

if __name__ == "__main__":
    # Тестовая диагностика
    optimizer = SystemOptimizer()
    profile = optimizer.diagnose(force=True)

    print("\n" + "=" * 60)
    print("📊 ПРОФИЛЬ СИСТЕМЫ")
    print("=" * 60)
    for key, value in asdict(profile).items():
        print(f"   {key}: {value}")

    print("\n" + "=" * 60)
    print("💡 РЕКОМЕНДАЦИИ")
    print("=" * 60)
    print(optimizer.get_recommendations())