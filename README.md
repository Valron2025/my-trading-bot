# 🤖 Торговый бот T-Bank

Автоматический торговый бот для T-Bank Investments с адаптивными настройками, поддержкой LONG/SHORT и Telegram уведомлениями.

## 📋 Оглавление
- [Установка](#установка)
- [Настройка](#настройка)
- [Запуск](#запуск)
- [Функции](#функции)
- [Структура проекта](#структура-проекта)

## 🚀 Установка

### 1. Клонирование репозитория

```bash
git clone https://github.com/Valron2025/my-trading-bot.git
cd my-trading-bot
```

### 2. Создание виртуального окружения

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate
```

### 3. Установка зависимостей

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

⚙️ Настройка
1. Создайте файл .env из примера:
```bash
copy .env.example .env
```

2. Заполните .env:
# T-Bank API (обязательно)
TBANK_TOKEN=t.MDz_ваш_токен_здесь

# Telegram (опционально)
TELEGRAM_TOKEN=ваш_токен_бота
TELEGRAM_CHAT_ID=ваш_chat_id

# Режим отладки
DEBUG=false

3. Получите токен T-Bank API:
Войдите в T-Bank Investments API

Создайте токен в личном кабинете

▶️ Запуск
# Основной запуск
```bash
python run.py
```

# Или через main.py
```bash
python main.py
```

# Для бэктеста
```bash
python backtest_runner.py GAZP 90
```

# Для оптимизации параметров
```bash
python backtest_runner.py GAZP 90 optimize
```

📊 Функции
Функция	Описание
🔄 Адаптивная настройка	Автоматический подбор параметров под рынок
📈 LONG торговля	Покупка акций, трендовые сигналы
📉 SHORT торговля	Продажа акций (при капитале > 2000₽)
🛑 Стоп-лосс	Автоматическая защита от убытков
🎯 Трейлинг-стоп	Фиксация прибыли при росте
⏰ Таймаут позиций	Автоматическое закрытие по времени
📱 Telegram уведомления	Графики, статус, уведомления о сделках
📊 Бэктестинг	Проверка стратегий на истории
🔧 Мониторинг маржи	Контроль использования маржинальной торговли

📁 Структура проекта
my-trading-bot/
├── run.py                 # Точка входа для Render
├── main.py                # Альтернативная точка входа
├── trading_bot.py         # Основной класс бота
├── tbank_client.py        # Клиент T-Bank API
├── market_checker.py      # Проверка торговых сессий
├── market_analyzer.py     # Анализ рыночных условий
├── position_manager.py    # Управление позициями
├── strategy_engine.py     # Движок стратегии (LONG/SHORT)
├── technical_analyzer.py  # Технический анализ
├── backtest.py            # Бэктестер
├── config.py              # Конфигурация
├── logger.py              # Логирование
├── telegram_notifier.py   # Telegram уведомления
├── telegram_bot.py        # Telegram меню
├── telegram_menu.py       # Динамическое меню
├── candle_builder.py      # Построитель свечей
├── candle_sync_wrapper.py # Синхронная обёртка
├── moex_client.py         # MOEX API клиент
├── moex_sync_fetcher.py   # Синхронный MOEX
├── ttl_cache.py           # Кэш с TTL
├── models.py              # Модели данных
├── requirements.txt       # Зависимости
├── .env.example           # Пример конфигурации
└── README.md              # Документация

🔧 Требования
Python 3.11+

Аккаунт T-Bank Investments

Токен API T-Bank

5000+ ₽ на счёте (для активной торговли)

⚠️ Важно
Тестируйте на демо-счёте перед реальной торговлей

Маржинальная торговля требует статуса квалифицированного инвестора

SHORT отключается при капитале < 2000₽

Автоматическое закрытие позиций за 15 мин до клиринга

📄 Лицензия
MIT License

👨‍💻 Автор
Valron2025