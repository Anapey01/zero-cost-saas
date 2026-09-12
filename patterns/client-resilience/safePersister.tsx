/**
 * Pattern: Safe localStorage Persister for React Query
 *
 * Context: Apps distributed as TWA (Trusted Web Activity) Android shells
 * can have localStorage revoked at runtime by the host app or Android's
 * battery optimizer. An uncaught SecurityError crashes the entire app.
 *
 * Solution:
 *   1. Always mount PersistQueryClientProvider (never conditionally swap
 *      to QueryClientProvider) — avoids a React tree-swap that cancels
 *      all in-flight queries and blanks the screen.
 *   2. Wrap every localStorage call in try/catch and silently no-op on
 *      any exception. React Query continues to work in-memory.
 *
 * What you give up:
 *   Cache is not persisted across sessions when storage is restricted.
 *   Failures are silent — you will not see them in error logs.
 */

import { QueryClient } from '@tanstack/react-query';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { useState } from 'react';

const CACHE_KEY = 'MY_APP_QUERY_CACHE';

/**
 * Creates a persister that silently no-ops on any storage error.
 * Safe in: TWA shells, private browsing, WebViews with restricted storage,
 * devices with full storage quotas.
 */
function createSafePersister() {
  return {
    persistClient: async (client: unknown) => {
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(client));
      } catch {
        // Storage unavailable or quota exceeded — in-memory cache still works
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
      } catch {
        // ignore
      }
    },
  };
}

// Stable singleton — must be created once at module level, not inside
// the component, to avoid re-creating the persister on every render.
const safePersister = createSafePersister();

export function QueryProvider({ children }: { children: React.ReactNode }) {
  // useState ensures the QueryClient is created once, not on every render.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 1000 * 60 * 60 * 2,  // 2 hours
            gcTime: 1000 * 60 * 60 * 24,    // 24 hours
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      })
  );

  // PersistQueryClientProvider is always mounted — no conditional swap.
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister: safePersister }}
    >
      {children}
    </PersistQueryClientProvider>
  );
}
