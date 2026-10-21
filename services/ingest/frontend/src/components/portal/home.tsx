'use client';

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore, type CSSProperties, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { AlertTriangle, ArrowRight, CheckCheck, Clock3, FilePlus, FileText, Search } from 'lucide-react';
import { useAuth } from '@/lib/auth-context';
import { firstName } from '@/lib/utils';
import { buttonVariants } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { dateLabel, documentState, documentUrl, loadDocuments, loadPortalStats, pipelineStage, portalError, type PipelineStage, type PortalDocument, type PortalStats } from '@/lib/portal';
import type { ImportSync, ImportSyncListResponse } from '@/lib/imports';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { Notice, PortalPage, SourceOrigin } from './shared';
import { ImportSyncSummary } from './import-sync';

/** Bounds the stat tiles / "Als Nächstes" / "Aktivität" below to the most recent N documents visible to the user — see /documents for the full, paginated list. */
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

/** Inline hits under the Übersicht search. */
const SEARCH_HITS = 5;

const documentsLink = (params: Record<string, string>) => `/documents?${new URLSearchParams(params)}`;

function cssVar(color: string): CSSProperties {
  return { '--c': color } as CSSProperties;
}

// 'decided' (parked/skipped) is labelled via documentState.
const ACTIVITY_LABEL: Record<Exclude<PipelineStage, 'decided'>, MessageKey> = {
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
  decided: 'var(--muted)',
};

export function PortalHome() {
  const { user } = useAuth();
  const { t, locale } = useI18n();
  const hour = useLocalHour();
  const [documents, setDocuments] = useState<PortalDocument[] | null>(null);
  const [error, setError] = useState('');
  const router = useRouter();
  const [search, setSearch] = useState('');
  // Hits belong to the query they were loaded for, so a cleared or changed search never shows stale ones.
  const [hitsFor, setHitsFor] = useState<{ q: string; items: PortalDocument[] } | null>(null);
  // Metrics are best-effort: a failure here never blocks the page.
  const [stats, setStats] = useState<PortalStats | null>(null);
  const [syncs, setSyncs] = useState<ImportSync[]>([]);
  useEffect(() => {
    loadPortalStats().then(setStats).catch(() => setStats(null));
    apiJson<ImportSyncListResponse>('/api/v1/import/syncs', { cache: 'no-store' }).then(response => setSyncs(response.items)).catch(() => setSyncs([]));
  }, []);
  // Top hits while typing (debounced), from the same search the Dokumente page uses.
  useEffect(() => {
    const needle = search.trim();
    if (needle.length < 2) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      loadDocuments(undefined, 0, 'all', undefined, SEARCH_HITS, undefined, { q: needle })
        .then(page => { if (!cancelled) setHitsFor({ q: needle, items: page.items }); })
        .catch(() => { if (!cancelled) setHitsFor(null); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [search]);
  const hits = hitsFor && hitsFor.q === search.trim() ? hitsFor.items : null;
  function submitSearch(event: FormEvent) {
    event.preventDefault();
    if (search.trim()) router.push(documentsLink({ q: search.trim() }));
  }
  const load = useCallback(() => loadDocuments(undefined, 0, 'all', undefined, HOME_DOCUMENT_LIMIT)
    .then(page => { setDocuments(page.items); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);

  const releasedIds = useMemo(() => (documents ?? []).filter(document => document.release).map(document => document.id), [documents]);
  const { items: live } = useIndexingStatus(releasedIds);
  const stageOf = useCallback((document: PortalDocument) => pipelineStage(document, live[document.id]), [live]);

  const counts = useMemo(() => {
    const totals: Record<PipelineStage, number> = { processing: 0, review: 0, indexing: 0, ready: 0, error: 0, decided: 0 };
    for (const document of documents ?? []) totals[stageOf(document)] += 1;
    return totals;
  }, [documents, stageOf]);

  const nextTasks = useMemo(() => (documents ?? [])
    .filter(document => ['review', 'error'].includes(stageOf(document)))
    .slice(0, 3), [documents, stageOf]);

  const recentActivity = useMemo(() => [...(documents ?? [])]
    .sort((a, b) => new Date(b.release?.created_at ?? b.created_at).getTime() - new Date(a.release?.created_at ?? a.created_at).getTime())
    .slice(0, 5), [documents]);

  return (
    <PortalPage title={`${greeting(t, hour)}${user ? `, ${firstName(user.username)}` : ''}.`} actions={<Link className={buttonVariants()} href="/sources/new"><FilePlus size={16} aria-hidden="true" />{t('portal.chrome.addSource')}</Link>}>
      {error && <Notice error action={load}>{error}</Notice>}
      <section className="portal-panel portal-home-search" aria-labelledby="home-search-title">
        <h2 className="sr-only" id="home-search-title">{t('portal.home.search.heading')}</h2>
        <form role="search" onSubmit={submitSearch} className="flex flex-wrap items-center gap-2">
          <Search size={18} aria-hidden="true" className="text-[var(--muted)]" />
          <input className="min-w-[200px] flex-1" type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder={t('portal.home.search.placeholder')} aria-label={t('portal.home.search.heading')} />
          <button type="submit" className={buttonVariants({ size: 'sm' })}>{t('portal.home.search.submit')}</button>
        </form>
        {hits && (hits.length ? <ul className="portal-task-list mt-3" aria-label={t('portal.home.search.hitsAria')}>
          {hits.map(document => <li className="portal-task" key={document.id}>
            <span className="portal-task-icon"><FileText size={18} aria-hidden="true" /></span>
            <span className="portal-task-text"><strong><Link href={documentUrl(document)}>{document.original_filename}</Link></strong><small>{document.collection_name}<SourceOrigin document={document} /></small></span>
          </li>)}
        </ul> : <p className="mt-3 text-sm text-[var(--muted)]">{t('portal.documents.noMatchTitle')}</p>)}
      </section>
      <section aria-labelledby="stand-title">
        <h2 className="sr-only" id="stand-title">{t('portal.home.statusHeading')}</h2>
        <div className="portal-tiles">
          <Link className="portal-tile" style={cssVar('var(--warn)')} href={documentsLink({ ansicht: 'review' })}>
            <span className="portal-tile-label">{t('portal.home.tile.review.label')}</span>
            <strong>{documents === null ? '–' : counts.review}</strong>
            <small>{t('portal.home.tile.review.hint')}</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--proc)')} href={documentsLink({ stand: 'processing' })}>
            <span className="portal-tile-label">{t('portal.home.tile.processing.label')}</span>
            <strong>{documents === null ? '–' : counts.processing}</strong>
            <small>{t('portal.home.tile.processing.hint')}</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--err)')} href={documentsLink({ ansicht: 'failed' })}>
            <span className="portal-tile-label">{t('common.error')}</span>
            <strong>{documents === null ? '–' : counts.error}</strong>
            <small>{t('portal.home.tile.error.hint')}</small>
          </Link>
          <Link className="portal-tile" style={cssVar('var(--ok)')} href={documentsLink({ stand: 'ready' })}>
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
          <Link className="portal-card-foot" href={documentsLink({ ansicht: 'review' })}>{t('portal.home.viewAllTasks')} <ArrowRight size={16} aria-hidden="true" /></Link>
        </section>
        <section className="portal-panel" aria-labelledby="activity-title">
          <header className="portal-section-heading"><h2 id="activity-title">{t('portal.home.activityTitle')}</h2></header>
          {documents === null ? <p role="status" className="portal-loading">{t('portal.home.activityLoading')}</p> : recentActivity.length ? <ol className="portal-activity">
            {recentActivity.map(document => {
              const stage = stageOf(document);
              return <li key={document.id} style={cssVar(ACTIVITY_COLOR[stage])}>
                <strong>{stage === 'decided' ? documentState(document, undefined, locale).label : t(ACTIVITY_LABEL[stage])}</strong> · {document.original_filename}
                <time>{dateLabel(document.release?.created_at ?? document.created_at, locale)}</time>
              </li>;
            })}
          </ol> : <div className="portal-task-empty"><Clock3 size={26} aria-hidden="true" /><span>{t('portal.home.activityEmpty')}</span></div>}
        </section>
      </div>
      <HomeMetrics stats={stats} syncs={syncs} />
    </PortalPage>
  );
}

function waitLabel(hours: number, t: (key: MessageKey, vars?: Record<string, string | number>) => string): string {
  return hours < 48 ? t('portal.home.metrics.hours', { count: Math.max(1, Math.round(hours)) }) : t('portal.home.metrics.days', { count: Math.round(hours / 24) });
}

/** Per space, sync and throughput metrics (GET /api/v1/portal/stats, GET /api/v1/import/syncs); each figure links into the matching Dokumente view. */
function HomeMetrics({ stats, syncs }: { stats: PortalStats | null; syncs: ImportSync[] }) {
  const { t, locale } = useI18n();
  const maxWeek = Math.max(1, ...(stats?.releases_per_week ?? []).map(week => week.count));
  return <div className="portal-overview-grid mt-6">
    {stats && stats.spaces.length > 0 && <section className="portal-panel" aria-labelledby="metrics-spaces-title">
      <header className="portal-section-heading"><h2 id="metrics-spaces-title">{t('portal.home.metrics.spacesTitle')}</h2></header>
      <div className="portal-table-scroll"><table className="portal-table">
        <thead><tr><th scope="col">{t('portal.documents.columnSpace')}</th><th scope="col">{t('portal.home.metrics.total')}</th><th scope="col">{t('portal.pipeline.ready.label')}</th><th scope="col">{t('portal.home.tile.review.label')}</th><th scope="col">{t('common.error')}</th></tr></thead>
        <tbody>{stats.spaces.map(space => <tr key={space.collection_id}>
          <th scope="row"><Link href={documentsLink({ bereich: space.slug })}>{space.name}</Link></th>
          <td><Link href={documentsLink({ bereich: space.slug, ansicht: 'all' })}>{space.total}</Link></td>
          <td><Link href={documentsLink({ bereich: space.slug, stand: 'ready' })}>{space.in_chat}</Link></td>
          <td><Link href={documentsLink({ bereich: space.slug, ansicht: 'review' })}>{space.review}</Link></td>
          <td><Link href={documentsLink({ bereich: space.slug, ansicht: 'failed' })}>{space.failed}</Link></td>
        </tr>)}</tbody>
      </table></div>
    </section>}
    {stats && <section className="portal-panel" aria-labelledby="metrics-throughput-title">
      <header className="portal-section-heading"><h2 id="metrics-throughput-title">{t('portal.home.metrics.throughputTitle')}</h2></header>
      <dl className="grid gap-3 px-5 pb-2 text-sm sm:grid-cols-3">
        <div><dt className="text-xs text-[var(--muted)]">{t('portal.home.metrics.waiting')}</dt><dd className="text-lg font-semibold"><Link href={documentsLink({ ansicht: 'review' })}>{stats.review_waiting}</Link></dd></div>
        <div><dt className="text-xs text-[var(--muted)]">{t('portal.home.metrics.medianWait')}</dt><dd className="text-lg font-semibold">{stats.review_wait_median_hours === null ? '–' : waitLabel(stats.review_wait_median_hours, t)}</dd></div>
        <div><dt className="text-xs text-[var(--muted)]">{t('portal.home.metrics.oldestWait')}</dt><dd className="text-lg font-semibold">{stats.review_oldest_since ? dateLabel(stats.review_oldest_since, locale) : '–'}</dd></div>
      </dl>
      <div className="px-5 pb-5">
        <h3 className="text-xs text-[var(--muted)]">{t('portal.home.metrics.releasesPerWeek')}</h3>
        <ol className="portal-week-bars" aria-label={t('portal.home.metrics.releasesPerWeek')}>
          {stats.releases_per_week.map(week => <li key={week.week_start} title={t('portal.home.metrics.weekBar', { date: dateLabel(week.week_start, locale), count: week.count })}>
            <span className="portal-week-bar" style={{ height: `${Math.round((week.count / maxWeek) * 100)}%` }} aria-hidden="true" />
            <small>{week.count}</small>
            <span className="sr-only">{t('portal.home.metrics.weekBar', { date: dateLabel(week.week_start, locale), count: week.count })}</span>
          </li>)}
        </ol>
      </div>
    </section>}
    {syncs.length > 0 && <section className="portal-panel" aria-labelledby="metrics-syncs-title">
      <header className="portal-section-heading"><h2 id="metrics-syncs-title">{t('portal.home.metrics.syncsTitle')}</h2><Link className="ml-auto text-sm" href="/connections">{t('portal.chrome.breadcrumb.connections')}</Link></header>
      <ul className="grid gap-3 px-5 pb-5">{syncs.map(sync => <li key={sync.id} className="flex flex-wrap items-start justify-between gap-2">
        <ImportSyncSummary sync={sync} />
        {sync.collection_id && sync.collection_name && <Link className="portal-inline-link text-xs" href={`/knowledge/${encodeURIComponent(sync.collection_id)}`}>{sync.collection_name}</Link>}
      </li>)}</ul>
    </section>}
  </div>;
}
