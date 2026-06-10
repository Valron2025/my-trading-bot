-- Таблица для хранения истории фундаментальных показателей
CREATE TABLE IF NOT EXISTS fundamental_metrics_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    fetched_date DATE NOT NULL,

    -- Мультипликаторы
    pe_ratio REAL,
    pb_ratio REAL,
    ev_ebitda REAL,

    -- Рентабельность
    roe REAL,
    roa REAL,
    gross_margin REAL,
    net_margin REAL,

    -- Рост
    revenue_growth REAL,
    earnings_growth REAL,
    eps_growth REAL,

    -- Долг и ликвидность
    debt_to_equity REAL,
    current_ratio REAL,
    quick_ratio REAL,

    -- Дивиденды
    dividend_yield REAL,
    payout_ratio REAL,

    -- Прочее
    market_cap REAL,
    free_float REAL,
    beta REAL,

    -- Оценки
    value_score REAL,
    quality_score REAL,
    safety_score REAL,
    liquidity_score REAL,
    overall_score REAL,
    recommendation TEXT,

    -- Метаданные
    source TEXT DEFAULT 'moex_estimated',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(ticker, fetched_date)
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_fundamental_ticker ON fundamental_metrics_history(ticker);
CREATE INDEX IF NOT EXISTS idx_fundamental_date ON fundamental_metrics_history(fetched_date);
CREATE INDEX IF NOT EXISTS idx_fundamental_score ON fundamental_metrics_history(overall_score);

-- Таблица для текущих (актуальных) мультипликаторов
CREATE TABLE IF NOT EXISTS current_multipliers (
    ticker TEXT PRIMARY KEY,

    -- Сектор компании
    sector TEXT,

    -- Мультипликаторы
    pe_ratio REAL,
    pb_ratio REAL,
    roe REAL,
    dividend_yield REAL,
    payout_ratio REAL,

    -- Источник данных
    source TEXT DEFAULT 'estimated',
    last_updated DATE,

    -- Для автоматического обновления
    next_update DATE,
    update_frequency_days INTEGER DEFAULT 1,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Таблица для секторных коэффициентов (база знаний)
CREATE TABLE IF NOT EXISTS sector_multipliers (
    sector TEXT PRIMARY KEY,
    avg_pe REAL,
    avg_pb REAL,
    avg_roe REAL,
    avg_dividend_yield REAL,
    avg_payout_ratio REAL,
    last_calculated DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Вставка начальных данных по секторам
INSERT OR REPLACE INTO sector_multipliers (sector, avg_pe, avg_pb, avg_roe, avg_dividend_yield, avg_payout_ratio, last_calculated) VALUES
('bank', 5.5, 1.2, 22.0, 8.2, 45.0, date('now')),
('gas', 4.5, 1.2, 25.0, 10.5, 48.0, date('now')),
('oil', 5.0, 1.1, 23.0, 9.5, 47.0, date('now')),
('retail', 7.0, 2.0, 28.0, 6.5, 35.0, date('now')),
('telecom', 6.5, 1.5, 18.0, 7.0, 40.0, date('now'));