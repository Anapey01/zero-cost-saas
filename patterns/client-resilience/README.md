# Pattern: Client Resilience

Two frontend patterns that address the same root cause: the client environment
is not as controlled as you think, especially in TWA/WebView wrappers.

---

## safePersister.ts — Offline-tolerant query cache

**The failure mode:** Android TWA shells can revoke `localStorage` access at
runtime. `localStorage.setItem()` throws `SecurityError`. If this propagates
through React Query's persistence layer, the component tree unmounts and the
user sees a blank screen.

**The fix:** Wrap every `localStorage` call in `try/catch`. Silently no-op
on any exception. Always mount `PersistQueryClientProvider` unconditionally
(never swap between `QueryClientProvider` and `PersistQueryClientProvider`
based on a runtime check — the swap unmounts the entire React tree).

**When storage is restricted:** React Query works in memory. No error is
thrown. No crash. The user loses cache persistence across sessions, but
the app continues to function.

### Setup

```bash
npm install @tanstack/react-query @tanstack/react-query-persist-client
```

Replace your existing `QueryClientProvider` wrapper with `QueryProvider`
from `safePersister.ts`.

---

## imageLoader.ts — Cloudinary-backed image optimization

**The failure mode:** Vercel Hobby plan caps image optimization at 1,000
unique source images/month. An e-commerce catalog exhausts this in hours.

**The fix:** A custom Next.js image loader that routes all `next/image`
requests to Cloudinary instead of Vercel's optimizer. Cloudinary's free
tier (25 GB storage + bandwidth, unlimited transforms) handles the load.
For images not already uploaded to Cloudinary, the Fetch URL feature
proxies and transforms them on-the-fly.

### Setup

1. Set `NEXT_PUBLIC_CLOUDINARY_CLOUD_NAME` in your environment.
2. In `next.config.ts`:
   ```typescript
   images: {
     loader: 'custom',
     loaderFile: './src/lib/imageLoader.ts',
   }
   ```
3. In your Cloudinary account settings: restrict the Fetch URL feature
   to your own domain(s).
