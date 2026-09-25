'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import dynamic from 'next/dynamic';
import { useParams, useRouter } from 'next/navigation';
import { LoaderCircle, Trophy, Zap } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiSend, ConfirmDialog } from '@/components/admin/admin-shared';
import { API } from '@/components/dashboard/shared';
import {
  ApiError,
  apiFetch,
  apiJson,
  benchmarkStatusChip,
  benchmarkVariantStatusChip,
  qualityGradeChip,
  type BenchmarkReport,
  type BenchmarkRunDetail,
} from '@/lib/api';
import { useI18n } from '@/i18n/provider';
import { translate } from '@/i18n/messages';
import { DEFAULT_LOCALE } from '@/i18n/config';

// react-markdown + remark-gfm + rehype-sanitize are only needed for the
// "Rendered" tab — deferred + client-only, same rationale as jobs/[id].
const MarkdownView = dynamic(() => import('@/components/markdown/markdown-view').then((mod) => mod.MarkdownView), {
  ssr: false,
  loading: () => (
    <div className="animate-pulse space-y-3" role="status" aria-label={translate(DEFAULT_LOCALE, 'portal.benchmarkDetail.loadingPreview')}>
      <div className="h-4 w-3/4 rounded bg-slate-100" />
      <div className="h-4 w-full rounded bg-slate-100" />
      <div className="h-4 w-5/6 rounded bg-slate-100" />
    </div>
  ),
});

const POLL_INTERVAL_MS = 3000;

export default function BenchmarkRunPage() {
  const { t } = useI18n();
  const params = useParams<{ id: string }>();
  const runId = params.id;
  const router = useRouter();

  // Header extras (owner, content hash) — fetched once; the report poll below
  // carries everything the metrics table and status badge need on its own.
  const [run, setRun] = useState<BenchmarkRunDetail | null>(null);
  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const [activeVariantJobId, setActiveVariantJobId] = useState<string | null>(null);
  const [viewTab, setViewTab] = useState<'rendered' | 'raw'>('raw');
  const [markdownByJob, setMarkdownByJob] = useState<Record<string, string>>({});
  const [markdownError, setMarkdownError] = useState<Record<string, string>>({});
  const [markdownLoading, setMarkdownLoading] = useState<Record<string, boolean>>({});

  // Header metadata (owner) — one-shot, not polled.
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    apiJson<BenchmarkRunDetail>(`/api/v1/benchmarks/${runId}`, { cache: 'no-store' })
      .then((detail) => {
        if (!cancelled) setRun(detail);
      })
      .catch(() => {
        // Non-fatal: the header owner/hash line just stays blank; report
        // polling below still drives the rest of the page.
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  // Report is designed to be polled directly (it is always 200, with fields
  // filling in as variants finish, and `all_terminal` marking "done") — so
  // it alone drives both the status badge and the progressive metrics table.
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const payload = await apiJson<BenchmarkReport>(`/api/v1/benchmarks/${runId}/report`, { cache: 'no-store' });
        if (cancelled) return;
        setReport(payload);
        setLoadError(null);
        if (!payload.all_terminal) {
          timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
        }
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 404) {
          setNotFound(true);
          return;
        }
        setLoadError(error instanceof ApiError ? error.detail : t('portal.benchmarkDetail.reportLoadFailed'));
        // Transient failure: keep polling so a recovering backend resumes updates.
        timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [runId, t]);

  // Default the markdown tab to the first finished variant once the report
  // loads. Adjusted during render (matching processing-flow.tsx's
  // lastSettingsData idiom) rather than in an effect: this derives initial
  // state from `report`, it does not synchronize with an external system.
  const [lastReportForTab, setLastReportForTab] = useState<BenchmarkReport | null>(null);
  if (report !== lastReportForTab) {
    setLastReportForTab(report);
    if (report && !activeVariantJobId) {
      const finished = report.variants.find((variant) => variant.status === 'FINISHED');
      setActiveVariantJobId((finished ?? report.variants[0])?.job_id ?? null);
    }
  }

  // Lazily fetch the selected variant's markdown via the job's own preview
  // endpoint — the report itself never embeds markdown (that would make it
  // too heavy to poll), each variant is a real, individually-fetchable Job.
  useEffect(() => {
    if (!activeVariantJobId) return;
    if (
      markdownByJob[activeVariantJobId] !== undefined ||
      markdownError[activeVariantJobId] ||
      markdownLoading[activeVariantJobId]
    ) {
      return;
    }
    let cancelled = false;
    const load = async () => {
      setMarkdownLoading((current) => ({ ...current, [activeVariantJobId]: true }));
      try {
        const resp = await apiFetch(`/api/v1/jobs/${activeVariantJobId}/preview`, {
          cache: 'no-store',
          skipAuthRedirect: true,
        });
        if (cancelled) return;
        if (resp.status === 401) {
          setMarkdownError((current) => ({
            ...current,
            [activeVariantJobId]: t('portal.benchmarkDetail.passwordProtected'),
          }));
          return;
        }
        if (!resp.ok) {
          setMarkdownError((current) => ({
            ...current,
            [activeVariantJobId]: t('portal.benchmarkDetail.markdownLoadFailed'),
          }));
          return;
        }
        const text = await resp.text();
        if (cancelled) return;
        setMarkdownByJob((current) => ({ ...current, [activeVariantJobId]: text }));
      } catch {
        if (!cancelled) {
          setMarkdownError((current) => ({
            ...current,
            [activeVariantJobId]: t('portal.benchmarkDetail.markdownLoadFailed'),
          }));
        }
      } finally {
        if (!cancelled) {
          setMarkdownLoading((current) => ({ ...current, [activeVariantJobId]: false }));
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
    // Deliberately keyed only on the active tab: markdownByJob/markdownError/
    // markdownLoading are read for their current value as a guard, not to
    // retrigger the fetch when they change (mirrors data-cache.ts's ttlMs gate).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeVariantJobId]);

  if (notFound) {
    return (
      <main className="min-h-screen">
        <div className="mx-auto w-full max-w-4xl px-4 py-8 text-slate-950 sm:px-6 lg:px-8">
          <h1 className="text-3xl font-semibold">{t('portal.benchmarkDetail.notFoundTitle')}</h1>
          <p className="mt-2 text-sm text-slate-600">{t('portal.benchmarkDetail.notFoundBody')}</p>
          <Link href="/benchmark" className="mt-4 inline-block text-sm text-emerald-700 hover:text-emerald-800">
            {t('portal.benchmarkDetail.backToBenchmark')}
          </Link>
        </div>
      </main>
    );
  }

  if (!report) {
    return (
      <main className="min-h-screen">
        <div className="mx-auto w-full max-w-4xl px-4 py-8 text-slate-950 sm:px-6 lg:px-8">
          <div className="flex items-center gap-2 py-6 text-sm text-slate-600">
            <LoaderCircle className="h-4 w-4 animate-spin" /> {t('portal.benchmarkDetail.loading')}
          </div>
          {loadError && (
            <p role="alert" className="text-sm text-red-600">
              {loadError}
            </p>
          )}
        </div>
      </main>
    );
  }

  const activeVariant = report.variants.find((variant) => variant.job_id === activeVariantJobId) ?? null;
  const filename = report.original_filename;

  return (
    <main className="min-h-screen">
      <div className="mx-auto w-full max-w-6xl px-4 py-8 text-slate-950 sm:px-6 lg:px-8">
        <p className="mb-3">
          <Link href="/benchmark" className="text-sm text-emerald-700 hover:text-emerald-800">
            {t('portal.benchmarkDetail.backToBenchmark')}
          </Link>
        </p>

        <section className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="truncate text-3xl font-semibold">{filename}</h1>
            <p className="mt-1 text-sm text-slate-600">
              {run?.owner ? t('portal.benchmarkDetail.startedBy', { name: run.owner.username }) : ''}
              {new Date(report.created_at).toLocaleString()}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <span
              aria-live="polite"
              className={`rounded px-2 py-1 text-xs ${benchmarkStatusChip[report.status]}`}
            >
              {report.status}
            </span>
            {report.all_terminal && (
              <a href={`${API}/api/v1/benchmarks/${runId}/export.json`}>
                <Button variant="outline" size="sm">
                  {t('portal.benchmarkDetail.downloadJson')}
                </Button>
              </a>
            )}
            <Button variant="danger" size="sm" onClick={() => setConfirmingDelete(true)}>
              {t('portal.benchmarkDetail.deleteRun')}
            </Button>
          </div>
        </section>

        {loadError && (
          <p role="alert" className="mb-4 text-sm text-amber-700">
            {loadError} {t('portal.benchmarkDetail.retrying')}
          </p>
        )}

        <section className="mb-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-1 flex flex-wrap items-center justify-between gap-4">
            <h2 className="text-[17px] font-semibold">{t('portal.benchmarkDetail.compareVariants')}</h2>
            <p className="text-xs text-slate-500">{t('portal.benchmarkDetail.variantsCount', { count: report.variants.length })}</p>
          </div>
          <p className="mb-4 text-sm text-slate-500">{t('portal.benchmarkDetail.selectCardHint')}</p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {report.variants.map((variant) => {
              const isBest = variant.job_id === report.summary.highest_quality_variant_job_id;
              const isFastest = variant.job_id === report.summary.fastest_variant_job_id;
              const isActive = activeVariantJobId === variant.job_id;
              return (
                <div
                  key={variant.job_id}
                  className={`flex flex-col rounded-xl border p-4 transition ${
                    isActive
                      ? 'border-emerald-400 bg-emerald-50/40 ring-1 ring-emerald-300'
                      : 'border-slate-200 bg-white hover:border-slate-300'
                  }`}
                >
                  <button
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setActiveVariantJobId(variant.job_id)}
                    className="flex-1 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    <div className="mb-2 flex items-start justify-between gap-2">
                      <p className="min-w-0 truncate text-sm font-semibold text-slate-950">{variant.label}</p>
                      <span
                        className={`shrink-0 rounded px-2 py-0.5 text-xs ${
                          variant.kind === 'vl' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-700'
                        }`}
                      >
                        {variant.kind === 'vl' ? 'VL' : 'OCR'}
                      </span>
                    </div>

                    {(isBest || isFastest) && (
                      <div className="mb-3 flex flex-wrap gap-1.5">
                        {isBest && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
                            <Trophy className="h-3 w-3" /> {t('portal.benchmarkDetail.bestResult')}
                          </span>
                        )}
                        {isFastest && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600">
                            <Zap className="h-3 w-3" /> {t('portal.benchmarkDetail.fastest')}
                          </span>
                        )}
                      </div>
                    )}

                    <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                      <div>
                        <dt className="text-xs text-slate-500">{t('portal.benchmarkDetail.statStatus')}</dt>
                        <dd>
                          <span className={`rounded px-1.5 py-0.5 text-xs ${benchmarkVariantStatusChip[variant.status]}`}>
                            {variant.status}
                          </span>
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-500">{t('portal.benchmarkDetail.statQuality')}</dt>
                        <dd>
                          {variant.quality_grade ? (
                            <span className={`rounded px-1.5 py-0.5 text-xs ${qualityGradeChip[variant.quality_grade]}`}>
                              {variant.quality_grade}
                            </span>
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-500">{t('portal.benchmarkDetail.statTime')}</dt>
                        <dd className="tabular-nums text-slate-700">
                          {variant.duration_seconds !== null ? `${variant.duration_seconds.toFixed(1)}s` : '—'}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-500">{t('portal.benchmarkDetail.statPages')}</dt>
                        <dd className="tabular-nums text-slate-700">{variant.page_count ?? '—'}</dd>
                      </div>
                    </dl>

                    {variant.used_fallback && <p className="mt-2 text-xs text-amber-700">{t('portal.benchmarkDetail.usedOcrFallback')}</p>}
                    {variant.error && (
                      <p className="mt-2 truncate text-xs text-red-600" title={variant.error}>
                        {variant.error}
                      </p>
                    )}
                  </button>

                  <div className="mt-3 flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
                    <span className="tabular-nums text-xs text-slate-400">
                      {variant.output_chars !== null ? `${variant.output_chars.toLocaleString()}${t('portal.benchmarkDetail.charsSuffix')}` : ''}
                    </span>
                    <Link
                      href={`/jobs/${variant.job_id}`}
                      className="text-xs font-medium text-emerald-700 hover:text-emerald-800"
                    >
                      {t('portal.benchmarkDetail.openJob')}
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-4">
            <h2 className="text-[17px] font-semibold">{t('portal.benchmarkDetail.markdownPreviewHeading')}</h2>
            {activeVariant && (
              <p className="text-xs text-slate-500">
                {t('portal.benchmarkDetail.showing')} <span className="font-medium text-slate-700">{activeVariant.label}</span>
              </p>
            )}
          </div>
          {!report.all_terminal ? (
            <p className="text-sm text-slate-600">{t('portal.benchmarkDetail.markdownPendingRun')}</p>
          ) : (
            <>
              {activeVariant &&
                (activeVariant.status !== 'FINISHED' ? (
                  <p className="text-sm text-slate-600">
                    {activeVariant.status === 'FAILED'
                      ? `${t('portal.benchmarkDetail.variantFailed')}${activeVariant.error ? `: ${activeVariant.error}` : '.'}`
                      : t('portal.benchmarkDetail.variantNotFinished')}
                  </p>
                ) : markdownLoading[activeVariant.job_id] ? (
                  <div className="flex items-center gap-2 py-4 text-sm text-slate-600">
                    <LoaderCircle className="h-4 w-4 animate-spin" /> {t('portal.benchmarkDetail.loadingMarkdown')}
                  </div>
                ) : markdownError[activeVariant.job_id] ? (
                  <p role="alert" className="text-sm text-red-600">
                    {markdownError[activeVariant.job_id]}
                  </p>
                ) : (
                  markdownByJob[activeVariant.job_id] !== undefined && (
                    <>
                      <div className="mb-2 flex items-center gap-2">
                        <Button
                          size="sm"
                          aria-pressed={viewTab === 'rendered'}
                          variant={viewTab === 'rendered' ? 'default' : 'outline'}
                          onClick={() => setViewTab('rendered')}
                        >
                          {t('portal.benchmarkDetail.renderedTab')}
                        </Button>
                        <Button
                          size="sm"
                          aria-pressed={viewTab === 'raw'}
                          variant={viewTab === 'raw' ? 'default' : 'outline'}
                          onClick={() => setViewTab('raw')}
                        >
                          {t('portal.benchmarkDetail.rawTab')}
                        </Button>
                        <Link
                          href={`/jobs/${activeVariant.job_id}`}
                          className="ml-auto text-sm text-emerald-700 hover:text-emerald-800"
                        >
                          {t('portal.benchmarkDetail.openJob')}
                        </Link>
                      </div>
                      {viewTab === 'rendered' ? (
                        <div className="rounded-xl border border-slate-200 bg-white p-4">
                          <MarkdownView
                            markdown={markdownByJob[activeVariant.job_id]}
                            jobId={activeVariant.job_id}
                            artifacts={[]}
                          />
                        </div>
                      ) : (
                        <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-xl border border-slate-200 bg-white p-4 text-sm text-emerald-800">
                          {markdownByJob[activeVariant.job_id]}
                        </pre>
                      )}
                    </>
                  )
                ))}
            </>
          )}
        </section>

        {confirmingDelete && (
          <ConfirmDialog
            title={t('portal.benchmarkDetail.deleteDialogTitle')}
            body={
              <p>
                {t('portal.benchmarkDetail.deleteDialogPrefix')} <span className="font-semibold text-slate-950">{filename}</span>{t('portal.benchmarkDetail.deleteDialogSuffix')}
              </p>
            }
            confirmLabel={t('portal.benchmarkDetail.deleteRun')}
            onClose={() => setConfirmingDelete(false)}
            onConfirm={async () => {
              await apiSend(`/api/v1/benchmarks/${runId}`, { method: 'DELETE' });
              router.push('/benchmark');
            }}
          />
        )}
      </div>
    </main>
  );
}
