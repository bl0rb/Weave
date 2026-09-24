'use client';

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import Link from 'next/link';
import { AlertTriangle, ArrowRight, CheckCheck, Clock3, FileText } from 'lucide-react';
import { useAuth } from '@/lib/auth-context';
import { buttonVariants } from '@/components/ui/button';
import { dateLabel, loadDocuments, pipelineStage, portalError, type PipelineStage, type PortalDocument } from '@/lib/portal';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { Notice, PortalPage } from './shared';

/** Bounds the stat tiles / "Als Nächstes" / "Aktivität" below to the most recent N documents visible to the user — see the /aufgaben and /documents pages for the full, paginated lists. */
const HOME_DOCUMENT_LIMIT = 200;

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 11) return 'Guten Morgen';
  if (hour < 18) return 'Guten Tag';
  return 'Guten Abend';
}

function cssVar(color: string): CSSProperties {
  return { '--c': color } as CSSProperties;
}

const ACTIVITY_LABEL: Record<PipelineStage, string> = {
  processing: 'Verarbeitung gestartet',
  review: 'Bereit zur Prüfung',
  indexing: 'Freigegeben',
  ready: 'Im Chat verfügbar',
  error: 'Import fehlgeschlagen',
};
const ACTIVITY_COLOR: Record<PipelineStage, string> = {
  processing: 'var(--proc)',
  review: 'var(--warn)',
  indexing: 'var(--idx)',
  ready: 'var(--ok)',
  error: 'var(--err)',
};

export function PortalHome() {
  const { user } = useAuth();
  const [documents, setDocuments] = useState<PortalDocument[] | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => loadDocuments(undefined, 0, 'all', undefined, HOME_DOCUMENT_LIMIT)
    .then(page => { setDocuments(page.items); setError(''); })
    .catch(err => setError(portalError(err))), []);
  useEffect(() => { void load(); }, [load]);

  const releasedIds = useMemo(() => (documents ?? []).filter(document => document.release).map(document => document.id), [documents]);
  const { items: live } = useIndexingStatus(releasedIds);
  const stageOf = useCallback((document: PortalDocument) => pipelineStage(document, live[document.id]), [live]);

  const counts = useMemo(() => {
    const totals: Record<PipelineStage, number> = { processing: 0, review: 0, indexing: 0, ready: 0, error: 0 };
    for (const document of documents ?? []) totals[stageOf(document)] += 1;
    return totals;
  }, [documents, stageOf]);

  const nextTasks = useMemo(() => (documents ?? [])
    .filter(document => ['review', 'error'].includes(stageOf(document)))
    .slice(0, 3), [documents, stageOf]);

  const recentActivity = useMemo(() => [...(documents ?? [])]
    .sort((a, b) => new Date(b.release?.created_at ?? b.created_at).getTime() - new Date(a.release?.created_at ?? a.created_at).getTime())
    .slice(0, 5), [documents]);

  const summary = documents === null ? 'Dein Wissen wird geladen …' : counts.review || counts.error
    ? [counts.review && `${counts.review} ${counts.review === 1 ? 'Dokument wartet' : 'Dokumente warten'} auf deine Freigabe`,
      counts.error && `${counts.error} ${counts.error === 1 ? 'Import braucht' : 'Importe brauchen'} Hilfe`].filter(Boolean).join(' · ')
    : 'Alles geprüft – dein Team arbeitet mit dem aktuellen Stand.';

  return (
    <PortalPage title={`${greeting()}${user ? `, ${user.username}` : ''}.`} description={summary}>
      {error && <Notice error action={load}>{error}</Notice>}
      <section aria-labelledby="stand-title">
        <h2 className="sr-only" id="stand-title">Stand deines Wissens</h2>
        <div className="portal-tiles">
          <Link className="portal-tile" style={cssVar('var(--warn)')} href="/documents?stand=review">
            <span className="portal-tile-label">Zu prüfen</span>
            <strong>{documents === null ? '–' : counts.review}</strong>
            <small>wartet auf deine Freigabe</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--proc)')} href="/documents?stand=processing">
            <span className="portal-tile-label">In Arbeit</span>
            <strong>{documents === null ? '–' : counts.processing}</strong>
            <small>wird automatisch verarbeitet</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--err)')} href="/documents?stand=error">
            <span className="portal-tile-label">Fehler</span>
            <strong>{documents === null ? '–' : counts.error}</strong>
            <small>Import braucht Hilfe</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--ok)')} href="/documents?stand=ready">
            <span className="portal-tile-label">Im Chat verfügbar</span>
            <strong>{documents === null ? '–' : counts.ready}</strong>
            <small>für Berechtigte</small>
          </Link>
        </div>
      </section>
      <div className="portal-overview-grid">
        <section className="portal-panel portal-tasks-card" aria-labelledby="next-title">
          <header className="portal-section-heading">
            <h2 id="next-title">Als Nächstes</h2>
            {nextTasks.length > 0 && <span className="portal-pill">{nextTasks.length}</span>}
          </header>
          {documents === null ? <p role="status" className="portal-loading">Aufgaben werden geladen …</p> : nextTasks.length ? <ul className="portal-task-list" aria-live="polite">
            {nextTasks.map(document => {
              const stage = stageOf(document);
              const isError = stage === 'error';
              return <li className="portal-task" key={document.id}>
                <span className={`portal-task-icon ${isError ? 'portal-task-icon-err' : ''}`}>
                  {isError ? <AlertTriangle size={18} aria-hidden="true" /> : <FileText size={18} aria-hidden="true" />}
                </span>
                <span className="portal-task-text"><strong>{document.original_filename}</strong><small>{document.collection_name}</small></span>
                <Link className={buttonVariants({ size: 'sm', variant: isError ? 'outline' : 'default' })} href={isError ? `/jobs/${document.id}` : `/reviews/${document.id}`}>
                  {isError ? 'Auftrag ansehen' : 'Prüfen'}
                </Link>
              </li>;
            })}
          </ul> : <div className="portal-task-empty"><CheckCheck size={26} aria-hidden="true" /><span>Alles erledigt – dein Team arbeitet mit dem aktuellen Stand.</span></div>}
          <Link className="portal-card-foot" href="/aufgaben">Alle Aufgaben ansehen <ArrowRight size={16} aria-hidden="true" /></Link>
        </section>
        <section className="portal-panel" aria-labelledby="activity-title">
          <header className="portal-section-heading"><h2 id="activity-title">Aktivität</h2></header>
          {documents === null ? <p role="status" className="portal-loading">Aktivität wird geladen …</p> : recentActivity.length ? <ol className="portal-activity">
            {recentActivity.map(document => {
              const stage = stageOf(document);
              return <li key={document.id} style={cssVar(ACTIVITY_COLOR[stage])}>
                <strong>{ACTIVITY_LABEL[stage]}</strong> · {document.original_filename}
                <time>{dateLabel(document.release?.created_at ?? document.created_at)}</time>
              </li>;
            })}
          </ol> : <div className="portal-task-empty"><Clock3 size={26} aria-hidden="true" /><span>Noch keine Aktivität.</span></div>}
        </section>
      </div>
    </PortalPage>
  );
}
