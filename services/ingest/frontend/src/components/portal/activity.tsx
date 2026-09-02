'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, FileText, RefreshCw, Search } from 'lucide-react';

import { Button, buttonVariants } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { useVisiblePolling } from '@/lib/data-cache';
import { portalError } from '@/lib/portal';
import { publicationState, type IndexingItem } from '@/lib/indexing-status';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { EmptyState, Notice, PortalPage } from './shared';

const PAGE_SIZE = 20;
type JobStatus = 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
type ReleaseStatus = 'pending' | 'sent' | 'failed' | null;
type StatusFilter = JobStatus | '';

export type ActivityItem = {
  id: string;
  original_filename: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  collection_id: string | null;
  collection_name: string | null;
  import_run_id: string | null;
  import_status: string | null;
  release_status: ReleaseStatus;
  quality_grade: string | null;
  quality_recommendation: string | null;
};

type ActivityResponse = {
  items: ActivityItem[];
  total: number;
  counts: { pending: number; running: number; finished: number; failed: number };
};

type BadgeTone = 'neutral' | 'working' | 'warning' | 'success' | 'error';

const statusMeta: Array<{ status: StatusFilter; label: string; count: keyof ActivityResponse['counts'] | null }> = [
  { status: '', label: 'Alle Aufträge', count: null },
  { status: 'PENDING', label: 'Wartet', count: 'pending' },
  { status: 'RUNNING', label: 'In Verarbeitung', count: 'running' },
  { status: 'FINISHED', label: 'Verarbeitet', count: 'finished' },
  { status: 'FAILED', label: 'Fehlgeschlagen', count: 'failed' },
];

function dateLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function activityUrl(item: ActivityItem): string {
  return item.status === 'FINISHED' && item.collection_id ? `/reviews/${item.id}` : `/jobs/${item.id}`;
}

function normalized(value: string | null): string {
  return value?.trim().toLowerCase() ?? '';
}

function releaseBlocked(item: ActivityItem): boolean {
  return normalized(item.quality_recommendation) === 'block' || item.quality_grade?.trim().toUpperCase() === 'C';
}

function activityState(item: ActivityItem, live?: IndexingItem): { label: string; tone: BadgeTone; hint: string } {
  if (item.release_status) return publicationState(live?.release?.status ?? item.release_status, live?.indexing);
  if (item.status === 'FAILED') {
    return { label: 'Verarbeitung fehlgeschlagen', tone: 'error', hint: 'Öffne den Auftrag für die Fehlermeldung und mögliche nächste Schritte.' };
  }
  if (item.status === 'RUNNING') {
    return { label: 'In Verarbeitung', tone: 'working', hint: 'Der Auftrag wird gerade verarbeitet.' };
  }
  if (item.status === 'PENDING') {
    return { label: 'Wartet', tone: 'neutral', hint: 'Der Auftrag wartet auf die Verarbeitung.' };
  }
  const importStatus = normalized(item.import_status);
  if (item.status === 'FINISHED' && (importStatus === 'pending' || importStatus === 'running')) {
    return { label: 'Import läuft', tone: 'working', hint: 'Der zugehörige Confluence-Import ist noch nicht abgeschlossen.' };
  }
  if (item.status === 'FINISHED' && (importStatus === 'failed' || importStatus === 'cancelled')) {
    return { label: 'Import prüfen', tone: 'error', hint: 'Der zugehörige Confluence-Import ist fehlgeschlagen oder wurde beendet. Öffne den Import für Details.' };
  }
  if (item.status === 'FINISHED' && !item.collection_id) {
    return { label: 'Verarbeitet', tone: 'warning', hint: 'Für eine Freigabe füge die Quelle einem Wissensbereich hinzu.' };
  }
  if (releaseBlocked(item)) {
    return { label: 'Qualitätsprüfung blockiert', tone: 'error', hint: 'Die Freigabe ist wegen der Qualitätsprüfung blockiert. Bitte kläre den Inhalt vor einer Freigabe.' };
  }
  return { label: 'Bereit zur Prüfung', tone: 'warning', hint: 'Prüfe den Inhalt und entscheide anschließend über die Freigabe.' };
}

function ActivityRow({ item, live }: { item: ActivityItem; live?: IndexingItem }) {
  const state = activityState(item, live);
  const filename = item.original_filename || 'Unbenannter Auftrag';

  return (
    <tr>
      <td>
        <Link className="portal-document-link" href={activityUrl(item)}>
          <FileText size={17} aria-hidden="true" />
          <span>{filename}</span>
        </Link>
        {item.import_run_id && (
          <Link className="portal-inline-link mt-2 text-xs" href={`/imports/${item.import_run_id}`}>
            Confluence-Import ansehen <ArrowRight size={14} />
          </Link>
        )}
      </td>
      <td>
        {item.collection_id ? (
          <Link href={`/knowledge/${item.collection_id}`}>{item.collection_name || 'Wissensbereich öffnen'}</Link>
        ) : (
          <span className="text-slate-500">Ohne Wissensbereich</span>
        )}
      </td>
      <td>
        <span aria-live="polite" className={`portal-badge portal-badge-${state.tone}`}>{state.label}</span>
        <span className="mt-2 block max-w-[300px] text-xs leading-5 text-slate-500">{state.hint}</span>
        {item.quality_grade && <span className="mt-1 block text-xs text-slate-500">Qualitätsstufe {item.quality_grade}</span>}
      </td>
      <td className="portal-date">{dateLabel(item.updated_at || item.created_at)}</td>
      <td>
        <Link className="portal-open" href={activityUrl(item)} aria-label={`${filename} öffnen`}>
          <ArrowRight size={17} />
        </Link>
      </td>
    </tr>
  );
}

export function ProcessingActivity() {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<StatusFilter>('');
  const [offset, setOffset] = useState(0);
  const [activity, setActivity] = useState<ActivityResponse | null>(null);
  const [loadedPath, setLoadedPath] = useState('');
  const [error, setError] = useState('');
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const requestPath = useMemo(() => {
    const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) });
    if (query.trim()) params.set('q', query.trim());
    if (status) params.set('status', status);
    return `/api/v1/portal/activity?${params.toString()}`;
  }, [offset, query, status]);

  const load = useCallback(async () => {
    const currentRequest = ++requestId.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;

    try {
      const response = await apiJson<ActivityResponse>(requestPath, { cache: 'no-store', signal: nextController.signal });
      if (currentRequest !== requestId.current) return;
      setActivity(response);
      setLoadedPath(requestPath);
      setError('');
    } catch (reason) {
      if (nextController.signal.aborted || currentRequest !== requestId.current) return;
      setLoadedPath(requestPath);
      setError(portalError(reason));
    }
  }, [requestPath]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => {
      cancelled = true;
      controller.current?.abort();
    };
  }, [load]);

  const currentActivity = loadedPath === requestPath ? activity : null;
  const currentError = loadedPath === requestPath ? error : '';
  const { items: indexingItems, refresh: refreshIndexing } = useIndexingStatus(currentActivity?.items.filter(item => item.release_status).map(item => item.id) ?? []);
  const activeJobs = Boolean(currentActivity && (currentActivity.counts.pending > 0 || currentActivity.counts.running > 0));
  useVisiblePolling(() => void load(), activeJobs ? 5_000 : 20_000);

  const selectStatus = (nextStatus: StatusFilter) => {
    setStatus(nextStatus);
    setOffset(0);
  };

  const hasFilters = Boolean(query.trim() || status);
  const allCount = currentActivity
    ? status
      ? currentActivity.counts.pending + currentActivity.counts.running + currentActivity.counts.finished + currentActivity.counts.failed
      : currentActivity.total
    : 0;
  const totalPages = currentActivity ? Math.ceil(currentActivity.total / PAGE_SIZE) : 0;
  const currentPage = offset / PAGE_SIZE + 1;
  const firstItem = currentActivity && currentActivity.total > 0 ? offset + 1 : 0;
  const lastItem = currentActivity ? Math.min(offset + PAGE_SIZE, currentActivity.total) : 0;

  return (
    <PortalPage
      title="Verarbeitung"
      description="Behalte Uploads und Confluence-Inhalte im Blick, prüfe die Ergebnisse und entscheide über ihre Freigabe."
      eyebrow="DEIN VERARBEITUNGSVERLAUF"
      actions={<Link className={buttonVariants()} href="/sources/new">Quelle hinzufügen <ArrowRight size={16} /></Link>}
    >
      <section className="portal-panel mb-6" aria-labelledby="activity-status-title">
        <div className="portal-section-heading">
          <div>
            <p className="portal-eyebrow">AUF EINEN BLICK</p>
            <h2 id="activity-status-title">Auftragsstatus</h2>
          </div>
          <Button variant="ghost" size="sm" onClick={() => { void load(); void refreshIndexing(); }} aria-label="Verarbeitung aktualisieren">
            <RefreshCw size={15} /> Aktualisieren
          </Button>
        </div>
        <div className="portal-filter-row flex-wrap px-5 pb-5 sm:px-7" role="group" aria-label="Aufträge nach Status filtern">
          {statusMeta.map((meta) => {
              const count = meta.count
              ? currentActivity?.counts[meta.count] ?? 0
              : allCount;
            return (
              <button
                key={meta.status || 'all'}
                type="button"
                aria-pressed={status === meta.status}
                aria-label={`${meta.label}: ${count} Aufträge anzeigen`}
                onClick={() => selectStatus(meta.status)}
                className="flex min-w-[115px] flex-1 flex-col items-start gap-1 text-left sm:min-w-[130px]"
              >
                <span>{meta.label}</span>
                <strong className="text-xl text-slate-900">{count}</strong>
              </button>
            );
          })}
        </div>
      </section>

      {currentError && <Notice error action={() => void load()}>{currentError}</Notice>}

      <section className="portal-panel" aria-labelledby="activity-list-title">
        <div className="portal-section-heading items-end flex-wrap">
          <div>
            <p className="portal-eyebrow">ALLE AUFTRÄGE</p>
            <h2 id="activity-list-title">Verarbeitung nachverfolgen{currentActivity ? ` · ${currentActivity.total}` : ''}</h2>
          </div>
          <div className="flex w-full flex-wrap gap-4 sm:w-auto sm:items-end">
            <label className="portal-search m-0 min-w-[min(100%,320px)] flex-1 sm:flex-none">
              <span className="sr-only">Aufträge durchsuchen</span>
              <span className="relative block">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} aria-hidden="true" />
                <input className="!mt-0 !pl-9" type="search" value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} placeholder="Dateiname suchen" aria-label="Dateiname suchen" />
              </span>
            </label>
            <label className="min-w-[180px] text-sm font-semibold">
              Status
              <select value={status} onChange={(event) => selectStatus(event.target.value as StatusFilter)}>
                <option value="">Alle Status</option>
                <option value="PENDING">Wartet</option>
                <option value="RUNNING">In Verarbeitung</option>
                <option value="FINISHED">Verarbeitet</option>
                <option value="FAILED">Fehlgeschlagen</option>
              </select>
            </label>
          </div>
        </div>

        {currentActivity === null && !currentError ? (
          <Notice>Verarbeitungsaufträge werden geladen …</Notice>
        ) : currentActivity?.items.length ? (
          <>
            <div className="portal-table-scroll">
              <table className="portal-table">
                <caption className="sr-only">Verarbeitungsaufträge mit Wissensbereich, Status und Aktualisierungszeit</caption>
                <thead>
                  <tr><th scope="col">Auftrag</th><th scope="col">Wissensbereich</th><th scope="col">Stand</th><th scope="col">Aktualisiert</th><th scope="col"><span className="sr-only">Öffnen</span></th></tr>
                </thead>
                <tbody>{currentActivity.items.map((item) => <ActivityRow key={item.id} item={item} live={indexingItems[item.id]} />)}</tbody>
              </table>
            </div>
            {totalPages > 1 && (
              <nav className="portal-pagination" aria-label="Auftragsseiten">
                <span>{firstItem}–{lastItem} von {currentActivity.total}{hasFilters ? ' passenden Aufträgen' : ' Aufträgen'} · Seite {currentPage} von {totalPages}</span>
                <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Zurück</Button>
                <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= currentActivity.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Weiter</Button>
              </nav>
            )}
          </>
        ) : currentActivity ? (
          <EmptyState title={hasFilters ? 'Keine passenden Aufträge' : 'Noch keine Verarbeitung'}>
            {hasFilters ? 'Ändere deine Suche oder den Statusfilter, um weitere Aufträge zu sehen.' : 'Sobald du eine Quelle hinzufügst, erscheinen ihre Verarbeitungsaufträge hier.'}
          </EmptyState>
        ) : null}
      </section>
    </PortalPage>
  );
}
