# Торговый цикл бота (подробно)

## 1. Запуск бота (`trading_bot.start()`)

def start(self):
    # 1.1 Проверка токена
    if not config.tbank_token:
        error("❌ TBANK_TOKEN не найден!")
        return
    
    # 1.2 Получение начального баланса
    available, total, margin = tbank.get_available_funds()
    
    # 1.3 Проверка статуса инвестора
    is_qualified, tariff = tbank.check_qual_status()
    
    # 1.4 Загрузка оптимизированных параметров
    self._load_all_optimized_params()
    
    # 1.5 Диагностика портфеля
    self._diagnose_portfolio()
    
    # 1.6 Синхронизация существующих позиций
    self.sync_existing_positions()
    
    # 1.7 Адаптивная настройка параметров
    self._adaptive_configuration(total)
    
    # 1.8 Отправка сообщения о запуске в Telegram
    telegram.send_startup(total, config.min_trade_amount, config.stop_loss_pct)
    
    # 1.9 Запуск главного цикла
    self._trading_loop()

##  2. Главный цикл (_trading_loop)

def _trading_loop(self):
    while self._running:
        # 2.1 Синхронизация с брокером
        position_manager.sync_with_broker()
        
        # 2.2 Получение текущего баланса
        available, total, _ = tbank.get_available_funds()
        
        # 2.3 Определение торговой сессии
        is_active, session = self._get_trading_session()
        
        if not is_active:
            time.sleep(config.adaptive_cycle_seconds)
            continue
        
        # 2.4 Время до конца сессии
        minutes_left, session = self._get_minutes_to_session_end()
        
        # 2.5 Принудительное закрытие перед клирингом
        if self._should_close_positions(minutes_left, session):
            self._close_all_positions_forced(session, minutes_left)
            time.sleep(5)
            continue
        
        # 2.6 Проверка высокой маржи
        if self.check_and_close_if_margin_high():
            time.sleep(5)
            continue
        
        # 2.7 Критическая маржа (>85%) - аварийное закрытие
        if margin_rate >= 85:
            self.emergency_close_all_positions()
            time.sleep(10)
            continue
        
        # 2.8 Проверка платы за перенос
        self.check_margin_fee_warning()
        
        # 2.9 Управление открытыми позициями
        position_manager.check_all_positions()
        
        # 2.10 Поиск и открытие новых позиций
        positions_count = len(position_manager.get_all_positions())
        if positions_count < config.max_positions:
            self._find_and_open_positions(total, available, ...)
        
        # 2.11 Пауза до следующего цикла
        time.sleep(config.adaptive_cycle_seconds)

## 3. Поиск новых позиций (_find_and_open_positions)

def _find_and_open_positions(self, total_capital, available_funds, ...):
    # 3.1 Проверка таймаута (90 секунд)
    timer = threading.Timer(90.0, timeout_handler)
    
    # 3.2 Проверка SHORT доступности
    short_enabled = total_capital >= 500
    
    # 3.3 Фильтрация акций
    stocks = self._get_available_stocks(available_funds)
    
    # 3.4 Открытие лучших кандидатов
    for stock in stocks[:remaining_slots]:
        # 3.4.1 Проверка возможности открытия по времени
        if not self._can_open_position(stock, minutes_left, session):
            continue
        
        # 3.4.2 Расчёт размера позиции
        quantity = self._calculate_position_size(stock, available_funds)
        
        # 3.4.3 Открытие LONG или SHORT
        if stock.side == OrderSide.LONG:
            self._open_long(stock, quantity)
        else:
            self._open_short(stock, quantity)


## 4. Фильтрация акций (_get_available_stocks)

def _get_available_stocks(self, available_funds):
    # 4.1 Проверка кэша (60 секунд)
    if (now - self._stocks_cache_time) < 60:
        return self._stocks_cache
    
    # 4.2 Получение списка акций
    all_shares = tbank.get_all_shares(limit=1000)
    
    # 4.3 Фильтрация по каждому инструменту
    for stock_data in all_shares:
        # 4.3.1 Фильтр по цене
        if price < min_price_filter or price > max_price_filter:
            continue
        
        # 4.3.2 Фильтр по стоимости лота
        lot_price = price * stock_data['lot']
        if lot_price > max_lot_allowed or lot_price < config.min_trade_amount:
            continue
        
        # 4.3.3 Технический анализ
        analysis = analyzer.analyze_stock(figi, name, ticker)
        
        # 4.3.4 Проверка объёма
        if analysis.volume_ratio < 0.7:
            continue
        
        # 4.3.5 Проверка торгуемости
        if not tbank.is_tradable_automatically(figi):
            continue
        
        # 4.3.6 Определение стороны
        if analysis.score >= config.long_score_threshold:
            side = OrderSide.LONG
        elif analysis.score <= config.short_score_threshold:
            side = OrderSide.SHORT
        else:
            continue
        
        # 4.3.7 Расчёт оценки (rank_score)
        rank_score = abs(analysis.score) * 3 + ...  # RSI, объём, стоимость лота
        
        candidates.append(StockCandidate(...))
    
    # 4.4 Сохранение в кэш
    self._stocks_cache = candidates
    return candidates

## 5. Расчёт размера позиции (_calculate_position_size)
## 5.1 Для LONG
# Без маржи (только свои средства)
max_position_value = available * self.base_pct

# Ограничение по риску
final_pct = max_position_value / total_available * 100

# Проверка закрытия при стопе
required_for_close = quantity * stop_loss_price * 1.10
if required_for_close > available * 0.9:
    quantity = new_quantity  # Уменьшаем

## 5.2 Для SHORT
# Проверка средств для закрытия
if available < MIN_AVAILABLE_FOR_SHORT:  # 1000₽
    return 0

# Проверка времени до конца сессии
if minutes_to_end < 15:
    return 0

# Доступная маржа
available_borrow = max_borrow - current_uncovered

# Не более 50% от маржи и 50% от свободных средств
max_position_value = min(available_borrow, total_available * 0.5, available * 0.5)

# Проверка закрытия при стопе
if required_for_close > available * 0.9:
    return 0

# Если маржа > 70% - уменьшаем размер
if margin_rate > 70:
    max_position_value *= 0.5

# 6. Открытие позиций
# 6.1 LONG (_open_long_market)
def _open_long_market(self, stock, quantity):
    # 6.1.1 Защита от дублирования (30 секунд)
    if elapsed < 30:
        return False
    
    # 6.1.2 Проверка торгов
    if not self._is_trading_allowed(stock.ticker):
        return False
    
    # 6.1.3 Проверка торгового статуса через API
    status = tbank.get_trading_status(stock.figi)
    if not status.get('market_order_available'):
        return False
    
    # 6.1.4 Проверка средств для закрытия при стопе
    required_for_close = quantity * stop_loss_price * 1.05
    if required_for_close > available + available_margin:
        return False
    
    # 6.1.5 Выполнение рыночной заявки
    if tbank.buy(stock.figi, quantity):
        # 6.1.6 Добавление в PositionManager
        position_manager.add_position(stock.figi, quantity, stock.price, OrderSide.LONG)
        
        # 6.1.7 Отправка уведомления с графиком
        telegram.send_trade_opened("LONG", stock.name, quantity, stock.price)
        
        return True

# 6.2 SHORT (_open_short_market)

def _open_short_market(self, stock, quantity):
    # 6.2.1 Проверка маржинальной торговли
    margin_allowed, reason = tbank.check_margin_trading_allowed()
    if not margin_allowed:
        return False
    
    # 6.2.2 Защита от дублирования
    if pending_key in self._short_pending:
        if elapsed < 30:
            return False
    
    # 6.2.3 Проверка свободных средств для закрытия
    if required_for_close > available * 0.9:
        return False
    
    # 6.2.4 Проверка маржи (не более 70%)
    if margin_rate >= 70:
        return False
    
    # 6.2.5 Проверка маржинального лимита
    if entry_value > available_margin * 0.9:
        return False
    
    # 6.2.6 Проверка размера позиции (не более 30% капитала)
    if entry_value > total_available * 0.3:
        return False
    
    # 6.2.7 Выполнение SHORT (продажа)
    if tbank.sell(stock.figi, quantity):
        position_manager.add_position(stock.figi, quantity, stock.price, OrderSide.SHORT)
        telegram.send_trade_opened("SHORT", stock.name, quantity, stock.price)
        return True

# . Управление позициями (position_manager.py)
# 7.1 Проверка LONG позиции

def manage_long_position(self, position, current_price):
    profit_pct = (current_price - position.avg_price) / position.avg_price * 100
    
    # 7.1.1 Установка стоп-приказов (при входе)
    if not position.stop_order_placed:
        tbank.place_stop_loss_order(position.figi, position.quantity, stop_loss_price, "LONG")
        tbank.place_take_profit_order(position.figi, position.quantity, take_profit_price, "LONG")
    
    # 7.1.2 Эмуляция стоп-приказов (если не установлены)
    if profit_pct >= take_profit_trigger:
        self._close_position(position, current_price, "тейк-профит", profit_pct)
        return True
    
    if profit_pct <= stop_loss_trigger:
        self._close_position(position, current_price, "стоп-лосс", profit_pct)
        return True
    
    # 7.1.3 Трейлинг-стоп (при активации)
    if profit_pct > activation_threshold:
        trailing_stop_price = highest_price * (1 - dynamic_trailing / 100)
        if current_price <= trailing_stop_price:
            self._close_position(position, current_price, "трейлинг-стоп", profit_pct)
            return True
    
    # 7.1.4 Таймаут
    if hold_minutes >= max_hold:
        self._close_position(position, current_price, "таймаут", profit_pct)
        return True

# 7.2 Проверка SHORT позиции

def manage_short_position(self, position, current_price):
    profit_pct = (position.avg_price - current_price) / position.avg_price * 100
    
    # 7.2.1 Расчёт овернайт-комиссии
    overnight_fee = self._calculate_overnight_fee(position)
    
    # 7.2.2 Если комиссия > 50% прибыли - закрываем
    if overnight_fee > position.current_profit_amount(current_price) * 0.5:
        self._close_position(position, current_price, "высокая комиссия овернайт", profit_pct)
        return True
    
    # 7.2.3 Эмуляция стоп-приказов
    if profit_pct >= take_profit_pct_with_buffer:
        self._close_position(position, current_price, "тейк-профит SHORT", profit_pct)
        return True
    
    if profit_pct <= -stop_loss_pct_with_buffer:
        self._close_position(position, current_price, "стоп-лосс SHORT", profit_pct)
        return True
    
    # 7.2.4 Трейлинг-стоп для SHORT
    if profit_pct > activation_threshold:
        trailing_stop_price = lowest_price * (1 + dynamic_trailing / 100)
        if current_price >= trailing_stop_price:
            self._close_position(position, current_price, "трейлинг-стоп SHORT", profit_pct)
            return True
    
    # 7.2.5 Таймаут (увеличен для SHORT в 2 раза)
    if hold_minutes >= max_hold * 2:
        self._close_position(position, current_price, "таймаут SHORT", profit_pct)
        return True

# 8. Временные параметры
Параметр	Значение	Описание
adaptive_cycle_seconds	8-15 сек	Пауза между циклами
adaptive_timeout_minutes	8-20 мин	Таймаут позиции
price_cache_ttl	5 сек	Время жизни кэша цен
stocks_cache_time	60 сек	Время жизни кэша акций
temp_blacklist_duration_minutes	10 мин	Блокировка после ошибки
validation_cache_hours	24 час	Кэш валидации
_auto_close_minutes_before	5 мин	Авто-закрытие до клиринга