# telegram_notifier.py
"""Модуль уведомлений в Telegram - ПРОДАКШЕН ВЕРСИЯ"""

import requests
import threading
import time
from typing import List
from queue import Queue, Empty
from ..config import config
from ..logger import debug, info

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
        if not self.token:
            print("⚠️ TELEGRAM_TOKEN не установлен")
            return

        if not self.chat_id:
            print("⚠️ TELEGRAM_CHAT_ID не установлен")
            return

        # Не блокируем запуск бота при недоступности Telegram
        self.enabled = False
        print("⚠️ Telegram отключён (проблемы с сетью)")

        # Пробуем подключиться в фоне
        def try_connect():
            try:
                url = f"https://api.telegram.org/bot{self.token}/getMe"
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    self.enabled = True
                    print("✅ Telegram нотификатор включен")
                    self._start_worker()
                    self._send_sync("🤖 Telegram бот запущен", parse_mode="HTML")
            except Exception:
                pass

        threading.Thread(target=try_connect, daemon=True).start()

    def _start_worker(self):
        """Запуск фонового worker'а"""
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        print("✅ Telegram worker поток запущен")

    def _worker(self):
        """Фоновый worker для отправки сообщений"""
        while self._running:
            try:
                try:
                    item = self._queue.get(timeout=1.0)
                except Empty:
                    continue

                if item is None:
                    continue

                msg_type, args, kwargs = item

                if msg_type == "message":
                    self._send_sync(*args, **kwargs)
                elif msg_type == "reply_keyboard":
                    self._send_reply_keyboard_sync(*args, **kwargs)

                self._queue.task_done()
                time.sleep(0.1)

            except Exception as e:
                debug(f"Telegram worker error: {e}")
                time.sleep(1)

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

        except requests.exceptions.Timeout:
            self._error_count += 1
            self._last_error_time = now
            return False
        except Exception as e:
            self._error_count += 1
            self._last_error_time = now
            debug(f"Telegram send error: {e}")
            return False

    def _send_reply_keyboard_sync(self, chat_id: int, text: str, buttons: List[str]) -> bool:
        """Синхронная отправка Reply Keyboard"""
        if not self.enabled:
            return False

        try:
            keyboard = {
                "keyboard": [[{"text": btn}] for btn in buttons],
                "resize_keyboard": True,
                "one_time_keyboard": False
            }

            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": keyboard,
                    "disable_web_page_preview": True
                },
                timeout=30
            )

            if response.status_code == 200:
                info(f"Reply Keyboard отправлен в чат {chat_id}")
                return True
            return False

        except Exception as e:
            debug(f"Reply Keyboard error: {e}")
            return False

    # ========== ПУБЛИЧНЫЕ МЕТОДЫ ==========

    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """Асинхронная отправка сообщения"""
        if not self.enabled:
            return False
        try:
            self._queue.put(("message", (message, parse_mode), {}))
            return True
        except Exception:
            return False

    def send_async(self, message: str, parse_mode: str = "HTML") -> bool:
        """Алиас для send_message"""
        return self.send_message(message, parse_mode)

    def send_reply_keyboard(self, chat_id: int, text: str, buttons: List[str]) -> bool:
        """Асинхронная отправка Reply Keyboard"""
        if not self.enabled:
            return False
        try:
            self._queue.put(("reply_keyboard", (chat_id, text, buttons), {}))
            return True
        except Exception:
            return False

    def send_warning(self, message: str):
        self.send_message(f"⚠️ {message}")

    def send_success(self, message: str):
        self.send_message(f"✅ {message}")

    def send_error(self, message: str):
        self.send_message(f"❌ {message}")

    def send_info(self, message: str):
        self.send_message(f"ℹ️ {message}")

    def send_trade_opened(self, side: str, name: str, quantity: int, price: float):
        emoji = "🟢" if side == "LONG" else "🔴"
        self.send_message(f"{emoji} {side} {name}: {quantity} шт по {price:.2f}₽")

    def send_trade_closed(self, side: str, reason: str, profit_pct: float, profit_amount: float):
        emoji = "✅" if profit_pct > 0 else "❌"
        self.send_message(f"{emoji} Закрыт {side}: {reason} | {profit_pct:+.1f}% ({profit_amount:+.2f}₽)")

    def send_startup(self, total_capital: float, min_trade_amount: int, stop_loss_pct: float):
        from trading_bot.config import config
        message = (
            f"🚀 <b>Торговый бот запущен</b>\n\n"
            f"💰 Капитал: {total_capital:.2f}₽\n"
            f"📊 Мин.сделка: {min_trade_amount}₽\n"
            f"🛑 Стоп: {stop_loss_pct:.1f}%\n"
            f"🎯 Тейк: {config.take_profit_pct:.1f}%\n"
            f"⏰ Таймаут: {config.adaptive_timeout_minutes} мин"
        )
        self.send_message(message)

    def send_shutdown(self):
        self.send_message("🛑 Торговый бот остановлен")

    def stop(self):
        """Остановка сервиса"""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        print("🛑 Telegram нотификатор остановлен")

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Алиас для send_message"""
        return self.send_message(message, parse_mode)


def get_telegram_notifier():
    """Получение глобального экземпляра TelegramNotifier"""
    global _telegram_instance
    if _telegram_instance is None:
        _telegram_instance = TelegramNotifier()
    return _telegram_instance


# Для обратной совместимости
telegram = get_telegram_notifier()