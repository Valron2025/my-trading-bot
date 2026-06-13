#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_polling.py - ПОЛНОСТЬЮ ПЕРЕРАБОТАННАЯ ВЕРСИЯ
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
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


# ========== СОСТОЯНИЯ ==========
class UserState(Enum):
    IDLE = "idle"
    WAITING_TICKER = "waiting_ticker"
    WAITING_QUANTITY = "waiting_quantity"
    WAITING_CONFIRMATION = "waiting_confirmation"
    WAITING_TICKER_CLOSE = "waiting_ticker_close"
    WAITING_TP_VALUE = "waiting_tp_value"
    WAITING_SL_VALUE = "waiting_sl_value"


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
        except:
            pass
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
            f"{EMOJI['info']} <b>СТАТУС БОТА</b>\n\n"
            f"💰 Капитал: <b>{total:,.2f}</b> ₽\n"
            f"💵 Свободно: <b>{available:,.2f}</b> ₽\n"
            f"🎯 Тейк: +{config.take_profit_pct:.1f}% | Стоп: -{config.stop_loss_pct:.1f}%\n"
            f"🔻 SHORT: {'✅' if config.use_short else '❌'}\n"
            f"⏱ {now_msk()} МСК"
        )
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


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
        total_value = 0

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
            total_value += qty * current

            pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "⚪"
            message += (
                f"{i}. {side} <b>{ticker}</b> {qty}шт\n"
                f"   {avg:.2f} → {current:.2f} | "
                f"{pnl_emoji} {pnl:+.2f}₽ ({pnl_pct:+.1f}%)\n"
            )

        total_pnl_pct = (total_pnl / (total_value - total_pnl) * 100) if total_value > total_pnl else 0
        message += f"\n📊 <b>ИТОГО:</b>\n"
        message += f"   Стоимость: {total_value:,.2f}₽\n"
        message += f"   P&L: {total_pnl:+,.2f}₽ ({total_pnl_pct:+.1f}%)"

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


def get_orders_text() -> str:
    try:
        tbank = get_tbank()
        orders = tbank.get_active_orders()
        if not orders:
            return f"{EMOJI['orders']} <b>Нет активных заявок</b>"

        message = f"{EMOJI['orders']} <b>АКТИВНЫЕ ЗАЯВКИ</b>\n\n"
        for i, order in enumerate(orders[:10], 1):
            emoji = "🟢" if order.get('direction') == 'BUY' else "🔴"
            message += (
                f"{i}. {emoji} <b>{order.get('ticker', '?')}</b>\n"
                f"   {order.get('direction')} {order.get('quantity', 0)}шт @ "
                f"{order.get('price', 0):.2f}₽\n"
            )
        return message
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


def get_history_text() -> str:
    """История сделок"""
    try:
        bot = get_trading_bot()
        if not bot or not hasattr(bot, '_trades'):
            return f"{EMOJI['history']} История недоступна"

        trades = bot._trades[-10:] if bot._trades else []
        if not trades:
            return f"{EMOJI['history']} <b>История пуста</b>"

        message = f"{EMOJI['history']} <b>ПОСЛЕДНИЕ {len(trades)} СДЕЛОК</b>\n\n"
        total = 0
        wins = 0

        for t in trades[::-1]:
            pnl = t.get('pnl', 0)
            pnl_pct = t.get('pnl_pct', 0)
            ticker = t.get('ticker', '?')
            side = t.get('side', 'LONG')
            total += pnl
            if pnl > 0:
                wins += 1
            emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            message += f"{emoji} {side} {ticker}: {pnl:+.2f}₽ ({pnl_pct:+.1f}%)\n"

        win_rate = (wins / len(trades) * 100) if trades else 0
        message += f"\n📊 Win Rate: {win_rate:.1f}% | Общий P&L: {total:+.2f}₽"
        return message
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


def get_current_pnl_text() -> str:
    """Текущий P&L портфеля"""
    try:
        bot = get_trading_bot()
        if bot and hasattr(bot, 'get_detailed_pnl'):
            pnl_data = bot.get_detailed_pnl()
            return (
                f"{EMOJI['profit']} <b>ТЕКУЩИЙ P&L</b>\n\n"
                f"💰 Общий P&L: <b>{pnl_data.get('total_pnl', 0):+,.2f}</b> ₽\n"
                f"📊 P&L %: <b>{pnl_data.get('total_pnl_pct', 0):+.2f}%</b>\n"
                f"📈 Стоимость портфеля: <b>{pnl_data.get('total_value', 0):,.2f}</b> ₽\n"
                f"🔢 Позиций: <b>{pnl_data.get('positions_count', 0)}</b>"
            )
        return f"{EMOJI['error']} P&L недоступен"
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


def get_stats_text() -> str:
    """Статистика работы бота"""
    try:
        bot = get_trading_bot()
        if not bot:
            return f"{EMOJI['error']} Бот не доступен"

        stats = []
        if hasattr(bot, 'get_analyzers_status'):
            analyzers = bot.get_analyzers_status()
            stats.append(f"📊 Анализаторы: {sum(analyzers.values())}/{len(analyzers)}")

        if hasattr(bot, '_cycle_count'):
            stats.append(f"🔄 Циклов: {bot._cycle_count}")

        if hasattr(bot, '_trades') and bot._trades:
            total_trades = len(bot._trades)
            winning = sum(1 for t in bot._trades if t.get('pnl', 0) > 0)
            win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
            stats.append(f"📈 Сделок: {total_trades}, Win Rate: {win_rate:.1f}%")

        uptime = int(time.time() - bot._start_time) if hasattr(bot, '_start_time') else 0
        hours = uptime // 3600
        minutes = (uptime % 3600) // 60
        stats.append(f"⏱ Аптайм: {hours}ч {minutes}мин")

        return f"{EMOJI['stats']} <b>СТАТИСТИКА</b>\n\n" + "\n".join(f"• {s}" for s in stats)
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


def get_config_text() -> str:
    try:
        from trading_bot.config import config
        return (
            f"{EMOJI['settings']} <b>НАСТРОЙКИ</b>\n\n"
            f"🎯 Тейк-профит: <b>+{config.take_profit_pct:.1f}%</b>\n"
            f"🛑 Стоп-лосс: <b>-{config.stop_loss_pct:.1f}%</b>\n"
            f"🔻 Трейлинг: <b>{config.trailing_stop_pct:.2f}%</b>\n"
            f"📊 Макс. позиций: <b>{config.max_positions}</b>\n"
            f"🔻 SHORT: <b>{'✅ Вкл' if config.use_short else '❌ Выкл'}</b>\n"
            f"⏰ Таймаут: <b>{config.adaptive_timeout_minutes} мин</b>"
        )
    except Exception as e:
        return f"{EMOJI['error']} Ошибка: {str(e)[:50]}"


def get_help_text() -> str:
    return (
        "🤖 <b>ПОМОЩЬ ПО БОТУ</b>\n\n"
        "📊 <b>ИНФОРМАЦИЯ</b>\n"
        "   • Баланс - текущий баланс и капитал\n"
        "   • Позиции - открытые позиции с P&L\n"
        "   • Маржа - состояние маржинальной торговли\n"
        "   • Заявки - активные лимитные заявки\n"
        "   • История - последние сделки\n\n"
        "💰 <b>ТОРГОВЛЯ</b>\n"
        "   • Купить - открыть LONG позицию\n"
        "   • Продать (SHORT) - открыть SHORT позицию\n"
        "   • Закрыть позицию - по тикеру\n"
        "   • Закрыть всё - все позиции\n\n"
        "⚙️ <b>НАСТРОЙКИ</b>\n"
        "   • TP/SL - тейк-профит и стоп-лосс\n"
        "   • SHORT - включение/отключение\n"
        "   • Макс. позиций - лимит позиций\n"
        "   • Агрессивность - уровень риска\n\n"
        "🛠️ <b>СЕРВИС</b>\n"
        "   • Очистить кэш - сброс кэшей\n"
        "   • Health check - диагностика\n"
        "   • Статистика - работа бота\n\n"
        "❌ <b>/cancel</b> - отмена операции"
    )


# ========== ТОРГОВЫЕ ФУНКЦИИ ==========

def start_buy_flow(chat_id: int) -> bool:
    clear_user_state(chat_id)
    _user_states[chat_id] = {'state': UserState.WAITING_TICKER, 'data': {'direction': 'BUY'}}
    return send_message(chat_id,
                        "🟢 <b>ПОКУПКА</b>\n\nВведите тикер (SBER, GAZP, LKOH):\n\n❌ /cancel - отмена",
                        keyboard=CANCEL_KEYBOARD)


def start_sell_flow(chat_id: int) -> bool:
    clear_user_state(chat_id)
    _user_states[chat_id] = {'state': UserState.WAITING_TICKER, 'data': {'direction': 'SELL'}}
    return send_message(chat_id,
                        "🔴 <b>ПРОДАЖА (SHORT)</b>\n\nВведите тикер (SBER, GAZP, LKOH):\n\n❌ /cancel - отмена",
                        keyboard=CANCEL_KEYBOARD)


def start_close_flow(chat_id: int) -> bool:
    clear_user_state(chat_id)
    _user_states[chat_id] = {'state': UserState.WAITING_TICKER_CLOSE, 'data': {}}
    return send_message(chat_id,
                        "🔒 <b>ЗАКРЫТИЕ ПОЗИЦИИ</b>\n\nВведите тикер:\n\n❌ /cancel - отмена",
                        keyboard=CANCEL_KEYBOARD)


def process_ticker_input(chat_id: int, ticker: str) -> bool:
    state = _user_states.get(chat_id, {})
    if state.get('state') not in [UserState.WAITING_TICKER, UserState.WAITING_TICKER_CLOSE]:
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

    # Для закрытия позиции
    if state.get('state') == UserState.WAITING_TICKER_CLOSE:
        return execute_close_ticker(chat_id, ticker, figi)

    # Для покупки/продажи
    _user_states[chat_id] = {
        'state': UserState.WAITING_QUANTITY,
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
    if state.get('state') != UserState.WAITING_QUANTITY:
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
            'state': UserState.WAITING_CONFIRMATION,
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
    if state.get('state') != UserState.WAITING_CONFIRMATION:
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


def execute_close_ticker(chat_id: int, ticker: str, figi: str) -> bool:
    try:
        tbank = get_tbank()
        bot = get_trading_bot()
        positions = bot._get_positions() if bot and hasattr(bot, '_get_positions') else []

        for pos in positions:
            pos_ticker = pos.get('ticker', '').upper()
            if pos_ticker == ticker.upper():
                qty = abs(pos.get('quantity', 0))
                if pos.get('quantity', 0) < 0:
                    success = tbank.buy(figi, qty)
                else:
                    success = tbank.sell(figi, qty)

                clear_user_state(chat_id)
                if success:
                    send_message(chat_id, f"{EMOJI['success']} Позиция {ticker} ЗАКРЫТА!")
                else:
                    send_message(chat_id, f"{EMOJI['error']} Не удалось закрыть {ticker}")
                send_main_keyboard(chat_id)
                return True

        send_message(chat_id, f"{EMOJI['error']} Позиция {ticker} не найдена")
        clear_user_state(chat_id)
        send_main_keyboard(chat_id)
        return False
    except Exception as e:
        send_message(chat_id, f"{EMOJI['error']} Ошибка: {str(e)[:100]}")
        clear_user_state(chat_id)
        send_main_keyboard(chat_id)
        return False


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


# ========== НАСТРОЙКИ ==========

def show_settings_menu(chat_id: int) -> bool:
    return send_message(chat_id, "⚙️ <b>НАСТРОЙКИ</b>\n\nВыберите параметр:", keyboard=SETTINGS_KEYBOARD)


def show_tpsl_menu(chat_id: int) -> bool:
    from trading_bot.config import config
    message = (
        f"{EMOJI['tp_sl']} <b>TP/SL НАСТРОЙКИ</b>\n\n"
        f"📈 Тейк-профит: +{config.take_profit_pct:.1f}%\n"
        f"📉 Стоп-лосс: -{config.stop_loss_pct:.1f}%\n"
        f"🔻 Трейлинг: {config.trailing_stop_pct:.2f}%\n\n"
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
    return show_settings_menu(chat_id)


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
    return show_settings_menu(chat_id)


def show_max_positions_menu(chat_id: int) -> bool:
    global _waiting_for_value
    _waiting_for_value = {'param': 'max_positions', 'chat_id': chat_id}
    return send_message(chat_id,
                        f"{EMOJI['settings']} <b>МАКС. ПОЗИЦИЙ</b>\n\nВведите число от 1 до 20:\n\n❌ /cancel - отмена",
                        keyboard=CANCEL_KEYBOARD)


def handle_max_positions(chat_id: int, text: str) -> bool:
    try:
        value = int(text)
        if 1 <= value <= 20:
            from trading_bot.core.settings_manager import settings_manager
            settings_manager.set('max_positions', value)
            send_message(chat_id, f"{EMOJI['success']} Макс. позиций: {value}")
        else:
            send_message(chat_id, f"{EMOJI['error']} Введите число от 1 до 20")
            return False
    except ValueError:
        send_message(chat_id, f"{EMOJI['error']} Введите целое число")
        return False
    global _waiting_for_value
    _waiting_for_value = None
    return show_settings_menu(chat_id)


def show_aggressiveness_menu(chat_id: int) -> bool:
    global _waiting_for_value
    _waiting_for_value = {'param': 'aggressiveness', 'chat_id': chat_id}
    return send_message(chat_id,
                        f"{EMOJI['aggressive']} <b>АГРЕССИВНОСТЬ</b>\n\n"
                        f"1-3: Консервативная\n"
                        f"4-7: Умеренная\n"
                        f"8-10: Агрессивная\n\n"
                        f"Введите число от 1 до 10:\n\n❌ /cancel - отмена",
                        keyboard=CANCEL_KEYBOARD)


def handle_aggressiveness(chat_id: int, text: str) -> bool:
    try:
        value = int(text)
        if 1 <= value <= 10:
            from trading_bot.core.settings_manager import settings_manager
            settings_manager.set('aggressiveness', value)
            if value <= 3:
                desc = "🟢 КОНСЕРВАТИВНАЯ"
            elif value <= 7:
                desc = "🟡 УМЕРЕННАЯ"
            else:
                desc = "🔴 АГРЕССИВНАЯ"
            send_message(chat_id, f"{EMOJI['success']} Агрессивность: {value}/10 ({desc})")
        else:
            send_message(chat_id, f"{EMOJI['error']} Введите число от 1 до 10")
            return False
    except ValueError:
        send_message(chat_id, f"{EMOJI['error']} Введите целое число")
        return False
    global _waiting_for_value
    _waiting_for_value = None
    return show_settings_menu(chat_id)


def reset_settings(chat_id: int) -> bool:
    try:
        from trading_bot.core.settings_manager import settings_manager
        settings_manager.reset_to_defaults()
        send_message(chat_id, f"{EMOJI['success']} Настройки сброшены до значений по умолчанию!")
    except Exception as e:
        send_message(chat_id, f"{EMOJI['error']} {e}")
    return show_settings_menu(chat_id)


# ========== СЕРВИС ==========

def show_service_menu(chat_id: int) -> bool:
    return send_message(chat_id, "🛠️ <b>СЕРВИС</b>\n\nВыберите действие:", keyboard=SERVICE_KEYBOARD)


def clear_cache(chat_id: int) -> bool:
    try:
        from trading_bot.cache import price_cache, positions_cache, candles_cache, margin_cache, instruments_cache
        price_cache.clear()
        positions_cache.clear()
        candles_cache.clear()
        margin_cache.clear()
        instruments_cache.clear()

        bot = get_trading_bot()
        if bot and hasattr(bot, 'clear_validation_cache'):
            bot.clear_validation_cache()

        send_message(chat_id, f"{EMOJI['cache']} Кэш очищен!")
    except Exception as e:
        send_message(chat_id, f"{EMOJI['error']} {e}")
    return show_service_menu(chat_id)


def health_check(chat_id: int) -> bool:
    try:
        tbank = get_tbank()
        available, total, _ = tbank.get_available_funds()
        bot = get_trading_bot()
        is_running = bot._running if bot else False
        status = "🟢 РАБОТАЕТ" if is_running else "🔴 ОСТАНОВЛЕН"

        message = (
            f"{EMOJI['health']} <b>HEALTH CHECK</b>\n\n"
            f"🤖 Бот: {status}\n"
            f"💰 Капитал: {total:,.2f}₽\n"
            f"💵 Свободно: {available:,.2f}₽\n"
            f"📅 {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')} МСК"
        )
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"{EMOJI['error']} {e}")


def show_stats(chat_id: int) -> bool:
    return send_message(chat_id, get_stats_text())


# ========== ОСНОВНАЯ ОБРАБОТКА ==========

def process_command(text: str, chat_id: int) -> bool:
    global _waiting_for_value

    # Отмена
    if text.lower() == "/cancel":
        return cancel_operation(chat_id)

    # Подтверждение
    if text.lower() == "/confirm" or text == "✅ Подтвердить":
        if _user_states.get(chat_id, {}).get('state') == UserState.WAITING_CONFIRMATION:
            return execute_order(chat_id)

    # Ожидание ввода TP/SL
    if _waiting_for_value and _waiting_for_value.get('param') == 'tpsl' and _waiting_for_value.get(
            'chat_id') == chat_id:
        return handle_tpsl_value(chat_id, text)

    # Ожидание ввода max позиций
    if _waiting_for_value and _waiting_for_value.get('param') == 'max_positions' and _waiting_for_value.get(
            'chat_id') == chat_id:
        return handle_max_positions(chat_id, text)

    # Ожидание ввода агрессивности
    if _waiting_for_value and _waiting_for_value.get('param') == 'aggressiveness' and _waiting_for_value.get(
            'chat_id') == chat_id:
        return handle_aggressiveness(chat_id, text)

    # Состояния
    state = _user_states.get(chat_id, {}).get('state')
    if state == UserState.WAITING_TICKER:
        return process_ticker_input(chat_id, text)
    elif state == UserState.WAITING_TICKER_CLOSE:
        return process_ticker_input(chat_id, text)
    elif state == UserState.WAITING_QUANTITY:
        return process_quantity_input(chat_id, text)

    # ГЛАВНОЕ МЕНЮ
    if text in ["📊 Информация", "/info"]:
        return send_message(chat_id, "📊 <b>ИНФОРМАЦИЯ</b>\n\nВыберите раздел:", keyboard=INFO_KEYBOARD)
    elif text in ["💰 Торговля", "/trade"]:
        return send_message(chat_id, "💰 <b>ТОРГОВЛЯ</b>\n\nВыберите действие:", keyboard=TRADE_KEYBOARD)
    elif text in ["⚙️ Настройки", "/settings"]:
        return show_settings_menu(chat_id)
    elif text in ["🛠️ Сервис", "/service"]:
        return show_service_menu(chat_id)
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
    elif text in ["📋 Заявки", "/orders"]:
        return send_message(chat_id, get_orders_text())
    elif text in ["📜 История", "/history"]:
        return send_message(chat_id, get_history_text())

    # ТОРГОВЛЯ
    elif text in ["🟢 Купить", "/buy"]:
        return start_buy_flow(chat_id)
    elif text in ["🔴 Продать (SHORT)", "/sell"]:
        return start_sell_flow(chat_id)
    elif text in ["🔒 Закрыть позицию", "/close"]:
        return start_close_flow(chat_id)
    elif text in ["🛑 Закрыть всё", "/close_all"]:
        return close_all_positions(chat_id)
    elif text in ["📊 Текущий P&L", "/pnl"]:
        return send_message(chat_id, get_current_pnl_text())

    # НАСТРОЙКИ
    elif text == "🎯 TP/SL":
        return show_tpsl_menu(chat_id)
    elif text == "🔻 SHORT":
        return toggle_short(chat_id)
    elif text == "📊 Макс. позиций":
        return show_max_positions_menu(chat_id)
    elif text == "🤖 Агрессивность":
        return show_aggressiveness_menu(chat_id)
    elif text == "🔄 Сброс настроек":
        return reset_settings(chat_id)

    # СЕРВИС
    elif text == "🧹 Очистить кэш":
        return clear_cache(chat_id)
    elif text == "❤️ Health check":
        return health_check(chat_id)
    elif text == "📊 Статистика":
        return show_stats(chat_id)

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