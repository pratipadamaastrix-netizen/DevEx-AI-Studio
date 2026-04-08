# Gunicorn configuration for DevEx FM Platform
# Usage: gunicorn -c gunicorn.conf.py wsgi:app

import multiprocessing

# Bind
bind = "127.0.0.1:8000"

# Workers — 2-4 x CPU cores is a reasonable starting point
workers = multiprocessing.cpu_count() * 2 + 1

# Worker class — sync is fine for this app (no async routes)
worker_class = "sync"

# Timeouts — Gemini/DeepSeek calls can take up to 30s, give headroom
timeout = 60
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = "-"        # stdout
errorlog  = "-"        # stdout
loglevel  = "info"

# Reload on code change (disable in production, enable for staging)
reload = False
