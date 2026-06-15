#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_polling.py - РАБОЧАЯ ВЕРСИЯ ДЛЯ VPS
Использует polling (не webhook) - работает за NAT и без HTTPS
"""

import os
import sys
import time
import json
import logging
import threading
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MOSCOW_TZ = timezone(timedelta(hours=3))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ========== ЭМОДЗИ ==========
EMOJI = {
    'info': '📊', 'balance': '💰', 'positions': '📈', 'margin': '📊',
    'buy': '🟢', 'sell': '🔴', 'close': '🔒', 'close_all': '🛑',
    'settings': '⚙️', 'tp_sl': '🎯', 'short': '🔻', 'aggressive': '🤖',
    'service': '🛠️', 'cache': '🧹', 'health': '❤️', 'stats': '📊',
    'back': '🔙', 'cancel': '❌', 'confirm': '✅', 'error': '❌',
    'warning': '⚠️', 'success': '✅', 'profit': '📈', 'loss': '📉',
    'history': '📜', 'orders': '📋'
}

# ========== КЛАВИАТУРЫ ==========
MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "📊 Информация"}, {"text": "💰 Торговля"}],
        [{"text": "⚙️ Настройки"}, {"text": "🛠️ Сервис"}],
        [{"text": "❓ Помощь"}]
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False
}

INFO_KEYBOARD = {
    "keyboard": [
        [{"text": "💰 Баланс"}, {"text": "📈 Позиции"}],
        [{"text": "📊 Маржа"}, {"text": "📋 Заявки"}],
        [{"text": "📜 История"}, {"text": "🔙 Назад"}]
    ],
    "resize_keyboard": True
}

TRADE_KEYBOARD = {
    "keyboard": [
        [{"text": "🟢 Купить"}, {"text": "🔴 Продать (SHORT)"}],
        [{"text": "🔒 Закрыть позицию"}, {"text": "🛑 Закрыть всё"}],
        [{"text": "📊 Текущий P&L"}, {"text": "🔙 Назад"}]
    ],
    "resize_keyboard": True
}

SETTINGS_KEYBOARD = {
    "keyboard": [
        [{"text": "🎯 TP/SL"}, {"text": "🔻 SHORT"}],
        [{"text": "📊 Макс. позиций"}, {"text": "🤖 Агрессивность"}],
        [{"text": "🔄 Сброс настроек"}, {"text": "🔙 Назад"}]
    ],
    "resize_keyboard": True
}

SERVICE_KEYBOARD = {
    "keyboard": [
        [{"text": "🧹 Очистить кэш"}, {"text": "❤️ Health check"}],
        [{"text": "📊 Статистика"}, {"text": "🔙 Назад"}]
    ],
    "resize_keyboard": True
}

CANCEL_KEYBOARD = {
    "keyboard": [[{"text": "❌ Отмена"}]],
    "resize_keyboard": True,
    "one_time_keyboard": True
}

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
_trading_bot = None
_user_states: Dict[int, Dict] = {}
_waiting_for_value: Optional[Dict] = None
_last_response_time: Dict[int, float] = {}
_polling_thread = None
_running = False


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def now_msk() -> str:
    return datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')


def get_bot_token() -> Optional[str]:
    """Получение токена Telegram бота"""
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        try:
            from trading_bot.config import config
            token = config.telegram_token
        except:
            pass
    return token


def get_chat_id() -> Optional[int]:
    """Получение chat_id"""
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not chat_id:
        try:
            from trading_bot.config import config
            chat_id = config.telegram_chat_id
        except:
            pass
    if chat_id:
        try:
            return int(chat_id)
        except:
            return None
    return None


def send_message(chat_id: int, text: str, parse_mode: str = "HTML", keyboard: dict = None) -> bool:
    """Отправка сообщения с опциональной клавиатурой"""
    bot_token = get_bot_token()
    if not bot_token:
        return False

    if len(text) > 4000:
        text = text[:3997] + "..."

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)

    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False


def send_main_keyboard(chat_id: int, custom_text: str = None) -> bool:
    """Отправка главного меню"""
    text = custom_text or "🤖 <b>ТОРГОВЫЙ БОТ</b>\n\nВыберите действие:"
    return send_message(chat_id, text, keyboard=MAIN_KEYBOARD)


def get_trading_bot():
    global _trading_bot
    if _trading_bot is None:
        try:
            from trading_bot import get_trading_bot as get_bot
            _trading_bot = get_bot()
        except Exception as e:
            logger.error(f"Error getting trading bot: {e}")
    return _trading_bot


def get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def clear_user_state(chat_id: int):
    if chat_id in _user_states:
        _user_states.pop(chat_id, None)


def cancel_operation(chat_id: int) -> bool:
    clear_user_state(chat_id)
    global _waiting_for_value
    if _waiting_for_value and _waiting_for_value.get('chat_id') == chat_id:
        _waiting_for_value = None
    return send_main_keyboard(chat_id, "❌ Операция отменена")


# ========== ИНФОРМАЦИОННЫЕ МЕТОДЫ ==========

def get_balance_text() -> str:
    try:
        tbank = get_tbank()
        available, total, _ = tbank.get_available_funds()
        return (
            f"{EMOJI['balance']} <b>БАЛАНС</b>\n\n"
            f"💵 Свободно: <b>{available:,.2f}</b> ₽\n"
            f"💎 Капитал: <b>{total:,.2f}</b> ₽\n"
            f"📅 {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')} МСК"
        )
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


def get_positions_text() -> str:
    """Получение текста с позициями и P&L"""
    try:
        bot = get_trading_bot()
        if not bot:
            return f"{EMOJI['error']} Бот не доступен"

        positions = bot._get_positions() if hasattr(bot, '_get_positions') else []
        if not positions:
            return f"{EMOJI['positions']} <b>Нет открытых позиций</b>"

        message = f"{EMOJI['positions']} <b>ОТКРЫТЫЕ ПОЗИЦИИ</b>\n\n"
        total_pnl = 0

        for i, pos in enumerate(positions[:10], 1):
            side = "🔴 SHORT" if pos.get('quantity', 0) < 0 else "🟢 LONG"
            ticker = pos.get('ticker', pos.get('figi', '')[:8])
            qty = abs(pos.get('quantity', 0))
            avg = pos.get('avg_price', 0)
            current = pos.get('current_price', 0)

            if current == 0:
                from trading_bot.api.tbank_client import tbank
                current = tbank.get_current_price(pos.get('figi')) or avg

            if pos.get('quantity', 0) > 0:
                pnl = (current - avg) * qty
                pnl_pct = (current - avg) / avg * 100 if avg > 0 else 0
            else:
                pnl = (avg - current) * qty
                pnl_pct = (avg - current) / avg * 100 if avg > 0 else 0

            total_pnl += pnl

            pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "⚪"
            message += (
                f"{i}. {side} <b>{ticker}</b> {qty}шт\n"
                f"   {avg:.2f} → {current:.2f} | {pnl_emoji} {pnl:+.2f}₽ ({pnl_pct:+.1f}%)\n"
            )

        message += f"\n📊 Общий P&L: <b>{total_pnl:+,.2f}</b> ₽"
        return message
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


def get_margin_text() -> str:
    try:
        tbank = get_tbank()
        margin = tbank.get_margin_info()
        if not margin:
            return f"{EMOJI['error']} Не удалось получить данные о марже"

        rate = margin.get('margin_rate', 0)
        if rate < 70:
            status = "🟢 НОРМА"
        elif rate < 85:
            status = "🟡 ВНИМАНИЕ"
        else:
            status = "🔴 КРИТИЧЕСКИ"

        return (
            f"{EMOJI['margin']} <b>МАРЖА</b> {status}\n\n"
            f"💰 Портфель: <b>{margin.get('liquid_portfolio', 0):,.2f}</b> ₽\n"
            f"🔒 Использовано: <b>{margin.get('used_margin', 0):,.2f}</b> ₽\n"
            f"✅ Доступно: <b>{margin.get('available_margin', 0):,.2f}</b> ₽\n"
            f"📊 Загрузка: <b>{rate:.1f}%</b>"
        )
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


def get_help_text() -> str:
    return (
        "🤖 <b>ПОМОЩЬ ПО БОТУ</b>\n\n"
        "📊 <b>ИНФОРМАЦИЯ</b>\n"
        "   • Баланс - текущий баланс и капитал\n"
        "   • Позиции - открытые позиции с P&L\n"
        "   • Маржа - состояние маржинальной торговли\n\n"
        "💰 <b>ТОРГОВЛЯ</b>\n"
        "   • Купить - открыть LONG позицию\n"
        "   • Продать (SHORT) - открыть SHORT позицию\n"
        "   • Закрыть всё - все позиции\n\n"
        "⚙️ <b>НАСТРОЙКИ</b>\n"
        "   • TP/SL - тейк-профит и стоп-лосс\n"
        "   • SHORT - включение/отключение\n\n"
        "❌ <b>/cancel</b> - отмена операции"
    )


# ========== ТОРГОВЫЕ ФУНКЦИИ ==========

def start_buy_flow(chat_id: int) -> bool:
    clear_user_state(chat_id)
    _user_states[chat_id] = {'state': 'waiting_ticker', 'data': {'direction': 'BUY'}}
    return send_message(chat_id,
                        "🟢 <b>ПОКУПКА</b>\n\nВведите тикер (SBER, GAZP, LKOH):\n\n❌ /cancel - отмена",
                        keyboard=CANCEL_KEYBOARD)


def start_sell_flow(chat_id: int) -> bool:
    clear_user_state(chat_id)
    _user_states[chat_id] = {'state': 'waiting_ticker', 'data': {'direction': 'SELL'}}
    return send_message(chat_id,
                        "🔴 <b>ПРОДАЖА (SHORT)</b>\n\nВведите тикер (SBER, GAZP, LKOH):\n\n❌ /cancel - отмена",
                        keyboard=CANCEL_KEYBOARD)


def process_ticker_input(chat_id: int, ticker: str) -> bool:
    state = _user_states.get(chat_id, {})
    if state.get('state') != 'waiting_ticker':
        return False

    ticker = ticker.upper().strip()
    tbank = get_tbank()

    all_shares = tbank.get_all_shares()
    figi = None
    lot = 1
    for stock in all_shares:
        if stock.get('ticker') == ticker and stock.get('currency') == 'rub':
            figi = stock['figi']
            lot = stock.get('lot', 1)
            break

    if not figi:
        return send_message(chat_id, f"{EMOJI['error']} Тикер {ticker} не найден", keyboard=CANCEL_KEYBOARD)

    current_price = tbank.get_current_price(figi)
    if not current_price:
        return send_message(chat_id, f"{EMOJI['error']} Не удалось получить цену для {ticker}",
                            keyboard=CANCEL_KEYBOARD)

    _user_states[chat_id] = {
        'state': 'waiting_quantity',
        'data': {
            'direction': state['data']['direction'],
            'ticker': ticker,
            'figi': figi,
            'lot': lot,
            'current_price': current_price
        }
    }
    return send_message(chat_id,
                        f"💰 {ticker}: {current_price:.2f}₽\n📦 Лот: {lot} шт\n\nВведите количество (кратно {lot}):\n\n❌ /cancel - отмена",
                        keyboard=CANCEL_KEYBOARD)


def process_quantity_input(chat_id: int, quantity_str: str) -> bool:
    state = _user_states.get(chat_id, {})
    if state.get('state') != 'waiting_quantity':
        return False

    try:
        quantity = int(quantity_str)
        data = state['data']
        lot = data.get('lot', 1)

        if quantity <= 0:
            raise ValueError
        if quantity % lot != 0:
            return send_message(chat_id, f"{EMOJI['warning']} Количество должно быть кратно {lot}",
                                keyboard=CANCEL_KEYBOARD)

        total = quantity * data['current_price']
        side_text = "ПОКУПКА" if data['direction'] == 'BUY' else "ПРОДАЖА (SHORT)"

        _user_states[chat_id] = {
            'state': 'waiting_confirmation',
            'data': {**data, 'quantity': quantity, 'total': total}
        }

        confirm_keyboard = {
            "keyboard": [[{"text": "✅ Подтвердить"}, {"text": "❌ Отмена"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True
        }

        return send_message(chat_id,
                            f"📝 <b>{side_text}</b>\n\n"
                            f"Тикер: {data['ticker']}\n"
                            f"Количество: {quantity} шт\n"
                            f"Цена: {data['current_price']:.2f}₽\n"
                            f"Сумма: {total:,.2f}₽\n\n"
                            f"✅ Подтвердите операцию:",
                            keyboard=confirm_keyboard)
    except ValueError:
        return send_message(chat_id, f"{EMOJI['error']} Введите целое положительное число", keyboard=CANCEL_KEYBOARD)


def execute_order(chat_id: int) -> bool:
    state = _user_states.get(chat_id, {})
    if state.get('state') != 'waiting_confirmation':
        return send_message(chat_id, f"{EMOJI['error']} Нет активной заявки")

    data = state['data']
    direction = data['direction']
    ticker = data['ticker']
    figi = data['figi']
    quantity = data['quantity']

    send_message(chat_id, f"⏳ Исполнение {direction} {ticker}...")

    tbank = get_tbank()
    if direction == 'BUY':
        success = tbank.buy(figi, quantity)
    else:
        success = tbank.sell(figi, quantity)

    if success:
        send_message(chat_id, f"{EMOJI['success']} {direction} {quantity} {ticker} ИСПОЛНЕН!")
    else:
        send_message(chat_id, f"{EMOJI['error']} Ошибка при {direction} {ticker}")

    clear_user_state(chat_id)
    send_main_keyboard(chat_id)
    return True


def close_all_positions(chat_id: int) -> bool:
    try:
        tbank = get_tbank()
        bot = get_trading_bot()
        if not bot:
            return send_message(chat_id, f"{EMOJI['error']} Бот не доступен")

        positions = bot._get_positions() if hasattr(bot, '_get_positions') else []
        if not positions:
            return send_message(chat_id, f"{EMOJI['info']} Нет открытых позиций")

        send_message(chat_id, f"⏳ Закрытие {len(positions)} позиций...")
        closed = 0
        for pos in positions:
            figi = pos['figi']
            qty = abs(pos['quantity'])
            if pos['quantity'] < 0:
                if tbank.buy(figi, qty):
                    closed += 1
            else:
                if tbank.sell(figi, qty):
                    closed += 1
        return send_message(chat_id, f"{EMOJI['success']} Закрыто: {closed}/{len(positions)}")
    except Exception as e:
        return send_message(chat_id, f"{EMOJI['error']} {str(e)[:100]}")


def toggle_short(chat_id: int) -> bool:
    try:
        from trading_bot.core.settings_manager import settings_manager
        from trading_bot.config import config
        current = settings_manager.get('short_enabled', False)
        new_value = not current
        settings_manager.set('short_enabled', new_value)
        config.use_short = new_value
        status = "ВКЛЮЧЕНА" if new_value else "ВЫКЛЮЧЕНА"
        send_message(chat_id, f"{EMOJI['short']} SHORT торговля: <b>{status}</b>")
    except Exception as e:
        send_message(chat_id, f"{EMOJI['error']} {e}")
    return send_main_keyboard(chat_id)


def show_tpsl_menu(chat_id: int) -> bool:
    from trading_bot.config import config
    message = (
        f"{EMOJI['tp_sl']} <b>TP/SL НАСТРОЙКИ</b>\n\n"
        f"📈 Тейк-профит: +{config.take_profit_pct:.1f}%\n"
        f"📉 Стоп-лосс: -{config.stop_loss_pct:.1f}%\n\n"
        f"📝 Введите: <code>TP 1.5</code> или <code>SL 0.8</code>"
    )
    global _waiting_for_value
    _waiting_for_value = {'param': 'tpsl', 'chat_id': chat_id}
    return send_message(chat_id, message, keyboard=CANCEL_KEYBOARD)


def handle_tpsl_value(chat_id: int, text: str) -> bool:
    global _waiting_for_value
    parts = text.strip().upper().split()
    if len(parts) != 2:
        return send_message(chat_id, f"{EMOJI['error']} Формат: TP 1.5 или SL 0.8", keyboard=CANCEL_KEYBOARD)

    param, value = parts[0], parts[1]
    try:
        val = float(value.replace(',', '.'))
        from trading_bot.core.settings_manager import settings_manager

        if param == 'TP':
            if 0.3 <= val <= 5.0:
                settings_manager.set('take_profit_pct', val)
                send_message(chat_id, f"{EMOJI['success']} Тейк-профит: +{val:.1f}%")
            else:
                send_message(chat_id, f"{EMOJI['error']} Диапазон: 0.3-5.0%")
        elif param == 'SL':
            if 0.2 <= val <= 3.0:
                settings_manager.set('stop_loss_pct', val)
                send_message(chat_id, f"{EMOJI['success']} Стоп-лосс: -{val:.2f}%")
            else:
                send_message(chat_id, f"{EMOJI['error']} Диапазон: 0.2-3.0%")
        else:
            send_message(chat_id, f"{EMOJI['error']} Используйте TP или SL")
            return False
    except ValueError:
        send_message(chat_id, f"{EMOJI['error']} Некорректное число")
        return False

    _waiting_for_value = None
    return send_main_keyboard(chat_id)


# ========== ОСНОВНАЯ ОБРАБОТКА ==========

def process_command(text: str, chat_id: int) -> bool:
    global _waiting_for_value

    # Отмена
    if text.lower() == "/cancel":
        return cancel_operation(chat_id)

    # Подтверждение
    if text.lower() == "/confirm" or text == "✅ Подтвердить":
        if _user_states.get(chat_id, {}).get('state') == 'waiting_confirmation':
            return execute_order(chat_id)

    # Ожидание ввода TP/SL
    if _waiting_for_value and _waiting_for_value.get('param') == 'tpsl' and _waiting_for_value.get(
            'chat_id') == chat_id:
        return handle_tpsl_value(chat_id, text)

    # Состояния
    state = _user_states.get(chat_id, {}).get('state')
    if state == 'waiting_ticker':
        return process_ticker_input(chat_id, text)
    elif state == 'waiting_quantity':
        return process_quantity_input(chat_id, text)

    # ГЛАВНОЕ МЕНЮ
    if text in ["📊 Информация", "/info"]:
        return send_message(chat_id, "📊 <b>ИНФОРМАЦИЯ</b>\n\nВыберите раздел:", keyboard=INFO_KEYBOARD)
    elif text in ["💰 Торговля", "/trade"]:
        return send_message(chat_id, "💰 <b>ТОРГОВЛЯ</b>\n\nВыберите действие:", keyboard=TRADE_KEYBOARD)
    elif text in ["⚙️ Настройки", "/settings"]:
        return send_message(chat_id, "⚙️ <b>НАСТРОЙКИ</b>\n\nВыберите параметр:", keyboard=SETTINGS_KEYBOARD)
    elif text in ["🛠️ Сервис", "/service"]:
        return send_message(chat_id, "🛠️ <b>СЕРВИС</b>\n\nВыберите действие:", keyboard=SERVICE_KEYBOARD)
    elif text in ["❓ Помощь", "/help"]:
        return send_message(chat_id, get_help_text())
    elif text in ["🔙 Назад", "/menu", "/start"]:
        return send_main_keyboard(chat_id)

    # ИНФОРМАЦИЯ
    elif text in ["💰 Баланс", "/balance"]:
        return send_message(chat_id, get_balance_text())
    elif text in ["📈 Позиции", "/positions"]:
        return send_message(chat_id, get_positions_text())
    elif text in ["📊 Маржа", "/margin"]:
        return send_message(chat_id, get_margin_text())

    # ТОРГОВЛЯ
    elif text in ["🟢 Купить", "/buy"]:
        return start_buy_flow(chat_id)
    elif text in ["🔴 Продать (SHORT)", "/sell"]:
        return start_sell_flow(chat_id)
    elif text in ["🛑 Закрыть всё", "/close_all"]:
        return close_all_positions(chat_id)

    # НАСТРОЙКИ
    elif text == "🎯 TP/SL":
        return show_tpsl_menu(chat_id)
    elif text == "🔻 SHORT":
        return toggle_short(chat_id)
    elif text == "🔄 Сброс настроек":
        from trading_bot.core.settings_manager import settings_manager
        settings_manager.reset_to_defaults()
        send_message(chat_id, f"{EMOJI['success']} Настройки сброшены!")
        return send_main_keyboard(chat_id)

    # СЕРВИС
    elif text == "🧹 Очистить кэш":
        bot = get_trading_bot()
        if bot and hasattr(bot, 'clear_validation_cache'):
            bot.clear_validation_cache()
        send_message(chat_id, f"{EMOJI['cache']} Кэш очищен!")
        return send_main_keyboard(chat_id)
    elif text == "❤️ Health check":
        try:
            tbank = get_tbank()
            _, total, _ = tbank.get_available_funds()
            send_message(chat_id, f"{EMOJI['health']} <b>HEALTH CHECK</b>\n\n✅ Бот работает\n💰 Капитал: {total:,.2f}₽")
        except Exception as e:
            send_message(chat_id, f"{EMOJI['error']} {e}")
        return send_main_keyboard(chat_id)

    return False


# ========== ПОЛЛИНГ ==========

def polling_loop():
    """Основной цикл polling - получает сообщения от Telegram"""
    global _running

    bot_token = get_bot_token()
    chat_id = get_chat_id()

    print(f"🔍 POLLING STARTED")
    print(f"   Bot token: {'SET' if bot_token else 'NOT SET'}")
    print(f"   Chat ID: {chat_id}")

    if not bot_token or not chat_id:
        print("❌ TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set!")
        return

    last_update_id = 0

    # Отправляем приветственное сообщение при старте
    send_main_keyboard(chat_id, "🤖 Бот запущен!\n\nВыберите действие:")

    while _running:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            response = requests.get(
                url,
                params={'offset': last_update_id + 1, 'timeout': 30},
                timeout=35
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    for update in data.get('result', []):
                        last_update_id = update['update_id']
                        if 'message' in update:
                            msg = update['message']
                            text = msg.get('text', '')
                            user_id = msg.get('chat', {}).get('id')

                            # Проверяем, что сообщение от правильного пользователя
                            if str(user_id) == str(chat_id):
                                process_command(text, user_id)

            time.sleep(1)

        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)


def start_polling_in_background():
    """Запуск polling в фоновом потоке"""
    global _running, _polling_thread

    if _polling_thread and _polling_thread.is_alive():
        print("⚠️ Polling already running")
        return _polling_thread

    _running = True
    _polling_thread = threading.Thread(target=polling_loop, daemon=True)
    _polling_thread.start()
    print("✅ Telegram polling started in background")
    return _polling_thread


def stop_polling():
    """Остановка polling"""
    global _running
    _running = False
    if _polling_thread:
        _polling_thread.join(timeout=5)
    print("🛑 Telegram polling stopped")


# Для тестирования
if __name__ == "__main__":
    start_polling_in_background()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_polling()