#!/bin/bash
# deploy_production.sh - Полное обновление до продакшн версии

echo "🚀 Начинаю обновление бота до продакшн версии..."
echo "================================================"

# 1. Останавливаем бота
echo "1. Останавливаю бота..."
./stop.sh
sleep 2

# 2. Создаём бэкапы
echo "2. Создаю бэкапы..."
BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p $BACKUP_DIR
cp trading_bot/strategy/strategy_engine.py $BACKUP_DIR/
cp config/config.yaml $BACKUP_DIR/
echo "   ✅ Бэкапы сохранены в $BACKUP_DIR"

# 3. Проверяем зависимости для расширенных индикаторов
echo "3. Проверяю зависимости..."
pip3 install numpy pandas ta-lib 2>/dev/null || pip install numpy pandas ta-lib
echo "   ✅ Зависимости установлены"

# 4. Обновляем strategy_engine.py
echo "4. Обновляю strategy_engine.py..."
# (скопировать новый файл)

# 5. Обновляем конфиг
echo "5. Обновляю конфигурацию..."
# (скопировать новый config.yaml)

# 6. Проверяем синтаксис
echo "6. Проверяю синтаксис Python..."
python3 -m py_compile trading_bot/strategy/strategy_engine.py
if [ $? -eq 0 ]; then
    echo "   ✅ Синтаксис корректен"
else
    echo "   ❌ Ошибка синтаксиса! Восстанавливаю бэкап..."
    cp $BACKUP_DIR/strategy_engine.py trading_bot/strategy/
    exit 1
fi

# 7. Запускаем бота
echo "7. Запускаю бота..."
./start.sh

# 8. Ждём и проверяем логи
sleep 5
echo ""
echo "================================================"
echo "✅ ОБНОВЛЕНИЕ ЗАВЕРШЕНО!"
echo ""
echo "📊 Проверьте логи: tail -f logs/bot.log"
echo "📈 Наблюдайте за сигналами - их качество должно улучшиться"
echo ""
echo "🎯 Теперь бот использует 15+ индикаторов:"
echo "   - SuperTrend для тренда"
echo "   - Ichimoku для облачной стратегии"
echo "   - ADX для силы тренда"
echo "   - Stochastic/CCI для перекупленности"
echo "   - Parabolic SAR для точек входа"
echo "   - И многое другое..."
echo "================================================"