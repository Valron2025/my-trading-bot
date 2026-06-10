#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_polling.py - КОМПАКТНОЕ МЕНЮ ДЛЯ TELEGRAM
Категории: 📊 Инфо | 💰 Торговля | ⚙️ Настройки | 🛠️ Сервис
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MOSCOW_TZ = timezone(timedelta(hours=3))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Глобальные переменные
_trading_bot = None
_user_states: Dict[int, Dict[str, Any]] = {}
_waiting_for_value: Optional[Dict] = None
_last_response_time: Dict[int, float] = {}


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def now_msk() -> str:
    return datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')


def get_bot_token() -> Optional[str]:
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        try:
            from trading_bot.config import config
            token = config.telegram_token
        except:
            pass
    return token


def get_chat_id() -> Optional[int]:
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
            return chat_id
    return None


def send_message(chat_id: int, text: str, parse_mode: str = "HTML") -> bool:
    bot_token = get_bot_token()
    if not bot_token:
        return False

    if len(text) > 4000:
        text = text[:3997] + "..."

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False


def send_main_keyboard(chat_id: int, custom_text: str = None) -> bool:
    """Отправка КОМПАКТНОГО главного меню (14 кнопок, 2 ряда)"""
    bot_token = get_bot_token()
    if not bot_token:
        return False

    # КОМПАКТНОЕ МЕНЮ - группировка по категориям
    buttons = [
        "📊 Статус", "💰 Баланс", "📈 Позиции", "📊 Маржа",
        "🟢 Купить", "🔴 Продать", "🔒 Закрыть всё", "❌ Закрыть тикер",
        "⚙️ Настройки", "🎯 TP/SL", "🔄 АВТО", "📋 Заявки",
        "❓ Помощь", "🛠️ Сервис"
    ]

    keyboard = {
        "keyboard": [[{"text": btn}] for btn in buttons],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

    text = custom_text or "📱 <b>ГЛАВНОЕ МЕНЮ</b>\n\nВыберите действие:"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending keyboard: {e}")
        return False


def send_custom_keyboard(chat_id: int, text: str, buttons: List[str]) -> bool:
    """Отправка кастомной клавиатуры"""
    bot_token = get_bot_token()
    if not bot_token:
        return False

    keyboard = {
        "keyboard": [[{"text": btn}] for btn in buttons],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending custom keyboard: {e}")
        return False


def get_trading_bot():
    global _trading_bot
    if _trading_bot is None:
        try:
            from trading_bot import get_trading_bot as get_bot
            _trading_bot = get_bot()
        except:
            pass
    return _trading_bot


def get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def clear_user_state(chat_id: int):
    if chat_id in _user_states:
        _user_states[chat_id] = {'state': 'idle', 'data': {}}
    else:
        _user_states[chat_id] = {'state': 'idle', 'data': {}}


def cancel_operation(chat_id: int) -> bool:
    clear_user_state(chat_id)
    global _waiting_for_value
    if _waiting_for_value and _waiting_for_value.get('chat_id') == chat_id:
        _waiting_for_value = None
    return send_message(chat_id, "❌ Операция отменена")


# ========== ИНФОРМАЦИОННЫЕ МЕТОДЫ ==========

def get_status_text() -> str:
    try:
        from trading_bot.config import config
        tbank = get_tbank()
        available, total, _ = tbank.get_available_funds()

        if total == 0:
            try:
                margin_info = tbank.get_margin_info()
                if margin_info:
                    total = margin_info.get('liquid_portfolio', 0)
                    starting = margin_info.get('starting_margin', 0)
                    available = total - starting
            except:
                pass

        return (
            f"📊 <b>СТАТУС БОТА</b>\n\n"
            f"💰 Капитал: <b>{total:.2f}</b> ₽\n"
            f"💵 Свободно: <b>{available:.2f}</b> ₽\n"
            f"🎯 Тейк: +{config.take_profit_pct:.1f}% | Стоп: -{config.stop_loss_pct:.1f}%\n"
            f"🔻 SHORT: {'✅' if config.use_short else '❌'}\n"
            f"⏱ {now_msk()} МСК"
        )
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


def get_balance_text() -> str:
    try:
        tbank = get_tbank()
        available, total, _ = tbank.get_available_funds()
        return (
            f"💰 <b>БАЛАНС</b>\n\n"
            f"💵 Свободно: <b>{available:.2f}</b> ₽\n"
            f"💎 Капитал: <b>{total:.2f}</b> ₽\n"
            f"⏱ {now_msk()} МСК"
        )
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


def get_positions_text() -> str:
    try:
        bot = get_trading_bot()
        if not bot:
            return "❌ Торговый бот не инициализирован"

        positions = bot._get_positions() if hasattr(bot, '_get_positions') else []
        if not positions:
            return "📭 <b>Нет открытых позиций</b>"

        message = "📈 <b>ОТКРЫТЫЕ ПОЗИЦИИ</b>\n\n"
        total_pnl = 0
        for i, pos in enumerate(positions[:8], 1):
            side = "🔴 SHORT" if pos.get('quantity', 0) < 0 else "🟢 LONG"
            ticker = pos.get('ticker', pos.get('figi', '')[:8])
            qty = abs(pos.get('quantity', 0))
            avg = pos.get('avg_price', 0)
            current = pos.get('current_price', 0)
            pnl = (current - avg) * qty if pos.get('quantity', 0) > 0 else (avg - current) * qty
            total_pnl += pnl
            message += f"{i}. {side} <b>{ticker}</b> {qty}шт\n   {avg:.2f}→{current:.2f} | {pnl:+.2f}₽\n"
        message += f"\n📊 Общий P&L: <b>{total_pnl:+.2f}</b> ₽"
        return message
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


def get_margin_text() -> str:
    try:
        tbank = get_tbank()
        margin = tbank.get_margin_info()
        if not margin:
            return "❌ Не удалось получить данные о марже"
        rate = margin.get('margin_rate', 0)
        status = "🟢 НОРМА" if rate < 70 else "🟡 ВНИМАНИЕ" if rate < 85 else "🔴 КРИТИЧЕСКИ"
        return (
            f"📊 <b>МАРЖА</b> {status}\n\n"
            f"💰 Портфель: <b>{margin.get('liquid_portfolio', 0):.2f}</b> ₽\n"
            f"🔒 Использовано: <b>{margin.get('used_margin', 0):.2f}</b> ₽\n"
            f"✅ Доступно: <b>{margin.get('available_margin', 0):.2f}</b> ₽\n"
            f"📊 Загрузка: <b>{rate:.1f}%</b>"
        )
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


def get_active_orders_text() -> str:
    try:
        tbank = get_tbank()
        orders = tbank.get_active_orders()
        if not orders:
            return "📭 <b>Нет активных заявок</b>"
        message = "📋 <b>АКТИВНЫЕ ЗАЯВКИ</b>\n\n"
        for i, order in enumerate(orders[:10], 1):
            emoji = "🟢" if order.get('direction') == 'BUY' else "🔴"
            message += f"{i}. {emoji} <b>{order.get('ticker', '?')}</b> {order.get('quantity', 0)}шт @ {order.get('price', 0):.2f}₽\n"
        return message
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


def get_config_text() -> str:
    try:
        from trading_bot.config import config
        return (
            f"⚙️ <b>НАСТРОЙКИ</b>\n\n"
            f"🎯 Тейк-профит: <b>+{config.take_profit_pct:.1f}%</b>\n"
            f"🛑 Стоп-лосс: <b>-{config.stop_loss_pct:.1f}%</b>\n"
            f"🔻 Трейлинг: <b>{config.trailing_stop_pct:.2f}%</b>\n"
            f"📊 Макс. позиций: <b>{config.max_positions}</b>\n"
            f"🔻 SHORT: <b>{'✅ Вкл' if config.use_short else '❌ Выкл'}</b>\n"
            f"⏰ Таймаут: <b>{config.adaptive_timeout_minutes} мин</b>"
        )
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:50]}"


def get_help_text() -> str:
    return (
        "🤖 <b>ТОРГОВЫЙ БОТ - ПОМОЩЬ</b>\n\n"
        "📊 <b>ИНФОРМАЦИЯ:</b>\n"
        "   Статус, Баланс, Позиции, Маржа\n\n"
        "💰 <b>ТОРГОВЛЯ:</b>\n"
        "   Купить, Продать, Закрыть всё, Закрыть тикер\n\n"
        "⚙️ <b>НАСТРОЙКИ:</b>\n"
        "   Настройки, TP/SL, АВТО, Заявки\n\n"
        "❌ <b>Отмена:</b> /cancel"
    )


# ========== ТОРГОВЫЕ ФУНКЦИИ ==========

def start_buy_flow(chat_id: int) -> bool:
    clear_user_state(chat_id)
    _user_states[chat_id] = {'state': 'waiting_ticker', 'data': {'direction': 'BUY'}}
    return send_message(chat_id, "🟢 <b>ПОКУПКА</b>\n\nВведите тикер (SBER, GAZP, LKOH):\n\n/cancel - отмена")


def start_sell_flow(chat_id: int) -> bool:
    clear_user_state(chat_id)
    _user_states[chat_id] = {'state': 'waiting_ticker', 'data': {'direction': 'SELL'}}
    return send_message(chat_id, "🔴 <b>ПРОДАЖА</b>\n\nВведите тикер (SBER, GAZP, LKOH):\n\n/cancel - отмена")


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
        return send_message(chat_id, f"❌ Тикер {ticker} не найден")

    current_price = tbank.get_current_price(figi)
    if not current_price:
        return send_message(chat_id, f"❌ Не удалось получить цену для {ticker}")

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
    return send_message(chat_id, f"💰 {ticker}: {current_price:.2f}₽\n📦 Лот: {lot} шт\n\nВведите количество (кратно {lot}):")


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
            return send_message(chat_id, f"❌ Количество должно быть кратно {lot}")

        _user_states[chat_id] = {
            'state': 'waiting_confirmation',
            'data': {**data, 'quantity': quantity}
        }
        return send_message(
            chat_id,
            f"📝 {data['ticker']}: {quantity} шт по {data['current_price']:.2f}₽\n"
            f"💵 Сумма: {quantity * data['current_price']:.2f}₽\n\n"
            f"✅ Подтвердите: /confirm\n❌ Отмена: /cancel"
        )
    except ValueError:
        return send_message(chat_id, "❌ Введите целое положительное число")


def execute_order(chat_id: int) -> bool:
    state = _user_states.get(chat_id, {})
    if state.get('state') != 'waiting_confirmation':
        return send_message(chat_id, "❌ Нет активной заявки")

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
        send_message(chat_id, f"✅ {direction} {quantity} {ticker} исполнен")
    else:
        send_message(chat_id, f"❌ Ошибка при {direction} {ticker}")

    clear_user_state(chat_id)
    return True


def close_all_positions(chat_id: int) -> bool:
    try:
        tbank = get_tbank()
        bot = get_trading_bot()
        if not bot:
            return send_message(chat_id, "❌ Бот не доступен")

        positions = bot._get_positions() if hasattr(bot, '_get_positions') else []
        if not positions:
            return send_message(chat_id, "📭 Нет позиций")

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
        return send_message(chat_id, f"✅ Закрыто: {closed}/{len(positions)}")
    except Exception as e:
        return send_message(chat_id, f"❌ {str(e)[:100]}")


def close_by_ticker(chat_id: int, ticker: str) -> bool:
    ticker = ticker.upper().strip()
    try:
        tbank = get_tbank()
        bot = get_trading_bot()
        positions = bot._get_positions() if bot and hasattr(bot, '_get_positions') else []

        for pos in positions:
            pos_ticker = pos.get('ticker', '').upper()
            if pos_ticker == ticker:
                figi = pos['figi']
                qty = abs(pos['quantity'])
                if pos['quantity'] < 0:
                    success = tbank.buy(figi, qty)
                else:
                    success = tbank.sell(figi, qty)
                if success:
                    return send_message(chat_id, f"✅ {ticker} закрыт")
                else:
                    return send_message(chat_id, f"❌ Ошибка закрытия {ticker}")
        return send_message(chat_id, f"❌ Позиция {ticker} не найдена")
    except Exception as e:
        return send_message(chat_id, f"❌ {str(e)[:100]}")


def start_close_ticker(chat_id: int) -> bool:
    clear_user_state(chat_id)
    _user_states[chat_id] = {'state': 'close_ticker', 'data': {}}
    return send_message(chat_id, "🔒 <b>ЗАКРЫТИЕ ПО ТИКЕРУ</b>\n\nВведите тикер:\n\n/cancel - отмена")


# ========== НАСТРОЙКИ ==========

def show_settings_menu(chat_id: int) -> bool:
    buttons = ["📈 Тейк-профит", "📉 Стоп-лосс", "🔻 SHORT", "🎯 TP/SL", "🔄 АВТО", "🔙 Назад"]
    return send_custom_keyboard(chat_id, "⚙️ <b>НАСТРОЙКИ</b>\n\nВыберите параметр:", buttons)


def handle_auto_settings(chat_id: int) -> bool:
    send_message(chat_id, "🔄 Автонастройка...")
    try:
        tbank = get_tbank()
        _, total, _ = tbank.get_available_funds()
        bot = get_trading_bot()
        if bot and hasattr(bot, 'trading_loop'):
            bot.trading_loop._adaptive_configuration(total)
            send_message(chat_id, f"✅ Настройки адаптированы под капитал {total:.0f}₽")
        else:
            send_message(chat_id, "❌ Ошибка автонастройки")
    except Exception as e:
        send_message(chat_id, f"❌ {e}")
    return show_settings_menu(chat_id)


def handle_short_toggle(chat_id: int) -> bool:
    try:
        from trading_bot.core.settings_manager import settings_manager
        from trading_bot.config import config
        current = settings_manager.get('short_enabled', False)
        new_value = not current
        settings_manager.set('short_enabled', new_value)
        config.use_short = new_value
        send_message(chat_id, f"🔻 SHORT {'✅ ВКЛ' if new_value else '❌ ВЫКЛ'}")
    except Exception as e:
        send_message(chat_id, f"❌ {e}")
    return show_settings_menu(chat_id)


def show_tpsl_menu(chat_id: int) -> bool:
    from trading_bot.config import config
    message = (
        f"🎯 <b>TP/SL НАСТРОЙКИ</b>\n\n"
        f"📈 Тейк-профит: +{config.take_profit_pct:.1f}%\n"
        f"📉 Стоп-лосс: -{config.stop_loss_pct:.1f}%\n"
        f"🔻 Трейлинг: {config.trailing_stop_pct:.2f}%\n\n"
        f"📝 Введите: TP 1.5 или SL 0.8"
    )
    _waiting_for_value = {'param': 'tpsl', 'chat_id': chat_id}
    return send_message(chat_id, message)


def handle_tpsl_value(chat_id: int, text: str) -> bool:
    global _waiting_for_value
    parts = text.strip().upper().split()
    if len(parts) != 2:
        return send_message(chat_id, "❌ Формат: TP 1.5 или SL 0.8")

    param, value = parts[0], parts[1]
    try:
        val = float(value.replace(',', '.'))
        from trading_bot.core.settings_manager import settings_manager
        if param == 'TP':
            if 0.3 <= val <= 3.0:
                settings_manager.set('take_profit_pct', val)
                send_message(chat_id, f"✅ Тейк-профит: +{val:.1f}%")
            else:
                send_message(chat_id, "❌ Диапазон: 0.3-3.0%")
        elif param == 'SL':
            if 0.2 <= val <= 2.0:
                settings_manager.set('stop_loss_pct', val)
                send_message(chat_id, f"✅ Стоп-лосс: -{val:.1f}%")
            else:
                send_message(chat_id, "❌ Диапазон: 0.2-2.0%")
        else:
            send_message(chat_id, "❌ Используйте TP или SL")
    except ValueError:
        send_message(chat_id, "❌ Некорректное число")

    _waiting_for_value = None
    return show_tpsl_menu(chat_id)


# ========== СЕРВИС ==========

def show_service_menu(chat_id: int) -> bool:
    buttons = ["🗂️ Кэш", "❤️ Health", "📋 Заявки", "🔙 Назад"]
    return send_custom_keyboard(chat_id, "🛠️ <b>СЕРВИС</b>", buttons)


def clear_validation_cache(chat_id: int) -> bool:
    bot = get_trading_bot()
    if bot and hasattr(bot, 'clear_validation_cache'):
        bot.clear_validation_cache()
        return send_message(chat_id, "✅ Кеш очищен")
    return send_message(chat_id, "❌ Ошибка")


def health_check(chat_id: int) -> bool:
    try:
        tbank = get_tbank()
        available, total, _ = tbank.get_available_funds()
        return send_message(chat_id, f"❤️ <b>HEALTH</b>\n\n✅ Бот работает\n💰 Капитал: {total:.2f}₽\n⏱ {now_msk()} МСК")
    except Exception as e:
        return send_message(chat_id, f"❌ {e}")


# ========== ОСНОВНАЯ ОБРАБОТКА ==========

def process_command(text: str, chat_id: int) -> bool:
    global _waiting_for_value

    # Отмена
    if text.lower() == "/cancel":
        return cancel_operation(chat_id)

    # Подтверждение
    if text.lower() == "/confirm":
        return execute_order(chat_id)

    # Ожидание ввода TP/SL
    if _waiting_for_value and _waiting_for_value.get('param') == 'tpsl' and _waiting_for_value.get('chat_id') == chat_id:
        return handle_tpsl_value(chat_id, text)

    # Состояния
    state = _user_states.get(chat_id, {'state': 'idle'})
    if state.get('state') == 'waiting_ticker':
        return process_ticker_input(chat_id, text)
    elif state.get('state') == 'waiting_quantity':
        return process_quantity_input(chat_id, text)
    elif state.get('state') == 'close_ticker':
        return close_by_ticker(chat_id, text)

    # ГЛАВНОЕ МЕНЮ
    if text in ["📊 Статус", "/status"]:
        return send_message(chat_id, get_status_text())
    elif text in ["💰 Баланс", "/balance"]:
        return send_message(chat_id, get_balance_text())
    elif text in ["📈 Позиции", "/positions"]:
        return send_message(chat_id, get_positions_text())
    elif text in ["📊 Маржа", "/margin"]:
        return send_message(chat_id, get_margin_text())
    elif text in ["🟢 Купить", "/buy"]:
        return start_buy_flow(chat_id)
    elif text in ["🔴 Продать", "/sell"]:
        return start_sell_flow(chat_id)
    elif text in ["🔒 Закрыть всё", "/close_all"]:
        return close_all_positions(chat_id)
    elif text in ["❌ Закрыть тикер", "/close_ticker"]:
        return start_close_ticker(chat_id)
    elif text in ["⚙️ Настройки", "/settings"]:
        return show_settings_menu(chat_id)
    elif text in ["🎯 TP/SL", "/tpsl"]:
        return show_tpsl_menu(chat_id)
    elif text in ["🔄 АВТО", "/auto"]:
        return handle_auto_settings(chat_id)
    elif text in ["📋 Заявки", "/orders"]:
        return send_message(chat_id, get_active_orders_text())
    elif text in ["❓ Помощь", "/help"]:
        return send_message(chat_id, get_help_text())
    elif text in ["🛠️ Сервис", "/service"]:
        return show_service_menu(chat_id)
    elif text in ["🗂️ Кэш", "/cache"]:
        return clear_validation_cache(chat_id)
    elif text in ["❤️ Health", "/health"]:
        return health_check(chat_id)
    elif text in ["🔙 Назад", "/menu", "/start"]:
        return send_main_keyboard(chat_id)
    elif text in ["🔻 SHORT"]:
        return handle_short_toggle(chat_id)
    elif text in ["📈 Тейк-профит", "📉 Стоп-лосс"]:
        return show_tpsl_menu(chat_id)

    return False


# ========== ПОЛЛИНГ ==========

def polling_loop():
    bot_token = get_bot_token()
    chat_id = get_chat_id()

    print(f"🔍 DEBUG: bot_token={'SET' if bot_token else 'NOT SET'}")
    print(f"🔍 DEBUG: chat_id={chat_id}")

    if not bot_token or not chat_id:
        print("❌ TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set!")
        return

    # Тестовая отправка
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": "🤖 Бот запущен!"}, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram test: {e}")

    last_update_id = 0
    send_main_keyboard(chat_id)
    print("✅ Telegram polling started")

    while True:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            response = requests.get(url, params={'offset': last_update_id + 1, 'timeout': 30}, timeout=35)

            if response.status_code != 200:
                time.sleep(5)
                continue

            data = response.json()
            if data.get('ok'):
                for update in data.get('result', []):
                    last_update_id = update['update_id']
                    if 'message' in update:
                        msg = update['message']
                        text = msg.get('text', '')
                        user_id = msg.get('chat', {}).get('id')
                        if str(user_id) == str(chat_id):
                            process_command(text, user_id)

        except requests.exceptions.Timeout:
            time.sleep(5)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(15)


def start_polling_in_background():
    thread = threading.Thread(target=polling_loop, daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    polling_loop()