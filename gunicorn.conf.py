"""
Gunicorn configuration file for Render deployment
"""

import os
import multiprocessing

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

# Startup message
print(f"🚀 Gunicorn configured:")
print(f"   bind: {bind}")
print(f"   workers: {workers}")
print(f"   threads: {threads}")
print(f"   timeout: {timeout}s")
print(f"   loglevel: {loglevel}")