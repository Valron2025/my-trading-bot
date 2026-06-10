#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_sessions.py - МАРКЕТ СЕССИИ
Реализация отображения торговых сессий из Pine Script
"""

from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

MOSCOW_TZ = timezone(timedelta(hours=3))


class MarketSession(Enum):
    """Торговые сессии"""
    LONDON = "London"
    NEW_YORK = "NewYork"
    TOKYO = "Tokyo"
    HONG_KONG = "HongKong"
    SYDNEY = "Sydney"
    EU_BRINKS = "EU Brinks"
    US_BRINKS = "US Brinks"


@dataclass
class SessionInfo:
    """Информация о сессии"""
    name: str
    start_utc: dt_time
    end_utc: dt_time
    color: str
    is_active: bool = False
    start_msk: Optional[dt_time] = None
    end_msk: Optional[dt_time] = None


class MarketSessions:
    """
    Управление торговыми сессиями
    Конвертация UTC в МСК (UTC+3)
    """

    # Сессии в UTC
    SESSIONS_UTC = {
        MarketSession.LONDON: (dt_time(8, 0), dt_time(16, 30)),
        MarketSession.NEW_YORK: (dt_time(14, 30), dt_time(21, 0)),
        MarketSession.TOKYO: (dt_time(0, 0), dt_time(6, 0)),
        MarketSession.HONG_KONG: (dt_time(1, 30), dt_time(8, 0)),
        MarketSession.SYDNEY: (dt_time(22, 0), dt_time(6, 0)),
        MarketSession.EU_BRINKS: (dt_time(8, 0), dt_time(9, 0)),
        MarketSession.US_BRINKS: (dt_time(14, 0), dt_time(15, 0)),
    }

    # Цвета сессий
    SESSION_COLORS = {
        MarketSession.LONDON: "#787b86",
        MarketSession.NEW_YORK: "#fb565b",
        MarketSession.TOKYO: "#50ae55",
        MarketSession.HONG_KONG: "#807f17",
        MarketSession.SYDNEY: "#25e47b",
        MarketSession.EU_BRINKS: "#ffffff",
        MarketSession.US_BRINKS: "#ffffff",
    }

    def __init__(self):
        self._active_sessions: List[SessionInfo] = []
        self._update_active_sessions()

    def _utc_to_msk(self, utc_time: dt_time) -> dt_time:
        """Конвертация UTC в МСК (UTC+3)"""
        # Создаём datetime для конвертации
        now = datetime.now(MOSCOW_TZ)
        utc_datetime = datetime.combine(now.date(), utc_time)
        # Добавляем 3 часа для МСК
        msk_datetime = utc_datetime + timedelta(hours=3)
        return msk_datetime.time()

    def _update_active_sessions(self):
        """Обновление списка активных сессий"""
        now_utc = datetime.utcnow().time()
        self._active_sessions = []

        for session, (start_utc, end_utc) in self.SESSIONS_UTC.items():
            is_active = start_utc <= now_utc <= end_utc

            session_info = SessionInfo(
                name=session.value,
                start_utc=start_utc,
                end_utc=end_utc,
                color=self.SESSION_COLORS[session],
                is_active=is_active,
                start_msk=self._utc_to_msk(start_utc),
                end_msk=self._utc_to_msk(end_utc)
            )
            self._active_sessions.append(session_info)

    def get_active_sessions(self) -> List[SessionInfo]:
        """Получение активных сессий"""
        self._update_active_sessions()
        return [s for s in self._active_sessions if s.is_active]

    def get_all_sessions(self) -> List[SessionInfo]:
        """Получение всех сессий"""
        self._update_active_sessions()
        return self._active_sessions.copy()

    def get_current_session_name(self) -> Optional[str]:
        """Получение названия текущей активной сессии"""
        active = self.get_active_sessions()
        if active:
            return active[0].name
        return None

    def is_session_active(self, session_name: str) -> bool:
        """Проверка, активна ли указанная сессия"""
        active = self.get_active_sessions()
        return any(s.name == session_name for s in active)

    def get_session_info(self, session_name: str) -> Optional[SessionInfo]:
        """Получение информации о сессии"""
        for s in self.get_all_sessions():
            if s.name == session_name:
                return s
        return None

    def get_sessions_summary(self) -> str:
        """Получение сводки по сессиям для отображения"""
        lines = ["📊 ТОРГОВЫЕ СЕССИИ (МСК):"]
        for session in self.get_all_sessions():
            status = "🟢" if session.is_active else "⚪"
            lines.append(
                f"  {status} {session.name}: "
                f"{session.start_msk.strftime('%H:%M')} - {session.end_msk.strftime('%H:%M')}"
            )
        return "\n".join(lines)


# Глобальный экземпляр
market_sessions = MarketSessions()