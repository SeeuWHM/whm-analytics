"""
WHM Analytics Collector - Gunicorn Configuration

Запуск: gunicorn -c gunicorn.conf.py app.main:app
"""

import multiprocessing
import os

# Bind
bind = os.getenv("WHM_BIND", "0.0.0.0:9100")

# Workers
# Формула: 2 * CPU + 1, но не больше 4 для небольших серверов
workers = int(os.getenv("WHM_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 4)))

# Worker class (uvicorn для async)
worker_class = "uvicorn.workers.UvicornWorker"

# Threads per worker
threads = 1  # Uvicorn использует async, threads не нужны

# Timeouts
timeout = 30  # секунд на запрос
keepalive = 5  # секунд keep-alive

# Graceful timeout
graceful_timeout = 30

# Max requests (для предотвращения memory leaks)
max_requests = 10000
max_requests_jitter = 1000

# Logging
loglevel = os.getenv("WHM_LOG_LEVEL", "info")
accesslog = "-"  # stdout
errorlog = "-"   # stderr
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "whm-collector"

# Preload app (быстрее запуск workers)
preload_app = True

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190


def on_starting(server):
    """Hook: перед запуском"""
    print(f"Starting WHM Collector with {workers} workers")


def on_exit(server):
    """Hook: при завершении"""
    print("WHM Collector shutting down")
