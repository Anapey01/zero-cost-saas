# Pattern: Cron Webhook

## What this replaces

Celery Beat + Celery Worker — two always-on processes consuming 1,488
instance-hours/month on a platform with 750 free hours/month.

## How it works

```
Vercel Cron (free) → GET /api/cron/daily/ → Django view → runs tasks → returns JSON
```

1. `vercel.json` schedules two cron entries:
   - A daily trigger at 2 AM UTC that runs all maintenance tasks.
   - A 14-minute keep-alive ping that prevents the backend from sleeping
     before the daily trigger fires (Render free tier sleeps after 15 min).

2. `django_view.py` receives the request, validates the `Authorization: Bearer`
   header against `CRON_SECRET`, then runs tasks sequentially.

3. Each task is wrapped in `run_safe()` — a failed task is logged and skipped,
   not allowed to abort the rest of the run.

## Environment variables required

```
CRON_SECRET=<a long random string>
```

Set this on your backend host (e.g. Render). Configure Vercel to send it
in the `Authorization` header when calling your backend URL.

> **Note:** Vercel Cron calls routes on Vercel apps with no authentication
> configuration needed — the cron service and your Next.js app share the
> same deployment. To hit an *external* backend, configure a Next.js API
> route that forwards the request (adding the Bearer header) to your
> backend URL.

## What you give up

- **Isolation:** If `check_batch_cutoffs` hangs, every subsequent task in
  the list waits. Add a per-task timeout if tasks can block indefinitely.
- **Reliability:** If the backend is cold when the cron fires, the 30-second
  HTTP timeout may expire before tasks complete. The keep-alive cron mitigates
  this but doesn't eliminate it.
- **Retry logic:** External cron services do not retry failed HTTP requests
  by default. If a run is missed, it's missed.

## Running the security tests

```bash
python manage.py test myapp.tests_security
```
