# trading_bot/analysis/fundamental_updater.py
# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fundamental_updater.py - Автоматическое обновление фундаментальных данных
Ежедневное обновление мультипликаторов, сохранение истории
"""

import asyncio
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any
import aiohttp

from trading_bot.analysis.fundamental_analyzer import FundamentalAnalyzer, FundamentalMetrics
from trading_bot.analysis.fundamental_db import FundamentalDatabase
from trading_bot.logger import info, success, error, debug, sep


class FundamentalUpdater:
    """Автоматическое обновление фундаментальных данных"""

    def __init__(self, db: FundamentalDatabase = None, analyzer: FundamentalAnalyzer = None):
        self.db = db or FundamentalDatabase()
        self.analyzer = analyzer or FundamentalAnalyzer()

        # Базовый список отслеживаемых тикеров (будет расширяться динамически)
        self.base_watchlist = ["SBER", "GAZP", "LKOH", "ROSN", "TATN", "NVTK", "MGNT"]

        # Обновление раз в день (по умолчанию)
        self.update_interval_hours = 24

        # Статистика
        self.stats = {
            'total_updates': 0,
            'successful_updates': 0,
            'failed_updates': 0,
            'last_update': None
        }

    def _get_dynamic_watchlist(self) -> List[str]:
        """Динамическое получение watchlist из БД"""
        try:
            # Получаем тикеры из current_multipliers (активные)
            watchlist = list(self.db.get_all_current_multipliers().keys())

            # Если в БД ещё нет данных, используем базовый список
            if not watchlist:
                watchlist = self.base_watchlist.copy()

            return sorted(watchlist)
        except Exception as e:
            debug(f"Ошибка получения динамического watchlist: {e}")
            return self.base_watchlist.copy()

    async def update_ticker(self, ticker: str) -> Optional[FundamentalMetrics]:
        """Обновление данных для одного тикера"""
        try:
            # Получаем свежие данные
            metrics = await self.analyzer.fetch_metrics(ticker)

            if metrics:
                # Сохраняем в БД
                self.db.save_metrics(ticker, metrics, source="auto_update")

                success(f"🔄 Обновлён {ticker}: Score={metrics.overall_score:.0f}, P/E={metrics.pe_ratio:.1f}")
                return metrics
            else:
                error(f"❌ Не удалось обновить {ticker}")
                return None

        except Exception as e:
            error(f"❌ Ошибка обновления {ticker}: {e}")
            return None

    async def update_all(self, tickers: List[str] = None) -> Dict[str, Optional[FundamentalMetrics]]:
        """Обновление всех тикеров"""
        # Если tickers не передан, используем динамический watchlist
        if tickers is None:
            tickers = self._get_dynamic_watchlist()

        sep("=")
        info(f"🔄 НАЧАЛО ОБНОВЛЕНИЯ ФУНДАМЕНТАЛЬНЫХ ДАННЫХ ({len(tickers)} тикеров)")
        sep("=")

        results = {}
        successful = 0

        for ticker in tickers:
            metrics = await self.update_ticker(ticker)
            results[ticker] = metrics
            if metrics:
                successful += 1

            # Небольшая задержка между запросами
            await asyncio.sleep(0.5)

        # Обновляем статистику
        self.stats['total_updates'] += 1
        self.stats['successful_updates'] = successful
        self.stats['failed_updates'] = len(tickers) - successful
        self.stats['last_update'] = datetime.now()

        sep("=")
        success(f"✅ ОБНОВЛЕНИЕ ЗАВЕРШЕНО: {successful}/{len(tickers)} успешно")
        sep("=")

        # Обновляем секторные мультипликаторы на основе реальных данных
        await self._update_sector_multipliers(results)

        return results

    async def _update_sector_multipliers(self, results: Dict[str, Optional[FundamentalMetrics]]):
        """Обновление секторных мультипликаторов на основе реальных данных"""
        sector_data = {
            'bank': {'tickers': ['SBER'], 'multipliers': {'pe': [], 'pb': [], 'roe': [], 'dividend_yield': []}},
            'oil': {'tickers': ['LKOH', 'ROSN', 'TATN'],
                    'multipliers': {'pe': [], 'pb': [], 'roe': [], 'dividend_yield': []}},
            'gas': {'tickers': ['GAZP', 'NVTK'], 'multipliers': {'pe': [], 'pb': [], 'roe': [], 'dividend_yield': []}},
        }

        for sector, data in sector_data.items():
            for ticker in data['tickers']:
                metrics = results.get(ticker)
                if metrics and metrics.pe_ratio > 0:
                    data['multipliers']['pe'].append(metrics.pe_ratio)
                    data['multipliers']['pb'].append(metrics.pb_ratio)
                    data['multipliers']['roe'].append(metrics.roe)
                    data['multipliers']['dividend_yield'].append(metrics.dividend_yield)

            # Вычисляем средние значения
            if data['multipliers']['pe']:
                avg_multipliers = {
                    'pe': sum(data['multipliers']['pe']) / len(data['multipliers']['pe']),
                    'pb': sum(data['multipliers']['pb']) / len(data['multipliers']['pb']),
                    'roe': sum(data['multipliers']['roe']) / len(data['multipliers']['roe']),
                    'dividend_yield': sum(data['multipliers']['dividend_yield']) / len(
                        data['multipliers']['dividend_yield']),
                    'payout_ratio': 45.0
                }

                self.db.update_sector_multipliers(sector, avg_multipliers)
                debug(f"📊 Обновлены мультипликаторы сектора {sector}: P/E={avg_multipliers['pe']:.1f}")

    async def run_daily_update(self):
        """Ежедневное обновление (запускать по расписанию)"""
        info("⏰ Запуск ежедневного обновления фундаментальных данных")

        # Получаем устаревшие тикеры
        stale_tickers = self.db.get_stale_tickers()

        if stale_tickers:
            info(f"📋 Найдено устаревших тикеров: {len(stale_tickers)}")
            await self.update_all(stale_tickers)
        else:
            info("✅ Все тикеры актуальны, обновление не требуется")

    def get_update_report(self) -> Dict[str, Any]:
        """Получение отчёта об обновлениях"""
        watchlist = self._get_dynamic_watchlist()
        report = {
            'stats': self.stats,
            'watchlist_size': len(watchlist),
            'watchlist': watchlist,
            'current_multipliers': self.db.get_all_current_multipliers(),
            'trends': {}
        }

        # Добавляем тренды для каждого тикера
        for ticker in watchlist:
            report['trends'][ticker] = self.db.get_trend(ticker)

        return report

    def print_report(self):
        """Вывод отчёта в консоль"""
        sep("=")
        info("📊 ОТЧЁТ ПО ФУНДАМЕНТАЛЬНЫМ ДАННЫМ")
        sep("=")

        # Статистика обновлений
        info(f"🔄 Статистика обновлений:")
        info(f"   Всего обновлений: {self.stats['total_updates']}")
        info(f"   Успешных: {self.stats['successful_updates']}")
        info(f"   Ошибок: {self.stats['failed_updates']}")
        if self.stats['last_update']:
            info(f"   Последнее обновление: {self.stats['last_update'].strftime('%Y-%m-%d %H:%M:%S')}")

        # Текущие мультипликаторы
        sep("-")
        info("📈 ТЕКУЩИЕ МУЛЬТИПЛИКАТОРЫ:")
        sep("-")

        current = self.db.get_all_current_multipliers()
        if current:
            for ticker, data in current.items():
                pe = data.get('pe_ratio', 0)
                pb = data.get('pb_ratio', 0)
                roe = data.get('roe', 0)
                div = data.get('dividend_yield', 0)
                print(f"   {ticker}: P/E={pe:.1f} | P/B={pb:.2f} | ROE={roe:.0f}% | Див={div:.1f}%")
        else:
            info("   Нет данных в БД")

        # Тренды
        sep("-")
        info("📊 ТРЕНДЫ ФУНДАМЕНТАЛЬНЫХ ПОКАЗАТЕЛЕЙ:")
        sep("-")

        watchlist = self._get_dynamic_watchlist()
        has_trends = False
        for ticker in watchlist:
            trend = self.db.get_trend(ticker)
            if trend.get('trend') != 'insufficient_data' and trend.get('trend'):
                has_trends = True
                arrow = "📈" if trend['trend'] == 'improving' else "📉" if trend['trend'] == 'deteriorating' else "➡️"
                print(f"   {ticker}: {arrow} {trend['trend']} (изменение: {trend.get('change_pct', 0):+.1f}%)")

        if not has_trends:
            info("   Недостаточно данных для анализа трендов")

        sep("=")

    async def start_scheduler(self, interval_hours: int = 24):
        """Запуск планировщика обновлений"""
        info(f"🕐 Запуск планировщика фундаментальных данных (интервал: {interval_hours}ч)")

        while True:
            try:
                await self.run_daily_update()

                # Ждём до следующего обновления
                info(f"💤 Следующее обновление через {interval_hours} часов")
                await asyncio.sleep(interval_hours * 3600)

            except Exception as e:
                error(f"❌ Ошибка в планировщике: {e}")
                await asyncio.sleep(3600)  # При ошибке ждём час


# Тестирование
async def test_fundamental_updater():
    """Тест обновления фундаментальных данных"""
    sep("=")
    info("🧪 ТЕСТ ОБНОВЛЕНИЯ ФУНДАМЕНТАЛЬНЫХ ДАННЫХ")
    sep("=")

    updater = FundamentalUpdater()

    # Обновляем все тикеры
    results = await updater.update_all()

    # Выводим отчёт
    updater.print_report()

    # Проверяем историю
    sep("-")
    info("📜 ПРИМЕР ИСТОРИИ ДЛЯ SBER:")
    sep("-")

    history = updater.db.get_history("SBER", days=30)
    if history:
        for record in history[:5]:  # Последние 5 записей
            print(f"   {record['fetched_date']}: Score={record['overall_score']:.0f}, P/E={record['pe_ratio']:.1f}")
    else:
        info("   Нет исторических данных")

    sep("=")
    success("✅ Тест завершён")


if __name__ == "__main__":
    asyncio.run(test_fundamental_updater())