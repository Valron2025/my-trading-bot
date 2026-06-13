# trading_bot/analysis/fundamental_db.py
# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fundamental_db.py - Работа с БД фундаментальных показателей
Сохранение истории, автоматическое обновление
"""

import sqlite3
from datetime import date, datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

from trading_bot.analysis.fundamental_analyzer import FundamentalMetrics
from trading_bot.logger import info, success, error, debug, sep


class FundamentalDatabase:
    """Работа с БД фундаментальных показателей"""

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path
        self._init_tables()
        info(f"🗄️ FundamentalDatabase инициализирована: {db_path}")

    def _init_tables(self):
        """Инициализация таблиц"""
        with sqlite3.connect(self.db_path) as conn:
            # Таблица истории фундаментальных метрик
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fundamental_metrics_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    fetched_date DATE NOT NULL,
                    pe_ratio REAL,
                    pb_ratio REAL,
                    ev_ebitda REAL,
                    roe REAL,
                    roa REAL,
                    gross_margin REAL,
                    net_margin REAL,
                    revenue_growth REAL,
                    earnings_growth REAL,
                    eps_growth REAL,
                    debt_to_equity REAL,
                    current_ratio REAL,
                    quick_ratio REAL,
                    dividend_yield REAL,
                    payout_ratio REAL,
                    market_cap REAL,
                    free_float REAL,
                    beta REAL,
                    value_score REAL,
                    quality_score REAL,
                    safety_score REAL,
                    liquidity_score REAL,
                    overall_score REAL,
                    recommendation TEXT,
                    source TEXT DEFAULT 'moex_estimated',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ticker, fetched_date)
                )
            """)

            # Таблица текущих мультипликаторов
            conn.execute("""
                CREATE TABLE IF NOT EXISTS current_multipliers (
                    ticker TEXT PRIMARY KEY,
                    sector TEXT,
                    pe_ratio REAL,
                    pb_ratio REAL,
                    roe REAL,
                    dividend_yield REAL,
                    payout_ratio REAL,
                    source TEXT DEFAULT 'estimated',
                    last_updated DATE,
                    next_update DATE,
                    update_frequency_days INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица секторных коэффициентов
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sector_multipliers (
                    sector TEXT PRIMARY KEY,
                    avg_pe REAL,
                    avg_pb REAL,
                    avg_roe REAL,
                    avg_dividend_yield REAL,
                    avg_payout_ratio REAL,
                    last_calculated DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Индексы
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fundamental_ticker ON fundamental_metrics_history(ticker)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fundamental_date ON fundamental_metrics_history(fetched_date)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fundamental_score ON fundamental_metrics_history(overall_score)")

            # Вставка начальных данных по секторам
            conn.execute("""
                INSERT OR IGNORE INTO sector_multipliers 
                (sector, avg_pe, avg_pb, avg_roe, avg_dividend_yield, avg_payout_ratio, last_calculated)
                VALUES 
                ('bank', 5.5, 1.2, 22.0, 8.2, 45.0, date('now')),
                ('gas', 4.5, 1.2, 25.0, 10.5, 48.0, date('now')),
                ('oil', 5.0, 1.1, 23.0, 9.5, 47.0, date('now')),
                ('retail', 7.0, 2.0, 28.0, 6.5, 35.0, date('now')),
                ('telecom', 6.5, 1.5, 18.0, 7.0, 40.0, date('now'))
            """)

            debug("✅ Таблицы фундаментальных данных созданы/проверены")

    def save_metrics(self, ticker: str, metrics: FundamentalMetrics, source: str = "moex_estimated"):
        """Сохранение фундаментальных метрик в историю"""
        today = date.today()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO fundamental_metrics_history 
                (ticker, fetched_date, pe_ratio, pb_ratio, ev_ebitda, roe, roa, 
                 gross_margin, net_margin, revenue_growth, earnings_growth, eps_growth,
                 debt_to_equity, current_ratio, quick_ratio, dividend_yield, payout_ratio,
                 market_cap, free_float, beta, value_score, quality_score, safety_score,
                 liquidity_score, overall_score, recommendation, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker, today, metrics.pe_ratio if metrics.pe_ratio > 0 else None,
                metrics.pb_ratio if metrics.pb_ratio > 0 else None,
                metrics.ev_ebitda if metrics.ev_ebitda > 0 else None,
                metrics.roe if metrics.roe > 0 else None,
                metrics.roa if metrics.roa > 0 else None,
                metrics.gross_margin if metrics.gross_margin > 0 else None,
                metrics.net_margin if metrics.net_margin > 0 else None,
                metrics.revenue_growth if metrics.revenue_growth > 0 else None,
                metrics.earnings_growth if metrics.earnings_growth > 0 else None,
                metrics.eps_growth if metrics.eps_growth > 0 else None,
                metrics.debt_to_equity if metrics.debt_to_equity > 0 else None,
                metrics.current_ratio if metrics.current_ratio > 0 else None,
                metrics.quick_ratio if metrics.quick_ratio > 0 else None,
                metrics.dividend_yield if metrics.dividend_yield > 0 else None,
                metrics.payout_ratio if metrics.payout_ratio > 0 else None,
                metrics.market_cap if metrics.market_cap > 0 else None,
                metrics.free_float if metrics.free_float > 0 else None,
                metrics.beta if metrics.beta > 0 else None,
                metrics.value_score, metrics.quality_score,
                metrics.safety_score, metrics.liquidity_score,
                metrics.overall_score, metrics.recommendation[0], source
            ))

            # Обновляем текущие мультипликаторы
            conn.execute("""
                INSERT OR REPLACE INTO current_multipliers 
                (ticker, pe_ratio, pb_ratio, roe, dividend_yield, payout_ratio, 
                 source, last_updated, next_update, update_frequency_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, date('now', '+' || ? || ' days'), ?)
            """, (
                ticker,
                metrics.pe_ratio if metrics.pe_ratio > 0 else None,
                metrics.pb_ratio if metrics.pb_ratio > 0 else None,
                metrics.roe if metrics.roe > 0 else None,
                metrics.dividend_yield if metrics.dividend_yield > 0 else None,
                metrics.payout_ratio if metrics.payout_ratio > 0 else None,
                source, date.today(), 1, 1
            ))

        debug(f"💾 Сохранены данные для {ticker} за {today} (Score={metrics.overall_score:.0f})")

    def get_history(self, ticker: str, days: int = 30) -> List[Dict[str, Any]]:
        """Получение истории фундаментальных показателей"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM fundamental_metrics_history 
                WHERE ticker = ? AND fetched_date >= date('now', '-' || ? || ' days')
                ORDER BY fetched_date DESC
            """, (ticker, days))

            return [dict(row) for row in cursor.fetchall()]

    def get_current_multipliers(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Получение текущих мультипликаторов"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM current_multipliers WHERE ticker = ?
            """, (ticker,))

            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_current_multipliers(self) -> Dict[str, Dict[str, Any]]:
        """Получение всех текущих мультипликаторов"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM current_multipliers")

            return {row['ticker']: dict(row) for row in cursor.fetchall()}

    def get_sector_multipliers(self, sector: str) -> Optional[Dict[str, Any]]:
        """Получение мультипликаторов по сектору"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM sector_multipliers WHERE sector = ?", (sector,))

            row = cursor.fetchone()
            return dict(row) if row else None

    def update_sector_multipliers(self, sector: str, multipliers: Dict[str, float]):
        """Обновление мультипликаторов сектора на основе реальных данных"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE sector_multipliers 
                SET avg_pe = ?, avg_pb = ?, avg_roe = ?, 
                    avg_dividend_yield = ?, avg_payout_ratio = ?,
                    last_calculated = date('now')
                WHERE sector = ?
            """, (
                multipliers.get('pe', 0), multipliers.get('pb', 0),
                multipliers.get('roe', 0), multipliers.get('dividend_yield', 0),
                multipliers.get('payout_ratio', 0), sector
            ))

        debug(f"📊 Обновлены мультипликаторы сектора {sector}")

    def get_stale_tickers(self) -> List[str]:
        """Получение тикеров, у которых мультипликаторы устарели"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT ticker FROM current_multipliers 
                WHERE next_update <= date('now') OR next_update IS NULL
            """)

            return [row[0] for row in cursor.fetchall()]

    def get_trend(self, ticker: str, metric: str = 'overall_score', days: int = 30) -> Dict[str, Any]:
        """Анализ тренда фундаментального показателя"""
        history = self.get_history(ticker, days)

        if len(history) < 2:
            return {'trend': 'insufficient_data', 'change': 0, 'change_pct': 0, 'values': []}

        values = [h[metric] for h in history if h.get(metric) is not None]

        if len(values) < 2:
            return {'trend': 'insufficient_data', 'change': 0, 'change_pct': 0, 'values': values}

        change = values[0] - values[-1] if len(values) > 1 else 0
        change_pct = (change / values[-1] * 100) if values[-1] != 0 else 0

        if change_pct > 5:
            trend = 'improving'
        elif change_pct < -5:
            trend = 'deteriorating'
        else:
            trend = 'stable'

        return {
            'trend': trend,
            'change': change,
            'change_pct': change_pct,
            'current': values[0] if values else None,
            'previous': values[-1] if len(values) > 1 else None,
            'values': values
        }

    def get_summary(self) -> Dict[str, Any]:
        """Получение сводной информации по БД"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM fundamental_metrics_history")
            history_count = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM current_multipliers")
            current_count = cursor.fetchone()[0]

            cursor = conn.execute("SELECT DISTINCT ticker FROM fundamental_metrics_history")
            tickers = [row[0] for row in cursor.fetchall()]

        return {
            'history_records': history_count,
            'current_tickers': current_count,
            'unique_tickers': tickers,
            'db_path': self.db_path
        }

    def cleanup_old_records(self, days: int = 365):
        """Очистка старых записей (старше days дней)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM fundamental_metrics_history 
                WHERE fetched_date < date('now', '-' || ? || ' days')
            """, (days,))
            deleted = cursor.rowcount

        if deleted > 0:
            info(f"🧹 Удалено {deleted} старых записей (старше {days} дней)")
        return deleted


# Для тестирования
if __name__ == "__main__":
    sep("=")
    info("🧪 ТЕСТ FUNDAMENTAL DATABASE")
    sep("=")

    db = FundamentalDatabase("test_fundamental.db")
    summary = db.get_summary()

    info(f"📊 Сводка:")
    info(f"   История: {summary['history_records']} записей")
    info(f"   Текущие: {summary['current_tickers']} тикеров")
    info(f"   Уникальных тикеров: {summary['unique_tickers']}")

    sep("=")
    success("✅ Тест завершён")