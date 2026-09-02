'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiJson } from './api';
import { useVisiblePolling } from './data-cache';
import { unavailableIndexing, type IndexingItem } from './indexing-status';

/** One bounded status request per visible page, never per document row.
 * No markdown reloads, shared credential cache or changes to review inputs.
 */
export function useIndexingStatus(jobIds: string[]) {
  const key = [...new Set(jobIds)].sort().join(',');
  const [snapshot, setSnapshot] = useState<{ key: string; items: Record<string, IndexingItem> }>({ key: '', items: {} });
  const inFlight = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    if (!key || inFlight.current) return;
    const ids = key.split(',');
    const controller = new AbortController();
    inFlight.current = controller;
    try {
      const params = new URLSearchParams();
      ids.forEach(id => params.append('job_id', id));
      const result = await apiJson<{ items: IndexingItem[] }>(`/api/v1/portal/indexing-status?${params}`, { cache: 'no-store', signal: controller.signal });
      if (!Array.isArray(result.items)) throw new Error('Invalid status response');
      if (controller.signal.aborted) return;
      const byId = new Map(result.items.map(item => [item.job_id, item]));
      // Omitted IDs can mean permissions were revoked. Never retain a stale
      // green badge or inherit another document's status from a prior page.
      setSnapshot({ key, items: Object.fromEntries(ids.map(id => [id, byId.get(id) ?? { job_id: id, release: null, indexing: unavailableIndexing }])) });
    } catch {
      if (!controller.signal.aborted) setSnapshot({ key, items: Object.fromEntries(ids.map(id => [id, { job_id: id, release: null, indexing: unavailableIndexing }])) });
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
    }
  }, [key]);
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => { if (!cancelled) void refresh(); });
    return () => { cancelled = true; inFlight.current?.abort(); inFlight.current = null; };
  }, [refresh]);
  const items = snapshot.key === key ? snapshot.items : {};
  const waiting = key && key.split(',').some(id => !items[id]?.indexing || ['pending', 'not_received', 'unavailable'].includes(items[id].indexing!.state));
  useVisiblePolling(() => void refresh(), key ? (waiting ? 5_000 : 30_000) : null);
  return { items, refresh };
}
