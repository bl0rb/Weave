'use client';

import { Suspense, useEffect } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { AlertTriangle } from 'lucide-react';

import type { AdminProvider } from '@/lib/auth-types';
import type { WorkerLogsResponse } from '@/lib/api';
import { AdminPageShell, PageHead, oldTabRedirectUrl, useAdminJson } from '@/components/admin/admin-page-shared';
import { LoadingState } from '@/components/admin/admin-shared';

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

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' });
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
  const oldTab = searchParams.get('tab');
  const redirectTarget = oldTab ? oldTabRedirectUrl(oldTab) : null;

  useEffect(() => {
    if (redirectTarget) router.replace(redirectTarget);
  }, [redirectTarget, router]);

  if (redirectTarget) {
    return (
      <AdminPageShell>
        <LoadingState label="Weiterleitung…" />
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
      label: 'Verarbeitung',
      href: '/admin/verarbeitung?bereich=worker-logs',
      value: !workerLogs.data ? (workerLogs.loading ? '…' : '–') : recentErrorLog ? 'Fehler' : recentWarningLog ? 'Warnung' : 'Betriebsbereit',
      detail: recentErrorLog
        ? `Fehler ${formatTime(recentErrorLog.created_at)}`
        : recentWarningLog
          ? `Warnung ${formatTime(recentWarningLog.created_at)}`
          : latestLog
            ? `Letzter Eintrag ${formatTime(latestLog.created_at)}`
            : 'Noch keine Protokolleinträge',
      warn: Boolean(recentErrorLog || recentWarningLog),
    },
    {
      key: 'suchindex',
      label: 'Suchindex',
      href: '/admin/betrieb?bereich=werkzeuge',
      value: reindex.data ? `${reindex.data.total} Dokumente` : reindex.loading ? '…' : '–',
      detail: reindex.data ? (failedEmbeddings > 0 ? `${failedEmbeddings} fehlgeschlagen` : 'Ohne Fehler') : '',
      warn: failedEmbeddings > 0,
    },
    {
      key: 'anmeldung',
      label: 'Anmeldung',
      href: '/admin/menschen?bereich=anmeldung',
      value: !providers.data ? (providers.loading ? '…' : '–') : enabledProviders.length > 0 ? `${enabledProviders.length} Provider aktiv` : 'Keine Anmeldung aktiv',
      detail: providers.data ? `${providers.data.items.length} insgesamt konfiguriert` : '',
      warn: providers.data != null && enabledProviders.length === 0,
    },
    {
      key: 'modelle',
      label: 'Modelle / Provider',
      href: '/admin/wissen?bereich=chat-llm',
      value: !chatProvider.data ? (chatProvider.loading ? '…' : '–') : chatProvider.data.configured ? 'Bereit' : 'Nicht konfiguriert',
      detail: retrieval.data ? `${retrieval.data.embedding_model}${retrieval.data.rerank_provider !== 'none' ? ' · Reranking aktiv' : ''}` : '',
      warn: chatProvider.data != null && chatProvider.data.enabled && !chatProvider.data.configured,
    },
    {
      key: 'sicherung',
      label: 'Sicherung',
      href: '/admin/betrieb?bereich=sicherung',
      value: !backup.data ? (backup.loading ? '…' : '–') : latestExport ? `Vor ${backupAgeDays} Tag(en)` : 'Noch keine Sicherung',
      detail: 'Empfohlen: mindestens wöchentlich',
      warn: backupStale,
    },
  ];

  const attention: Attention[] = [];
  if (backupStale) {
    attention.push({
      id: 'backup-stale',
      title: latestExport ? `Letzte Sicherung ist ${backupAgeDays} Tag(e) alt` : 'Noch keine Sicherung erstellt',
      detail: 'Erstelle ein verschlüsseltes Archiv, bevor ihr Modelle oder Rechte ändert.',
      href: '/admin/betrieb?bereich=sicherung',
      label: 'Sicherung erstellen',
    });
  }
  for (const item of expiringIdentities) {
    const days = daysUntil(item.expires_at as string);
    attention.push({
      id: `identity-${item.id}`,
      title: days < 0 ? `Technische Identität „${item.name}“ ist abgelaufen` : `Technische Identität „${item.name}“ läuft bald ab`,
      detail: days < 0 ? 'Token erneuern, um die Integration wieder freizuschalten.' : `Noch ${days} Tag(e) gültig.`,
      href: '/admin/betrieb?bereich=identitaeten',
      label: 'Ansehen',
    });
  }
  if (chatProvider.data?.enabled && !chatProvider.data.configured) {
    attention.push({
      id: 'chat-provider',
      title: 'Chat-Provider ist aktiviert, aber unvollständig konfiguriert',
      detail: 'Endpoint und Modell prüfen, damit Bots zentral antworten können.',
      href: '/admin/wissen?bereich=chat-llm',
      label: 'Konfigurieren',
    });
  }
  if (failedEmbeddings > 0) {
    attention.push({
      id: 'embeddings-failed',
      title: `${failedEmbeddings} Dokument(e) beim Einbetten fehlgeschlagen`,
      detail: 'Erneut versuchen oder die Embedding-Konfiguration prüfen.',
      href: '/admin/betrieb?bereich=werkzeuge',
      label: 'Ansehen',
    });
  }
  if (failedDeliveries > 0) {
    attention.push({
      id: 'delivery-failed',
      title: `${failedDeliveries} Freigabe(n) bei der Auslieferung fehlgeschlagen`,
      detail: 'Den Wissensindex aus den Freigaben neu aufbauen.',
      href: '/admin/betrieb?bereich=werkzeuge',
      label: 'Ansehen',
    });
  }

  return (
    <>
      <PageHead
        title="Administration"
        description={attention.length > 0 ? `${attention.length} Punkt(e) brauchen in den nächsten Tagen deine Aufmerksamkeit.` : 'Alles läuft.'}
      />

      <section aria-labelledby="status-title" className="mb-6">
        <h2 className="sr-only" id="status-title">
          Systemstatus
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
            Braucht Aufmerksamkeit
          </h2>
          {attention.length > 0 && (
            <span className="inline-flex h-6 items-center rounded-full bg-amber-100 px-2.5 text-xs font-semibold text-amber-800">{attention.length}</span>
          )}
        </div>
        {attention.length === 0 ? (
          <p className="text-sm text-slate-500">Keine offenen Punkte.</p>
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
