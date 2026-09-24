'use client';

import { useEffect, useState } from 'react';
import { Bot } from 'lucide-react';

import { useAuth } from '@/lib/auth-context';
import { VlConnectionsTab } from '@/components/admin/vl-connections-tab';
import { apiJson, type VLConnection, type VLConnectionListResponse } from '@/lib/api';
import { ErrorNotice, errorMessage, LoadingState, SectionCard } from '@/components/admin/admin-shared';
import { useI18n } from '@/i18n/provider';

/**
 * VL connections are admin-managed, but every user needs visibility into
 * which ones exist (e.g. to pick one for a benchmark run). Admins get the
 * full CRUD tab; everyone else gets a read-only list.
 */
export function VlConnectionsPanel() {
  const { user } = useAuth();

  if (user?.role === 'admin') {
    return <VlConnectionsTab />;
  }

  return <ReadOnlyVlConnections />;
}

function ReadOnlyVlConnections() {
  const { t } = useI18n();
  const [connections, setConnections] = useState<VLConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson<VLConnectionListResponse>('/api/v1/vl-connections', { cache: 'no-store' });
        if (cancelled) return;
        setConnections(data.items);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(errorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <SectionCard
      title={t('portal.connections.vl.title')}
      description={t('portal.connections.vl.description')}
    >
      <div className="mb-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
        {t('portal.connections.vl.adminManaged')}
      </div>
      <ErrorNotice message={error} />
      {loading ? (
        <LoadingState label={t('portal.connections.vl.loading')} />
      ) : connections.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <Bot className="h-8 w-8 text-slate-300" />
          <p className="text-sm text-slate-500">{t('portal.connections.vl.empty')}</p>
        </div>
      ) : (
        <ul className="space-y-4">
          {connections.map((connection) => (
            <li key={connection.id} className="rounded-xl border border-slate-200 p-4">
              <span className="text-sm font-semibold text-slate-950">{connection.name}</span>
              <dl className="mt-2 space-y-1 text-xs text-slate-500">
                <div className="flex gap-2">
                  <dt className="w-16 flex-shrink-0 font-medium">{t('portal.connections.vl.modelLabel')}</dt>
                  <dd className="break-all font-mono">{connection.model}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
