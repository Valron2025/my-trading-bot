# trading_bot/config/sectors.py
"""
Маппинг тикеров к секторам для корреляционного анализа

Используется в advanced_risk_manager.py для расчёта штрафа за концентрацию
в одном секторе при открытии новых позиций.
"""

# Основная карта секторов
SECTOR_MAP = {
    # ========== БАНКОВСКИЙ СЕКТОР ==========
    'SBER': 'bank',
    'SBERP': 'bank',
    'VTBR': 'bank',
    'TCSG': 'bank',  # Т-Банк
    'CBOM': 'bank',  # МКБ

    # ========== НЕФТЕГАЗОВЫЙ СЕКТОР ==========
    'GAZP': 'oil_gas',
    'NVTK': 'oil_gas',
    'LKOH': 'oil_gas',
    'ROSN': 'oil_gas',
    'TATN': 'oil_gas',
    'TATNP': 'oil_gas',
    'SNGS': 'oil_gas',
    'SNGSP': 'oil_gas',
    'BANE': 'oil_gas',
    'BANEP': 'oil_gas',
    'SIBN': 'oil_gas',

    # ========== МЕТАЛЛУРГИЯ И ДОБЫЧА ==========
    'GMKN': 'metals',
    'NLMK': 'metals',
    'CHMF': 'metals',
    'MMK': 'metals',
    'MAGN': 'metals',
    'RUAL': 'metals',
    'PLZL': 'metals',
    'POLY': 'metals',
    'ALRS': 'metals',
    'VSMO': 'metals',

    # ========== РИТЕЙЛ ==========
    'MGNT': 'retail',
    'FIVE': 'retail',
    'MVID': 'retail',
    'DSKY': 'retail',
    'LENT': 'retail',
    'HHRU': 'retail',

    # ========== ТЕЛЕКОММУНИКАЦИИ ==========
    'MTSS': 'telecom',
    'RTKM': 'telecom',
    'RTTKM': 'telecom',
    'VKCO': 'telecom',

    # ========== ЭЛЕКТРОЭНЕРГЕТИКА ==========
    'FEES': 'power',
    'IRAO': 'power',
    'UPRO': 'power',
    'MSNG': 'power',
    'MRKP': 'power',
    'MRKV': 'power',
    'MRKC': 'power',
    'MRKU': 'power',

    # ========== ХИМИЯ И УДОБРЕНИЯ ==========
    'PHOR': 'chemicals',
    'ACRZ': 'chemicals',
    'KZRU': 'chemicals',
    'NKNC': 'chemicals',
    'NKNCP': 'chemicals',
    'UZAS': 'chemicals',

    # ========== МАШИНОСТРОЕНИЕ ==========
    'SVAV': 'industry',
    'KMAZ': 'industry',
    'UWGN': 'industry',

    # ========== ТРАНСПОРТ ==========
    'AFLT': 'transport',
    'FLOT': 'transport',
    'NMTP': 'transport',
    'FESH': 'transport',

    # ========== ДЕВЕЛОПЕРЫ ==========
    'PIKK': 'realty',
    'LSRG': 'realty',
    'ETLN': 'realty',
    'SMLT': 'realty',

    # ========== ИТ И ТЕХНОЛОГИИ ==========
    'YNDX': 'it',
    'OZON': 'it',
    'FIXP': 'it',
    'POSI': 'it',
    'SOFL': 'it',
    'CIAN': 'it',
    'QIWI': 'it',

    # ========== ПИЩЕВАЯ ПРОМЫШЛЕННОСТЬ ==========
    'ROST': 'food',
    'BELU': 'food',

    # ========== ДРУГОЕ ==========
    'MOEX': 'exchange',  # Московская биржа
}


def get_sector(ticker: str) -> str:
    """
    Получение сектора для тикера

    Args:
        ticker: Тикер инструмента

    Returns:
        Название сектора или 'other' если не найден
    """
    return SECTOR_MAP.get(ticker.upper(), 'other')


def get_sector_penalty(ticker: str, open_tickers: list, sector_penalty_multiplier: float = 0.3) -> float:
    """
    Расчёт штрафа за концентрацию в секторе

    Args:
        ticker: Новый тикер
        open_tickers: Список открытых тикеров
        sector_penalty_multiplier: Множитель штрафа за сектор (0-1)

    Returns:
        Штраф (0-1)
    """
    if not open_tickers:
        return 0.0

    sector = get_sector(ticker)
    if sector == 'other':
        return 0.05  # Небольшой штраф за неизвестный сектор

    same_sector_count = sum(1 for t in open_tickers if get_sector(t) == sector)

    if same_sector_count == 0:
        return 0.0
    elif same_sector_count == 1:
        return 0.1 * sector_penalty_multiplier
    elif same_sector_count == 2:
        return 0.3 * sector_penalty_multiplier
    elif same_sector_count == 3:
        return 0.5 * sector_penalty_multiplier
    else:
        return 0.7 * sector_penalty_multiplier


# Для быстрого доступа в других модулях
sector_map = SECTOR_MAP