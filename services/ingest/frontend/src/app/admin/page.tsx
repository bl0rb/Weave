'use client';

import { Suspense, useEffect } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { AlertTriangle } from 'lucide-react';

import type { AdminProvider } from '@/lib/auth-types';
import type { WorkerLogsResponse } from '@/lib/api';
import { AdminPageShell, PageHead, oldTabRedirectUrl, useAdminJson } from '@/components/admin/admin-page-shared';
import { LoadingState } from '@/components/admin/admin-shared';
import { useI18n } from '@/i18n/provider';

// Narrow local shapes of the data each admin tab already fetches — only the
// fields the Übersicht's tiles/attention card actually use. Kept local
// instead of importing from the tab files to avoid coupling this page to
// component-internal types.
type BackupRun = { kind: 'export' | 'import'; status: 'queued' | 'running' | 'finished' | 'failed'; created_at: string; finished_at: string | null };
type TechnicalIdentity = { id: string; name: string; expires_at: string | null; revoked_at: string | null };
type ChatProviderConfig = { configured: boolean; enabled: boolean };
type RetrievalConfig = { embedding_model: string; rerank_provider: string };
type ReindexStatus = { total: number; by_status: Record<string, number> };
type RebuildStatus = { total_releases: number; pending: number; sent: number; failed: number };

const STALE_BACKUP_DAYS = 7;
const EXPIRING_IDENTITY_DAYS = 30;

function daysSince(iso: string): number {
  return Math.floor((Date.now() - new Date(iso).getTime()) / (1000 * 60 * 60 * 24));
}

function daysUntil(iso: string): number {
  return Math.floor((new Date(iso).getTime() - Date.now()) / (1000 * 60 * 60 * 24));
}

export default function AdminPage() {
  // useSearchParams (to read a legacy ?tab=) requires a Suspense boundary,
  // same as app/connections/page.tsx.
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <AdminPageInner />
    </Suspense>
  );
}

function AdminPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { t } = useI18n();
  const oldTab = searchParams.get('tab');
  const redirectTarget = oldTab ? oldTabRedirectUrl(oldTab) : null;

  useEffect(() => {
    if (redirectTarget) router.replace(redirectTarget);
  }, [redirectTarget, router]);

  if (redirectTarget) {
    return (
      <AdminPageShell>
        <LoadingState label={t('admin.overview.redirecting')} />
      </AdminPageShell>
    );
  }

  return (
    <AdminPageShell>
      <Overview />
    </AdminPageShell>
  );
}

type Tile = { key: string; label: string; href: string; value: string; detail: string; warn: boolean };
type Attention = { id: string; title: string; detail: string; href: string; label: string };

function Overview() {
  const { t, formatDate } = useI18n();
  const formatTime = (iso: string) => formatDate(iso, { dateStyle: 'short', timeStyle: 'short' });
  const backup = useAdminJson<{ runs: BackupRun[] }>('/api/v1/admin/backup/runs');
  const identities = useAdminJson<{ items: TechnicalIdentity[] }>('/api/v1/auth/admin/technical-identities');
  const providers = useAdminJson<{ items: AdminProvider[] }>('/api/v1/auth/admin/providers');
  const chatProvider = useAdminJson<ChatProviderConfig>('/api/v1/auth/admin/chat-provider');
  const retrieval = useAdminJson<RetrievalConfig>('/api/v1/auth/admin/retrieval-provider');
  const reindex = useAdminJson<ReindexStatus>('/api/v1/admin/retrieval-provider/reindex-status');
  const rebuild = useAdminJson<RebuildStatus>('/api/v1/admin/knowledge/rebuild-status');
  const workerLogs = useAdminJson<WorkerLogsResponse>('/api/v1/auth/admin/worker-logs?limit=5');

  const latestExport = backup.data
    ? [...backup.data.runs]
        .filter((run) => run.kind === 'export' && run.status === 'finished')
        .sort((a, b) => new Date(b.finished_at ?? b.created_at).getTime() - new Date(a.finished_at ?? a.created_at).getTime())[0] ?? null
    : null;
  const backupAgeDays = latestExport ? daysSince(latestExport.finished_at ?? latestExport.created_at) : null;
  const backupStale = backup.data != null && (latestExport === null || (backupAgeDays ?? 0) >= STALE_BACKUP_DAYS);

  const expiringIdentities = (identities.data?.items ?? [])
    .filter((item) => !item.revoked_at && item.expires_at && daysUntil(item.expires_at) <= EXPIRING_IDENTITY_DAYS);

  const enabledProviders = (providers.data?.items ?? []).filter((p) => p.enabled);
  const failedEmbeddings = reindex.data?.by_status.failed ?? 0;
  const failedDeliveries = rebuild.data?.failed ?? 0;

  const recentErrorLog = workerLogs.data?.items.find((e) => e.level === 'ERROR' || e.level === 'CRITICAL') ?? null;
  const recentWarningLog = workerLogs.data?.items.find((e) => e.level === 'WARNING') ?? null;
  const latestLog = workerLogs.data?.items[0] ?? null;

  const tiles: Tile[] = [
    {
      key: 'verarbeitung',
      label: t('admin.overview.tile.processing.label'),
      href: '/admin/verarbeitung?bereich=worker-logs',
      value: !workerLogs.data
        ? workerLogs.loading
          ? '…'
          : '–'
        : recentErrorLog
          ? t('admin.overview.tile.processing.error')
          : recentWarningLog
            ? t('admin.overview.tile.processing.warning')
            : t('admin.overview.tile.processing.ok'),
      detail: recentErrorLog
        ? t('admin.overview.tile.processing.detailError', { time: formatTime(recentErrorLog.created_at) })
        : recentWarningLog
          ? t('admin.overview.tile.processing.detailWarning', { time: formatTime(recentWarningLog.created_at) })
          : latestLog
            ? t('admin.overview.tile.processing.detailLatest', { time: formatTime(latestLog.created_at) })
            : t('admin.overview.tile.processing.detailNone'),
      warn: Boolean(recentErrorLog || recentWarningLog),
    },
    {
      key: 'suchindex',
      label: t('admin.overview.tile.searchIndex.label'),
      href: '/admin/betrieb?bereich=werkzeuge',
      value: reindex.data ? t('common.documents.count', { count: reindex.data.total }) : reindex.loading ? '…' : '–',
      detail: reindex.data
        ? failedEmbeddings > 0
          ? t('admin.overview.tile.searchIndex.detailFailed', { count: failedEmbeddings })
          : t('admin.overview.tile.searchIndex.detailOk')
        : '',
      warn: failedEmbeddings > 0,
    },
    {
      key: 'anmeldung',
      label: t('admin.overview.tile.signIn.label'),
      href: '/admin/menschen?bereich=anmeldung',
      value: !providers.data
        ? providers.loading
          ? '…'
          : '–'
        : enabledProviders.length > 0
          ? t('admin.overview.tile.signIn.active', { count: enabledProviders.length })
          : t('admin.overview.tile.signIn.none'),
      detail: providers.data ? t('admin.overview.tile.signIn.detailTotal', { count: providers.data.items.length }) : '',
      warn: providers.data != null && enabledProviders.length === 0,
    },
    {
      key: 'modelle',
      label: t('admin.overview.tile.models.label'),
      href: '/admin/wissen?bereich=chat-llm',
      value: !chatProvider.data
        ? chatProvider.loading
          ? '…'
          : '–'
        : chatProvider.data.configured
          ? t('admin.overview.tile.models.ready')
          : t('admin.overview.tile.models.notConfigured'),
      detail: retrieval.data
        ? `${retrieval.data.embedding_model}${retrieval.data.rerank_provider !== 'none' ? t('admin.overview.tile.models.rerankSuffix') : ''}`
        : '',
      warn: chatProvider.data != null && chatProvider.data.enabled && !chatProvider.data.configured,
    },
    {
      key: 'sicherung',
      label: t('admin.overview.tile.backup.label'),
      href: '/admin/betrieb?bereich=sicherung',
      value: !backup.data
        ? backup.loading
          ? '…'
          : '–'
        : latestExport
          ? t('admin.overview.tile.backup.age', { count: backupAgeDays ?? 0 })
          : t('admin.overview.tile.backup.none'),
      detail: t('admin.overview.tile.backup.detail'),
      warn: backupStale,
    },
  ];

  const attention: Attention[] = [];
  if (backupStale) {
    attention.push({
      id: 'backup-stale',
      title: latestExport
        ? t('admin.overview.attention.backupStale.title', { count: backupAgeDays ?? 0 })
        : t('admin.overview.attention.backupNone.title'),
      detail: t('admin.overview.attention.backup.detail'),
      href: '/admin/betrieb?bereich=sicherung',
      label: t('admin.overview.attention.backup.cta'),
    });
  }
  for (const item of expiringIdentities) {
    const days = daysUntil(item.expires_at as string);
    attention.push({
      id: `identity-${item.id}`,
      title:
        days < 0
          ? t('admin.overview.attention.identityExpired.title', { name: item.name })
          : t('admin.overview.attention.identityExpiring.title', { name: item.name }),
      detail:
        days < 0
          ? t('admin.overview.attention.identityExpired.detail')
          : t('admin.overview.attention.identityExpiring.detail', { count: days }),
      href: '/admin/betrieb?bereich=identitaeten',
      label: t('admin.overview.attention.view'),
    });
  }
  if (chatProvider.data?.enabled && !chatProvider.data.configured) {
    attention.push({
      id: 'chat-provider',
      title: t('admin.overview.attention.chatProvider.title'),
      detail: t('admin.overview.attention.chatProvider.detail'),
      href: '/admin/wissen?bereich=chat-llm',
      label: t('admin.overview.attention.chatProvider.cta'),
    });
  }
  if (failedEmbeddings > 0) {
    attention.push({
      id: 'embeddings-failed',
      title: t('admin.overview.attention.embeddingsFailed.title', { count: failedEmbeddings }),
      detail: t('admin.overview.attention.embeddingsFailed.detail'),
      href: '/admin/betrieb?bereich=werkzeuge',
      label: t('admin.overview.attention.view'),
    });
  }
  if (failedDeliveries > 0) {
    attention.push({
      id: 'delivery-failed',
      title: t('admin.overview.attention.deliveryFailed.title', { count: failedDeliveries }),
      detail: t('admin.overview.attention.deliveryFailed.detail'),
      href: '/admin/betrieb?bereich=werkzeuge',
      label: t('admin.overview.attention.view'),
    });
  }

  return (
    <>
      <PageHead
        title={t('admin.title')}
        description={
          attention.length > 0
            ? t('admin.overview.attentionCount', { count: attention.length })
            : t('admin.overview.allGood')
        }
      />

      <section aria-labelledby="status-title" className="mb-6">
        <h2 className="sr-only" id="status-title">
          {t('admin.overview.statusHeading')}
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {tiles.map((tile) => (
            <Link
              key={tile.key}
              href={tile.href}
              className={`rounded-xl border p-4 transition hover:border-emerald-400 ${
                tile.warn ? 'border-amber-200 bg-amber-50' : 'border-slate-200 bg-white'
              }`}
            >
              <span className="block text-xs font-medium text-slate-500">{tile.label}</span>
              <strong className="mt-1 block text-lg font-semibold text-slate-950">{tile.value}</strong>
              <span className="mt-1 block text-xs text-slate-500">{tile.detail}</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5" aria-labelledby="attention-title">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h2 id="attention-title" className="text-[17px] font-semibold text-slate-950">
            {t('admin.overview.attentionHeading')}
          </h2>
          {attention.length > 0 && (
            <span className="inline-flex h-6 items-center rounded-full bg-amber-100 px-2.5 text-xs font-semibold text-amber-800">{attention.length}</span>
          )}
        </div>
        {attention.length === 0 ? (
          <p className="text-sm text-slate-500">{t('admin.overview.attentionEmpty')}</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {attention.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
                  <div>
                    <strong className="block text-sm font-medium text-slate-950">{item.title}</strong>
                    <span className="block text-xs text-slate-500">{item.detail}</span>
                  </div>
                </div>
                <Link
                  href={item.href}
                  className="flex h-8 flex-shrink-0 items-center rounded-lg border border-slate-200 px-3 text-xs font-medium text-emerald-800 hover:bg-emerald-50"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
