# Running a Real SaaS at ~$0/Month: A Decision Log

[![GitHub stars](https://img.shields.io/github/stars/Anapey01/zero-cost-saas?style=flat-square)](https://github.com/Anapey01/zero-cost-saas)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](./CONTRIBUTING.md)

> A living document. These are the trade-offs we made in production, not
> recommendations for everyone. If you've solved a similar problem differently,
> open a PR — the point of publishing this is to collect what actually works,
> not to be the last word on it.

We run a small-batch e-commerce marketplace — one backend, one frontend, a
mobile app shell, a handful of third-party APIs. It's not a side project;
it has real users, real payments, and real consequences when things break.
The run-rate target was simple: keep it under the cost of a cheap VPS (ideally
zero) without pretending we're running a toy.

This document is organized by decision, not by tech layer. For each pattern:
the constraint that forced it, the option we rejected, what we chose instead,
and the honest cost we paid.

The reference implementations for some of these are in [`/patterns`](./patterns/).
They're stripped of all business logic — the mechanism in isolation.

---

## Table of Contents

1. [Serverless Postgres: sleep economics](#1-serverless-postgres-sleep-economics)
2. [Killing the always-on scheduler](#2-killing-the-always-on-scheduler)
3. [Bypassing the frontend host's image transform quota](#3-bypassing-the-frontend-hosts-image-transform-quota)
4. [Cascading across free-tier AI endpoints](#4-cascading-across-free-tier-ai-endpoints)
5. [Client-side compression before upload](#5-client-side-compression-before-upload)
6. [Offline-tolerant caching that doesn't crash in sandboxed environments](#6-offline-tolerant-caching-that-doesnt-crash-in-sandboxed-environments)
7. [Recovering from CDN/deployment asset-hash disagreement](#7-recovering-from-cdndeployment-asset-hash-disagreement)

---

## 1. Serverless Postgres: sleep economics

**The constraint.** Serverless Postgres (we use Neon's free tier) earns its
zero-dollar price tag by suspending compute when no connections are open. The
free tier compute budget is measured in active compute-hours, not calendar
hours — so a database that's idle most of the day has a near-zero bill. This
is the entire value proposition.

**The option we rejected.** Django's default database configuration is
`conn_max_age=None` (or an integer number of seconds), which tells each
Gunicorn worker process to hold its database connection open and reuse it
across requests. This is a legitimate performance optimization on traditional
always-on Postgres: you avoid the TCP handshake, TLS negotiation, and
authentication round-trip on every request. On a serverless database, it's
exactly wrong. A worker holding an open connection is a worker keeping compute
awake. With two Gunicorn workers, that's two persistent connections — the
database never suspends, and you've turned a serverless database into an
always-on one while paying the latency cost of the network hop on every query.

We also observed a secondary failure mode: when Neon's compute *did* suspend
(after a period where all connections were dropped, e.g. a deploy), Django
workers with `conn_max_age > 0` would attempt to reuse a stale connection
object against a compute that had cold-started with a new TLS session. This
produced `SSL connection has been closed unexpectedly` errors on the first
request after idle — not on every request, just the unlucky first one that
hit the stale handle.

**What we chose.** `conn_max_age=0`. Every request opens a fresh connection,
runs its queries, and closes it. The database sees the connection drop
immediately after each request and can suspend as soon as no other connections
are open.

```python
# settings.py
DATABASES = {
    'default': dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=0,  # Close connections immediately → Neon can auto-suspend
    )
}
```

**The honest cost.** Every request pays the connection handshake: TCP + TLS +
Postgres authentication. On Neon's free tier this is roughly 50–150 ms on a
warm compute, and 300–800 ms on a cold start (the compute was suspended and
needs to spin up). For our request profile — product listings, order lookups,
payments — this is acceptable. For a high-frequency API with sub-50ms
requirements, it isn't. You'd want a connection pooler (PgBouncer, or Neon's
own pooling endpoint) sitting between your app and the database to absorb the
handshake cost while still allowing the database compute to suspend when the
pooler has no active traffic.

The minimal demo for this pattern is in
[`/patterns/neon-sleep/`](./patterns/neon-sleep/).

---

## 2. Killing the always-on scheduler

**The constraint.** We had eight periodic tasks: close expired order batches,
clean up abandoned carts, send delivery reminders, process auto-confirmations,
calculate vendor payouts, send review requests, send cart reminders, send
birthday messages. The standard Python way to run these is Celery Beat (the
scheduler) + a Celery Worker (the executor), talking through a Redis broker.

On Render's free tier, each process is a separate service, and each service
consumes instance hours against your 750 free-hours/month allowance. Our
`Procfile` had three entries: `web` (Gunicorn), `worker` (Celery Worker),
`beat` (Celery Beat). Three services running 24/7 = 3 × 744 hours/month =
2,232 instance-hours/month. That's three times the free allowance, and the
worker + beat services produce no direct revenue — they just run maintenance
jobs.

**The option we rejected.** Keeping Celery. At 2,232 hours versus 750 free,
we'd need to pay for the overage or collapse the worker and beat onto the same
service as the web process — which defeats the point of Celery's process
isolation and can starve web traffic when heavy tasks run.

We also considered APScheduler running inside the Gunicorn process. This is a
legitimate approach but has a subtle flaw in multi-worker deployments: with
two Gunicorn workers, APScheduler runs in both processes, and both will attempt
to fire the scheduled job at the same time. You need a database-backed job
store (like `APScheduler` with `SQLAlchemyJobStore`) to handle locking —
which adds a library dependency and still doesn't solve the 24/7 process
problem.

**What we chose.** A single authenticated webhook endpoint on the Django
backend, triggered by Vercel Cron.

The endpoint (`GET /api/v1/orders/cron/daily/`) runs all periodic tasks
sequentially in the same Gunicorn process that handles web traffic. No broker,
no worker process, no scheduler process. Vercel Cron hits it once a day;
for the time the tasks run (~5–30 seconds depending on dataset size), the web
process is occupied. After it returns, Gunicorn goes idle, and if traffic
stops, Render can let the service sleep.

Security: the endpoint rejects all requests that don't carry a matching
`Authorization: Bearer <CRON_SECRET>` header. We explicitly block query-string
secrets (`?secret=...`) because query params appear in server access logs and
Render's dashboard — a header secret doesn't.

```json
// vercel.json — fires daily at 2 AM UTC
{
  "crons": [{
    "path": "/api/cron/trigger",
    "schedule": "0 2 * * *"
  }]
}
```

The Vercel Cron service is free on all Vercel plans (including Hobby) with a
minimum granularity of once per day. It makes an HTTP request to any URL you
specify. The URL just happens to be on your backend, not on Vercel.

**The honest cost.**

*Contention.* If a user submits an order at exactly 2 AM while the cron tasks
are running, both requests compete for the same Gunicorn worker. With
`workers=2`, one worker handles the cron task and one handles the user request.
With `workers=1`, the user waits. Our task set runs in under 30 seconds; we
accepted this as a tolerable degradation window.

*Missing runs.* If the backend is suspended (Render free-tier services sleep
after 15 minutes of inactivity), Vercel Cron's HTTP request will either wake
it up (adding ~10–30 seconds of cold-start time to the task run) or time out
if the cold start exceeds Vercel's 30-second request timeout. In practice,
our cron fires at 2 AM when the backend is nearly always cold. We added a
lightweight keep-alive: a separate Vercel Cron at `*/14 * * * *` that pings
the backend's health-check endpoint to prevent it from sleeping in the first
place.

*Single point of failure.* If Vercel Cron has an outage, tasks don't run.
For maintenance tasks like "clean up carts older than 7 days" this is benign.
For financial tasks like "calculate vendor payouts," you want an alerting
mechanism or a manual re-trigger path.

The reference implementation is in [`/patterns/cron-webhook/`](./patterns/cron-webhook/).
It includes the Django view, the Vercel config, and the security tests.

---

## 3. Bypassing the frontend host's image transform quota

**The constraint.** Vercel's image optimization service resizes, converts to
WebP/AVIF, and CDN-caches images on demand via the `next/image` component.
On the Hobby plan, the limit is 1,000 *unique source images* per month.
An e-commerce catalog of 200 products with 3–5 images each, served to users
across 4–5 viewport breakpoints each, burns through that quota in hours.
Beyond the limit, Vercel either serves unoptimized originals (breaking layout
for components that expect specific dimensions) or returns errors.

**The option we rejected.** Upgrading to Vercel Pro is $20/month and removes
the image quota. That's not nothing when you're targeting zero fixed costs.
We also considered self-hosting Sharp (the Node.js image processor) as a
Next.js API route — but that runs server-side compute on Vercel's
infrastructure and would exhaust function invocation quotas instead.

**What we chose.** A custom Next.js image loader that bypasses Vercel's
optimization entirely and routes all image requests through Cloudinary's
free tier instead.

Cloudinary's free tier includes 25 GB of managed storage and 25 GB of
monthly bandwidth with unlimited transformations. Their **Fetch URL** feature
is the key: you can ask Cloudinary to fetch, transform, and serve *any
public URL* — including images hosted on your own backend — without
pre-uploading them.

```typescript
// imageLoader.ts
export default function imageLoader({ src, width, quality }: ImageLoaderParams): string {
  const cloudName = process.env.NEXT_PUBLIC_CLOUDINARY_CLOUD_NAME;

  // Already a Cloudinary upload URL → apply transformations inline
  if (src.includes('cloudinary.com') && src.includes('/image/upload/')) {
    const params = ['f_auto', 'c_limit', `w_${width}`, `q_${quality || 'auto'}`];
    const [base, rest] = src.split('/image/upload/');
    return `${base}/image/upload/${params.join(',')}/${rest}`;
  }

  // Backend media URL → proxy through Cloudinary Fetch
  if (src.includes('api.example.com') && process.env.NODE_ENV === 'production') {
    const params = ['f_auto', 'q_auto', 'c_limit', `w_${width}`];
    return `https://res.cloudinary.com/${cloudName}/image/fetch/${params.join(',')}/${encodeURIComponent(src)}`;
  }

  // Local dev fallback
  const connector = src.includes('?') ? '&' : '?';
  return `${src}${connector}w=${width}&q=${quality || 75}`;
}
```

```typescript
// next.config.ts
images: {
  loader: 'custom',
  loaderFile: './src/lib/imageLoader.ts',
}
```

Vercel never sees the image optimization request. The browser asks Cloudinary
directly. Cloudinary fetches the source image on the first request, transforms
it to the requested width + format, caches it at its CDN edge, and serves
subsequent requests from cache.

**The honest cost.**

*First-request latency.* On a cache miss, Cloudinary has to fetch the origin
image, resize it, and return it — this can take 1–3 seconds for large images
on a slow origin. After that first miss, the image is cached at Cloudinary's
CDN edge and subsequent requests are fast.

*The Cloudinary Fetch URL is public.* The transformation string is in the URL,
meaning anyone can construct a URL that asks Cloudinary to fetch and resize
arbitrary external images using your cloud name. Cloudinary allows you to
restrict fetch to a whitelist of source domains in your account settings —
enable this.

*Format negotiation is server-side.* `f_auto` lets Cloudinary pick WebP or
AVIF based on the request's `Accept` header. This works correctly when
Cloudinary is the origin, but the `next/image` component's
`formats: ['image/avif', 'image/webp']` config has no effect when you're using
a custom loader — format selection is entirely delegated to Cloudinary.

The loader code is in [`/patterns/client-resilience/imageLoader.ts`](./patterns/client-resilience/imageLoader.ts).

---

## 4. Cascading across free-tier AI endpoints

**The constraint.** We use a vision-capable AI model to analyze product images
and extract structured data (product name, category, estimated pricing, search
keywords). The task is user-facing: the results display after a few seconds.

Google AI Studio's Gemini free tier offers 1,500 requests per day at zero cost.
The catch is that this limit is per *model*, per *API version*, per *API key*.
A quota hit on `gemini-2.0-flash` on `v1beta` doesn't block you from trying
`gemini-2.5-flash` on `v1`, or `gemini-2.0-flash-lite` on `v1beta`.

**The option we rejected.** A single model with exponential backoff. This is
the correct pattern for transient failures (network errors, 503s), but it's
wrong for quota exhaustion: a 429 on a daily quota limit won't resolve in
30 seconds — it resolves the next day. Retrying the same model harder just
wastes the user's time.

**What we chose.** A waterfall: try a list of `(api_version, model_name)` pairs
in order. On a 200, extract the result and return immediately. On a 404, skip.
On a 429, move to the next entry.

```python
models_to_try = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.5-pro",
]

api_versions = ["v1", "v1beta"]

for version in api_versions:
    for model in models_to_try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/{version}/models/{model}:generateContent?key={api_key}",
            json=payload,
            timeout=30,
        )
        if response.status_code == 200:
            return parse_response(response)
        elif response.status_code == 404:
            continue          # model not in this version
        elif response.status_code == 429:
            continue          # quota exhausted, try next bucket
```

Images are resized to 1024px maximum before being base64-encoded and sent.
This reduces token count for vision requests and keeps payloads within context
windows without sacrificing extraction quality for product thumbnails.

**The honest cost.**

*Cascade latency.* If the first several models are all 429ing, the user waits
for sequential HTTP round-trips before getting a response. Set per-call
timeouts aggressively (`timeout=30`) and consider a hard ceiling on total
cascade time.

*Multiple quota buckets per request.* Each cascade attempt that reaches a
model consumes a quota unit, even on failure. A request that falls through
three models before a 200 has consumed three quota units. Order the cascade
with highest-capacity models first.

*The model list decays.* Google periodically deprecates version strings. A
model that worked last month may 404 today. Review the list when Google
announces deprecations.

The reference implementation is in [`/patterns/ai-cascade/`](./patterns/ai-cascade/).

---

## 5. Client-side compression before upload

**The constraint.** Users upload product photos from phones. Phone cameras in
2024 produce 8–20 MB HEIC/JPEG files. Our backend runs on Render's free tier:
512 MB RAM, shared CPU, and a 30-second request timeout that cannot be
configured.

On a 2G connection (common in our target market), uploading a 12 MB file
takes 80–400 seconds. The request times out at 30 seconds, the user gets an
error, and the partial upload is discarded. On 3G, the same file takes 10–40
seconds — right at the boundary.

**The option we rejected.** Resumable uploads (chunked uploads to cloud storage
with a signed URL, bypassing the backend entirely). This is architecturally
correct at scale, but adds significant complexity: signed URL generation,
multipart completion callbacks, intermediate state storage. For a team of one
or two, not worth it yet.

**What we chose.** Client-side compression in a Web Worker before the file
reaches the upload handler.

```typescript
import imageCompression from 'browser-image-compression';

export async function compressImage(file: File): Promise<File> {
  if (file.size / 1024 / 1024 < 1) {
    return file; // already small enough
  }

  const options = {
    maxSizeMB: 1,
    maxWidthOrHeight: 1920,
    useWebWorker: true,   // non-blocking — UI stays responsive
    fileType: file.type,
  };

  try {
    return await imageCompression(file, options);
  } catch {
    return file; // silent fallback — never fail the upload
  }
}
```

On a 12 MP phone photo: output is typically 200–600 KB. Upload time on 2G
drops from minutes to under 10 seconds. The backend receives a payload it can
buffer and process without strain.

`useWebWorker: true` is critical. Without it, the resize operation runs on the
main thread and freezes the UI for 2–5 seconds on midrange phones.

**The honest cost.**

*Quality loss.* 1 MB at 1920px is fine for a product thumbnail and for AI
analysis (which we cap at 1024px server-side anyway), but not archival quality.

*HEIC on older Android WebViews.* `browser-image-compression` uses the Canvas
API internally. HEIC files are not natively decodable by most Android WebViews;
the library throws and the catch block returns the original uncompressed file.
We accept the occasional timeout in this case.

*Processing time on low-end devices.* On a $50 Android phone, compressing a
12 MP image takes 3–8 seconds even in a Web Worker. The UI doesn't freeze, but
the user waits. Show a spinner and disable the submit button during this window.

---

## 6. Offline-tolerant caching that doesn't crash in sandboxed environments

**The constraint.** Our app is distributed as both a PWA and an Android TWA
(Trusted Web Activity — a Chrome Custom Tab wrapped in an APK shell). In a
TWA, the host app controls the browser session. Certain WebView isolation
policies, or Android's battery optimizer, can make `localStorage` throw
`SecurityError: The operation is insecure` on any read or write — *after*
the app has already loaded and initialized. An uncaught exception propagating
through React Query's persistence layer will unmount the entire component tree
and show a blank screen.

The second problem was a React tree-swap bug. We wanted to conditionally use
`PersistQueryClientProvider` only when storage was available, otherwise
`QueryClientProvider`. React treats these as different component types;
swapping them unmounts the entire child tree, cancels all in-flight queries,
and causes the product grid to go blank at the worst possible moment.

**The option we rejected.** Conditional rendering with a `localStorage`
availability check on mount. This is the obvious solution and produces exactly
the tree-swap bug described above.

IndexedDB as the persistence backend: more reliably available in WebView
environments, but the async nature means the restore can happen after the
first render — causing a flash of uncached content even when cache exists.

**What we chose.** Two changes in combination.

Always mount `PersistQueryClientProvider`. No runtime branch on storage
availability. This eliminates the tree-swap bug unconditionally.

Wrap every `localStorage` call in the persister in a `try/catch` and
silently no-op on any exception:

```typescript
function createSafePersister() {
  return {
    persistClient: async (client: unknown) => {
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(client));
      } catch {
        // Storage unavailable (TWA restriction, quota exceeded, private mode)
        // In-memory cache still works, just not persisted across sessions
      }
    },
    restoreClient: async () => {
      try {
        const raw = localStorage.getItem(CACHE_KEY);
        return raw ? JSON.parse(raw) : undefined;
      } catch {
        return undefined; // treat as empty cache
      }
    },
    removeClient: async () => {
      try {
        localStorage.removeItem(CACHE_KEY);
      } catch { }
    },
  };
}

// Stable singleton — created once at module load, never re-created between renders
const safePersister = createSafePersister();
```

When storage is unavailable, `restoreClient` returns `undefined` and
`persistClient` silently drops the write. React Query works entirely in memory.
The user doesn't see an error; they just don't get cache persistence across
sessions.

**The honest cost.**

*Silent degradation.* When storage is restricted, the app behaves as though
it has no cache: products re-fetch on every cold launch, cart state isn't
persisted, the offline page can't show cached data. Users in restricted TWA
environments get a worse experience, and you'll never know from error logs —
all failures are swallowed.

*`QuotaExceededError` is also silently dropped.* If the device is genuinely
out of storage, writes fail. We treat this the same as a security restriction.
The cache can become stale without the user knowing.

The reference implementation is in
[`/patterns/client-resilience/`](./patterns/client-resilience/).

---

## 7. Recovering from CDN/deployment asset-hash disagreement

**The constraint.** Next.js content-hashes its static assets:
`/_next/static/css/22d843d29efef3ed.css`. The hash changes whenever CSS content
changes, which happens on nearly every deployment.

Our PWA service worker uses `CacheFirst` for static assets and caches them at
their full hashed URLs. After a deploy:

- New users: SW fetches the new hash, caches it, everything works.
- Returning users with an *updating* SW: the new SW activates, fetches the
  new hash, works.
- Returning users with an *old* SW still active: the SW intercepts requests
  for the old hash and may return a stale cached file. Worse, the old SW may
  attempt to load a hash that no longer exists on the CDN at all, returning
  a 404 and breaking the layout.

The window of breakage is the time between a new deployment going live and
all service workers updating — which can be hours.

**The option we rejected.** `NetworkFirst` for static assets. This defeats
the performance benefit of caching static assets entirely.

Query-string versioning (`?v=deploy-sha`). Architecturally correct, but
requires significant changes to Next.js's asset pipeline and conflicts with
how `next/image` and RSC payloads are fingerprinted.

**What we chose.** A webpack plugin that runs at build time and aliases the
most-recently-seen legacy CSS hash paths to the current build's main CSS asset.

```typescript
// next.config.ts — webpack plugin
{
  apply(compiler) {
    compiler.hooks.thisCompilation.tap('LegacyCssPlugin', (compilation) => {
      compilation.hooks.processAssets.tap(
        { name: 'LegacyCssPlugin', stage: PROCESS_ASSETS_STAGE_ADDITIONS },
        (assets) => {
          // Find the largest CSS file — heuristic for the main stylesheet
          const mainCssKey = Object.keys(assets)
            .filter(k => k.startsWith('static/css/') && k.endsWith('.css'))
            .sort((a, b) => (assets[b]?.size?.() || 0) - (assets[a]?.size?.() || 0))[0];

          if (!mainCssKey) return;

          const legacyHashes = [
            'static/css/22d843d29efef3ed.css',
            'static/css/8ef137cc7b45679b.css',
            'static/css/2702258296b3bdef.css',
            'static/css/0b77e07caaecc1ad.css',
          ];

          for (const legacy of legacyHashes) {
            if (!assets[legacy]) {
              assets[legacy] = assets[mainCssKey]; // alias to current CSS
            }
          }
        }
      );
    });
  }
}
```

Every deployment ships all four legacy hash aliases alongside the real asset.
A service worker that requests the old hash gets the current CSS instead of
a 404. Layout works. The styles may be slightly ahead of what the old HTML
expected, but "slightly new styles" is better than "blank page."

**The honest cost.**

*Manual rotation required.* After a deploy that changes CSS significantly, add
the old hash to the list. Hashes that roll off the list before all SWs have
updated will cause 404s again. Without automation, this decays.

*This is a mitigation, not a fix.* The plugin buys enough time for SWs to
auto-update. It doesn't eliminate the disagreement window, it shortens the
impact from "broken page" to "slightly inconsistent styles."

*The largest-CSS heuristic can mismatch.* If your build produces multiple
large CSS files from code splitting, this may alias to the wrong one. Inspect
your build output.

---

## What this costs, in total

| Service | Free tier used | Notes |
|---|---|---|
| Vercel (frontend) | Hobby plan | 1 project, no image optimization quota used |
| Render (backend) | 750 hrs/month | Single `web` service; sleeps after 15 min idle |
| Neon (database) | Free tier | 0.5 GB storage, compute auto-suspends |
| Cloudinary (media) | Free tier | 25 GB storage + bandwidth, fetch URL enabled |
| Google AI Studio | Free tier | 1,500 req/day per model, cascaded across models |
| Vercel Cron | Free (all plans) | Daily task trigger + 14-min keep-alive |

The fixed monthly cost is $0. Variable costs that could appear: Cloudinary
bandwidth over 25 GB/month, or Neon storage over 0.5 GB.

The constraints this architecture accepts:
- Backend cold starts of 10–30 seconds after 15 minutes of inactivity
- No WebSockets (Render free tier; long-poll or SSE instead)
- Cron tasks have a single-point-of-failure in Vercel Cron
- Daily AI request budget is finite and shared across all users

These are acceptable at early-to-mid scale. The first thing you'd pay for is
a persistent backend (removing cold starts), then a connection pooler (removing
DB handshake latency), then a dedicated task queue. Each upgrade targets the
highest-pain constraint rather than requiring a full rewrite.

---

## Contributing

If you've shipped a real free-tier pattern that isn't here, open a PR.
Same format: constraint → rejected option → chosen approach → honest cost.
No sales-pitch framing; the goal is an honest decision log, not marketing.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.