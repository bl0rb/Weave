'use client';

import { useEffect, useState } from 'react';
import { LoaderCircle, RefreshCcw, ScrollText } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError, apiJson, type LogLevel, type WorkerLogEntry, type WorkerLogsResponse } from '@/lib/api';
import { useVisiblePolling } from '@/lib/data-cache';
import { useI18n } from '@/i18n/provider';
import {
  ErrorNotice,
  errorMessage,
  Field,
  inputClass,
  LoadingState,
  SectionCard,
  Toggle,
} from '@/components/admin/admin-shared';

/** Rows per fetch — the backend allows 1-500; 200 matches its own default. */
const LIMIT = 200;
/** Live-tail cadence while auto-refresh is on. */
const AUTO_REFRESH_MS = 5000;

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'text-slate-500',
  INFO: 'text-emerald-400',
  WARNING: 'text-amber-400',
  ERROR: 'text-red-400',
  CRITICAL: 'text-red-300',
};

/**
 * Deterministic color per worker (pod/container name) so distinct
 * replicas stay visually distinguishable in a multi-worker deployment.
 * Sky/cyan/teal/fuchsia are otherwise unused in this app, so a worker
 * chip can never be mistaken for a status color.
 */
const WORKER_COLORS = ['text-sky-400', 'text-cyan-400', 'text-teal-400', 'text-fuchsia-400'];
function workerColor(name: string): string {
  let sum = 0;
  for (let i = 0; i < name.length; i += 1) sum += name.charCodeAt(i);
  return WORKER_COLORS[sum % WORKER_COLORS.length];
}

const LEVEL_LABEL_KEYS: Record<string, 'admin.logs.levelCritical' | 'admin.logs.levelError' | 'admin.logs.levelWarning' | 'admin.logs.levelInfo' | 'admin.logs.levelDebug'> = {
  CRITICAL: 'admin.logs.levelCritical',
  ERROR: 'admin.logs.levelError',
  WARNING: 'admin.logs.levelWarning',
  INFO: 'admin.logs.levelInfo',
  DEBUG: 'admin.logs.levelDebug',
};

export function LogsTab() {
  const { t, formatDate } = useI18n();
  const [entries, setEntries] = useState<WorkerLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 404 means the backend hasn't shipped this endpoint yet, not a real
  // failure — rendered as a distinct, non-alarming notice.
  const [unavailable, setUnavailable] = useState(false);
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [level, setLevel] = useState<LogLevel | ''>('');
  const [worker, setWorker] = useState('');
  const [service, setService] = useState('');
  const [query, setQuery] = useState('');
  const hasFilters = level !== '' || worker.trim() !== '' || service !== '' || query.trim() !== '';

  // No setState before the first `await` here on purpose — callers flip
  // the relevant spinner flag themselves before calling `load`, so this
  // is safe to invoke directly from the mount effect below. `filters`
  // lets a caller fetch with values other than this render's state
  // (needed by resetFilters, whose setters only land on the next render).
  async function load(
    offset: number,
    filters?: { level: LogLevel | ''; worker: string; service: string; query: string },
  ): Promise<void> {
    const active = filters ?? { level, worker, service, query };
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
    if (active.level) params.set('level', active.level);
    if (active.worker.trim()) params.set('worker', active.worker.trim());
    if (active.service) params.set('service', active.service);
    if (active.query.trim()) params.set('q', active.query.trim());
    try {
      const res = await apiJson<WorkerLogsResponse>(
        `/api/v1/auth/admin/worker-logs?${params.toString()}`,
        { cache: 'no-store' },
      );
      setEntries((prev) => {
        if (offset === 0) return res.items;
        // Offset-based "load older" against a newest-first, continuously
        // growing table: rows inserted between the page-0 fetch and this
        // fetch shift the window, so the older page can re-include rows
        // already rendered. Drop those by id to avoid duplicate rows and
        // duplicate React keys.
        const existingIds = new Set(prev.map((e) => e.id));
        return [...prev, ...res.items.filter((e) => !existingIds.has(e.id))];
      });
      setTotal(res.total);
      setError(null);
      setUnavailable(false);
      setLastFetchedAt(new Date());
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setUnavailable(true);
        setError(null);
      } else {
        setError(errorMessage(err));
        setUnavailable(false);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
      setLoadingMore(false);
    }
  }

  // Initial load only — filters apply on demand (Apply filters / Enter),
  // matching the rest of the app's filter forms (see document-browser.tsx).
  useEffect(() => {
    const run = async () => {
      await load(0);
    };
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-refresh always re-pulls the newest page (offset 0) and replaces
  // what's visible — any "Load older" pages fetched below get dropped on
  // the next tick, same as the tail-with-refresh pattern the endpoint
  // recommends. Pauses while the tab is hidden. No spinner here — that's
  // reserved for user-triggered fetches so the tail doesn't flicker.
  useVisiblePolling(() => void load(0), autoRefresh ? AUTO_REFRESH_MS : null);

  function refresh() {
    setRefreshing(true);
    void load(0);
  }

  function loadMore() {
    setLoadingMore(true);
    void load(entries.length);
  }

  function resetFilters() {
    setLevel('');
    setWorker('');
    setQuery('');
    // The setters above only land on the next render, and this closure's
    // `load` still sees the old state — pass the cleared values explicitly.
    setRefreshing(true);
    void load(0, { level: '', worker: '', service: '', query: '' });
    void load(0, { level: '', worker: '', service: '', query: '' });
  }

  function onEnterApply(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      refresh();
    }
  }

  const hasMore = entries.length < total;
  const serviceSummary = entries.reduce<Record<string, { count: number; errors: number }>>((summary, entry) => {
    const current = summary[entry.service] ?? { count: 0, errors: 0 };
    current.count += 1;
    if (entry.level === 'ERROR' || entry.level === 'CRITICAL') current.errors += 1;
    summary[entry.service] = current;
    return summary;
  }, {});
  const serviceOptions = Object.keys(serviceSummary).sort();

  return (
    <SectionCard
      title={t('admin.logs.title')}
      description={t('admin.logs.description')}
      actions={
        <>
          <Toggle checked={autoRefresh} onChange={setAutoRefresh} label={t('admin.logs.autoRefresh')} />
          <Button variant="outline" size="sm" onClick={refresh} disabled={refreshing}>
            {refreshing ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCcw className="h-4 w-4" />
            )}
            {t('common.refresh')}
          </Button>
        </>
      }
    >
      <div className="mb-4 flex flex-wrap items-end gap-3 rounded-xl border border-slate-100 bg-slate-50/60 p-3">
        <div className="w-36">
          <Field label={t('admin.logs.levelLabel')}>
            <select
              value={level}
              onChange={(e) => setLevel(e.target.value as LogLevel | '')}
              className={inputClass}
            >
              <option value="">{t('admin.logs.allLevels')}</option>
              <option value="CRITICAL">{t('admin.logs.levelCritical')}</option>
              <option value="ERROR">{t('admin.logs.levelError')}</option>
              <option value="WARNING">{t('admin.logs.levelWarning')}</option>
              <option value="INFO">{t('admin.logs.levelInfo')}</option>
              <option value="DEBUG">{t('admin.logs.levelDebug')}</option>
            </select>
          </Field>
        </div>
        <div className="w-48">
          <Field label={t('admin.logs.serviceLabel')}>
            <select value={service} onChange={(e) => setService(e.target.value)} className={inputClass}>
              <option value="">{t('admin.logs.allServices')}</option>
              {serviceOptions.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </Field>
        </div>
        <div className="w-48">
          <Field label={t('admin.logs.workerLabel')}>
            <input
              value={worker}
              onChange={(e) => setWorker(e.target.value)}
              onKeyDown={onEnterApply}
              placeholder={t('admin.logs.workerPlaceholder')}
              className={inputClass}
            />
          </Field>
        </div>
        <div className="min-w-[12rem] flex-1">
          <Field label={t('common.search')}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onEnterApply}
              placeholder={t('admin.logs.searchPlaceholder')}
              className={inputClass}
            />
          </Field>
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={refresh}>
            {t('admin.logs.applyFilters')}
          </Button>
          <Button variant="outline" size="sm" onClick={resetFilters} disabled={!hasFilters}>
            {t('admin.logs.reset')}
          </Button>
        </div>
      </div>

      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
        <span>{total > 0 ? t('admin.logs.showingCount', { shown: entries.length, total }) : ''}</span>
        <span>{lastFetchedAt ? t('admin.logs.updatedAt', { time: formatDate(lastFetchedAt, { timeStyle: 'medium' }) }) : ''}</span>
      </div>
      {Object.keys(serviceSummary).length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2" aria-label={t('admin.logs.summaryAriaLabel')}>
          {Object.entries(serviceSummary).sort(([a], [b]) => a.localeCompare(b)).map(([name, summary]) => (
            <button key={name} type="button" onClick={() => { setService(name); setRefreshing(true); void load(0, { level, worker, service: name, query }); }} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-left text-xs text-slate-600 hover:border-emerald-300 hover:bg-emerald-50">
              <strong className="text-slate-800">{name}</strong> · {t('admin.logs.entriesCount', { count: summary.count })}
              {summary.errors ? ` · ${t('admin.logs.errorsCount', { count: summary.errors })}` : ''}
            </button>
          ))}
        </div>
      )}

      <ErrorNotice message={error} />
      {unavailable && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {t('admin.logs.unavailable')}
        </div>
      )}

      {loading ? (
        <LoadingState label={t('admin.logs.loading')} />
      ) : unavailable ? null : entries.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <ScrollText className="h-8 w-8 text-slate-300" />
          <p className="text-sm text-slate-500">
            {hasFilters ? t('admin.logs.emptyFiltered') : t('admin.logs.emptyNone')}
          </p>
        </div>
      ) : (
        <>
          <div className="max-h-[32rem] overflow-y-auto overflow-x-hidden rounded-xl border border-slate-800 bg-slate-950 p-4 font-mono text-xs">
            {entries.map((entry) => (
              <LogRow key={entry.id} entry={entry} />
            ))}
          </div>
          {hasMore && (
            <div className="mt-3 flex justify-center">
              <Button variant="outline" size="sm" onClick={loadMore} disabled={loadingMore}>
                {loadingMore && <LoaderCircle className="h-4 w-4 animate-spin" />}
                {t('admin.logs.loadOlder')}
              </Button>
            </div>
          )}
        </>
      )}
    </SectionCard>
  );
}

function LogRow({ entry }: { entry: WorkerLogEntry }) {
  const { t, formatDate } = useI18n();
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="border-b border-slate-900/60 py-1.5 last:border-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="shrink-0 text-slate-500">
          {formatDate(entry.created_at, { timeStyle: 'medium' })}
        </span>
        <span className="shrink-0 rounded bg-slate-800 px-1.5 text-emerald-300" title={t('admin.logs.serviceLabel')}>
          {entry.service}
        </span>
        <span
          className={`shrink-0 w-20 font-semibold ${LEVEL_COLORS[entry.level] ?? 'text-slate-300'}`}
        >
          {LEVEL_LABEL_KEYS[entry.level] ? t(LEVEL_LABEL_KEYS[entry.level]) : entry.level}
        </span>
        <span
          className={`shrink-0 rounded px-1.5 ${workerColor(entry.worker_name)}`}
          title={entry.worker_name}
        >
          {entry.worker_name}
        </span>
        {entry.task_name && <span className="shrink-0 text-slate-500">[{entry.task_name}]</span>}
        <span className="whitespace-pre-wrap break-all text-slate-200">{entry.message}</span>
        {entry.exc_text && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="shrink-0 rounded px-1.5 text-[11px] font-semibold text-red-400 underline decoration-dotted hover:text-red-300"
          >
            {expanded ? t('admin.logs.hideTraceback') : t('admin.logs.showTraceback')}
          </button>
        )}
      </div>
      {expanded && entry.exc_text && (
        <pre className="mt-1 overflow-x-auto rounded-lg bg-slate-900 p-2 text-[11px] text-red-300">
          {entry.exc_text}
        </pre>
      )}
    </div>
  );
}
