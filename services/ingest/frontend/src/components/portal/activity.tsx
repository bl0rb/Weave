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
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { DEFAULT_LOCALE, INTL_LOCALE, type Locale } from '@/i18n/config';
import { EmptyState, Notice, PortalPage, QualityGradeLegend } from './shared';

type T = (key: MessageKey, vars?: Record<string, string | number>) => string;

const PAGE_SIZE = 20;
type JobStatus = 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
type ReleaseStatus = 'pending' | 'sent' | 'failed' | null;
type StatusFilter = JobStatus | '';
type GradeFilter = 'A' | 'B' | 'C' | 'none' | '';

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

function statusMeta(t: T): Array<{ status: StatusFilter; label: string; count: keyof ActivityResponse['counts'] | null }> {
  return [
    { status: '', label: t('portal.activity.filterAll'), count: null },
    { status: 'PENDING', label: t('portal.activity.filterPending'), count: 'pending' },
    { status: 'RUNNING', label: t('portal.activity.filterRunning'), count: 'running' },
    { status: 'FINISHED', label: t('portal.activity.filterFinished'), count: 'finished' },
    { status: 'FAILED', label: t('portal.activity.filterFailed'), count: 'failed' },
  ];
}

function dateLabel(value: string, locale: Locale = DEFAULT_LOCALE): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(INTL_LOCALE[locale], {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function activityUrl(item: ActivityItem): string {
  const importStatus = normalized(item.import_status);
  if (item.import_run_id && ['pending', 'running', 'failed', 'cancelled'].includes(importStatus)) {
    return `/imports/${item.import_run_id}`;
  }
  return item.status === 'FINISHED' && item.collection_id ? `/reviews/${item.id}` : `/jobs/${item.id}`;
}

function normalized(value: string | null): string {
  return value?.trim().toLowerCase() ?? '';
}

function releaseBlocked(item: ActivityItem): boolean {
  return normalized(item.quality_recommendation) === 'block' || item.quality_grade?.trim().toUpperCase() === 'C';
}

function activityState(item: ActivityItem, t: T, locale: Locale, live?: IndexingItem): { label: string; tone: BadgeTone; hint: string } {
  if (item.release_status) return publicationState(live?.release?.status ?? item.release_status, live?.indexing, locale);
  if (item.status === 'FAILED') {
    return { label: t('portal.documents.state.failed'), tone: 'error', hint: t('portal.activity.failedHint') };
  }
  if (item.status === 'RUNNING') {
    return { label: t('portal.activity.filterRunning'), tone: 'working', hint: t('portal.activity.runningHint') };
  }
  if (item.status === 'PENDING') {
    return { label: t('portal.activity.filterPending'), tone: 'neutral', hint: t('portal.activity.pendingHint') };
  }
  const importStatus = normalized(item.import_status);
  if (item.status === 'FINISHED' && (importStatus === 'pending' || importStatus === 'running')) {
    return { label: t('portal.activity.importRunning'), tone: 'working', hint: t('portal.activity.importRunningHint') };
  }
  if (item.status === 'FINISHED' && (importStatus === 'failed' || importStatus === 'cancelled')) {
    return { label: t('portal.activity.importFailed'), tone: 'error', hint: t('portal.activity.importFailedHint') };
  }
  if (item.status === 'FINISHED' && !item.collection_id) {
    return { label: t('portal.activity.filterFinished'), tone: 'warning', hint: t('portal.activity.noCollectionHint') };
  }
  if (releaseBlocked(item)) {
    return { label: t('portal.activity.qualityRequired'), tone: 'warning', hint: t('portal.activity.qualityRequiredHint') };
  }
  return { label: t('portal.documents.state.readyForReview'), tone: 'warning', hint: t('portal.activity.readyHint') };
}

function ActivityRow({ item, live }: { item: ActivityItem; live?: IndexingItem }) {
  const { t, locale } = useI18n();
  const state = activityState(item, t, locale, live);
  const filename = item.original_filename || t('portal.activity.unnamedJob');

  return (
    <tr>
      <td>
        <Link className="portal-document-link" href={activityUrl(item)}>
          <FileText size={17} aria-hidden="true" />
          <span>{filename}</span>
        </Link>
        {item.import_run_id && (
          <Link className="portal-inline-link mt-2 text-xs" href={`/imports/${item.import_run_id}`}>
            {t('portal.activity.viewConfluenceImport')} <ArrowRight size={14} />
          </Link>
        )}
      </td>
      <td>
        {item.collection_id ? (
          <Link href={`/knowledge/${item.collection_id}`}>{item.collection_name || t('portal.activity.openSpace')}</Link>
        ) : (
          <span className="text-slate-500">{t('portal.activity.noSpace')}</span>
        )}
      </td>
      <td>
        <span aria-live="polite" className={`portal-badge portal-badge-${state.tone}`}>{state.label}</span>
        <span className="mt-2 block max-w-[300px] text-xs leading-5 text-slate-500">{state.hint}</span>
        {item.quality_grade && <span className="mt-1 block text-xs text-slate-500">{t('portal.documents.qualityGrade', { grade: item.quality_grade })}</span>}
      </td>
      <td className="portal-date">{dateLabel(item.updated_at || item.created_at, locale)}</td>
      <td>
        <Link className="portal-open" href={activityUrl(item)} aria-label={t('portal.documents.openAria', { filename })}>
          <ArrowRight size={17} />
        </Link>
      </td>
    </tr>
  );
}

export function ProcessingActivity() {
  const { t, locale } = useI18n();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<StatusFilter>('');
  const [grade, setGrade] = useState<GradeFilter>('');
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
    if (grade) params.set('quality_grade', grade);
    return `/api/v1/portal/activity?${params.toString()}`;
  }, [offset, query, status, grade]);

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
      setError(portalError(reason, locale));
    }
  }, [requestPath, locale]);

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

  const selectGrade = (nextGrade: GradeFilter) => {
    setGrade(nextGrade);
    setOffset(0);
  };

  const hasFilters = Boolean(query.trim() || status || grade);
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
      title={t('portal.chrome.breadcrumb.processing')}
      description={t('portal.activity.pageDescription')}
      eyebrow={t('portal.activity.eyebrow')}
      actions={<Link className={buttonVariants()} href="/sources/new">{t('portal.chrome.addSource')} <ArrowRight size={16} /></Link>}
    >
      <section className="portal-panel mb-6" aria-labelledby="activity-status-title">
        <div className="portal-section-heading">
          <div>
            <p className="portal-eyebrow">{t('portal.activity.glanceEyebrow')}</p>
            <h2 id="activity-status-title">{t('portal.activity.statusHeading')}</h2>
          </div>
          <Button variant="ghost" size="sm" onClick={() => { void load(); void refreshIndexing(); }} aria-label={t('portal.activity.refreshAria')}>
            <RefreshCw size={15} /> {t('common.refresh')}
          </Button>
        </div>
        <div className="portal-filter-row flex-wrap px-5 pb-5 sm:px-7" role="group" aria-label={t('portal.activity.filterByStatusAria')}>
          {statusMeta(t).map((meta) => {
              const count = meta.count
              ? currentActivity?.counts[meta.count] ?? 0
              : allCount;
            return (
              <button
                key={meta.status || 'all'}
                type="button"
                aria-pressed={status === meta.status}
                aria-label={t('portal.activity.filterButtonAria', { label: meta.label, count })}
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
            <p className="portal-eyebrow">{t('portal.activity.allJobsEyebrow')}</p>
            <h2 id="activity-list-title">{t('portal.activity.trackHeading')}{currentActivity ? ` · ${currentActivity.total}` : ''}</h2>
          </div>
          <div className="flex w-full flex-wrap gap-4 sm:w-auto sm:items-end">
            <label className="portal-search m-0 min-w-[min(100%,320px)] flex-1 sm:flex-none">
              <span className="sr-only">{t('portal.activity.searchJobsAria')}</span>
              <span className="relative block">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} aria-hidden="true" />
                <input className="!mt-0 !pl-9" type="search" value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} placeholder={t('portal.activity.searchPlaceholder')} aria-label={t('portal.activity.searchPlaceholder')} />
              </span>
            </label>
            <label className="min-w-[180px] text-sm font-semibold">
              {t('common.status')}
              <select value={status} onChange={(event) => selectStatus(event.target.value as StatusFilter)}>
                <option value="">{t('portal.activity.allStatuses')}</option>
                <option value="PENDING">{t('portal.activity.filterPending')}</option>
                <option value="RUNNING">{t('portal.activity.filterRunning')}</option>
                <option value="FINISHED">{t('portal.activity.filterFinished')}</option>
                <option value="FAILED">{t('portal.activity.filterFailed')}</option>
              </select>
            </label>
            <label className="min-w-[180px] text-sm font-semibold">
              {t('portal.activity.qualityGradeLabel')}
              <select value={grade} onChange={(event) => selectGrade(event.target.value as GradeFilter)}>
                <option value="">{t('portal.activity.allGrades')}</option>
                <option value="A">A</option>
                <option value="B">B</option>
                <option value="C">C</option>
                <option value="none">{t('portal.documents.noGrade')}</option>
              </select>
            </label>
          </div>
        </div>
        <QualityGradeLegend />

        {currentActivity === null && !currentError ? (
          <Notice>{t('portal.activity.loadingJobs')}</Notice>
        ) : currentActivity?.items.length ? (
          <>
            <div className="portal-table-scroll">
              <table className="portal-table">
                <caption className="sr-only">{t('portal.activity.tableCaption')}</caption>
                <thead>
                  <tr><th scope="col">{t('portal.activity.colJob')}</th><th scope="col">{t('portal.documents.columnSpace')}</th><th scope="col">{t('portal.documents.columnStatus')}</th><th scope="col">{t('portal.activity.colUpdated')}</th><th scope="col"><span className="sr-only">{t('common.open')}</span></th></tr>
                </thead>
                <tbody>{currentActivity.items.map((item) => <ActivityRow key={item.id} item={item} live={indexingItems[item.id]} />)}</tbody>
              </table>
            </div>
            {totalPages > 1 && (
              <nav className="portal-pagination" aria-label={t('portal.activity.paginationAria')}>
                <span>{t('portal.activity.paginationRange', { first: firstItem, last: lastItem, total: currentActivity.total })} {hasFilters ? t('portal.activity.matchingJobsSuffix') : t('portal.activity.jobsSuffix')} · {t('portal.activity.pageOf', { page: currentPage, totalPages })}</span>
                <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{t('common.back')}</Button>
                <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= currentActivity.total} onClick={() => setOffset(offset + PAGE_SIZE)}>{t('common.next')}</Button>
              </nav>
            )}
          </>
        ) : currentActivity ? (
          <EmptyState title={hasFilters ? t('portal.activity.noMatchTitle') : t('portal.activity.emptyTitle')}>
            {hasFilters ? t('portal.activity.noMatchBody') : t('portal.activity.emptyBody')}
          </EmptyState>
        ) : null}
      </section>
    </PortalPage>
  );
}
