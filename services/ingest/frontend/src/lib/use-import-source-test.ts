'use client';

import { useEffect, useState } from 'react';
import { ApiError, apiJson } from '@/lib/api';
import { TEST_COOLDOWN_FALLBACK_MS, type ImportSourceTestResponse } from '@/lib/imports';

export type ImportSourceTestOutcome =
  | { kind: 'result'; result: ImportSourceTestResponse }
  | { kind: 'error'; error: unknown; rateLimited: boolean };

/**
 * POST /api/v1/import/sources/{id}/test plus the client-side cooldown every
 * call site needs: the backend anchors its per-source cooldown on EVERY
 * attempt (import_routes.py), a 429 included, so the button re-enables in
 * step with the server instead of inviting a second attempt that can only
 * fail. One hook instance per surface (setup dialog, source form); each
 * tracks its own cooldowns by source id.
 */
export function useImportSourceTest() {
  const [testingId, setTestingId] = useState<string | null>(null);
  const [cooldowns, setCooldowns] = useState<Record<string, number>>({});
  const [nowTick, setNowTick] = useState(() => Date.now());

  useEffect(() => {
    if (!Object.values(cooldowns).some((until) => until > Date.now())) return;
    const timer = window.setInterval(() => {
      const now = Date.now();
      setNowTick(now);
      if (!Object.values(cooldowns).some((until) => until > now)) window.clearInterval(timer);
    }, 250);
    return () => window.clearInterval(timer);
  }, [cooldowns]);

  const cooldownSecondsFor = (id: string): number => Math.max(0, Math.ceil(((cooldowns[id] ?? 0) - nowTick) / 1000));

  async function test(id: string): Promise<ImportSourceTestOutcome> {
    setTestingId(id);
    try {
      const result = await apiJson<ImportSourceTestResponse>(`/api/v1/import/sources/${encodeURIComponent(id)}/test`, { method: 'POST' });
      return { kind: 'result', result };
    } catch (error) {
      return { kind: 'error', error, rateLimited: error instanceof ApiError && error.status === 429 };
    } finally {
      const now = Date.now();
      setNowTick(now);
      setCooldowns((current) => ({ ...current, [id]: now + TEST_COOLDOWN_FALLBACK_MS }));
      setTestingId(null);
    }
  }

  return { test, testingId, cooldownSecondsFor };
}
