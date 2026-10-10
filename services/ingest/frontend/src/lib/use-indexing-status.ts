'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiJson } from './api';
import { useVisiblePolling } from './data-cache';
import { unavailableIndexing, type IndexingItem } from './indexing-status';

// Same windows as Ingest's own grace (contracts/indexing-status.md), here for
// the case that Ingest itself cannot be reached.
const STALE_GRACE_MS = 5 * 60_000;
const RETRYING_FOR_MS = 10 * 60_000;
// Ingest (and Knowledge behind it) accept at most 50 IDs per request.
const MAX_IDS_PER_REQUEST = 50;

function markStale(items: Record<string, IndexingItem>, fetchedAt: number): Record<string, IndexingItem> {
  const checkedAt = new Date(fetchedAt).toISOString();
  return Object.fromEntries(Object.entries(items).map(([id, item]) => [id, !item.indexing || item.indexing.state === 'unavailable'
    ? item
    : { ...item, indexing: { ...item.indexing, stale: true, checked_at: item.indexing.checked_at ?? checkedAt } }]));
}

/** Bounded status requests of at most 50 IDs per visible list, never per
 * document row. No markdown reloads, shared credential cache or changes to
 * review inputs.
 */
export function useIndexingStatus(jobIds: string[]) {
  const key = [...new Set(jobIds)].sort().join(',');
  const [snapshot, setSnapshot] = useState<{ key: string; items: Record<string, IndexingItem>; fetchedAt: number }>({ key: '', items: {}, fetchedAt: 0 });
  const inFlight = useRef<AbortController | null>(null);
  const failing = useRef<{ key: string; since: number } | null>(null);
  const refresh = useCallback(async () => {
    if (!key || inFlight.current) return;
    const ids = key.split(',');
    const controller = new AbortController();
    inFlight.current = controller;
    try {
      const batches = Array.from({ length: Math.ceil(ids.length / MAX_IDS_PER_REQUEST) }, (_, index) => ids.slice(index * MAX_IDS_PER_REQUEST, (index + 1) * MAX_IDS_PER_REQUEST));
      const results = await Promise.all(batches.map(batch => {
        const params = new URLSearchParams();
        batch.forEach(id => params.append('job_id', id));
        return apiJson<{ items: IndexingItem[] }>(`/api/v1/portal/indexing-status?${params}`, { cache: 'no-store', signal: controller.signal });
      }));
      if (results.some(result => !Array.isArray(result.items))) throw new Error('Invalid status response');
      if (controller.signal.aborted) return;
      const byId = new Map(results.flatMap(result => result.items).map(item => [item.job_id, item]));
      // Omitted IDs can mean permissions were revoked. Never retain a stale
      // green badge or inherit another document's status from a prior page.
      failing.current = null;
      setSnapshot({ key, items: Object.fromEntries(ids.map(id => [id, byId.get(id) ?? { job_id: id, release: null, indexing: unavailableIndexing }])), fetchedAt: Date.now() });
    } catch (error) {
      if (controller.signal.aborted) return;
      // Only network errors and 5xx/429 are transient: they keep this page's
      // last answer for STALE_GRACE_MS, marked stale. A rejected session or
      // request (4xx) never does, so a real fault is not hidden.
      const status = (error as { status?: unknown } | null)?.status;
      const transient = typeof status !== 'number' || status >= 500 || status === 429;
      const now = Date.now();
      if (failing.current?.key !== key) failing.current = { key, since: now };
      const retrying = transient && now - failing.current.since < RETRYING_FOR_MS;
      setSnapshot(prev => transient && prev.key === key && now - prev.fetchedAt <= STALE_GRACE_MS
        ? { ...prev, items: markStale(prev.items, prev.fetchedAt) }
        : { key, items: Object.fromEntries(ids.map(id => [id, { job_id: id, release: null, indexing: { ...unavailableIndexing, retrying } }])), fetchedAt: 0 });
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
  const waiting = key && key.split(',').some(id => !items[id]?.indexing || items[id].indexing!.stale || ['pending', 'not_received', 'unavailable'].includes(items[id].indexing!.state));
  useVisiblePolling(() => void refresh(), key ? (waiting ? 5_000 : 30_000) : null);
  return { items, refresh };
}
