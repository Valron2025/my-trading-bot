#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_polling.py - ПОЛЛИНГ ДЛЯ TELEGRAM (ПОЛНАЯ ВЕРСИЯ)
Поддерживает все функции: статус, баланс, позиции, настройки, АВТО, покупку/продажу
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

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MOSCOW_TZ = timezone(timedelta(hours=3))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Глобальные переменные
_telegram_bot = None
_trading_bot = None
_user_states: Dict[int, Dict[str, Any]] = {}
_waiting_for_value: Optional[Dict] = None


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
    if not token:
        logger.error("TELEGRAM_TOKEN not set!")
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
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False


def send_reply_keyboard(chat_id: int) -> bool:
    bot_token = get_bot_token()
    if not bot_token:
        return False

    buttons = [
        "📊 Статус", "💰 Баланс", "📈 Позиции", "💼 Портфель",
        "📉 P&L", "📊 Маржа", "🟢 Купить", "🔴 Продать",
        "🔒 Закрыть всё", "⚙️ Настройки", "🛠️ Сервис", "❓ Помощь"
    ]

    keyboard = {
        "keyboard": [[{"text": btn}] for btn in buttons],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": "📱 <b>ГЛАВНОЕ МЕНЮ</b>\n\nВыберите действие:",
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard)
    }

    try:
        response = requests.post(url, json=data, timeout=10)
        logger.info(f"Reply Keyboard отправлен в чат {chat_id}")
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending reply keyboard: {e}")
        return False


# ========== ОСНОВНЫЕ ФУНКЦИИ ==========

def send_status_message(chat_id: int) -> bool:
    try:
        from trading_bot.api.tbank_client import tbank
        from trading_bot.config import config

        available, total, _ = tbank.get_available_funds()

        total_pnl = 0
        positions_count = 0
        try:
            from trading_bot import get_trading_bot
            trading_bot = get_trading_bot()
            if trading_bot and hasattr(trading_bot, 'get_detailed_pnl'):
                pnl_data = trading_bot.get_detailed_pnl()
                if pnl_data:
                    total_pnl = pnl_data.get('total_pnl', 0)
                    positions = pnl_data.get('positions', [])
                    positions_count = len(positions)
        except:
            pass

        message = (
            f"📊 <b>СТАТУС БОТА</b>\n\n"
            f"💰 Капитал: <b>{total:.2f}</b> ₽\n"
            f"💵 Свободно: <b>{available:.2f}</b> ₽\n"
            f"📈 Позиций: <b>{positions_count}</b>\n"
            f"📊 P&L общий: <b>{total_pnl:+.2f}</b> ₽\n"
            f"🎯 Режим: <b>{'Микро' if total < 5000 else 'Стандартный'}</b>\n"
            f"⚙️ Тейк: +{config.take_profit_pct:.1f}% | Стоп: -{config.stop_loss_pct:.1f}%\n"
            f"⏱ {now_msk()} МСК"
        )
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def send_balance_message(chat_id: int) -> bool:
    try:
        from trading_bot.api.tbank_client import tbank
        available, total, _ = tbank.get_available_funds()
        message = (
            f"💰 <b>БАЛАНС</b>\n\n"
            f"💵 Свободно: <b>{available:.2f}</b> ₽\n"
            f"💎 Капитал: <b>{total:.2f}</b> ₽\n"
            f"⏱ {now_msk()} МСК"
        )
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def send_positions_message(chat_id: int) -> bool:
    try:
        from trading_bot import get_trading_bot
        trading_bot = get_trading_bot()
        if not trading_bot:
            return send_message(chat_id, "❌ Торговый бот не инициализирован")

        if hasattr(trading_bot, 'get_detailed_pnl'):
            pnl_data = trading_bot.get_detailed_pnl()
        else:
            pnl_data = {}

        if not pnl_data or not pnl_data.get('positions'):
            return send_message(chat_id, "📭 <b>Нет открытых позиций</b>")

        message = "📈 <b>ОТКРЫТЫЕ ПОЗИЦИИ</b>\n\n"
        for i, pos in enumerate(pnl_data['positions'][:10], 1):
            side = pos['side']
            side_emoji = "🔴" if side == "SHORT" else "🟢"
            profit_icon = "🟢" if pos['net_pnl'] > 0 else "🔴" if pos['net_pnl'] < 0 else "⚪"
            ticker = pos.get('ticker', pos['figi'][:12])
            message += (
                f"{i}. {side_emoji} <b>{side}</b> {ticker}\n"
                f"   📦 {pos['quantity']} шт @ {pos['avg_price']:.2f}₽\n"
                f"   💰 Текущая: {pos['current_price']:.2f}₽\n"
                f"   {profit_icon} P&L: <b>{pos['net_pnl']:+.2f}</b>₽ (<b>{pos['pnl_pct']:+.2f}%</b>)\n"
            )
        message += f"\n📊 Всего: <b>{len(pnl_data['positions'])}</b> позиций"
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def send_pnl_message(chat_id: int) -> bool:
    try:
        from trading_bot import get_trading_bot
        trading_bot = get_trading_bot()
        if not trading_bot:
            return send_message(chat_id, "❌ Торговый бот не инициализирован")

        if hasattr(trading_bot, 'get_detailed_pnl'):
            pnl_data = trading_bot.get_detailed_pnl()
        else:
            pnl_data = {}

        if not pnl_data:
            return send_message(chat_id, "📊 <b>ОТЧЁТ P&L</b>\n\nНет данных")

        positions = pnl_data.get('positions', [])
        if not positions:
            return send_message(chat_id, "📊 <b>ОТЧЁТ P&L</b>\n\nНет открытых позиций")

        total_pnl = pnl_data.get('total_pnl', 0)
        winning = sum(1 for p in positions if p.get('net_pnl', 0) > 0)
        losing = sum(1 for p in positions if p.get('net_pnl', 0) < 0)
        win_rate = (winning / (winning + losing) * 100) if (winning + losing) > 0 else 0

        message = "📊 <b>ОТЧЁТ P&L</b>\n\n"
        for pos in positions[:5]:
            ticker = pos.get('ticker', pos['figi'][:12])
            profit_icon = "🟢" if pos['net_pnl'] > 0 else "🔴" if pos['net_pnl'] < 0 else "⚪"
            message += f"{profit_icon} <b>{ticker}</b>: {pos['pnl_pct']:+.1f}% ({pos['net_pnl']:+.2f}₽)\n"

        message += f"\n━━━━━━━━━━━━━━━━━━━━━\n"
        message += f"📊 Всего позиций: <b>{len(positions)}</b>\n"
        message += f"✅ Прибыльных: <b>{winning}</b> | ❌ Убыточных: <b>{losing}</b>\n"
        message += f"📈 Win Rate: <b>{win_rate:.1f}%</b>\n"
        message += f"{'🟢' if total_pnl > 0 else '🔴'} Общий P&L: <b>{total_pnl:+.2f}</b> ₽\n"
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def send_margin_message(chat_id: int) -> bool:
    try:
        from trading_bot import get_trading_bot
        trading_bot = get_trading_bot()
        if not trading_bot:
            return send_message(chat_id, "❌ Торговый бот не инициализирован")

        margin_status = trading_bot.get_margin_status()
        status_emoji = "🟢✅" if margin_status.get('status') == 'ok' else "🟡⚠️" if margin_status.get(
            'status') == 'warning' else "🔴🔥"

        message = (
            f"📊 <b>МАРЖИНАЛЬНЫЙ СТАТУС</b> {status_emoji}\n\n"
            f"💰 Ликвидный портфель: <b>{margin_status.get('liquid_portfolio', 0):.2f}</b> ₽\n"
            f"🔒 Использовано маржи: <b>{margin_status.get('used_margin', 0):.2f}</b> ₽\n"
            f"✅ Доступно маржи: <b>{margin_status.get('available_margin', 0):.2f}</b> ₽\n"
            f"📊 Процент использования: <b>{margin_status.get('margin_rate', 0):.1f}%</b>"
        )
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def send_portfolio_message(chat_id: int) -> bool:
    return send_positions_message(chat_id)


def send_help_message(chat_id: int) -> bool:
    message = (
        "🤖 <b>ТОРГОВЫЙ БОТ - ПОМОЩЬ</b>\n\n"
        "📱 <b>ИСПОЛЬЗУЙТЕ МЕНЮ ВНИЗУ ЭКРАНА:</b>\n"
        "   Просто нажмите на нужную кнопку\n\n"
        "📊 <b>ДОСТУПНЫЕ ДЕЙСТВИЯ:</b>\n"
        "   • Статус - состояние бота\n"
        "   • Баланс - средства на счёте\n"
        "   • Позиции - открытые позиции\n"
        "   • Портфель - состав портфеля\n"
        "   • P&L - прибыль/убыток\n"
        "   • Маржа - маржинальный статус\n"
        "   • Купить - ручная покупка\n"
        "   • Продать - ручная продажа\n"
        "   • Закрыть всё - закрыть все позиции\n"
        "   • Настройки - параметры бота\n"
        "   • Сервис - сервисные функции"
    )
    return send_message(chat_id, message)


def send_service_message(chat_id: int) -> bool:
    message = (
        "📊 <b>СЕРВИСНЫЕ ФУНКЦИИ</b>\n\n"
        "/clear_cache - Очистить кеш валидации\n"
        "/health - Проверка здоровья\n"
        "/test - Тест подключения\n"
        "/menu - Показать меню"
    )
    return send_message(chat_id, message)


def send_settings_message(chat_id: int) -> bool:
    try:
        from trading_bot.config import config
        message = (
            f"⚙️ <b>ТЕКУЩИЕ НАСТРОЙКИ</b>\n\n"
            f"🎯 Тейк-профит: <b>+{config.take_profit_pct:.1f}%</b>\n"
            f"🛑 Стоп-лосс: <b>-{config.stop_loss_pct:.1f}%</b>\n"
            f"🔻 Трейлинг-стоп: <b>{config.trailing_stop_pct:.2f}%</b>\n"
            f"⏰ Таймаут: <b>{config.adaptive_timeout_minutes} мин</b>\n"
            f"📊 Макс. позиций: <b>{config.max_positions}</b>\n"
            f"💰 Мин. сделка: <b>{config.min_trade_amount}₽</b>\n"
            f"📈 Размер позиции: <b>{config.adaptive_position_size_pct * 100:.0f}%</b>\n"
            f"🎫 Score порог: <b>{config.long_score_threshold}</b>\n"
            f"🔄 Цикл: <b>{config.adaptive_cycle_seconds} сек</b>\n"
            f"🔻 SHORT: <b>{'✅ ВКЛ' if config.use_short else '❌ ВЫКЛ'}</b>"
        )
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def show_settings_menu(chat_id: int) -> bool:
    """Показать меню настроек с кнопками"""
    buttons = [
        "1️⃣ Тейк-профит", "2️⃣ Стоп-лосс", "3️⃣ Трейлинг",
        "4️⃣ Макс.позиций", "5️⃣ Мин.сделка", "6️⃣ Размер",
        "7️⃣ Score", "8️⃣ Таймаут", "9️⃣ Цикл", "🔟 SHORT",
        "📊 Сброс", "🔄 АВТО", "🔙 Назад"
    ]

    bot_token = get_bot_token()
    if not bot_token:
        return False

    keyboard = {
        "keyboard": [[{"text": btn}] for btn in buttons],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": "📱 Выберите параметр для изменения:",
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard)
    }

    try:
        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error showing settings menu: {e}")
        return False


def handle_auto_settings(chat_id: int) -> bool:
    """Автоматическая настройка параметров под капитал"""
    global _waiting_for_value
    _waiting_for_value = None

    try:
        from trading_bot.api.tbank_client import tbank
        from trading_bot.config import config

        available, total, _ = tbank.get_available_funds()

        # Расчет параметров под капитал
        if total < 5000:
            long_threshold = 2
            short_threshold = -2
            position_size = 8
            min_trade = 200
            use_short = False
            tp = 1.5
            sl = 1.0
            mode = "МИКРО"
        elif total < 15000:
            long_threshold = 1
            short_threshold = -1
            position_size = 10
            min_trade = 300
            use_short = True
            tp = 1.2
            sl = 0.8
            mode = "МАЛЫЙ"
        else:
            long_threshold = 1
            short_threshold = -1
            position_size = 12
            min_trade = 500
            use_short = True
            tp = 1.0
            sl = 0.6
            mode = "СТАНДАРТНЫЙ"

        # Применяем настройки
        config.long_score_threshold = long_threshold
        config.short_score_threshold = short_threshold
        config.adaptive_position_size_pct = position_size / 100
        config.min_trade_amount = min_trade
        config.use_short = use_short
        config.take_profit_pct = tp
        config.stop_loss_pct = sl

        # Сохраняем в settings_manager если есть
        try:
            from trading_bot.core.settings_manager import settings_manager
            if settings_manager:
                settings_manager.set('score_threshold_long', long_threshold)
                settings_manager.set('score_threshold_short', short_threshold)
                settings_manager.set('position_size_pct', position_size)
                settings_manager.set('min_trade_amount', min_trade)
                settings_manager.set('short_enabled', use_short)
                settings_manager.set('take_profit_pct', tp)
                settings_manager.set('stop_loss_pct', sl)
        except:
            pass

        message = (
            f"🔄 <b>АВТОМАТИЧЕСКИЕ НАСТРОЙКИ ВОССТАНОВЛЕНЫ</b>\n\n"
            f"📊 <b>РЕЖИМ: {mode}</b>\n"
            f"💰 Капитал: <b>{total:.0f}₽</b>\n\n"
            f"🎯 Тейк-профит: <b>+{tp:.1f}%</b>\n"
            f"🛑 Стоп-лосс: <b>-{sl:.1f}%</b>\n"
            f"📊 Макс. позиций: <b>{config.max_positions}</b>\n"
            f"📈 Размер позиции: <b>{position_size}%</b>\n"
            f"💰 Мин. сделка: <b>{min_trade}₽</b>\n"
            f"🎫 Score порог: <b>≥ {long_threshold}</b> (LONG) / <b>≤ {short_threshold}</b> (SHORT)\n"
            f"🔻 SHORT: <b>{'✅ ВКЛЮЧЕН' if use_short else '❌ ВЫКЛЮЧЕН'}</b>\n\n"
            f"💡 Настройки адаптированы под ваш капитал.\n"
            f"📱 Используйте /menu для возврата в главное меню."
        )
        return send_message(chat_id, message)

    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка автонастройки: {str(e)[:100]}")


def handle_reset_settings(chat_id: int) -> bool:
    """Сброс настроек до значений по умолчанию"""
    try:
        from trading_bot.core.settings_manager import settings_manager
        if settings_manager:
            for key, value in settings_manager.DEFAULT_SETTINGS.items():
                settings_manager.set(key, value)
        send_message(chat_id, "✅ Настройки сброшены до значений по умолчанию")
        return show_settings_menu(chat_id)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка сброса: {str(e)[:100]}")


def handle_setting_value(chat_id: int, text: str) -> bool:
    """Обработка ввода значения для настройки"""
    global _waiting_for_value
    if not _waiting_for_value or _waiting_for_value.get('chat_id') != chat_id:
        return False

    try:
        from trading_bot.core.settings_manager import settings_manager
        value = float(text.replace(',', '.'))
        param = _waiting_for_value['param']

        if param == 'tp':
            if 0.3 <= value <= 3.0:
                settings_manager.set('take_profit_pct', value)
                send_message(chat_id, f"✅ Тейк-профит изменён на +{value:.1f}%")
        elif param == 'sl':
            if 0.2 <= value <= 2.0:
                settings_manager.set('stop_loss_pct', value)
                send_message(chat_id, f"✅ Стоп-лосс изменён на -{value:.1f}%")
        elif param == 'trail':
            if 0.1 <= value <= 1.0:
                settings_manager.set('trailing_stop_pct', value)
                send_message(chat_id, f"✅ Трейлинг-стоп изменён на {value:.2f}%")
        elif param == 'max_pos':
            value = int(value)
            if 1 <= value <= 5:
                settings_manager.set('max_positions', value)
                send_message(chat_id, f"✅ Макс. позиций изменено на {value}")
        elif param == 'min_amount':
            value = int(value)
            if 50 <= value <= 1000:
                settings_manager.set('min_trade_amount', value)
                send_message(chat_id, f"✅ Мин. сумма изменена на {value}₽")
        elif param == 'pos_size':
            if 5 <= value <= 30:
                settings_manager.set('position_size_pct', value)
                send_message(chat_id, f"✅ Размер позиции изменён на {value:.0f}%")
        elif param == 'score':
            value = int(value)
            if 0 <= value <= 5:
                settings_manager.set('score_threshold_long', value)
                settings_manager.set('score_threshold_short', -value)
                send_message(chat_id, f"✅ Score порог изменён на ≥{value} (LONG) и ≤{-value} (SHORT)")
        elif param == 'timeout':
            value = int(value)
            if 5 <= value <= 60:
                settings_manager.set('timeout_minutes', value)
                send_message(chat_id, f"✅ Таймаут изменён на {value} мин")
        elif param == 'cycle':
            value = int(value)
            if 5 <= value <= 60:
                settings_manager.set('cycle_seconds', value)
                send_message(chat_id, f"✅ Цикл изменён на {value} сек")

        _waiting_for_value = None
        return show_settings_menu(chat_id)
    except ValueError:
        send_message(chat_id, "❌ Ошибка: введите число")
        return True


def handle_buy_command(chat_id: int) -> bool:
    """Начало процесса покупки"""
    _user_states[chat_id] = {'state': 'waiting_ticker', 'data': {'direction': 'BUY'}}
    return send_message(chat_id,
                        "🟢 <b>ПОКУПКА АКТИВА</b>\n\nВведите тикер акции (например, SBER, GAZP, LKOH):\n\n❌ Для отмены введите /cancel")


def handle_sell_command(chat_id: int) -> bool:
    """Начало процесса продажи"""
    _user_states[chat_id] = {'state': 'waiting_ticker', 'data': {'direction': 'SELL'}}
    return send_message(chat_id,
                        "🔴 <b>ПРОДАЖА АКТИВА</b>\n\nВведите тикер акции (например, SBER, GAZP, LKOH):\n\n❌ Для отмены введите /cancel")


def handle_ticker_input(chat_id: int, ticker: str) -> bool:
    """Обработка ввода тикера"""
    state = _user_states.get(chat_id, {})
    if state.get('state') != 'waiting_ticker':
        return False

    ticker = ticker.upper().strip()
    try:
        from trading_bot.api.tbank_client import tbank
        all_shares = tbank.get_all_shares()
        figi = None
        stock_name = None
        lot = None
        for stock in all_shares:
            if stock.get('ticker') == ticker and stock.get('currency') == 'rub':
                figi = stock['figi']
                stock_name = stock['name']
                lot = stock['lot']
                break

        if not figi:
            return send_message(chat_id, f"❌ Тикер {ticker} не найден. Попробуйте другой.")

        current_price = tbank.get_current_price(figi)
        if not current_price:
            return send_message(chat_id, f"❌ Не удалось получить цену для {ticker}")

        _user_states[chat_id] = {
            'state': 'waiting_quantity',
            'data': {
                'direction': state['data']['direction'],
                'ticker': ticker,
                'figi': figi,
                'stock_name': stock_name,
                'lot': lot,
                'current_price': current_price
            }
        }

        return send_message(chat_id,
                            f"📊 <b>ИНФОРМАЦИЯ О {ticker}</b>\n\n"
                            f"📝 Название: {stock_name[:40]}\n"
                            f"💰 Текущая цена: <b>{current_price:.2f}</b> ₽\n"
                            f"📦 Лот: {lot} шт\n\n"
                            f"Введите количество (кратно {lot}):")
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def handle_quantity_input(chat_id: int, quantity_str: str) -> bool:
    """Обработка ввода количества"""
    state = _user_states.get(chat_id, {})
    if state.get('state') != 'waiting_quantity':
        return False

    try:
        quantity = int(quantity_str)
        data = state['data']
        lot = data.get('lot', 1)
        current_price = data['current_price']

        if quantity <= 0:
            raise ValueError
        if quantity % lot != 0:
            return send_message(chat_id, f"❌ Количество должно быть кратно {lot} (лот).")

        direction = data['direction']
        total_cost = quantity * current_price

        from trading_bot.api.tbank_client import tbank
        available, total, _ = tbank.get_available_funds()

        if direction == 'BUY' and total_cost > available * 0.95:
            return send_message(chat_id,
                                f"❌ Недостаточно средств!\n\n"
                                f"💰 Требуется: <b>{total_cost:.2f}</b> ₽\n"
                                f"💵 Доступно: <b>{available:.2f}</b> ₽\n"
                                f"Уменьшите количество")

        _user_states[chat_id] = {
            'state': 'waiting_price',
            'data': {**data, 'quantity': quantity, 'total_cost': total_cost}
        }

        return send_message(chat_id,
                            f"📝 Количество: <b>{quantity}</b> шт\n"
                            f"💰 Сумма: <b>{total_cost:.2f}</b> ₽\n\n"
                            f"Введите цену (или 0 для рыночной):\n"
                            f"💡 Текущая цена: {current_price:.2f}₽")
    except ValueError:
        return send_message(chat_id, "❌ Некорректное количество. Введите целое положительное число:")


def handle_price_input(chat_id: int, price_str: str) -> bool:
    """Обработка ввода цены"""
    state = _user_states.get(chat_id, {})
    if state.get('state') != 'waiting_price':
        return False

    try:
        if price_str == "0" or price_str.lower() == "рыночная":
            price = 0
            price_text = "Рыночная"
        else:
            price = float(price_str.replace(',', '.'))
            if price <= 0:
                raise ValueError
            price_text = f"{price:.2f} ₽"

        data = state['data']
        direction = data['direction']
        ticker = data['ticker']
        quantity = data['quantity']
        current_price = data['current_price']
        action_text = "ПОКУПКА" if direction == 'BUY' else "ПРОДАЖА"
        action_emoji = "🟢" if direction == 'BUY' else "🔴"
        final_price = price if price > 0 else current_price
        final_total = quantity * final_price

        _user_states[chat_id] = {
            'state': 'waiting_confirmation',
            'data': {**data, 'price': price}
        }

        message = (
            f"{action_emoji} <b>ПОДТВЕРЖДЕНИЕ {action_text}</b>\n\n"
            f"📊 Тикер: <b>{ticker}</b>\n"
            f"🔢 Количество: <b>{quantity}</b> шт\n"
            f"💰 Цена: <b>{price_text}</b>\n"
            f"💵 Ориентир: <b>{final_total:.2f}</b> ₽\n\n"
            f"✅ Подтвердите операцию командой <b>/confirm</b>\n"
            f"❌ Или отмените командой <b>/cancel</b>"
        )
        return send_message(chat_id, message)
    except ValueError:
        return send_message(chat_id, "❌ Некорректная цена. Введите число больше 0 или 0 для рыночной:")


def handle_confirm_order(chat_id: int) -> bool:
    """Подтверждение и исполнение заявки"""
    state = _user_states.get(chat_id, {})
    if state.get('state') != 'waiting_confirmation':
        return send_message(chat_id, "❌ Нет активной заявки для подтверждения")

    data = state['data']
    direction = data['direction']
    ticker = data['ticker']
    figi = data['figi']
    quantity = data['quantity']
    price = data.get('price', 0)

    send_message(chat_id, f"⏳ Исполнение заявки на {direction} {ticker}...")

    try:
        from trading_bot.api.tbank_client import tbank
        if direction == 'BUY':
            if price > 0:
                success_flag = tbank.place_pending_order(figi, quantity, "BUY", price)
            else:
                success_flag = tbank.buy(figi, quantity)
        else:
            if price > 0:
                success_flag = tbank.place_pending_order(figi, quantity, "SELL", price)
            else:
                success_flag = tbank.sell(figi, quantity)

        if success_flag:
            send_message(chat_id,
                         f"✅ <b>ЗАЯВКА ИСПОЛНЕНА</b>\n\n"
                         f"📊 {direction} {quantity} {ticker}\n"
                         f"{'💰 Лимит: ' + str(price) + '₽' if price > 0 else '🏷 Рыночная'}\n"
                         f"💵 Сумма: {quantity * (price if price > 0 else data['current_price']):.2f}₽")
        else:
            send_message(chat_id, f"❌ Не удалось исполнить заявку. Проверьте баланс.")
    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")

    _user_states.pop(chat_id, None)
    return True


def handle_cancel(chat_id: int) -> bool:
    """Отмена текущей операции"""
    _user_states.pop(chat_id, None)
    _waiting_for_value = None
    return send_message(chat_id, "❌ Операция отменена")


def close_all_positions(chat_id: int) -> bool:
    """Закрытие всех позиций"""
    try:
        from trading_bot.api.tbank_client import tbank
        from trading_bot import get_trading_bot
        trading_bot = get_trading_bot()
        if not trading_bot:
            return send_message(chat_id, "❌ Торговый бот не доступен")

        positions = trading_bot._get_positions() if hasattr(trading_bot, '_get_positions') else []
        if not positions:
            return send_message(chat_id, "📭 Нет открытых позиций для закрытия")

        send_message(chat_id, f"⏳ Закрытие {len(positions)} позиций...")
        closed = 0
        for pos in positions:
            figi = pos['figi']
            quantity = abs(pos['quantity'])
            if pos['quantity'] < 0:
                if tbank.buy(figi, quantity):
                    closed += 1
            else:
                if tbank.sell(figi, quantity):
                    closed += 1

        return send_message(chat_id, f"✅ Закрыто позиций: <b>{closed}</b> из {len(positions)}")
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def clear_validation_cache(chat_id: int) -> bool:
    try:
        from trading_bot import get_trading_bot
        trading_bot = get_trading_bot()
        if trading_bot and hasattr(trading_bot, 'clear_validation_cache'):
            trading_bot.clear_validation_cache()
            return send_message(chat_id, "✅ Кеш валидации очищен")
        return send_message(chat_id, "❌ Метод не найден")
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def health_check_message(chat_id: int) -> bool:
    try:
        from trading_bot.api.tbank_client import tbank
        available, total, _ = tbank.get_available_funds()
        message = (
            f"❤️ <b>HEALTH CHECK</b>\n\n"
            f"✅ Торговый бот: <b>{'работает' if total > 0 else 'ошибка'}</b>\n"
            f"💰 Баланс: {total:.2f}₽\n"
            f"📱 Telegram: <b>OK</b>\n"
            f"⏱ {now_msk()} МСК"
        )
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


def test_connection_message(chat_id: int) -> bool:
    start = time.time()
    try:
        from trading_bot.api.tbank_client import tbank
        available, total, _ = tbank.get_available_funds()
        api_time = (time.time() - start) * 1000
        message = (
            f"✅ <b>ТЕСТ ПОДКЛЮЧЕНИЯ</b>\n\n"
            f"✅ API Т-Банк: <b>{api_time:.0f}ms</b>\n"
            f"💰 Баланс: {total:.2f}₽\n"
            f"✅ Telegram: <b>OK</b>\n"
            f"⏱ {now_msk()} МСК"
        )
        return send_message(chat_id, message)
    except Exception as e:
        return send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")


# ========== ОСНОВНОЙ ЦИКЛ ПОЛЛИНГА ==========

def process_command(text: str, chat_id: int) -> bool:
    """Обработка всех команд и кнопок"""
    global _waiting_for_value

    # Обработка ввода значений для настроек
    if _waiting_for_value and _waiting_for_value.get('chat_id') == chat_id:
        return handle_setting_value(chat_id, text)

    # Обработка шагов покупки/продажи
    state = _user_states.get(chat_id, {})
    current_state = state.get('state', '')

    if current_state == 'waiting_ticker':
        if text.lower() == "/cancel":
            return handle_cancel(chat_id)
        return handle_ticker_input(chat_id, text)
    elif current_state == 'waiting_quantity':
        if text.lower() == "/cancel":
            return handle_cancel(chat_id)
        return handle_quantity_input(chat_id, text)
    elif current_state == 'waiting_price':
        if text.lower() == "/cancel":
            return handle_cancel(chat_id)
        return handle_price_input(chat_id, text)
    elif current_state == 'waiting_confirmation':
        if text.lower() == "/confirm":
            return handle_confirm_order(chat_id)
        elif text.lower() == "/cancel":
            return handle_cancel(chat_id)
        else:
            return send_message(chat_id, "❌ Используйте /confirm или /cancel")

    # Обработка основных команд
    cmd = text.strip()

    # Кнопки настроек
    if cmd in ["1️⃣ Тейк-профит", "2️⃣ Стоп-лосс", "3️⃣ Трейлинг",
               "4️⃣ Макс.позиций", "5️⃣ Мин.сделка", "6️⃣ Размер",
               "7️⃣ Score", "8️⃣ Таймаут", "9️⃣ Цикл"]:
        param_map = {
            "1️⃣ Тейк-профит": "tp", "2️⃣ Стоп-лосс": "sl", "3️⃣ Трейлинг": "trail",
            "4️⃣ Макс.позиций": "max_pos", "5️⃣ Мин.сделка": "min_amount",
            "6️⃣ Размер": "pos_size", "7️⃣ Score": "score",
            "8️⃣ Таймаут": "timeout", "9️⃣ Цикл": "cycle"
        }
        _waiting_for_value = {'param': param_map[cmd], 'chat_id': chat_id}
        return send_message(chat_id, f"📝 Введите новое значение для {cmd}:")

    elif cmd == "🔟 SHORT":
        from trading_bot.core.settings_manager import settings_manager
        current = settings_manager.get('short_enabled')
        settings_manager.set('short_enabled', not current)
        send_message(chat_id, f"🔻 SHORT торговля {'✅ ВКЛЮЧЕНА' if not current else '❌ ОТКЛЮЧЕНА'}")
        return show_settings_menu(chat_id)

    elif cmd == "📊 Сброс":
        return handle_reset_settings(chat_id)

    elif cmd == "🔄 АВТО":
        return handle_auto_settings(chat_id)

    elif cmd == "🔙 Назад":
        return send_reply_keyboard(chat_id)

    # Основные кнопки меню
    if cmd in ["📊 Статус", "/status"]:
        return send_status_message(chat_id)
    elif cmd in ["💰 Баланс", "/balance"]:
        return send_balance_message(chat_id)
    elif cmd in ["📈 Позиции", "/positions"]:
        return send_positions_message(chat_id)
    elif cmd in ["💼 Портфель", "/portfolio"]:
        return send_portfolio_message(chat_id)
    elif cmd in ["📉 P&L", "/pnl"]:
        return send_pnl_message(chat_id)
    elif cmd in ["📊 Маржа", "/margin"]:
        return send_margin_message(chat_id)
    elif cmd in ["🟢 Купить", "/buy"]:
        return handle_buy_command(chat_id)
    elif cmd in ["🔴 Продать", "/sell"]:
        return handle_sell_command(chat_id)
    elif cmd in ["🔒 Закрыть всё", "/close_all"]:
        return close_all_positions(chat_id)
    elif cmd in ["⚙️ Настройки", "/settings"]:
        return show_settings_menu(chat_id)
    elif cmd in ["🛠️ Сервис", "/service"]:
        return send_service_message(chat_id)
    elif cmd in ["❓ Помощь", "/help"]:
        return send_help_message(chat_id)
    elif cmd in ["/start", "/menu"]:
        return send_reply_keyboard(chat_id)
    elif cmd == "/clear_cache":
        return clear_validation_cache(chat_id)
    elif cmd == "/health":
        return health_check_message(chat_id)
    elif cmd == "/test":
        return test_connection_message(chat_id)

    return False


def polling_loop():
    """Основной цикл polling"""
    bot_token = get_bot_token()
    chat_id = get_chat_id()

    if not bot_token or not chat_id:
        logger.error("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set!")
        return

    last_update_id = 0
    retry_count = 0

    logger.info("Sending start menu...")
    send_reply_keyboard(chat_id)

    logger.info("Starting polling loop...")

    while True:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            response = requests.get(
                url,
                params={'offset': last_update_id + 1, 'timeout': 30},
                timeout=35
            )

            if response.status_code != 200:
                retry_count += 1
                if retry_count > 5:
                    time.sleep(60)
                    retry_count = 0
                else:
                    time.sleep(5)
                continue

            retry_count = 0
            data = response.json()

            if data.get('ok'):
                for update in data.get('result', []):
                    last_update_id = update['update_id']

                    if 'message' in update:
                        msg = update['message']
                        text = msg.get('text', '')
                        user_id = msg.get('chat', {}).get('id')

                        if str(user_id) == str(chat_id):
                            logger.info(f"📩 Received: {text}")
                            process_command(text, user_id)

                    elif 'callback_query' in update:
                        cb = update['callback_query']
                        cb_data = cb.get('data', '')
                        user_id = cb.get('message', {}).get('chat', {}).get('id')
                        cb_id = cb.get('id')

                        if str(user_id) == str(chat_id):
                            logger.info(f"🔘 Button: {cb_data}")
                            try:
                                answer_url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
                                requests.post(answer_url, json={"callback_query_id": cb_id}, timeout=5)
                            except:
                                pass
                            process_command(cb_data, user_id)

        except requests.exceptions.Timeout:
            logger.warning("Telegram API timeout, retrying...")
            time.sleep(5)
        except requests.exceptions.ConnectionError:
            logger.warning("Connection error, retrying...")
            time.sleep(10)
        except Exception as e:
            logger.error(f"Unexpected error in polling loop: {e}")
            time.sleep(15)


def start_polling_in_background():
    """Запуск polling в фоновом потоке"""
    thread = threading.Thread(target=polling_loop, daemon=True)
    thread.start()
    logger.info("✅ Telegram polling started in background thread")
    return thread


def main():
    print("=" * 60)
    print("🔄 TELEGRAM POLLING SERVICE (ПОЛНАЯ ВЕРСИЯ)")
    print("   Поддерживает: статус, баланс, позиции, настройки, АВТО, покупку/продажу")
    print("=" * 60)
    polling_loop()


if __name__ == "__main__":
    main()