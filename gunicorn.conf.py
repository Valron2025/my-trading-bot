"""
Gunicorn configuration file for Render deployment
"""

import os
import multiprocessing
import ssl

# Bind address
port = os.environ.get('PORT', '10000')
bind = f"0.0.0.0:{port}"

# Worker processes
workers = 1
threads = 1
worker_class = "sync"
worker_connections = 10

# Timeouts
timeout = 300
graceful_timeout = 60
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get('LOG_LEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process name
proc_name = "trading-bot-web"

# Maximum requests before restart (to prevent memory leaks)
max_requests = 500
max_requests_jitter = 50

# Preload app (set to False to avoid issues with background threads)
preload_app = False

# Daemon mode (don't daemonize when running on Render)
daemon = False

# Debug
debug = os.environ.get('DEBUG', 'false').lower() == 'true'

# SSL Environment setup for gRPC (CRITICAL for T-Bank API)
def post_fork(server, worker):
    """Настройка SSL окружения после форка воркера"""
    # Устанавливаем переменные окружения для SSL сертификатов
    os.environ['SSL_CERT_FILE'] = '/etc/ssl/certs/ca-certificates.crt'
    os.environ['REQUESTS_CA_BUNDLE'] = '/etc/ssl/certs/ca-certificates.crt'
    os.environ['GRPC_DNS_RESOLVER'] = 'native'
    os.environ['GRPC_SSL_CIPHER_SUITES'] = 'HIGH+ECDSA+HIGH'

    # Импортируем certifi и настраиваем глобально
    try:
        import certifi
        os.environ['SSL_CERT_FILE'] = certifi.where()
        print(f"✅ SSL configured with certifi: {certifi.where()}")
    except ImportError:
        print("⚠️ certifi not installed, using system certificates")

    print(f"🔐 SSL Environment configured for worker {worker.pid}")

def on_starting(server):
    """Запускается перед стартом мастер-процесса"""
    print("🚀 Gunicorn master starting...")
    print(f"   bind: {bind}")
    print(f"   workers: {workers}")
    print(f"   threads: {threads}")
    print(f"   timeout: {timeout}s")
    print(f"   loglevel: {loglevel}")
    print(f"   Python version: {os.sys.version}")

    # Проверка SSL сертификатов
    try:
        import certifi
        print(f"✅ certifi found at: {certifi.where()}")
    except ImportError:
        print("⚠️ WARNING: certifi not installed - SSL may fail!")

    # Установка глобальных переменных для мастер-процесса
    os.environ['GRPC_DNS_RESOLVER'] = 'native'
    os.environ['GRPC_SSL_CIPHER_SUITES'] = 'HIGH+ECDSA+HIGH'

# Hook for worker initialization
post_fork = post_fork
on_starting = on_starting

# Pre-exec hook for additional setup
def pre_exec(server):
    """Перед exec для обновления окружения"""
    os.environ['GRPC_DNS_RESOLVER'] = 'native'