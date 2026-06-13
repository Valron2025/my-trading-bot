# gunicorn.conf.py

import os
import multiprocessing

# Привязка к порту Render
port = os.environ.get('PORT', '5000')
bind = f"0.0.0.0:{port}"

# Количество воркеров (1 достаточно для бота)
workers = 1

# Количество потоков на воркер
threads = 1

# Таймауты
timeout = 300
keepalive = 5
graceful_timeout = 60

# Логирование
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Управление процессами
pidfile = "/tmp/gunicorn.pid"
daemon = False
worker_class = "sync"
preload_app = False

# Безопасность
limit_request_line = 4094
limit_request_fields = 100

# Перезапуск после N запросов
max_requests = 500
max_requests_jitter = 50

# Для совместимости с Render
proc_name = "trading-bot"