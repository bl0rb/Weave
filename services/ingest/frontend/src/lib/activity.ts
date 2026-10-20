import { useEffect, useState } from 'react';

import { apiJson } from '@/lib/api';
import { type ImportRunListResponse, isRunActive } from '@/lib/imports';

/** Jobs queued or running plus import runs still crawling -- the number the
 * sidebar's mark weaves for. Three cheap requests (`limit=0`, only `total` is
 * read; the run list is short by construction). */
export async function countActiveWork(signal?: AbortSignal): Promise<number> {
  const [running, pending, runs] = await Promise.all([
    apiJson<{ total: number }>('/api/v1/search?status=RUNNING&limit=0&offset=0', { signal }),
    apiJson<{ total: number }>('/api/v1/search?status=PENDING&limit=0&offset=0', { signal }),
    apiJson<ImportRunListResponse>('/api/v1/import/runs', { signal, cache: 'no-store' }),
  ]);
  return running.total + pending.total + runs.items.filter((run) => isRunActive(run.status)).length;
}

const POLL_MS = 20_000;

/**
 * Whether anything is being processed right now, refreshed every 20 s while
 * the tab is visible (and at once on navigation, so a freshly started upload
 * or import shows up promptly). `false` until the first answer and whenever
 * the request fails -- a sidebar must never block on this.
 */
export function useActiveWork(enabled: boolean, refreshKey?: string): boolean {
  const [active, setActive] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    const poll = () => {
      if (document.visibilityState !== 'visible') return;
      controller?.abort();
      controller = new AbortController();
      countActiveWork(controller.signal)
        .then((count) => { if (!cancelled) setActive(count > 0); })
        .catch(() => { /* keep the last known state */ });
    };
    poll();
    const timer = window.setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', poll);
    return () => {
      cancelled = true;
      controller?.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', poll);
    };
  }, [enabled, refreshKey]);
  return active;
}
