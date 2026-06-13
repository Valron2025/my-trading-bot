# telegram_notifier.py - ИСПРАВЛЕННАЯ ВЕРСИЯ
"""Модуль уведомлений в Telegram - ОПТИМИЗИРОВАННАЯ ВЕРСИЯ"""

import requests
import threading
import time
from typing import List, Optional, Dict, Any
from queue import Queue, Empty
from datetime import datetime, timedelta, timezone

from trading_bot.config import config
from ..logger import debug, info, error

MOSCOW_TZ = timezone(timedelta(hours=3))

# Глобальный экземпляр
_telegram_instance = None


class TelegramNotifier:
    """Отправка уведомлений в Telegram"""

    def __init__(self):
        self.token = config.telegram_token
        self.chat_id_raw = config.telegram_chat_id
        self.chat_id = None

        # Обработка chat_id
        if self.chat_id_raw and self.chat_id_raw != "your_chat_id_here":
            try:
                self.chat_id = int(self.chat_id_raw)
            except ValueError:
                self.chat_id = self.chat_id_raw

        self.enabled = False
        self._last_error_time = 0
        self._error_count = 0

        # Очередь сообщений
        self._queue = Queue(maxsize=1000)
        self._running = False
        self._worker_thread = None

        # Инициализация
        self._init_connection()

    def _init_connection(self):
        """Инициализация соединения с Telegram API"""
        if not self.token or not self.chat_id:
            print("⚠️ TELEGRAM_TOKEN или TELEGRAM_CHAT_ID не установлены")
            return

        def try_connect():
            try:
                url = f"https://api.telegram.org/bot{self.token}/getMe"
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    self.enabled = True
                    print("✅ Telegram нотификатор включен")
                    self._start_worker()
                    self._send_sync("🤖 Торговый бот запущен")
            except Exception as e:
                print(f"⚠️ Telegram не доступен: {e}")

        threading.Thread(target=try_connect, daemon=True).start()

    def _start_worker(self):
        """Запуск фонового worker'а"""
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def _worker(self):
        """Фоновый worker для отправки сообщений"""
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
                if item is None:
                    continue

                msg_type, args, kwargs = item
                if msg_type == "message":
                    self._send_sync(*args, **kwargs)
                self._queue.task_done()
                time.sleep(0.1)
            except Empty:
                continue
            except Exception as e:
                debug(f"Telegram worker error: {e}")

    def _send_sync(self, message: str, parse_mode: str = "HTML") -> bool:
        """Синхронная отправка сообщения"""
        if not self.enabled:
            return False

        now = time.time()
        if now - self._last_error_time < 60 and self._error_count > 5:
            return False

        try:
            if len(message) > 4000:
                message = message[:3997] + "..."

            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True
                },
                timeout=30
            )

            if response.status_code == 200:
                self._error_count = 0
                return True
            else:
                self._error_count += 1
                self._last_error_time = now
                return False

        except Exception as e:
            self._error_count += 1
            self._last_error_time = now
            debug(f"Telegram send error: {e}")
            return False

    # ========== ПУБЛИЧНЫЕ МЕТОДЫ ==========

    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """Отправка сообщения"""
        if not self.enabled:
            return False
        try:
            self._queue.put(("message", (message, parse_mode), {}))
            return True
        except Exception:
            return False

    def send(self, message: str) -> bool:
        """Алиас для send_message"""
        return self.send_message(message)

    def send_warning(self, message: str):
        self.send_message(f"⚠️ {message}")

    def send_success(self, message: str):
        self.send_message(f"✅ {message}")

    def send_error(self, message: str):
        self.send_message(f"❌ {message}")

    def send_info(self, message: str):
        self.send_message(f"ℹ️ {message}")

    def send_trade_opened(self, side: str, ticker: str, quantity: int, price: float):
        emoji = "🟢" if side == "LONG" else "🔴"
        self.send_message(f"{emoji} {side} {ticker}: {quantity} шт по {price:.2f}₽")

    def send_trade_closed(self, side: str, reason: str, profit_pct: float, profit_amount: float, ticker: str = "", quantity: int = 0):
        emoji = "✅" if profit_pct > 0 else "❌"
        msg = f"{emoji} Закрыт {side}"
        if ticker:
            msg += f" {ticker}"
        msg += f": {reason} | {profit_pct:+.1f}% ({profit_amount:+.2f}₽)"
        self.send_message(msg)

    # ========== НОВЫЕ МЕТОДЫ ДЛЯ ОТЧЁТОВ ==========

    def send_daily_report(self, stats: Dict[str, Any]):
        """Отправка ежедневного отчёта"""
        now = datetime.now(MOSCOW_TZ)
        report = (
            f"📊 <b>ЕЖЕДНЕВНЫЙ ОТЧЁТ</b>\n"
            f"📅 {now.strftime('%d.%m.%Y')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Капитал: {stats.get('capital', 0):,.2f}₽\n"
            f"📈 Позиций: {stats.get('positions', 0)}\n"
            f"💵 P&L: {stats.get('pnl', 0):+,.2f}₽\n"
            f"🎯 Win Rate: {stats.get('win_rate', 0):.1f}%\n"
            f"🔄 Сделок: {stats.get('trades', 0)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Бот работает стабильно"
        )
        self.send_message(report)

    def send_pnl_alert(self, ticker: str, profit_pct: float, profit_amount: float, is_tp: bool = True):
        """Отправка alert о сработавшем TP/SL"""
        if is_tp:
            msg = f"🎯 <b>ТЕЙК-ПРОФИТ СРАБОТАЛ!</b>\n"
        else:
            msg = f"🛑 <b>СТОП-ЛОСС СРАБОТАЛ!</b>\n"
        msg += f"📊 {ticker}: {profit_pct:+.1f}% ({profit_amount:+.2f}₽)"
        self.send_message(msg)

    def stop_notifier(self):
        """Остановка сервиса"""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=3)


def get_telegram_notifier():
    """Получение глобального экземпляра"""
    global _telegram_instance
    if _telegram_instance is None:
        _telegram_instance = TelegramNotifier()
    return _telegram_instance


telegram = get_telegram_notifier()