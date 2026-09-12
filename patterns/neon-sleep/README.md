# Pattern: Neon Postgres Sleep Economics

## The one-line fix

```python
# In Django settings.py
DATABASES = {
    'default': dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=0,  # ← this is it
    )
}
```

## Why this matters

Neon's free tier suspends compute when no connections are open. Django's
default (`conn_max_age=None`) keeps connections open between requests.
Result: compute never suspends, free tier compute budget drains, and you
get an unexpected bill.

`conn_max_age=0` closes the connection immediately after each request.
Neon can suspend between requests. Your compute budget goes from
"exhausted in days" to "near-zero" if your app has low or bursty traffic.

## The secondary failure mode

With `conn_max_age > 0`: when Neon's compute restarts (after a rare forced
recycle or after you change connection parameters), Django worker processes
hold stale connection objects. The first request after restart throws:

```
SSL connection has been closed unexpectedly
OperationalError: server closed the connection unexpectedly
```

This hits only the first request — subsequent requests open fresh connections
and succeed. It's a silent intermittent failure that's hard to reproduce
locally. `conn_max_age=0` eliminates it.

## When NOT to use this

If your API has high, sustained request volume (hundreds of req/s), the
per-request connection handshake overhead (~50–150 ms) becomes significant.
At that scale, use Neon's connection pooling endpoint or add PgBouncer.

At low-to-medium volume (< a few hundred requests/minute), the overhead is
acceptable and the savings are real.

## Dependencies

```bash
pip install dj-database-url
```
