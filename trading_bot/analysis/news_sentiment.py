#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
News Sentiment Analyzer - Анализ новостного фона
- Сбор новостей по тикерам
- Оценка тональности (положительная/отрицательная)
- Влияние на торговые сигналы
"""

import re
import json
import asyncio
import aiohttp
import socket
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

from trading_bot.logger import info, success, error, warning, debug
from trading_bot.cache.unified_cache import UnifiedCache, USE_UNIFIED_CACHE
from trading_bot.utils.time_utils import get_moscow_time


@dataclass
class NewsItem:
    """Новостная запись"""
    ticker: str
    title: str
    content: str
    source: str
    timestamp: datetime
    sentiment_score: float = 0.0
    relevance: float = 0.0
    impact: int = 0


@dataclass
class SentimentResult:
    """Результат сентимент-анализа"""
    ticker: str
    overall_score: float
    positive_count: int
    negative_count: int
    neutral_count: int
    recent_news: List[NewsItem]
    impact_on_score: int
    confidence: float


class NewsSentimentAnalyzer:
    """Анализатор новостного фона"""

    SENTIMENT_WORDS = {
        'positive': {
            'рост', 'вырос', 'увеличился', 'поднялся', 'прибыль', 'доход',
            'рекорд', 'максимум', 'успех', 'успешный', 'прорыв', 'дивиденды',
            'контракт', 'сделка', 'партнёрство', 'инвестиции', 'покупка',
            'слияние', 'поглощение', 'рекомендация', 'покупать', 'buy',
            'upgrade', 'outperform', 'overweight', 'positive', 'growth',
            'profit', 'record', 'success', 'breakthrough', 'dividend',
        },
        'negative': {
            'падение', 'упал', 'снизился', 'уменьшился', 'убыток', 'долг',
            'антирекорд', 'минимум', 'проблема', 'кризис', 'санкции',
            'иск', 'расследование', 'штраф', 'потеря', 'увольнение',
            'снижение', 'продавать', 'sell', 'downgrade', 'underperform',
            'underweight', 'negative', 'loss', 'debt', 'crisis', 'sanctions',
        }
    }

    SOURCE_WEIGHTS = {
        'tbank': 1.0,
        'moex': 1.0,
        'rbc': 0.9,
        'interfax': 0.9,
        'tass': 0.8,
        'other': 0.5
    }

    def __init__(self, cache_ttl_minutes: int = 30, max_news_per_ticker: int = 50):
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.max_news_per_ticker = max_news_per_ticker

        # ← КЛЮЧЕВЫЕ ФЛАГИ
        self.enabled = True
        self.max_impact = 5

        self.news_cache: Dict[str, deque] = {}
        self.last_update: Dict[str, datetime] = {}
        if USE_UNIFIED_CACHE:
            self._unified_cache = UnifiedCache(default_ttl=1800, name="news_sentiment")

        self.stats = {
            'total_requests': 0,
            'cache_hits': 0,
            'api_errors': 0
        }

        # Проверяем доступность API
        self._api_available = None
        self._check_api_availability()

        info("📰 NewsSentimentAnalyzer инициализирован")
        info(f"   📦 TTL кэша: {cache_ttl_minutes} мин")
        info(f"   ⚡ Макс. влияние: ±{self.max_impact}")
        info(f"   🔧 enabled: {self.enabled}")
        info(f"   🌐 API доступен: {self._api_available}")

    def _check_api_availability(self):
        """Проверка доступности Telegram API"""
        try:
            socket.create_connection(("api.telegram.org", 443), timeout=2)
            self._api_available = True
        except:
            self._api_available = False
            warning("⚠️ Telegram API недоступен, новостной анализ отключён")
            self.enabled = False

    async def analyze_ticker(self, ticker: str, hours_back: int = 24) -> Optional[SentimentResult]:
        """Полный сентимент-анализ для тикера с проверкой enabled"""
        # ← ГЛАВНАЯ ПРОВЕРКА
        if not self.enabled:
            debug(f"📰 Новостной анализ отключён для {ticker}")
            return None

        info(f"📰 Анализ новостей для {ticker}...")

        # Собираем новости с таймаутом
        try:
            async with asyncio.timeout(10.0):
                news_items = await self.fetch_news(ticker, hours_back)
        except asyncio.TimeoutError:
            debug(f"⏰ Таймаут сбора новостей для {ticker}")
            return None
        except Exception as e:
            debug(f"❌ Ошибка сбора новостей для {ticker}: {e}")
            return None

        if not news_items:
            debug(f"⚠️ Нет новостей для {ticker}")
            info(f"📰 {ticker}: новости не найдены (impact=0)")
            return None

        info(f"📰 {ticker}: найдено {len(news_items)} новостей")

        # Анализируем каждую новость
        for news in news_items:
            sentiment, relevance = self.analyze_sentiment(news.title + " " + news.content)
            news.sentiment_score = sentiment
            news.relevance = relevance
            news.impact = self.calculate_impact(sentiment, relevance)

        # Агрегируем результаты
        positive = [n for n in news_items if n.sentiment_score > 0.2]
        negative = [n for n in news_items if n.sentiment_score < -0.2]
        neutral = [n for n in news_items if -0.2 <= n.sentiment_score <= 0.2]

        # Взвешенная средняя тональность
        total_weight = sum(n.relevance for n in news_items)
        if total_weight > 0:
            overall = sum(n.sentiment_score * n.relevance for n in news_items) / total_weight
        else:
            overall = 0

        # Общее влияние на score
        total_impact = sum(n.impact for n in news_items)
        total_impact = max(-self.max_impact, min(self.max_impact, total_impact))

        if abs(total_impact) >= 1:
            direction = "🟢 ПОЗИТИВНЫЙ" if total_impact > 0 else "🔴 НЕГАТИВНЫЙ"
            info(
                f"📰 {ticker}: {direction} новостной фон (impact={total_impact:+d}, позитивных={len(positive)}, негативных={len(negative)}, нейтральных={len(neutral)})")
        else:
            info(f"📰 {ticker}: 🟡 НЕЙТРАЛЬНЫЙ новостной фон (impact={total_impact:+d})")

        # Уверенность
        confidence = min(1, len(news_items) / 20) * min(1, total_weight / 10)

        result = SentimentResult(
            ticker=ticker,
            overall_score=overall,
            positive_count=len(positive),
            negative_count=len(negative),
            neutral_count=len(neutral),
            recent_news=news_items[:5],
            impact_on_score=total_impact,
            confidence=confidence
        )

        if abs(total_impact) >= 3:
            direction = "🟢 ПОЗИТИВНЫЙ" if total_impact > 0 else "🔴 НЕГАТИВНЫЙ"
            info(f"📰 {ticker}: {direction} новостной фон (impact={total_impact:+d})")

        return result

    async def analyze_ticker_news(self, figi: str, ticker: str) -> Optional[Dict]:
        """Анализ новостей для тикера с проверкой enabled"""
        if not self.enabled:
            return {'sentiment_score': 0, 'headlines': [], 'impact': 0}

        sentiment = await self.analyze_ticker(ticker)

        if not sentiment or sentiment.confidence < 0.3:
            return {'sentiment_score': 0, 'headlines': [], 'impact': 0}

        return {
            'sentiment_score': max(-self.max_impact, min(self.max_impact, sentiment.impact_on_score)),
            'headlines': [n.title[:100] for n in sentiment.recent_news[:3]],
            'impact': sentiment.impact_on_score,
            'confidence': sentiment.confidence,
            'positive_count': sentiment.positive_count,
            'negative_count': sentiment.negative_count
        }

    async def fetch_news(self, ticker: str, hours_back: int = 24) -> List[NewsItem]:
        """
        Сбор новостей для тикера из российских и международных источников
        Источники: РБК, Интерфакс, ТАСС, Коммерсантъ, Финам, Московская биржа,
                   Google News (RU/EN), Яндекс.Новости
        """
        import aiohttp
        import asyncio

        if not self.enabled or not self._api_available:
            return []

        # Проверка кэша
        now = get_moscow_time()
        if ticker in self.last_update:
            if now - self.last_update[ticker] < self.cache_ttl:
                self.stats['cache_hits'] += 1
                return list(self.news_cache.get(ticker, []))

        self.stats['total_requests'] += 1
        info(f"🔍 Сбор новостей для {ticker}...")

        news_items = []

        # ========================================================================
        # 1. РОССИЙСКИЕ RSS-ИСТОЧНИКИ (финансовые новости)
        # ========================================================================
        russian_sources = [
            {
                "name": "rbc",
                "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
                "need_filter": True
            },
            {
                "name": "interfax",
                "url": "https://www.interfax.ru/rss.asp",
                "need_filter": True
            },
            {
                "name": "tass",
                "url": "https://tass.ru/rss/v2/economy.rss",
                "need_filter": True
            },
            {
                "name": "kommersant",
                "url": "https://www.kommersant.ru/RSS/news.xml",
                "need_filter": True
            },
            {
                "name": "finam",
                "url": "https://www.finam.ru/analysis/conews/rsspoint",
                "need_filter": True
            },
            {
                "name": "moex",
                "url": "https://www.moex.com/export/rss/news/",
                "need_filter": True
            },
            {
                "name": "smartlab",
                "url": "https://smart-lab.ru/rss/news/",
                "need_filter": True
            }
        ]

        for source in russian_sources:
            try:
                async with asyncio.timeout(8.0):  # ✅ УВЕЛИЧЕНО: 5.0 → 8.0
                    async with aiohttp.ClientSession() as session:
                        async with session.get(source["url"],
                                               timeout=aiohttp.ClientTimeout(total=8)) as resp:  # ✅ 5 → 8
                            if resp.status == 200:
                                text = await resp.text()
                                items = self._parse_rss(text, ticker, source["name"])

                                # Фильтруем новости, содержащие тикер
                                if source["need_filter"] and items:
                                    filtered = [
                                        i for i in items
                                        if (ticker.upper() in i.title.upper() or
                                            ticker.lower() in i.title.lower())
                                    ]
                                    news_items.extend(filtered[:5])
                                else:
                                    news_items.extend(items[:5])
                                debug(
                                    f"   ✅ {source['name']}: {len(items)} новостей, релевантных {len(filtered) if source['need_filter'] else len(items[:5])}")
            except asyncio.TimeoutError:
                debug(f"   ⏰ Таймаут {source['name']}")
            except Exception as e:
                debug(f"   ❌ Ошибка {source['name']}: {e}")

        # ========================================================================
        # 2. ПОИСК ПО ТИКЕРУ В НОВОСТЯХ
        # ========================================================================
        search_sources = [
            {
                "name": "google_ru",
                "url": f"https://news.google.com/rss/search?q={ticker}+акции&hl=ru&ceid=RU:ru",
                "need_filter": False
            },
            {
                "name": "google_en",
                "url": f"https://news.google.com/rss/search?q={ticker}+stock&hl=en&ceid=US:en",
                "need_filter": False
            },
            {
                "name": "yandex_news",
                "url": f"https://news.yandex.ru/quotes/{ticker}.rss",
                "need_filter": False
            }
        ]

        for source in search_sources:
            try:
                async with asyncio.timeout(8.0):  # ✅ УВЕЛИЧЕНО: 5.0 → 8.0
                    async with aiohttp.ClientSession() as session:
                        async with session.get(source["url"],
                                               timeout=aiohttp.ClientTimeout(total=8)) as resp:  # ✅ 5 → 8
                            if resp.status == 200:
                                text = await resp.text()
                                items = self._parse_rss(text, ticker, source["name"])
                                news_items.extend(items[:10])
                                debug(f"   ✅ {source['name']}: {len(items)} новостей")
            except asyncio.TimeoutError:
                debug(f"   ⏰ Таймаут {source['name']}")
            except Exception as e:
                debug(f"   ❌ Ошибка {source['name']}: {e}")

        # ========================================================================
        # 3. УДАЛЕНИЕ ДУБЛИКАТОВ И СОРТИРОВКА
        # ========================================================================
        seen_titles = set()
        unique_items = []
        for item in news_items:
            # Нормализуем заголовок для сравнения
            norm_title = item.title.lower().strip()
            # Удаляем источник в скобках [Источник]
            import re
            norm_title = re.sub(r'\[.*?\]', '', norm_title)
            norm_title = re.sub(r'\(.*?\)', '', norm_title)
            norm_title = norm_title[:100]  # Обрезаем для сравнения

            if norm_title not in seen_titles:
                seen_titles.add(norm_title)
                unique_items.append(item)

        # Сортировка по времени (свежие первые)
        unique_items.sort(key=lambda x: x.timestamp, reverse=True)

        # Ограничиваем количество
        unique_items = unique_items[:self.max_news_per_ticker]

        # Обновляем кэш
        self.news_cache[ticker] = deque(unique_items, maxlen=self.max_news_per_ticker)
        self.last_update[ticker] = now

        info(
            f"📰 Для {ticker} собрано {len(unique_items)} уникальных новостей из {len(russian_sources) + len(search_sources)} источников")
        return unique_items

    def analyze_sentiment(self, text: str) -> Tuple[float, float]:
        """Анализ тональности текста"""
        text_lower = text.lower()

        positive_count = 0
        negative_count = 0

        for word in self.SENTIMENT_WORDS['positive']:
            if word in text_lower:
                positive_count += 1

        for word in self.SENTIMENT_WORDS['negative']:
            if word in text_lower:
                negative_count += 1

        total = positive_count + negative_count
        if total == 0:
            sentiment = 0.0
        else:
            sentiment = (positive_count - negative_count) / total

        sentiment = max(-1, min(1, sentiment))
        relevance = min(1, total / 10)

        return sentiment, relevance

    def calculate_impact(self, sentiment: float, relevance: float) -> int:
        """Расчёт влияния на торговый score"""
        impact = sentiment * relevance * self.max_impact
        return int(round(impact))

    async def enhance_signal(self, ticker: str, current_score: int, current_signals: List[str]) -> Tuple[
        int, List[str], Dict[str, Any]]:
        """Усиление торгового сигнала новостным фоном"""
        if not self.enabled:
            return current_score, current_signals, {}

        info(f"📰 Проверка новостей для усиления сигнала {ticker}...")

        sentiment = await self.analyze_ticker(ticker)

        if not sentiment or sentiment.confidence < 0.3:
            debug(f"📰 {ticker}: недостаточно новостей (confidence={sentiment.confidence if sentiment else 0})")
            return current_score, current_signals, {}

        impact = max(-self.max_impact, min(self.max_impact, sentiment.impact_on_score))
        new_score = current_score + impact
        new_signals = current_signals.copy()

        if impact > 0:
            new_signals.append(f"📰 Позитивный новостной фон (+{impact})")
            info(f"📰 {ticker}: ✅ новости УСИЛИВАЮТ сигнал: {current_score} → {new_score} (+{impact})")
        elif impact < 0:
            new_signals.append(f"📰 Негативный новостной фон ({impact})")
            info(f"📰 {ticker}: ❌ новости ОСЛАБЛЯЮТ сигнал: {current_score} → {new_score} ({impact})")
        else:
            info(f"📰 {ticker}: 🟡 новости нейтральны, сигнал не изменился ({current_score})")

        sentiment_data = {
            'overall_score': sentiment.overall_score,
            'positive_news': sentiment.positive_count,
            'negative_news': sentiment.negative_count,
            'impact': impact,
            'confidence': sentiment.confidence,
        }

        return new_score, new_signals, sentiment_data

    def _parse_rss(self, xml_text: str, ticker: str, source: str) -> List[NewsItem]:
        """Парсинг RSS ленты"""
        items = []
        try:
            import xml.etree.ElementTree as ET
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timedelta

            root = ET.fromstring(xml_text)

            for item in root.findall('.//item'):
                title = item.find('title')
                title_text = title.text if title is not None else ""

                pub_date = item.find('pubDate')
                if pub_date and pub_date.text:
                    try:
                        # Парсим время из RSS (обычно UTC)
                        parsed_time = parsedate_to_datetime(pub_date.text)
                        # Приводим к datetime и конвертируем в МСК (UTC+3)
                        if isinstance(parsed_time, datetime):
                            timestamp = parsed_time + timedelta(hours=3)
                        else:
                            timestamp = get_moscow_time()
                    except:
                        timestamp = get_moscow_time()
                else:
                    timestamp = get_moscow_time()

                news = NewsItem(
                    ticker=ticker,
                    title=title_text,
                    content="",
                    source=source,
                    timestamp=timestamp
                )
                items.append(news)
        except Exception as e:
            debug(f"Ошибка парсинга RSS: {e}")

        return items

    def get_stats(self) -> Dict[str, Any]:
        """Статистика анализатора"""
        hit_rate = (self.stats['cache_hits'] / max(1, self.stats['total_requests'])) * 100
        return {
            'enabled': self.enabled,
            'cached_tickers': len(self.news_cache),
            'total_requests': self.stats['total_requests'],
            'cache_hits': self.stats['cache_hits'],
            'hit_rate': hit_rate,
            'api_errors': self.stats['api_errors']
        }


# Глобальный экземпляр
news_sentiment = NewsSentimentAnalyzer()
