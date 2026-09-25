'use client';

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore, type CSSProperties } from 'react';
import Link from 'next/link';
import { AlertTriangle, ArrowRight, CheckCheck, Clock3, FileText } from 'lucide-react';
import { useAuth } from '@/lib/auth-context';
import { buttonVariants } from '@/components/ui/button';
import { dateLabel, loadDocuments, pipelineStage, portalError, type PipelineStage, type PortalDocument } from '@/lib/portal';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { Notice, PortalPage } from './shared';

/** Bounds the stat tiles / "Als Nächstes" / "Aktivität" below to the most recent N documents visible to the user — see the /aufgaben and /documents pages for the full, paginated lists. */
const HOME_DOCUMENT_LIMIT = 200;

const noSubscription = () => () => {};

/** The browser's local hour, or null during SSR/hydration: the server's
 * time zone differs from the user's, so rendering it there would produce a
 * hydration text mismatch. */
function useLocalHour(): number | null {
  return useSyncExternalStore(noSubscription, () => new Date().getHours(), () => null);
}

function greeting(t: (key: MessageKey) => string, hour: number | null): string {
  if (hour === null) return t('portal.home.greeting.day');
  if (hour < 11) return t('portal.home.greeting.morning');
  if (hour < 18) return t('portal.home.greeting.day');
  return t('portal.home.greeting.evening');
}

function cssVar(color: string): CSSProperties {
  return { '--c': color } as CSSProperties;
}

const ACTIVITY_LABEL: Record<PipelineStage, MessageKey> = {
  processing: 'portal.home.activity.processing',
  review: 'portal.documents.state.readyForReview',
  indexing: 'portal.indexing.released.label',
  ready: 'portal.home.tile.ready.label',
  error: 'portal.home.activity.error',
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
  const { t, locale } = useI18n();
  const hour = useLocalHour();
  const [documents, setDocuments] = useState<PortalDocument[] | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => loadDocuments(undefined, 0, 'all', undefined, HOME_DOCUMENT_LIMIT)
    .then(page => { setDocuments(page.items); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
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

  const summary = documents === null ? t('portal.home.summaryLoading') : counts.review || counts.error
    ? [counts.review && t('portal.home.summaryReview', { count: counts.review }),
      counts.error && t('portal.home.summaryError', { count: counts.error })].filter(Boolean).join(' · ')
    : t('portal.home.summaryAllClear');

  return (
    <PortalPage title={`${greeting(t, hour)}${user ? `, ${user.username}` : ''}.`} description={summary}>
      {error && <Notice error action={load}>{error}</Notice>}
      <section aria-labelledby="stand-title">
        <h2 className="sr-only" id="stand-title">{t('portal.home.statusHeading')}</h2>
        <div className="portal-tiles">
          <Link className="portal-tile" style={cssVar('var(--warn)')} href="/documents?stand=review">
            <span className="portal-tile-label">{t('portal.home.tile.review.label')}</span>
            <strong>{documents === null ? '–' : counts.review}</strong>
            <small>{t('portal.home.tile.review.hint')}</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--proc)')} href="/documents?stand=processing">
            <span className="portal-tile-label">{t('portal.home.tile.processing.label')}</span>
            <strong>{documents === null ? '–' : counts.processing}</strong>
            <small>{t('portal.home.tile.processing.hint')}</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--err)')} href="/documents?stand=error">
            <span className="portal-tile-label">{t('common.error')}</span>
            <strong>{documents === null ? '–' : counts.error}</strong>
            <small>{t('portal.home.tile.error.hint')}</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--ok)')} href="/documents?stand=ready">
            <span className="portal-tile-label">{t('portal.home.tile.ready.label')}</span>
            <strong>{documents === null ? '–' : counts.ready}</strong>
            <small>{t('portal.pipeline.ready.hint')}</small>
          </Link>
        </div>
      </section>
      <div className="portal-overview-grid">
        <section className="portal-panel portal-tasks-card" aria-labelledby="next-title">
          <header className="portal-section-heading">
            <h2 id="next-title">{t('portal.home.nextTitle')}</h2>
            {nextTasks.length > 0 && <span className="portal-pill">{nextTasks.length}</span>}
          </header>
          {documents === null ? <p role="status" className="portal-loading">{t('portal.home.tasksLoading')}</p> : nextTasks.length ? <ul className="portal-task-list" aria-live="polite">
            {nextTasks.map(document => {
              const stage = stageOf(document);
              const isError = stage === 'error';
              return <li className="portal-task" key={document.id}>
                <span className={`portal-task-icon ${isError ? 'portal-task-icon-err' : ''}`}>
                  {isError ? <AlertTriangle size={18} aria-hidden="true" /> : <FileText size={18} aria-hidden="true" />}
                </span>
                <span className="portal-task-text"><strong>{document.original_filename}</strong><small>{document.collection_name}</small></span>
                <Link className={buttonVariants({ size: 'sm', variant: isError ? 'outline' : 'default' })} href={isError ? `/jobs/${document.id}` : `/reviews/${document.id}`}>
                  {isError ? t('portal.tasks.viewJob') : t('portal.tasks.reviewAction')}
                </Link>
              </li>;
            })}
          </ul> : <div className="portal-task-empty"><CheckCheck size={26} aria-hidden="true" /><span>{t('portal.home.nextEmpty')}</span></div>}
          <Link className="portal-card-foot" href="/aufgaben">{t('portal.home.viewAllTasks')} <ArrowRight size={16} aria-hidden="true" /></Link>
        </section>
        <section className="portal-panel" aria-labelledby="activity-title">
          <header className="portal-section-heading"><h2 id="activity-title">{t('portal.home.activityTitle')}</h2></header>
          {documents === null ? <p role="status" className="portal-loading">{t('portal.home.activityLoading')}</p> : recentActivity.length ? <ol className="portal-activity">
            {recentActivity.map(document => {
              const stage = stageOf(document);
              return <li key={document.id} style={cssVar(ACTIVITY_COLOR[stage])}>
                <strong>{t(ACTIVITY_LABEL[stage])}</strong> · {document.original_filename}
                <time>{dateLabel(document.release?.created_at ?? document.created_at, locale)}</time>
              </li>;
            })}
          </ol> : <div className="portal-task-empty"><Clock3 size={26} aria-hidden="true" /><span>{t('portal.home.activityEmpty')}</span></div>}
        </section>
      </div>
    </PortalPage>
  );
}
