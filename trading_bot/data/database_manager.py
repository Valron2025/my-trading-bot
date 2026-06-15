# database_manager.py
"""Менеджер базы данных для сохранения состояния бота"""

import sqlite3
import json
import threading
from trading_bot.cache import TTLCache
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

from trading_bot.logger import info, debug

# Часовой пояс МСК
MOSCOW_TZ = timezone(timedelta(hours=3))


class DatabaseManager:
    """Менеджер SQLite БД для персистентного хранения состояния"""

    def __init__(self, db_path: str = "trading_state.db", use_memory_cache: bool = True):
        self.db_path = db_path
        self.use_memory_cache = use_memory_cache
        self._lock = threading.RLock()
        self.conn: Optional[sqlite3.Connection] = None

        # Добавляем слой in-memory кэша для горячих данных
        if use_memory_cache:
            self._memory_cache = TTLCache(default_ttl=300, max_size=1000, name="db_cache")

        self._init_db()
        info(f"✅ DatabaseManager инициализирован: {db_path} (mem_cache={use_memory_cache})")

    def _init_db(self):
        """Инициализация всех таблиц"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)

            # ✅ ОПТИМИЗАЦИЯ SQLite
            conn.execute("PRAGMA journal_mode=WAL")  # WAL режим для конкурентности
            conn.execute("PRAGMA synchronous=NORMAL")  # Безопасный баланс скорости
            conn.execute("PRAGMA cache_size=-64000")  # 64MB кэша
            conn.execute("PRAGMA temp_store=MEMORY")  # Временные таблицы в RAM

            cursor = conn.cursor()

            # Таблица позиций
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    figi TEXT PRIMARY KEY,
                    ticker TEXT,
                    quantity INTEGER,
                    avg_price REAL,
                    side TEXT,
                    entry_time TEXT,
                    highest_price REAL,
                    lowest_price REAL,
                    updated_at TEXT
                )
            """)

            # Таблица сделок
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    figi TEXT,
                    ticker TEXT,
                    side TEXT,
                    quantity INTEGER,
                    price REAL,
                    pnl REAL,
                    pnl_pct REAL,
                    reason TEXT,
                    time TEXT
                )
            """)

            # Таблица состояния бота
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            """)

            # Таблица активных заявок
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    figi TEXT,
                    ticker TEXT,
                    direction TEXT,
                    quantity INTEGER,
                    price REAL,
                    status TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)

            # Таблица кэша
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    expires_at TEXT,
                    created_at TEXT
                )
            """)

            # Таблица чёрного списка
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS blacklist (
                    figi TEXT PRIMARY KEY,
                    ticker TEXT,
                    reason TEXT,
                    added_at TEXT,
                    expires_at TEXT
                )
            """)

            # Индексы
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_blacklist_expires ON blacklist(expires_at)")

            # ✅ ДОПОЛНИТЕЛЬНЫЕ ИНДЕКСЫ ДЛЯ УСКОРЕНИЯ
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_ticker ON orders(ticker)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions(ticker)")

            conn.commit()
            conn.close()

    # ========== ПОЗИЦИИ ==========

    def save_positions(self, positions: Dict[str, Any]) -> None:
        """Сохранение всех позиций"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM positions")

            for figi, pos in positions.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO positions 
                    (figi, ticker, quantity, avg_price, side, entry_time, highest_price, lowest_price, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    figi,
                    pos.get('ticker', figi[:8]),
                    pos.get('quantity', 0),
                    pos.get('avg_price', 0),
                    pos.get('side', 'LONG'),
                    pos.get('entry_time', datetime.now(MOSCOW_TZ).isoformat()),
                    pos.get('highest_price', 0),
                    pos.get('lowest_price', 0),
                    datetime.now(MOSCOW_TZ).isoformat()
                ))

            conn.commit()
            conn.close()
            debug(f"💾 Сохранено {len(positions)} позиций")

    def load_positions(self) -> List[Dict[str, Any]]:
        """Загрузка позиций из БД"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT figi, ticker, quantity, avg_price, side, entry_time FROM positions")
            rows = cursor.fetchall()
            conn.close()
            return [{
                'figi': r[0],
                'ticker': r[1],
                'quantity': r[2],
                'avg_price': r[3],
                'side': r[4],
                'entry_time': r[5]
            } for r in rows]

    # ========== СДЕЛКИ ==========

    def save_trade(self, trade: Dict[str, Any]) -> int:
        """Сохранение сделки"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (figi, ticker, side, quantity, price, pnl, pnl_pct, reason, time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get('figi', ''),
                trade.get('ticker', ''),
                trade.get('side', ''),
                trade.get('quantity', 0),
                trade.get('price', 0),
                trade.get('pnl', 0),
                trade.get('pnl_pct', 0),
                trade.get('reason', ''),
                trade.get('time', datetime.now(MOSCOW_TZ).isoformat())
            ))
            trade_id = cursor.lastrowid
            conn.commit()
            conn.close()
            debug(f"💾 Сохранена сделка #{trade_id}: {trade.get('ticker')}")
            return trade_id

    def get_recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Получение последних сделок"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trades ORDER BY time DESC LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]

    # ========== СОСТОЯНИЕ БОТА ==========

    def save_bot_state(self, key: str, value: Any) -> None:
        """Сохранение состояния бота"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO bot_state (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, json.dumps(value, default=str), datetime.now(MOSCOW_TZ).isoformat()))
            conn.commit()
            conn.close()

    def load_bot_state(self, key: str) -> Optional[Any]:
        """Загрузка состояния бота"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
            return None

    def save_cycle_state(self, cycle_count: int, capital: float, margin_rate: float) -> None:
        """Сохранение состояния цикла"""
        state = {
            'cycle_count': cycle_count,
            'capital': capital,
            'margin_rate': margin_rate,
            'timestamp': datetime.now(MOSCOW_TZ).isoformat()
        }
        self.save_bot_state('cycle_state', state)

    def load_cycle_state(self) -> Dict[str, Any]:
        """Загрузка состояния цикла"""
        return self.load_bot_state('cycle_state') or {}

    # ========== КЭШ ==========

    def set_cache(self, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        """Сохранение в кэш"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            expires_at = (datetime.now(MOSCOW_TZ) + timedelta(seconds=ttl_seconds)).isoformat()
            cursor.execute("""
                INSERT OR REPLACE INTO cache (key, value, expires_at, created_at)
                VALUES (?, ?, ?, ?)
            """, (key, json.dumps(value, default=str), expires_at, datetime.now(MOSCOW_TZ).isoformat()))
            conn.commit()
            conn.close()

    def get_cache(self, key: str) -> Optional[Any]:
        """Получение из кэша"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,))
            row = cursor.fetchone()
            conn.close()
            if row:
                value, expires_at = row
                if datetime.now(MOSCOW_TZ).isoformat() < expires_at:
                    return json.loads(value)
            return None

    def cleanup_cache(self) -> int:
        """Очистка просроченного кэша"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cache WHERE expires_at < ?", (datetime.now(MOSCOW_TZ).isoformat(),))
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted > 0:
                debug(f"🧹 Очищено {deleted} просроченных записей кэша")
            return deleted

    # ========== ЧЁРНЫЙ СПИСОК ==========

    def add_to_blacklist(self, figi: str, ticker: str, reason: str, minutes: int = 60) -> None:
        """Добавление в чёрный список"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            expires_at = (datetime.now(MOSCOW_TZ) + timedelta(minutes=minutes)).isoformat()
            cursor.execute("""
                INSERT OR REPLACE INTO blacklist (figi, ticker, reason, added_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (figi, ticker, reason, datetime.now(MOSCOW_TZ).isoformat(), expires_at))
            conn.commit()
            conn.close()
            info(f"⛔ {ticker} добавлен в чёрный список на {minutes} мин: {reason}")

    def is_blacklisted(self, figi: str) -> Tuple[bool, Optional[str]]:
        """Проверка в чёрном списке"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT reason, expires_at FROM blacklist WHERE figi = ?", (figi,))
            row = cursor.fetchone()
            conn.close()
            if row:
                reason, expires_at = row
                if datetime.now(MOSCOW_TZ).isoformat() < expires_at:
                    return True, reason
            return False, None

    def cleanup_blacklist(self) -> int:
        """Очистка просроченного чёрного списка"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blacklist WHERE expires_at < ?", (datetime.now(MOSCOW_TZ).isoformat(),))
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted > 0:
                debug(f"🧹 Очищено {deleted} записей чёрного списка")
            return deleted

    # ========== АКТИВНЫЕ ЗАЯВКИ ==========

    def save_order(self, order: Dict[str, Any]) -> None:
        """Сохранение заявки"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO orders (order_id, figi, ticker, direction, quantity, price, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.get('order_id'),
                order.get('figi', ''),
                order.get('ticker', ''),
                order.get('direction', ''),
                order.get('quantity', 0),
                order.get('price', 0),
                order.get('status', 'PENDING'),
                order.get('created_at', datetime.now(MOSCOW_TZ).isoformat()),
                datetime.now(MOSCOW_TZ).isoformat()
            ))
            conn.commit()
            conn.close()

    def update_order_status(self, order_id: str, status: str) -> None:
        """Обновление статуса заявки"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?
            """, (status, datetime.now(MOSCOW_TZ).isoformat(), order_id))
            conn.commit()
            conn.close()

    def get_active_orders(self) -> List[Dict[str, Any]]:
        """Получение активных заявок"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE status IN ('PENDING', 'ACTIVE')")
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]

    # ========== ВСПОМОГАТЕЛЬНЫЕ ==========

    def cleanup_otc_positions(self, otc_figis: List[str]) -> int:
        """
        Удаление OTC-позиций из базы данных

        Args:
            otc_figis: Список FIGI OTC-инструментов

        Returns:
            int: Количество удалённых записей
        """
        if not otc_figis:
            return 0

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Удаляем дубликаты из списка
            unique_figis = list(set(otc_figis))

            # Создаём placeholders для SQL запроса
            placeholders = ','.join(['?'] * len(unique_figis))

            deleted_positions = 0
            deleted_orders = 0

            try:
                # Удаляем из таблицы positions
                cursor.execute(f"DELETE FROM positions WHERE figi IN ({placeholders})", unique_figis)
                deleted_positions = cursor.rowcount

                # Удаляем связанные заявки из orders (опционально)
                cursor.execute(f"DELETE FROM orders WHERE figi IN ({placeholders})", unique_figis)
                deleted_orders = cursor.rowcount

                # Удаляем из кэша (опционально)
                cursor.execute(f"DELETE FROM cache WHERE key LIKE '%{unique_figis[0]}%' OR key IN ({placeholders})",
                               unique_figis)

                conn.commit()

                if deleted_positions > 0:
                    info(f"🧹 Очищено {deleted_positions} OTC-позиций и {deleted_orders} заявок из БД")

                return deleted_positions

            except Exception as e:
                error(f"❌ Ошибка очистки OTC позиций: {e}")
                conn.rollback()
                return 0
            finally:
                conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Статистика базы данных"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM positions")
            positions_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM trades")
            trades_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM blacklist")
            blacklist_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM cache")
            cache_count = cursor.fetchone()[0]

            conn.close()

            return {
                'positions': positions_count,
                'trades': trades_count,
                'blacklist': blacklist_count,
                'cache': cache_count,
                'db_file': self.db_path
            }

    def vacuum(self) -> None:
        """Оптимизация базы данных"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("VACUUM")
            conn.close()
            info("🗜️ База данных оптимизирована (VACUUM)")

    def close(self) -> None:
        """Закрытие соединения"""
        self.vacuum()
        info("✅ DatabaseManager закрыт")