'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { useI18n } from '@/i18n/provider';
import { loadSystemStatus, type SystemStatus } from '@/lib/system-status';

const POLL_MS = 60_000;

const DOT: Record<SystemStatus['status'] | 'unknown', string> = {
  ok: 'bg-[var(--ok)]',
  degraded: 'bg-[var(--warn)]',
  down: 'bg-[var(--err)]',
  unknown: 'bg-[var(--muted)]',
};

/**
 * Sidebar line "Alle Systeme betriebsbereit" / "Eingeschränkt: …" for every
 * signed-in person, polled once a minute. Admins get a link to the full
 * per-service page (/admin/status).
 */
export function SystemStatusIndicator({ isAdmin, onNavigate }: { isAdmin: boolean; onNavigate?: () => void }) {
  const { t } = useI18n();
  // Re-read on navigation too (the backend caches a probe round for 15 s).
  const pathname = usePathname();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const load = () => loadSystemStatus(controller.signal)
      .then((value) => {
        // An unexpected body (proxy error page, older backend) reads as "unknown", never as a crash.
        if (!Array.isArray(value?.areas)) throw new Error('unexpected system status');
        setStatus(value); setFailed(false);
      })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    void load();
    const timer = window.setInterval(load, POLL_MS);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [pathname]);

  const limited = status?.areas.filter((area) => area.status !== 'ok') ?? [];
  const label = failed ? t('portal.systemStatus.unknown')
    : !status ? t('portal.systemStatus.checking')
      : status.status === 'ok' ? t('portal.systemStatus.ok')
        : t('portal.systemStatus.limited', { areas: limited.map((area) => t(`portal.systemStatus.area.${area.key}`)).join(', ') });
  const dot = DOT[failed || !status ? 'unknown' : status.status];
  const className = 'flex items-center gap-2 rounded-[10px] bg-[var(--surface-2)] px-2.5 py-2 text-xs leading-relaxed text-[var(--ink-2)]';
  const content = <>
    <span className={`h-2 w-2 flex-shrink-0 rounded-full ${dot}`} aria-hidden="true" />
    <span className="min-w-0">{label}</span>
  </>;

  return isAdmin
    ? <Link href="/admin/status" onClick={onNavigate} role="status" aria-live="polite" className={`${className} no-underline hover:bg-[var(--hover)]`}>{content}</Link>
    : <p role="status" aria-live="polite" className={className}>{content}</p>;
}
