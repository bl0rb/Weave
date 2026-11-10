'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, LoaderCircle, RefreshCcw, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError, apiJson } from '@/lib/api';
import { useVisiblePolling } from '@/lib/data-cache';
import type { MessageKey } from '@/i18n/messages';
import { useI18n } from '@/i18n/provider';
import { Badge, ConfirmDialog, ErrorNotice, LoadingState, SectionCard, Toggle, errorMessage } from './admin-shared';

// Re-ingest: re-process every document and re-import every connection with
// the current conversion and quality rating (app/api/reingest.py). Starting
// is a deliberate, expensive admin decision -- the admin confirms the exact
// preview counts, and the backend refuses (409) if the inventory changed since.

const BASE_PATH = '/api/v1/admin/reingest';
const POLL_INTERVAL_MS = 3000;
const RUN_LIMIT = 10;
const ITEM_LIMIT = 100;

type Preview = {
  documents: { rerelease: number; reprocess: number; skipped: Record<string, number>; imported: number };
  connections: { ready: number; blocked: Record<string, number> };
  publication_configured: boolean;
  knowledge_available: boolean;
  active_run_id: string | null;
};

type RunStatus = 'running' | 'cancelling' | 'finished' | 'failed' | 'cancelled';
type Kind = 'document' | 'connection';

type Counts = {
  pending?: number;
  queued?: number;
  done?: number;
  failed?: number;
  skipped?: number;
  results: Record<string, number>;
};

type Run = {
  id: string;
  status: RunStatus;
  options: {
    include_documents: boolean;
    include_connections: boolean;
    rerelease: boolean;
    accept_quality_warnings: boolean;
    include_edited: boolean;
  };
  counts: Record<Kind, Counts>;
  summary: Record<string, unknown> | null;
  error_message: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
};

type RunItem = {
  id: number;
  kind: Kind;
  action: string;
  status: string;
  result: string | null;
  message: string | null;
  label: string | null;
  job_id: string | null;
  new_job_id: string | null;
  sync_id: string | null;
  import_run_id: string | null;
  updated_at: string;
};

type Translate = ReturnType<typeof useI18n>['t'];

const STATUS_TONES: Record<RunStatus, 'slate' | 'amber' | 'emerald' | 'red'> = {
  running: 'amber',
  cancelling: 'amber',
  finished: 'emerald',
  failed: 'red',
  cancelled: 'slate',
};

function isRunActive(run: Run): boolean {
  return run.status === 'running' || run.status === 'cancelling';
}

/** Label for a backend reason/result code: the first group that knows it, else the raw code. */
function codeLabel(t: Translate, groups: string[], code: string | null): string {
  if (!code) return '–';
  for (const group of groups) {
    const key = `admin.reingest.${group}.${code}` as MessageKey;
    const label = t(key);
    if (label !== key) return label;
  }
  return code;
}

function runKinds(run: Run): Kind[] {
  const kinds: Kind[] = [];
  if (run.options?.include_documents) kinds.push('document');
  if (run.options?.include_connections) kinds.push('connection');
  return kinds;
}

function countsOf(run: Run, kind: Kind): Counts {
  return run.counts?.[kind] ?? { results: {} };
}

/** Processed = done + failed + skipped; total = every item status count. */
function progressOf(counts: Counts): { processed: number; total: number } {
  let total = 0;
  for (const [key, value] of Object.entries(counts)) {
    if (key !== 'results' && typeof value === 'number') total += value;
  }
  return { processed: (counts.done ?? 0) + (counts.failed ?? 0) + (counts.skipped ?? 0), total };
}

function sum(values: Record<string, number>): number {
  return Object.values(values).reduce((total, value) => total + value, 0);
}

export function ReingestTab() {
  const { t } = useI18n();
  const [includeDocuments, setIncludeDocuments] = useState(true);
  const [includeConnections, setIncludeConnections] = useState(true);
  const [rerelease, setRerelease] = useState(true);
  const [acceptQualityWarnings, setAcceptQualityWarnings] = useState(true);
  const [includeEdited, setIncludeEdited] = useState(false);

  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [runs, setRuns] = useState<Run[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);

  const [confirmingStart, setConfirmingStart] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [cancelling, setCancelling] = useState<Run | null>(null);

  // Without a configured publication nothing can be released again: the
  // switch shows off and preview + start use rerelease=false.
  const publicationConfigured = preview?.publication_configured ?? true;
  const effectiveRerelease = rerelease && publicationConfigured;

  // The preview endpoint is rate limited: it is loaded on mount, when an
  // option that changes it flips, after a refused start and once a run ends
  // -- never on a timer. Only the newest request may set state.
  const previewRequest = useRef(0);
  const loadPreview = useCallback(async () => {
    const request = ++previewRequest.current;
    setPreviewLoading(true);
    try {
      const data = await apiJson<Preview>(`${BASE_PATH}/preview?include_edited=${includeEdited}&rerelease=${effectiveRerelease}`);
      if (request === previewRequest.current) {
        setPreview(data);
        setPreviewError(null);
      }
    } catch (err) {
      if (request === previewRequest.current) setPreviewError(errorMessage(err));
    } finally {
      if (request === previewRequest.current) setPreviewLoading(false);
    }
  }, [includeEdited, effectiveRerelease]);

  const reloadRuns = useCallback(async () => {
    try {
      const data = await apiJson<{ items: Run[] }>(`${BASE_PATH}/runs?limit=${RUN_LIMIT}`);
      setRuns(data.items);
      setRunsError(null);
    } catch (err) {
      setRunsError(errorMessage(err));
    } finally {
      setRunsLoading(false);
    }
  }, []);

  // Initial loads; the preview again whenever its query changes.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadPreview();
  }, [loadPreview]);

  useEffect(() => {
    // reloadRuns only sets state after its await (same as backup-tab).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reloadRuns();
  }, [reloadRuns]);

  const activeRun = runs.find(isRunActive) ?? null;
  const activeRunId = activeRun?.id ?? null;

  useVisiblePolling(() => {
    void reloadRuns();
  }, activeRunId ? POLL_INTERVAL_MS : null);

  // A run that ended changed the inventory and frees the start button.
  const hadActiveRun = useRef(false);
  useEffect(() => {
    if (activeRunId) {
      hadActiveRun.current = true;
    } else if (hadActiveRun.current) {
      hadActiveRun.current = false;
      void loadPreview();
    }
  }, [activeRunId, loadPreview]);

  const expectedDocuments = preview && includeDocuments ? preview.documents.rerelease + preview.documents.reprocess : 0;
  const expectedConnections = preview && includeConnections ? preview.connections.ready : 0;
  const knowledgeBlocked = preview != null && effectiveRerelease && !preview.knowledge_available;
  const previewRunId = preview?.active_run_id ?? null;
  // The run list wins once it knows the run the preview reported.
  const runActive = activeRun != null || (previewRunId != null && !runs.some((run) => run.id === previewRunId));
  const nothingToDo = preview != null && expectedDocuments + expectedConnections === 0;
  const canStart = preview != null && !previewLoading && !previewError && !runActive && !nothingToDo && !knowledgeBlocked;
  const history = runs.filter((run) => run !== activeRun);

  const start = async () => {
    try {
      await apiJson<Run>(`${BASE_PATH}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          include_documents: includeDocuments,
          include_connections: includeConnections,
          rerelease: effectiveRerelease,
          accept_quality_warnings: effectiveRerelease && acceptQualityWarnings,
          include_edited: includeEdited,
          expected_documents: expectedDocuments,
          expected_connections: expectedConnections,
          // Only the publishing part must still match the confirmed preview;
          // documents finishing meanwhile just change what is re-processed.
          expected_rereleases: includeDocuments && preview ? preview.documents.rerelease : 0,
        }),
      });
    } catch (err) {
      // 409/503: another run, a restore, a changed inventory or Knowledge
      // gone -- reload so the admin re-checks the new numbers (the dialog
      // shows the backend's detail).
      if (err instanceof ApiError && (err.status === 409 || err.status === 503)) {
        setAcknowledged(false);
        void loadPreview();
        void reloadRuns();
      }
      throw err;
    }
    setConfirmingStart(false);
    setAcknowledged(false);
    await reloadRuns();
  };

  const optionHint = (text: string | null) =>
    text && <p className="mt-1 ml-[46px] text-xs text-[var(--muted)]">{text}</p>;

  return (
    <div className="space-y-4">
      <SectionCard title={t('admin.reingest.title')} description={t('admin.reingest.description')}>
        <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">
          <li>{t('admin.reingest.explain.released')}</li>
          <li>{t('admin.reingest.explain.unreleased')}</li>
          <li>{t('admin.reingest.explain.edited')}</li>
          <li>{t('admin.reingest.explain.connections')}</li>
          <li>{t('admin.reingest.explain.cancel')}</li>
        </ul>

        <h3 className="mt-6 mb-3 font-semibold text-slate-950">{t('admin.reingest.optionsTitle')}</h3>
        <div className="space-y-3">
          <Toggle checked={includeDocuments} onChange={setIncludeDocuments} label={t('admin.reingest.option.documents')} />
          <Toggle checked={includeConnections} onChange={setIncludeConnections} label={t('admin.reingest.option.connections')} />
          <div>
            <Toggle
              checked={effectiveRerelease}
              onChange={setRerelease}
              disabled={!publicationConfigured}
              label={t('admin.reingest.option.rerelease')}
            />
            {optionHint(
              !publicationConfigured
                ? t('admin.reingest.option.rerelease.unavailable')
                : !rerelease
                  ? t('admin.reingest.option.rerelease.off')
                  : null,
            )}
          </div>
          <div>
            <Toggle
              checked={acceptQualityWarnings}
              onChange={setAcceptQualityWarnings}
              disabled={!effectiveRerelease}
              label={t('admin.reingest.option.qualityWarnings')}
            />
            {optionHint(t('admin.reingest.option.qualityWarnings.hint'))}
          </div>
          <Toggle checked={includeEdited} onChange={setIncludeEdited} label={t('admin.reingest.option.includeEdited')} />
        </div>

        <h3 className="mt-6 mb-3 font-semibold text-slate-950">{t('admin.reingest.preview.title')}</h3>
        <ErrorNotice message={previewError} />
        {!preview ? (
          !previewError && <LoadingState label={t('admin.reingest.preview.loading')} />
        ) : (
          <div className={`grid gap-4 lg:grid-cols-2 ${previewLoading ? 'opacity-60' : ''}`} aria-busy={previewLoading}>
            <PreviewBox title={t('admin.reingest.preview.documents')} selected={includeDocuments}>
              <CountRow label={t('admin.reingest.preview.rerelease')} value={preview.documents.rerelease} />
              <CountRow label={t('admin.reingest.preview.reprocess')} value={preview.documents.reprocess} />
              <CountRow label={t('admin.reingest.preview.skipped')} value={sum(preview.documents.skipped)} />
              {Object.entries(preview.documents.skipped).map(([code, count]) => (
                <CountRow key={code} nested label={codeLabel(t, ['docSkip'], code)} value={count} />
              ))}
              <CountRow label={t('admin.reingest.preview.imported')} value={preview.documents.imported} />
            </PreviewBox>
            <PreviewBox title={t('admin.reingest.preview.connections')} selected={includeConnections}>
              <CountRow label={t('admin.reingest.preview.ready')} value={preview.connections.ready} />
              <CountRow label={t('admin.reingest.preview.blocked')} value={sum(preview.connections.blocked)} />
              {Object.entries(preview.connections.blocked).map(([code, count]) => (
                <CountRow key={code} nested label={codeLabel(t, ['connBlocked'], code)} value={count} />
              ))}
            </PreviewBox>
          </div>
        )}
        {knowledgeBlocked && (
          <div role="alert" className="mt-4 flex gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <AlertTriangle size={15} className="mt-0.5 flex-shrink-0" />
            <p>{t('admin.reingest.preview.knowledgeUnavailable')}</p>
          </div>
        )}
        {nothingToDo && !runActive && <p className="mt-3 text-sm text-slate-500">{t('admin.reingest.preview.nothingToDo')}</p>}

        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
          <Button
            variant="danger"
            disabled={!canStart}
            onClick={() => {
              setAcknowledged(false);
              setConfirmingStart(true);
            }}
          >
            {previewLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
            {runActive ? t('admin.reingest.start.runActive') : t('admin.reingest.start')}
          </Button>
        </div>

        {activeRun && <ActiveRunPanel run={activeRun} onCancel={() => setCancelling(activeRun)} />}
      </SectionCard>

      <SectionCard title={t('admin.reingest.history.title')}>
        <ErrorNotice message={runsError} />
        {runsLoading ? (
          <LoadingState label={t('admin.reingest.history.loading')} />
        ) : history.length === 0 ? (
          <p className="py-4 text-sm text-slate-500">{t('admin.reingest.history.empty')}</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {history.map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </ul>
        )}
      </SectionCard>

      {confirmingStart && preview && (
        <ConfirmDialog
          title={t('admin.reingest.confirm.title')}
          body={
            <div className="space-y-3">
              <p>{t('admin.reingest.confirm.intro')}</p>
              <ul className="list-disc space-y-1 pl-5">
                {includeDocuments && effectiveRerelease && (
                  <li>{t('admin.reingest.confirm.rerelease', { count: preview.documents.rerelease })}</li>
                )}
                {includeDocuments && <li>{t('admin.reingest.confirm.reprocess', { count: preview.documents.reprocess })}</li>}
                {includeConnections && (
                  <li>{t('admin.reingest.confirm.connections', { count: preview.connections.ready })}</li>
                )}
                {effectiveRerelease && (
                  <li>{t(acceptQualityWarnings ? 'admin.reingest.confirm.qualityOn' : 'admin.reingest.confirm.qualityOff')}</li>
                )}
                {includeDocuments && includeEdited && (
                  <li>
                    <strong>{t('admin.reingest.confirm.edited')}</strong>
                  </li>
                )}
              </ul>
              <p>{t('admin.reingest.explain.cancel')}</p>
              <label className="flex items-start gap-2 font-medium">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                />
                {t('admin.reingest.confirm.acknowledge')}
              </label>
            </div>
          }
          confirmLabel={t('admin.reingest.confirm.label')}
          confirmDisabled={!acknowledged || !canStart}
          onClose={() => setConfirmingStart(false)}
          onConfirm={start}
        />
      )}

      {cancelling && (
        <ConfirmDialog
          title={t('admin.reingest.cancel.title')}
          body={<p>{t('admin.reingest.cancel.body')}</p>}
          confirmLabel={t('admin.reingest.cancel.confirm')}
          onClose={() => setCancelling(null)}
          onConfirm={async () => {
            await apiJson<Run>(`${BASE_PATH}/runs/${encodeURIComponent(cancelling.id)}/cancel`, { method: 'POST' });
            setCancelling(null);
            await reloadRuns();
          }}
        />
      )}
    </div>
  );
}

function PreviewBox({ title, selected, children }: { title: string; selected: boolean; children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <div className={`rounded-xl border border-slate-200 p-4 ${selected ? '' : 'opacity-60'}`}>
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-semibold text-slate-950">{title}</h4>
        {!selected && <Badge tone="slate">{t('admin.reingest.preview.notSelected')}</Badge>}
      </div>
      <dl className="mt-2">{children}</dl>
    </div>
  );
}

function CountRow({ label, value, nested = false }: { label: string; value: number; nested?: boolean }) {
  const { formatNumber } = useI18n();
  return (
    <div className={`flex justify-between gap-3 py-0.5 ${nested ? 'pl-4 text-xs text-slate-500' : 'text-sm text-slate-700'}`}>
      <dt>{label}</dt>
      <dd className="font-semibold tabular-nums">{formatNumber(value)}</dd>
    </div>
  );
}

function RunStatusBadge({ status }: { status: RunStatus }) {
  const { t } = useI18n();
  return <Badge tone={STATUS_TONES[status] ?? 'slate'}>{codeLabel(t, ['status'], status)}</Badge>;
}

function RunMeta({ run }: { run: Run }) {
  const { t, formatDate } = useI18n();
  const format = (value: string) => formatDate(value, { dateStyle: 'medium', timeStyle: 'short' });
  return (
    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-slate-500">
      <span>
        {t('admin.reingest.run.startedAt', { time: format(run.created_at) })}
        {run.created_by && ` ${t('admin.reingest.run.startedBy', { name: run.created_by })}`}
      </span>
      {run.finished_at && <span>{t('admin.reingest.run.finishedAt', { time: format(run.finished_at) })}</span>}
    </div>
  );
}

function KindProgress({ run, kind, bar = false }: { run: Run; kind: Kind; bar?: boolean }) {
  const { t, formatNumber } = useI18n();
  const counts = countsOf(run, kind);
  const { processed, total } = progressOf(counts);
  const percent = total > 0 ? Math.round((processed / total) * 100) : 0;
  const groups = kind === 'document' ? ['docResult', 'docSkip'] : ['connResult', 'connBlocked'];
  const results = Object.entries(counts.results ?? {}).filter(([, count]) => count > 0);
  const label = t(kind === 'document' ? 'admin.reingest.run.kind.document' : 'admin.reingest.run.kind.connection');
  return (
    <div className="text-sm">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-medium text-slate-800">{label}</span>
        <span className="tabular-nums text-slate-600">
          {t('admin.reingest.run.progress', { processed: formatNumber(processed), total: formatNumber(total) })}
        </span>
      </div>
      {bar && (
        <div
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={processed}
          className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-200"
        >
          <div className="h-full rounded-full bg-emerald-600 transition-all" style={{ width: `${percent}%` }} />
        </div>
      )}
      {((counts.failed ?? 0) > 0 || (counts.skipped ?? 0) > 0) && (
        <p className="mt-1 text-xs text-slate-500">
          {t('admin.reingest.run.problems', { failed: formatNumber(counts.failed ?? 0), skipped: formatNumber(counts.skipped ?? 0) })}
        </p>
      )}
      {results.length > 0 && (
        <p className="mt-1 text-xs text-slate-500">
          {results.map(([code, count]) => `${codeLabel(t, groups, code)}: ${formatNumber(count)}`).join(' · ')}
        </p>
      )}
    </div>
  );
}

function ActiveRunPanel({ run, onCancel }: { run: Run; onCancel: () => void }) {
  const { t } = useI18n();
  return (
    <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <LoaderCircle className="h-4 w-4 animate-spin text-amber-700" />
          <h3 className="font-semibold text-slate-950">{t('admin.reingest.run.activeTitle')}</h3>
          <RunStatusBadge status={run.status} />
        </div>
        {run.status === 'running' && (
          <Button variant="outline" size="sm" onClick={onCancel}>
            <X className="h-4 w-4" />
            {t('admin.reingest.run.cancel')}
          </Button>
        )}
      </div>
      <RunMeta run={run} />
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {runKinds(run).map((kind) => (
          <KindProgress key={kind} run={run} kind={kind} bar />
        ))}
      </div>
    </div>
  );
}

function RunRow({ run }: { run: Run }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const kinds = runKinds(run);
  const problems = kinds.reduce((total, kind) => total + (countsOf(run, kind).failed ?? 0) + (countsOf(run, kind).skipped ?? 0), 0);
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <RunStatusBadge status={run.status} />
          <RunMeta run={run} />
          {run.error_message && <p className="mt-1 text-xs text-red-600">{run.error_message}</p>}
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            {kinds.map((kind) => (
              <KindProgress key={kind} run={run} kind={kind} />
            ))}
          </div>
        </div>
        {problems > 0 && (
          <Button variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            {open ? t('admin.reingest.problems.hide') : t('admin.reingest.problems.show')}
          </Button>
        )}
      </div>
      {open && <RunProblems runId={run.id} />}
    </li>
  );
}

function RunProblems({ runId }: { runId: string }) {
  const { t } = useI18n();
  const [data, setData] = useState<{ items: RunItem[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiJson<{ items: RunItem[]; total: number }>(
      `${BASE_PATH}/runs/${encodeURIComponent(runId)}/items?status=failed&status=skipped&limit=${ITEM_LIMIT}`,
    )
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (error) return <div className="mt-3"><ErrorNotice message={error} /></div>;
  if (!data) return <LoadingState label={t('admin.reingest.problems.loading')} />;
  if (data.items.length === 0) return <p className="mt-3 text-sm text-slate-500">{t('admin.reingest.problems.empty')}</p>;
  return (
    <div className="mt-3 rounded-xl border border-slate-100 px-4 py-2">
      <ul className="divide-y divide-slate-100 text-sm">
        {data.items.map((item) => {
          const groups = item.kind === 'connection'
            ? (item.status === 'skipped' ? ['connBlocked', 'connResult'] : ['connResult', 'connBlocked'])
            : (item.status === 'skipped' ? ['docSkip', 'docResult'] : ['docResult', 'docSkip']);
          return (
            <li key={item.id} className="py-2">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Badge tone={item.status === 'failed' ? 'red' : 'slate'}>{codeLabel(t, ['item.status'], item.status)}</Badge>
                <span className="text-xs text-slate-500">{codeLabel(t, ['item.kind'], item.kind)}</span>
                <span className="min-w-0 break-all font-medium text-slate-950">
                  {item.label ?? item.job_id ?? item.sync_id ?? String(item.id)}
                </span>
                <span className="text-slate-600">{codeLabel(t, groups, item.result)}</span>
              </div>
              {item.message && <p className="mt-0.5 break-words text-xs text-slate-500">{item.message}</p>}
            </li>
          );
        })}
      </ul>
      {data.total > data.items.length && (
        <p className="py-2 text-xs text-slate-500">
          {t('admin.reingest.problems.truncated', { shown: data.items.length, total: data.total })}
        </p>
      )}
    </div>
  );
}
