"""
Pattern: Neon Serverless Postgres — Closing connections to enable auto-suspend.

Context: Neon's free tier bills by active compute-hours, not calendar time.
Compute suspends automatically when all connections are closed. Django's
default conn_max_age keeps connections open between requests, preventing
Neon from ever suspending — effectively converting a serverless database
into an always-on one.

Setting conn_max_age=0 ensures every request closes its connection after
use. Neon can then suspend compute between requests.

The cost: every request pays the connection handshake (TCP + TLS + Postgres
authentication). On Neon free tier: ~50–150 ms on warm compute, ~300–800 ms
on cold start. For high-frequency APIs requiring sub-50ms DB latency, use a
connection pooler (Neon's pooling endpoint, or PgBouncer) instead.
"""
import os
import dj_database_url

DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL and not DATABASE_URL.startswith('sqlite'):
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            # conn_max_age=0 closes each connection immediately after the request.
            # This allows Neon to auto-suspend when no clients are connected.
            #
            # DO NOT use conn_max_age > 0 with Neon free tier: persistent
            # connections keep compute awake and defeat the auto-suspend mechanism.
            # They also cause 'SSL connection has been closed unexpectedly' errors
            # when Neon's compute restarts and invalidates open TLS sessions.
            conn_max_age=0,
        )
    }
else:
    # Development: use SQLite for zero-config local setup
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
        }
    }
