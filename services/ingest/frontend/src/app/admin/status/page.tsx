'use client';

import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { AdminPageShell, PageHead } from '@/components/admin/admin-page-shared';
import { Badge, ErrorNotice, LoadingState, SectionCard, errorMessage } from '@/components/admin/admin-shared';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { loadAdminSystemStatus, type AdminSystemStatus, type SystemArea, type SystemStatusValue } from '@/lib/system-status';

const POLL_MS = 30_000;
const AREAS: SystemArea[] = ['portal', 'processing', 'chat'];
const TONE: Record<SystemStatusValue, 'emerald' | 'amber' | 'red'> = { ok: 'emerald', degraded: 'amber', down: 'red' };

export default function AdminStatusPage() {
  // Inside the shell: only mounted (and polling) for administrators.
  return <AdminPageShell><SystemStatusView /></AdminPageShell>;
}

function SystemStatusView() {
  const { t, formatDate } = useI18n();
  const [data, setData] = useState<AdminSystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback((force: boolean) => loadAdminSystemStatus(force)
    .then((value) => { setData(value); setError(null); })
    .catch((err) => setError(`${t('admin.system.loadFailed')} ${errorMessage(err)}`)), [t]);

  useEffect(() => {
    void load(false);
    const timer = window.setInterval(() => void load(false), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const head = (
    <PageHead
      title={t('admin.system.pageTitle')}
      description={t('admin.system.pageDescription')}
      actions={
        <Button type="button" variant="outline" disabled={refreshing} onClick={() => { setRefreshing(true); void load(true).finally(() => setRefreshing(false)); }}>
          <RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />
          {t('admin.system.refresh')}
        </Button>
      }
    />
  );

  if (!data) return <>{head}{error ? <ErrorNotice message={error} /> : <LoadingState />}</>;

  const problems = data.components.filter((component) => component.status !== 'ok').length;
  return (
    <>
      {head}
      <div className="space-y-6">
        <ErrorNotice message={error} />
        <div className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-5 py-4 ${problems ? 'border-[var(--warn)] bg-[var(--warn-bg)]' : 'border-[var(--line)] bg-[var(--ok-bg)]'}`}>
          <p className="text-[15px] font-semibold text-[var(--ink)]">
            {problems ? t('admin.system.summary.problems', { count: problems }) : t('admin.system.summary.ok')}
          </p>
          <p className="text-sm text-[var(--muted)]">
            {t('admin.system.checkedAt', { time: formatDate(data.checked_at, { dateStyle: 'short', timeStyle: 'medium' }) })}
          </p>
        </div>
        {AREAS.map((area) => {
          const components = data.components.filter((component) => component.area === area);
          if (!components.length) return null;
          return (
            <SectionCard key={area} title={t(`portal.systemStatus.area.${area}`)}>
              <div className="-mx-5 -my-5 overflow-x-auto">
                <table className="w-full min-w-[640px] table-fixed text-left text-sm">
                  <thead className="text-xs uppercase tracking-wide text-[var(--muted)]">
                    <tr className="border-b border-[var(--line)]">
                      <th scope="col" className="w-[40%] px-5 py-2.5 font-semibold">{t('admin.system.col.service')}</th>
                      <th scope="col" className="w-[18%] px-5 py-2.5 font-semibold">{t('admin.system.col.status')}</th>
                      <th scope="col" className="w-[17%] px-5 py-2.5 font-semibold">{t('admin.system.col.latency')}</th>
                      <th scope="col" className="px-5 py-2.5 font-semibold">{t('admin.system.col.detail')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--line)]">
                    {components.map((component) => (
                      <tr key={component.key}>
                        <th scope="row" className="px-5 py-3 font-medium text-[var(--ink)]">
                          {componentLabel(t, component.key)}
                          {component.target && <span className="block truncate font-mono text-xs font-normal text-[var(--muted)]">{component.target}</span>}
                        </th>
                        <td className="px-5 py-3"><Badge tone={TONE[component.status]}>{t(`admin.system.status.${component.status}`)}</Badge></td>
                        <td className="px-5 py-3 tabular-nums text-[var(--ink-2)]">{component.latency_ms === null ? '—' : `${component.latency_ms} ms`}</td>
                        <td className="px-5 py-3 text-[var(--ink-2)]">{component.detail ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          );
        })}
      </div>
    </>
  );
}

function componentLabel(t: (key: MessageKey) => string, key: string): string {
  const messageKey = `admin.system.component.${key}` as MessageKey;
  const label = t(messageKey);
  return label === messageKey ? key : label;
}
