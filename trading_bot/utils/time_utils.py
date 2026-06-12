# trading_bot/utils/time_utils.py

"""Утилиты для работы со временем и торговыми сессиями"""

from datetime import datetime, time, timedelta, timezone
from typing import Optional, Tuple, Dict, List, Any
import pytz
from ..logger import info, success, error, warning, debug

# Московская временная зона (UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))

# Времена торговых сессий (МСК)
PRE_MARKET_START = time(6, 50)  # Начало утренней сессии
PRE_MARKET_END = time(9, 49, 59)  # Конец утренней сессии
MAIN_SESSION_START = time(9, 50)  # Начало основной сессии
MAIN_SESSION_END = time(18, 59, 59)  # Конец основной сессии
EVENING_SESSION_START = time(19, 0, 1)  # Начало вечерней сессии
EVENING_SESSION_END = time(23, 49, 59)  # Конец вечерней сессии

# Технический перерыв между сессиями (2 секунды)
TECH_BREAK_START = time(18, 59, 59)
TECH_BREAK_END = time(19, 0, 1)

# ========== ДСВД (Дополнительная сессия выходного дня) ==========
# БИРЖЕВЫЕ торги в выходные
# Время: 09:50 - 18:59 МСК
DSVD_START = time(9, 50)
DSVD_END = time(18, 59)

# ========== OTC (Внебиржевые торги) ==========
# ТОРГИ ТОЖЕ ДОСТУПНЫ через API, но ТОЛЬКО лимитные заявки!
# Время: 02:00 - 23:50 МСК
OTC_START = time(2, 0)
OTC_END = time(23, 50)

# Для обратной совместимости (WEEKEND_START/END теперь = OTC)
WEEKEND_START = OTC_START
WEEKEND_END = OTC_END

# Вечерняя сессия выходного дня (если будет введена)
WEEKEND_EVENING_START = time(19, 0, 1)
WEEKEND_EVENING_END = time(23, 49, 59)


def get_moscow_time() -> datetime:
    """Получение текущего московского времени (UTC+3)"""
    return datetime.now(MOSCOW_TZ)


def get_moscow_time_iso() -> str:
    """Получение московского времени в ISO формате"""
    return get_moscow_time().isoformat()


def format_time_for_log(dt: datetime = None) -> str:
    """Форматирование времени для логов"""
    if dt is None:
        dt = get_moscow_time()
    return dt.strftime("%H:%M:%S")


def is_pre_market_time() -> bool:
    """Проверка, сейчас ли утренняя сессия (УДС)"""
    now = get_moscow_time().time()
    return PRE_MARKET_START <= now <= PRE_MARKET_END


def is_main_session_time() -> bool:
    """Проверка, сейчас ли основная сессия (ОС)"""
    now = get_moscow_time().time()
    return MAIN_SESSION_START <= now <= MAIN_SESSION_END


def is_evening_session_time() -> bool:
    """Проверка, сейчас ли вечерняя сессия (ВДС)"""
    now = get_moscow_time().time()
    return EVENING_SESSION_START <= now <= EVENING_SESSION_END


def is_technical_break() -> bool:
    """Проверка, сейчас ли технический перерыв между сессиями"""
    now = get_moscow_time().time()
    return TECH_BREAK_START <= now <= TECH_BREAK_END


def is_trading_time() -> bool:
    """Проверка, можно ли торговать сейчас (биржевые сессии)"""
    return is_pre_market_time() or is_main_session_time() or is_evening_session_time()


def is_dsvd_trading_time() -> bool:
    """
    Проверка ДСВД (биржевые торги в выходные/праздники)
    ДСВД проходит:
    - Суббота, воскресенье И ПРАЗДНИЧНЫЕ ДНИ
    - Время: 09:50 - 18:59 МСК
    """
    now = get_moscow_time()
    current_time = now.time()
    weekday = now.weekday()
    is_holiday_today = is_holiday(now)

    # В праздник торгуем по ДСВД (в часы ДСВД)
    if is_holiday_today:
        return DSVD_START <= current_time <= DSVD_END

    # В выходные торгуем по ДСВД (в часы ДСВД)
    if weekday in (5, 6):  # Суббота или воскресенье
        return DSVD_START <= current_time <= DSVD_END

    return False

def is_weekend_evening_trading_time() -> bool:
    """
    Проверка вечерней сессии выходного дня (если будет введена)
    В настоящее время не активна, но оставлено для будущего
    """
    now = get_moscow_time()
    current_time = now.time()
    weekday = now.weekday()

    if weekday not in (5, 6):
        return False

    if is_holiday(now):
        return False

    # Пока отключено, возвращаем False
    # Если Мосбиржа введёт вечерние торги в выходные, раскомментировать:
    # return WEEKEND_EVENING_START <= current_time <= WEEKEND_EVENING_END
    return False


def is_otc_trading_time() -> bool:
    """
    Проверка, идёт ли сейчас OTC (внебиржевые торги в выходные)
    OTC проходит:
    - Суббота и воскресенье
    - Время: 02:00 - 23:50 МСК
    - ТОЛЬКО лимитные заявки!

    ВНИМАНИЕ: OTC считается отдельным режимом, НЕ ДСВД!
    """
    now = get_moscow_time()
    current_time = now.time()
    weekday = now.weekday()

    if weekday not in (5, 6):
        return False

    if is_holiday(now):
        return False

    # Исключаем часы ДСВД (09:50-18:59) из OTC
    # В эти часы идут БИРЖЕВЫЕ торги, а не OTC
    if DSVD_START <= current_time <= DSVD_END:
        return False

    return OTC_START <= current_time <= OTC_END


def is_weekend_trading_time() -> bool:
    """
    Проверка, идёт ли сейчас любая торговля в выходные

    ВНИМАНИЕ: Эта функция возвращает True ТОЛЬКО в часы, когда
    реально можно торговать через API:

    - ДСВД (биржевые): 09:50 - 18:59
    - Вечерняя сессия выходного (если будет): 19:00 - 23:49
    - OTC (внебиржевые): 02:00 - 09:49 и 19:00 - 23:50

    Используется в trading_loop.py для определения режима торговли
    """
    return is_dsvd_trading_time() or is_weekend_evening_trading_time() or is_otc_trading_time()


def get_current_session_name() -> str:
    """Получение названия текущей сессии (короткое)"""
    if is_dsvd_trading_time():
        return "dsvd"
    elif is_weekend_evening_trading_time():
        return "weekend_evening"
    elif is_otc_trading_time():
        return "otc"
    elif is_pre_market_time():
        return "pre_market"
    elif is_main_session_time():
        return "main"
    elif is_evening_session_time():
        return "evening"
    else:
        return "closed"


def get_current_session_name_detailed() -> str:
    """Получение названия текущей сессии (подробное)"""
    now = get_moscow_time()
    current_time = now.time()
    weekday = now.weekday()
    is_weekend = weekday >= 5

    if is_weekend:
        if is_dsvd_trading_time():
            return "ДСВД (биржевые, рыночные+лимитные) 09:50-18:59"
        elif is_weekend_evening_trading_time():
            return "ДСВД вечерняя (биржевые, рыночные+лимитные) 19:00-23:49"
        elif is_otc_trading_time():
            return "OTC (внебиржевые, ТОЛЬКО лимитные)"
        else:
            return f"закрыта (выходной, текущее время {current_time.hour:02d}:{current_time.minute:02d})"

    if is_pre_market_time():
        return "УДС (утренняя) 06:50-09:49"
    elif is_main_session_time():
        return "ОС (основная) 09:50-18:59"
    elif is_evening_session_time():
        return "ВДС (вечерняя) 19:00-23:49"
    elif is_technical_break():
        return "технический перерыв (18:59:59-19:00:01)"
    else:
        return f"закрыта (текущее время {current_time.hour:02d}:{current_time.minute:02d})"


def get_time_until_next_session() -> Tuple[Optional[str], int]:
    """
    Получение времени до следующей торговой сессии
    """
    now = get_moscow_time()
    current_time = now.time()
    weekday = now.weekday()

    # Выходные - следующая сессия в понедельник
    if weekday >= 5:
        days_until_monday = (7 - weekday) if weekday < 7 else 1
        next_open = now.replace(hour=PRE_MARKET_START.hour, minute=PRE_MARKET_START.minute,
                                second=0, microsecond=0) + timedelta(days=days_until_monday)
        minutes = int((next_open - now).total_seconds() / 60)
        return ("УДС (понедельник)", minutes)

    # Будни
    if current_time < PRE_MARKET_START:
        next_open = now.replace(hour=PRE_MARKET_START.hour, minute=PRE_MARKET_START.minute)
        minutes = int((next_open - now).total_seconds() / 60)
        return ("УДС (утренняя)", minutes)
    elif PRE_MARKET_START <= current_time < MAIN_SESSION_START:
        next_open = now.replace(hour=MAIN_SESSION_START.hour, minute=MAIN_SESSION_START.minute)
        minutes = int((next_open - now).total_seconds() / 60)
        return ("ОС (основная)", minutes)
    elif MAIN_SESSION_START <= current_time < EVENING_SESSION_START:
        next_open = now.replace(hour=EVENING_SESSION_START.hour, minute=EVENING_SESSION_START.minute)
        minutes = int((next_open - now).total_seconds() / 60)
        return ("ВДС (вечерняя)", minutes)
    elif current_time > EVENING_SESSION_END:
        next_open = now.replace(hour=PRE_MARKET_START.hour, minute=PRE_MARKET_START.minute) + timedelta(days=1)
        minutes = int((next_open - now).total_seconds() / 60)
        return ("УДС (завтра)", minutes)
    else:
        return (None, 0)


def get_minutes_until_session_start(session_name: str) -> int:
    """Получение минут до начала указанной сессии"""
    next_session, minutes = get_time_until_next_session()
    if next_session and session_name.lower() in next_session.lower():
        return minutes
    return 0


def get_minutes_until_session_end() -> int:
    """Получение минут до конца текущей сессии"""
    now = get_moscow_time()
    current_time = now.time()
    weekday = now.weekday()

    if is_dsvd_trading_time():
        end_time = now.replace(hour=DSVD_END.hour, minute=DSVD_END.minute, second=0, microsecond=0)
        minutes = max(0, int((end_time - now).total_seconds() / 60))
        return minutes

    if is_weekend_evening_trading_time():
        end_time = now.replace(hour=WEEKEND_EVENING_END.hour, minute=WEEKEND_EVENING_END.minute, second=0, microsecond=0)
        minutes = max(0, int((end_time - now).total_seconds() / 60))
        return minutes

    if is_otc_trading_time():
        end_time = now.replace(hour=OTC_END.hour, minute=OTC_END.minute, second=0, microsecond=0)
        minutes = max(0, int((end_time - now).total_seconds() / 60))
        return minutes

    if is_pre_market_time():
        end_time = now.replace(hour=PRE_MARKET_END.hour, minute=PRE_MARKET_END.minute, second=PRE_MARKET_END.second)
    elif is_main_session_time():
        end_time = now.replace(hour=MAIN_SESSION_END.hour, minute=MAIN_SESSION_END.minute,
                               second=MAIN_SESSION_END.second)
    elif is_evening_session_time():
        end_time = now.replace(hour=EVENING_SESSION_END.hour, minute=EVENING_SESSION_END.minute,
                               second=EVENING_SESSION_END.second)
    else:
        return 0

    minutes = max(0, int((end_time - now).total_seconds() / 60))
    return minutes


def get_trading_day_status() -> Dict[str, Any]:
    """
    Получение полного статуса торгового дня
    """
    now = get_moscow_time()
    weekday = now.weekday()
    is_weekend = weekday >= 5

    session_name = get_current_session_name_detailed()
    is_trading = is_weekend_trading_time() or is_trading_time()

    minutes_left = get_minutes_until_session_end()
    time_until_next = get_time_until_next_session()

    return {
        'timestamp': now.isoformat(),
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M:%S'),
        'weekday': weekday,
        'is_weekend': is_weekend,
        'is_trading': is_trading,
        'session_name': session_name,
        'is_dsvd': is_dsvd_trading_time(),
        'is_otc': is_otc_trading_time(),
        'minutes_until_close': minutes_left if is_trading else 0,
        'time_until_next': time_until_next,
        'next_session_name': time_until_next[0] if time_until_next[0] else None,
        'minutes_until_next': time_until_next[1] if time_until_next[1] else 0
    }


def format_holding_time(entry_time: datetime, current_time: datetime = None) -> str:
    """
    Форматирование времени удержания позиции
    """
    if current_time is None:
        current_time = get_moscow_time()

    delta = current_time - entry_time
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return f"{total_seconds}с"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}м {seconds}с" if seconds > 0 else f"{minutes}м"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}ч {minutes}м" if minutes > 0 else f"{hours}ч"


def is_weekend() -> bool:
    """Проверка, является ли сегодня выходной день"""
    return get_moscow_time().weekday() >= 5


def get_session_time_remaining_percentage() -> float:
    """
    Получение процента оставшегося времени в текущей сессии
    """
    if not is_weekend_trading_time() and not is_trading_time():
        return 0

    minutes_left = get_minutes_until_session_end()

    session_durations = {
        "pre_market": 180,   # 06:50-09:50 = 180 минут
        "main": 549,         # 09:50-18:59 = 549 минут
        "evening": 290,      # 19:00-23:50 = 290 минут (23:50, не 23:49)
        "dsvd": 549,         # 09:50-18:59 = 549 минут
        "weekend_evening": 290,  # 19:00-23:49 = 289? уточнить
        "otc": 1310,         # 02:00-23:50 = 1310 минут
    }

    if is_dsvd_trading_time():
        session_name = "dsvd"
    elif is_weekend_evening_trading_time():
        session_name = "weekend_evening"
    elif is_otc_trading_time():
        session_name = "otc"
    else:
        session_name = get_current_session_name()

    total_duration = session_durations.get(session_name, 0)
    if total_duration == 0:
        return 0

    elapsed = total_duration - minutes_left
    percentage = (elapsed / total_duration) * 100

    return min(100, max(0, percentage))

def get_days_until_monday() -> int:
    """Сколько дней до понедельника"""
    now = get_moscow_time()
    days_until_monday = (7 - now.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    return days_until_monday


def is_holiday(dt: datetime = None) -> bool:
    """
    Проверка, является ли день праздничным (торги закрыты)
    Учитывает ДСВД/OTC торги в выходные и праздники
    """
    if dt is None:
        dt = get_moscow_time()

    # Список официальных праздников MOEX (дни, когда биржа закрыта)
    holidays = {
        (1, 1),  # Новый год
        (1, 2),  # Новый год
        (1, 7),  # Рождество
        (2, 23),  # День защитника Отечества
        (3, 8),  # Международный женский день
        (5, 1),  # Праздник Весны и Труда
        (5, 9),  # День Победы
        (6, 12),  # День России
        (11, 4),  # День народного единства
    }

    # Проверяем, праздник ли сегодня
    if (dt.month, dt.day) in holidays:
        # ДАЖЕ В ПРАЗДНИК — проверяем, идут ли ДСВД/OTC торги!
        if is_weekend_trading_time() or is_otc_trading_time():
            # Если ДСВД/OTC активны — не считаем праздником для торговли
            debug(f"🎉 {dt.strftime('%d.%m')} праздник, но ДСВД/OTC активны — торговля разрешена")
            return False
        debug(f"🎄 {dt.strftime('%d.%m')} праздник, ДСВД/OTC не активны — торговля запрещена")
        return True

    return False


def is_friday_evening() -> bool:
    """Проверить, пятница ли сегодня после 18:00"""
    now = get_moscow_time()
    return now.weekday() == 4 and now.hour >= 18


# ========== ✅ ИСПРАВЛЕННАЯ ФУНКЦИЯ (УДАЛЁН BLUE_CHIPS) ==========

def is_trading_time_for_ticker(ticker: str) -> Tuple[bool, str]:
    """
    Проверить, можно ли торговать тикером в текущее время
    ✅ ИСПРАВЛЕНО: удалена проверка на голубые фишки

    Args:
        ticker: Тикер акции

    Returns:
        (можно_торговать, причина)
    """
    now = get_moscow_time()

    # Выходные
    if now.weekday() >= 5:
        # В выходные торгуются все инструменты через OTC (лимитные заявки)
        # Ограничений по тикеру нет, только по типу заявки
        return True, "Торговля через OTC (лимитные заявки)"

    # Пятница вечер - предупреждение, но не блокировка
    if is_friday_evening():
        # Просто предупреждаем, но разрешаем
        return True, "Пятница вечер - высокий риск зависания на выходные"

    return True, "ОК"


def is_trading_time_for_ticker_advanced(ticker: str, figi: str = None) -> Tuple[bool, str]:
    """
    ✅ НОВАЯ ФУНКЦИЯ: расширенная проверка с использованием API
    """
    # Сначала базовая проверка времени
    can_trade, reason = is_trading_time_for_ticker(ticker)
    if not can_trade:
        return False, reason

    # Если есть FIGI, проверяем API
    if figi:
        try:
            from trading_bot.api.tbank_client import tbank
            status = tbank.get_trading_status(figi)
            if status.get('api_trade_available', False):
                return True, f"API торговля доступна ({reason})"
            else:
                return False, f"API торговля недоступна для {ticker}"
        except Exception:
            pass

    return True, reason