'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import dynamic from 'next/dynamic';
import { useParams, useSearchParams } from 'next/navigation';
import { Mail, Pencil, Settings2, Webhook } from 'lucide-react';

import { ErrorNotice, Field, inputClass, LoadingState, Modal } from '@/components/admin/admin-shared';
import { QualityGradeLegend } from '@/components/portal/shared';
import { Button, buttonVariants } from '@/components/ui/button';
import type { PaddleCapabilities } from '@/components/dashboard/shared';
import type { JobArtifact } from '@/components/markdown/markdown-view';
import { WebhookSendDialog } from '@/components/webhook-send-dialog';
import { apiFetch, redirectIfSessionExpired, type JobVersionEntry, type JobVersionsResponse } from '@/lib/api';
import { API_BASE_URL } from '@/lib/api-base';
import { peekCached, setCached } from '@/lib/data-cache';
import { useI18n } from '@/i18n/provider';
import { translate, type MessageKey } from '@/i18n/messages';
import { DEFAULT_LOCALE } from '@/i18n/config';

const CAPABILITIES_PATH = '/api/v1/paddle/capabilities';

// react-markdown + remark-gfm + rehype-sanitize are only needed for the
// "Rendered" tab (the default tab is "Raw" for everything but Confluence
// imports) — loading them eagerly would tax every job-detail page visit
// with a parser bundle most visits never use. Deferred + client-only: the
// server-rendered shell never waits on it, and the chunk is fetched on
// first actual need.
const MarkdownView = dynamic(() => import('@/components/markdown/markdown-view').then((mod) => mod.MarkdownView), {
  ssr: false,
  loading: () => (
    <div className="animate-pulse space-y-3" role="status" aria-label={translate(DEFAULT_LOCALE, 'portal.jobDetail.loadingPreview')}>
      <div className="h-4 w-3/4 rounded bg-slate-100" />
      <div className="h-4 w-full rounded bg-slate-100" />
      <div className="h-4 w-5/6 rounded bg-slate-100" />
      <div className="h-4 w-2/3 rounded bg-slate-100" />
    </div>
  ),
});

const qualityRecommendationKeys: Record<string, MessageKey> = {
  allow: 'portal.jobDetail.recommendation.allow',
  warn: 'portal.jobDetail.recommendation.warn',
  block: 'portal.jobDetail.recommendation.block',
};

const LOWER_PROFILE_RETRY_MAP: Record<string, string> = {
  ppocrv6_medium_structurev3: 'ppocrv6_small_structurev3',
  ppocrv6_small_structurev3: 'ppocrv6_tiny_structurev3',
  ppocrv6_medium: 'ppocrv6_tiny',
  ppocrv6_small: 'ppocrv6_tiny',
};

const VERSION_STATUS_BADGE: Record<string, string> = {
  PENDING: 'bg-slate-100 text-slate-700',
  RUNNING: 'bg-emerald-100 text-emerald-800',
  FINISHED: 'bg-emerald-100 text-emerald-800',
  FAILED: 'bg-red-600/20 text-red-300',
};

/**
 * Display form for a stored `settings.profile_id`. Static profile ids pass
 * through unchanged; a `vl:<connection-id>` id (dynamic VL connection, see
 * GET /api/v1/paddle/capabilities) is shown as "VL: <name>" using the
 * `variant_label` companion field the backend stamps into settings
 * (mirroring benchmark variant jobs, see backend/app/api/benchmarks.py),
 * falling back to the raw connection id if that field is absent.
 */
function profileIdDisplay(profileId: string, settings: Record<string, unknown> | undefined): string {
  if (!profileId.startsWith('vl:')) {
    return profileId;
  }
  const label = settings?.variant_label;
  const name = typeof label === 'string' && label.trim() ? label.trim() : null;
  return name ? `VL: ${name}` : `VL: ${profileId.slice('vl:'.length)}`;
}

/** Defensive read of settings.mail.mail_message_id, mirroring the backend's isinstance() guards on processing_info. */
function mailMessageIdFromSettings(settings: Record<string, unknown> | undefined): string | null {
  if (!settings || typeof settings !== 'object') {
    return null;
  }
  const mail = settings.mail;
  if (!mail || typeof mail !== 'object') {
    return null;
  }
  const id = (mail as Record<string, unknown>).mail_message_id;
  return typeof id === 'string' ? id : null;
}

type Job = {
  id: string;
  original_filename: string;
  status: 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
  tags?: string[];
  processing_info?: {
    settings?: Record<string, unknown>;
    execution?: Record<string, unknown>;
  } | null;
  created_at: string;
  content_sha256?: string | null;
  document_version?: number;
  previous_job_id?: string | null;
};

const API = API_BASE_URL;

export default function JobDetailsPage() {
  // useSearchParams (used below to read ?edit=1) requires a Suspense
  // boundary around it, same as app/connections/page.tsx — omitting this
  // breaks `next build`.
  return (
    <Suspense fallback={<main className="min-h-screen bg-white" />}>
      <JobDetailsPageInner />
    </Suspense>
  );
}

function JobDetailsPageInner() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  if (!params.id) {
    return null;
  }
  const openEditOnLoad = searchParams.get('edit') === '1';
  // The Versions table links between jobs on this same dynamic route. Keying
  // the details component by id remounts it on every id change, so all
  // per-job state (markdown, artifacts, password gate, edit mode) starts
  // fresh instead of leaking from the previously viewed version.
  return <JobDetails key={params.id} jobId={params.id} openEditOnLoad={openEditOnLoad} />;
}

function JobDetails({ jobId, openEditOnLoad }: { jobId: string; openEditOnLoad: boolean }) {
  const { t } = useI18n();
  // Job metadata (unlike the markdown preview) isn't gated behind the
  // document password, so it's safe to reuse: re-opening a job you already
  // viewed this session (e.g. the browser back button) paints its header
  // and status instantly instead of the loading skeleton every time.
  const [job, setJob] = useState<Job | null>(
    () => peekCached<Job>(`/api/v1/jobs/${jobId}`) ?? null
  );
  const [markdown, setMarkdown] = useState('');
  const [draftMarkdown, setDraftMarkdown] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [requirePassword, setRequirePassword] = useState(false);
  const [password, setPassword] = useState('');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isRetryingLower, setIsRetryingLower] = useState(false);
  const [viewTab, setViewTab] = useState<'rendered' | 'raw'>('raw');
  // null = artifact list not fetched yet (images render skeletons); [] after
  // a completed fetch that found nothing or failed (broken-image placeholder).
  const [artifacts, setArtifacts] = useState<JobArtifact[] | null>(null);
  // null = not fetched yet, or the backend doesn't support this endpoint yet
  // (404) — the Versions section stays hidden in both cases.
  const [versions, setVersions] = useState<JobVersionEntry[] | null>(null);
  const [webhookDialogOpen, setWebhookDialogOpen] = useState(false);
  const [restartProfileDialogOpen, setRestartProfileDialogOpen] = useState(false);
  const markdownSectionRef = useRef<HTMLElement>(null);

  // `isActive` lets the load effect discard results that finish after the
  // user has navigated to another job id on this same dynamic route (the
  // component does not remount, so a late response would otherwise clobber
  // the newer job's state). Direct user actions pass the always-true default.
  const loadVersions = async (id: string, isActive: () => boolean = () => true) => {
    try {
      const resp = await apiFetch(`/api/v1/jobs/${id}/versions`, { cache: 'no-store', skipAuthRedirect: true });
      if (!isActive()) {
        return;
      }
      if (!resp.ok) {
        setVersions(null);
        return;
      }
      const payload = (await resp.json()) as JobVersionsResponse;
      if (!isActive()) {
        return;
      }
      setVersions(Array.isArray(payload?.items) ? payload.items : null);
    } catch {
      // Non-fatal: an older backend without this endpoint (or any transient
      // error) simply hides the Versions section.
      if (isActive()) {
        setVersions(null);
      }
    }
  };

  const loadArtifacts = async (id: string, pw: string, isActive: () => boolean = () => true) => {
    try {
      const url = new URL(`${API}/api/v1/jobs/${id}/artifacts`);
      if (pw) {
        url.searchParams.set('password', pw);
      }
      // skipAuthRedirect: a 401 here means the document password, same as
      // the sibling /preview fetches — never bounce to /login for it.
      const resp = await apiFetch(url.toString(), { cache: 'no-store', skipAuthRedirect: true });
      if (!isActive()) {
        return;
      }
      if (resp.ok) {
        const payload = await resp.json();
        if (!isActive()) {
          return;
        }
        setArtifacts(Array.isArray(payload?.items) ? payload.items : []);
      } else {
        // Completed but failed: resolve to "no artifacts" so images show the
        // error placeholder instead of a never-ending skeleton.
        setArtifacts([]);
      }
    } catch {
      // Non-fatal: artifact images fall back to their placeholder.
      if (isActive()) {
        setArtifacts([]);
      }
    }
  };

  useEffect(() => {
    // Per-job state resets are handled by the key={id} remount in
    // JobDetailsPage; this effect only fetches.
    const id = jobId;
    let active = true;
    const run = async () => {
      const jobResp = await apiFetch(`/api/v1/jobs/${id}`, { cache: 'no-store' });
      if (!active) {
        return;
      }
      if (!jobResp.ok) {
        setLoadError(t('portal.jobDetail.loadFailed'));
        return;
      }
      const jobData = await jobResp.json();
      if (!active) {
        return;
      }
      setJob(jobData);
      setCached(`/api/v1/jobs/${id}`, jobData);
      void loadVersions(id, () => active);
      const jobSettings = jobData.processing_info?.settings as Record<string, unknown> | undefined;
      setViewTab(jobSettings?.mode === 'import' ? 'rendered' : 'raw');
      if (jobData.status === 'FINISHED') {
        const previewResp = await apiFetch(`/api/v1/jobs/${id}/preview`, { cache: 'no-store', skipAuthRedirect: true });
        if (!active) {
          return;
        }
        if (previewResp.status === 401) {
          // Could also be an expired session — redirect instead of
          // showing a password prompt that can never succeed.
          if (await redirectIfSessionExpired()) {
            return;
          }
          if (!active) {
            return;
          }
          setRequirePassword(true);
          return;
        }
        if (previewResp.ok) {
          const text = await previewResp.text();
          if (!active) {
            return;
          }
          setMarkdown(text);
          setDraftMarkdown(text);
          if (openEditOnLoad) {
            setIsEditing(true);
          }
          void loadArtifacts(id, '', () => active);
        }
      }
    };
    void run();
    return () => {
      active = false;
    };
  }, [jobId, openEditOnLoad, t]);

  const loadMarkdownWithPassword = async () => {
    const id = jobId;
    
    const url = new URL(`${API}/api/v1/jobs/${id}/preview`);
    if (password) {
      url.searchParams.set('password', password);
    }
    
    const previewResp = await apiFetch(url.toString(), { cache: 'no-store', skipAuthRedirect: true });
    if (previewResp.status === 401) {
      if (await redirectIfSessionExpired()) {
        return;
      }
      setLoadError(t('portal.jobDetail.invalidPassword'));
      return;
    }
    if (previewResp.ok) {
      const text = await previewResp.text();
      setMarkdown(text);
      setDraftMarkdown(text);
      setRequirePassword(false);
      setLoadError(null);
      if (openEditOnLoad) {
        setIsEditing(true);
      }
      void loadArtifacts(id, password);
    }
  };

  if (!job) {
    return (
      <main className="min-h-screen bg-white px-4 py-6 text-slate-950 sm:px-6 lg:px-8">
        <div
          className="mx-auto w-full max-w-6xl animate-pulse space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-6 lg:p-8"
          role="status"
          aria-label={t('portal.jobDetail.loadingJobAria')}
        >
          <div className="h-9 w-32 rounded-xl bg-slate-100" />
          <div className="h-7 w-48 rounded bg-slate-100" />
          <div className="space-y-2">
            <div className="h-4 w-2/3 rounded bg-slate-100" />
            <div className="h-4 w-1/3 rounded bg-slate-100" />
            <div className="h-4 w-1/4 rounded bg-slate-100" />
          </div>
          <div className="h-64 rounded-xl border border-slate-100 bg-slate-50" />
        </div>
      </main>
    );
  }

  if (requirePassword) {
    return (
      <main className="min-h-screen bg-white p-8 text-slate-950">
        <div className="mx-auto max-w-md space-y-4 rounded-xl border border-slate-200 bg-white p-6">
          <h1 className="text-3xl font-semibold">{t('portal.jobs.browser.passwordRequired')}</h1>
          <p className="text-sm text-slate-600">{t('portal.jobs.browser.passwordProtected')}</p>
          {loadError && (
            <p className="text-sm text-red-600" role="alert">
              {loadError}
            </p>
          )}
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void loadMarkdownWithPassword()}
            placeholder={t('portal.jobs.browser.enterPassword')}
            className={inputClass}
          />
          <div className="flex gap-2">
            <Button onClick={loadMarkdownWithPassword}>{t('portal.jobDetail.unlock')}</Button>
            <Link href="/jobs">
              <Button variant="outline">{t('common.back')}</Button>
            </Link>
          </div>
        </div>
      </main>
    );
  }

  const settings = job.processing_info?.settings as Record<string, unknown> | undefined;
  const execution = job.processing_info?.execution as Record<string, unknown> | undefined;
  const selectedProfileId = typeof settings?.profile_id === 'string' ? settings.profile_id : null;
  const selectedProfileLabel = typeof execution?.profile_label === 'string' ? execution.profile_label : null;
  const converter = typeof execution?.converter === 'string' ? execution.converter : null;
  const structure = execution?.structure as Record<string, unknown> | undefined;
  const blockCount = typeof structure?.block_count === 'number' ? structure.block_count : null;
  const pageCount = typeof structure?.page_count === 'number' ? structure.page_count : null;
  const warning = typeof execution?.warning === 'string' ? execution.warning : null;
  // Requeue paths (restart, retry-lower) keep the previous run's execution
  // fields until a worker claims the job, so both banners must only reflect
  // a finished run — otherwise they show stale/contradictory data while
  // the job is PENDING/RUNNING.
  const usedFallback = job.status === 'FINISHED' && execution?.used_fallback === true;
  const fallbackReason = typeof execution?.fallback_reason === 'string' ? execution.fallback_reason : null;
  const engine = typeof execution?.engine === 'string' ? execution.engine : null;
  const resolvedProfileId = typeof execution?.profile_id === 'string' ? execution.profile_id : null;
  // A vl:<connection-id> request always resolves to the openai_vision
  // pipeline internally (same as benchmark VL variants) -- that's expected
  // behavior, not the worker falling back from an unknown profile, so it's
  // excluded from the mismatch check below.
  const profileMismatch = Boolean(
    job.status === 'FINISHED' &&
      selectedProfileId &&
      !selectedProfileId.startsWith('vl:') &&
      resolvedProfileId &&
      selectedProfileId !== resolvedProfileId
  );
  const suggestedLowerProfile =
    (typeof execution?.suggested_profile_id === 'string' ? execution.suggested_profile_id : null) ||
    (typeof settings?.profile_id === 'string' ? LOWER_PROFILE_RETRY_MAP[settings.profile_id] ?? null : null);
  // Same derivation as document-browser.tsx's isImportJob (not imported —
  // that component owns its own copy of this check).
  const isImportJob = settings?.mode === 'import';
  const canRestartWithProfile = !isImportJob && (job.status === 'FINISHED' || job.status === 'FAILED');

  const retryWithLowerProfile = async () => {
    if (!job?.id) {
      return;
    }
    setIsRetryingLower(true);
    setLoadError(null);
    try {
      const response = await apiFetch(`/api/v1/jobs/${job.id}/retry-lower-profile`, { method: 'POST' });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = typeof payload?.detail === 'string' ? payload.detail : t('portal.jobs.browser.alert.retryLowerFailed');
        setLoadError(detail);
        return;
      }
      const refreshed = await apiFetch(`/api/v1/jobs/${job.id}`, { cache: 'no-store' });
      if (refreshed.ok) {
        const jobData = await refreshed.json();
        setJob(jobData);
        setCached(`/api/v1/jobs/${job.id}`, jobData);
      }
    } finally {
      setIsRetryingLower(false);
    }
  };
  const qualityGate = execution?.quality_gate as Record<string, unknown> | undefined;
  const qualityGrade = typeof qualityGate?.grade === 'string' ? qualityGate.grade : null;
  const qualityScore = typeof qualityGate?.score === 'number' ? qualityGate.score : null;
  const qualityRecommendation = typeof qualityGate?.recommendation === 'string' ? qualityGate.recommendation : null;
  const collectionId = typeof settings?.collection_id === 'string' ? settings.collection_id : null;

  const saveMarkdown = async () => {
    setIsSaving(true);
    setSaveMessage(null);
    const url = new URL(`${API}/api/v1/jobs/${job?.id || ''}/save`);
    if (password) {
      url.searchParams.set('password', password);
    }
    try {
      const response = await apiFetch(url.toString(), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markdown: draftMarkdown }),
        skipAuthRedirect: true,
      });
      if (!response.ok) {
        if (response.status === 401 && (await redirectIfSessionExpired())) return;
        setSaveMessage(
          response.status === 401
            ? t('portal.jobDetail.saveFailedPassword')
            : t('portal.jobDetail.saveFailedFrontmatter')
        );
        return;
      }
      const payload = await response.json();
      setMarkdown(draftMarkdown);
      setSaveMessage(t('portal.jobDetail.savedAsVersion', { version: payload.version }));
      setIsEditing(false);
    } catch {
      setSaveMessage(t('portal.jobDetail.saveFailedRetry'));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <main className="min-h-screen bg-white px-4 py-6 text-slate-950 sm:px-6 lg:px-8">
      <div className="mx-auto w-full max-w-6xl space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-6 lg:p-8">
        <div className="flex justify-start">
          <Link href="/jobs">
            <Button variant="outline">{t('portal.jobDetail.backToJobs')}</Button>
          </Link>
        </div>
        <h1 className="text-3xl font-semibold">{t('portal.jobDetail.title')}</h1>
        <p>{t('portal.jobDetail.filenameLabel', { name: job.original_filename })}</p>
        {job.tags && job.tags.length > 0 && <p>{t('portal.jobDetail.tagsLabel', { tags: job.tags.join(', ') })}</p>}
        <p className="flex items-center gap-2">
          {t('portal.jobDetail.statusLabel')} {job.status}
          {typeof job.document_version === 'number' && (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">
              v{job.document_version}
            </span>
          )}
          {settings?.mode === 'mail_attachment' && mailMessageIdFromSettings(settings) && (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-800"
            >
              <Mail className="h-3 w-3" /> {t('portal.jobs.browser.mailAttachment')}
            </span>
          )}
        </p>
        <p>{t('portal.jobDetail.createdLabel', { date: new Date(job.created_at).toLocaleString() })}</p>
        {selectedProfileId && <p>{t('portal.jobDetail.profileLabel', { name: profileIdDisplay(selectedProfileId, settings) })}</p>}
        {selectedProfileLabel && <p>{t('portal.jobDetail.profileNameLabel', { name: selectedProfileLabel })}</p>}
        {converter && <p>{t('portal.jobDetail.converterLabel', { name: converter })}</p>}
        {pageCount !== null && blockCount !== null && <p>{t('portal.jobDetail.structureLabel', { pages: pageCount, blocks: blockCount })}</p>}
        {usedFallback && (
          <div className="rounded-xl border border-red-300 bg-red-50 p-3 text-red-900">
            <p className="text-sm font-semibold">
              {t('portal.jobDetail.ocrFallbackTitle', { engine: engine ?? 'plain-text' })}
            </p>
            <p className="mt-1 text-sm">
              {t('portal.jobDetail.ocrFallbackBody', { label: selectedProfileLabel ? ` (${selectedProfileLabel})` : '' })}
            </p>
            {fallbackReason && <p className="mt-1 break-words text-sm">{t('portal.jobDetail.reasonLabel', { reason: fallbackReason })}</p>}
          </div>
        )}
        {!usedFallback && profileMismatch && (
          <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-amber-900">
            <p className="text-sm">
              {t('portal.jobDetail.profileMismatch', { requested: selectedProfileId ?? '', resolved: resolvedProfileId ?? '' })}
            </p>
          </div>
        )}
        {job.status === 'FAILED' && (
          <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-amber-900">
            <p className="text-sm">
              {warning || t('portal.jobDetail.processingStoppedManual')}
            </p>
            {(suggestedLowerProfile || canRestartWithProfile) && (
              <div className="mt-2 flex flex-wrap gap-2">
                {suggestedLowerProfile && (
                  <Button size="sm" variant="outline" disabled={isRetryingLower} onClick={retryWithLowerProfile}>
                    {isRetryingLower ? t('portal.jobDetail.retrying') : t('portal.jobDetail.retryWith', { profile: suggestedLowerProfile })}
                  </Button>
                )}
                {canRestartWithProfile && (
                  <Button size="sm" variant="outline" onClick={() => setRestartProfileDialogOpen(true)}>
                    <Settings2 className="h-4 w-4" />
                    {t('portal.jobs.browser.rerunWithProfile')}
                  </Button>
                )}
              </div>
            )}
          </div>
        )}
        {qualityGrade && (
          <div>
            <p>
              {t('portal.documents.qualityGrade', { grade: qualityGrade })}
              {qualityScore !== null ? ` (${Math.round(qualityScore * 100)} %)` : ''}
              {qualityRecommendation ? ` – ${qualityRecommendationKeys[qualityRecommendation] ? t(qualityRecommendationKeys[qualityRecommendation]) : qualityRecommendation}` : ''}
            </p>
            <QualityGradeLegend />
          </div>
        )}
        {versions && versions.length > 1 && (
          <section>
            <h2 className="mb-2 text-[17px] font-semibold">{t('portal.jobDetail.versionsHeading')}</h2>
            <div className="overflow-x-auto rounded-xl border border-slate-200">
              <table className="w-full table-auto text-left text-sm">
                <thead className="bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">{t('portal.jobDetail.colVersion')}</th>
                    <th className="px-3 py-2 font-medium">{t('portal.jobDetail.colSha')}</th>
                    <th className="px-3 py-2 font-medium">{t('common.status')}</th>
                    <th className="px-3 py-2 font-medium">{t('portal.jobDetail.colCreated')}</th>
                    <th className="px-3 py-2 font-medium">{t('portal.jobDetail.colUploadedBy')}</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((entry) => (
                    <tr key={entry.job_id} className="border-t border-slate-100">
                      <td className="px-3 py-2 text-slate-950">
                        {entry.job_id === job.id ? (
                          <span className="font-medium">v{entry.document_version}</span>
                        ) : (
                          <Link href={`/jobs/${entry.job_id}`} className="font-medium text-emerald-700 hover:underline">
                            v{entry.document_version}
                          </Link>
                        )}
                        {entry.is_current && <span className="ml-2 text-xs font-medium text-emerald-700">{t('portal.jobDetail.current')}</span>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-600" title={entry.content_sha256 ?? ''}>
                        {entry.content_sha256 ? entry.content_sha256.slice(0, 12) : '-'}
                      </td>
                      <td className="px-3 py-2">
                        <span className={`rounded px-2 py-1 text-xs ${VERSION_STATUS_BADGE[entry.status] ?? 'bg-slate-100 text-slate-700'}`}>
                          {entry.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-slate-700">{new Date(entry.created_at).toLocaleString()}</td>
                      <td className="px-3 py-2 text-slate-700">{entry.uploaded_by ?? '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {!collectionId && (
          <section className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950">
            <h2 className="font-semibold">{t('portal.jobDetail.noSpaceTitle')}</h2>
            <p className="mt-1 text-sm">{t('portal.jobDetail.noSpaceBody')}</p>
            <Link href="/sources/new" className={`${buttonVariants({ variant: 'outline' })} mt-3`}>{t('portal.jobDetail.reimportSource')}</Link>
          </section>
        )}
        <details>
          <summary className="cursor-pointer font-semibold">{t('portal.jobDetail.technicalDetails')}</summary>
          <pre className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-4 text-sm text-emerald-800">
            {JSON.stringify(job.processing_info ?? {}, null, 2)}
          </pre>
        </details>
        {job.status === 'FINISHED' && (
          <div className="flex flex-wrap gap-2">
            <a href={`${API}/api/v1/jobs/${job.id}/download${password ? `?password=${encodeURIComponent(password)}` : ''}`}>
              <Button>{t('portal.jobDetail.downloadMarkdown')}</Button>
            </a>
            <a href={`${API}/api/v1/jobs/${job.id}/export.json${password ? `?password=${encodeURIComponent(password)}` : ''}`}>
              <Button variant="outline">{t('portal.jobDetail.downloadJson')}</Button>
            </a>
            <Button variant="outline" onClick={() => setWebhookDialogOpen(true)}>
              <Webhook className="h-4 w-4" />
              {t('portal.jobs.browser.sendToWebhook')}
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                setIsEditing(true);
                markdownSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }}
            >
              <Pencil className="h-4 w-4" />
              {t('portal.jobs.browser.editMarkdown')}
            </Button>
            {canRestartWithProfile && (
              <Button variant="outline" onClick={() => setRestartProfileDialogOpen(true)}>
                <Settings2 className="h-4 w-4" />
                {t('portal.jobs.browser.rerunWithProfile')}
              </Button>
            )}
          </div>
        )}
        <section ref={markdownSectionRef}>
          <h2 className="mb-2 text-[17px] font-semibold">{t('portal.jobDetail.markdownPreviewHeading')}</h2>
          <div className="mb-2 flex items-center gap-2">
            <Button size="sm" variant={isEditing ? 'outline' : 'default'} onClick={() => setIsEditing(false)}>
              {t('portal.jobDetail.previewTab')}
            </Button>
            <Button size="sm" variant={isEditing ? 'default' : 'outline'} onClick={() => setIsEditing(true)}>
              {t('common.edit')}
            </Button>
          </div>
          {!isEditing && (
            // Plain toggle buttons (aria-pressed), not an ARIA tabs widget:
            // the full tablist pattern needs panel wiring + arrow-key focus,
            // which these two view switchers do not implement.
            <div className="mb-2 flex items-center gap-2">
              <Button
                size="sm"
                aria-pressed={viewTab === 'rendered'}
                variant={viewTab === 'rendered' ? 'default' : 'outline'}
                onClick={() => setViewTab('rendered')}
              >
                {t('portal.jobDetail.renderedTab')}
              </Button>
              <Button
                size="sm"
                aria-pressed={viewTab === 'raw'}
                variant={viewTab === 'raw' ? 'default' : 'outline'}
                onClick={() => setViewTab('raw')}
              >
                {t('portal.jobDetail.rawTab')}
              </Button>
            </div>
          )}
          {isEditing ? (
            <div className="space-y-2">
              <textarea
                className="min-h-[380px] w-full rounded-lg border border-slate-200 bg-white p-4 text-sm text-emerald-800"
                value={draftMarkdown}
                onChange={(event) => setDraftMarkdown(event.target.value)}
              />
              <div className="flex items-center gap-2">
                <Button onClick={saveMarkdown} disabled={isSaving}>
                  {isSaving ? t('portal.jobDetail.savingVersion') : t('portal.jobDetail.saveAsVersion')}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setDraftMarkdown(markdown);
                    setIsEditing(false);
                    setSaveMessage(null);
                  }}
                >
                  {t('common.cancel')}
                </Button>
              </div>
              <div aria-live="polite">{saveMessage && <p className="text-sm text-slate-600">{saveMessage}</p>}</div>
            </div>
          ) : viewTab === 'rendered' ? (
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <MarkdownView markdown={markdown} jobId={job.id} password={password || undefined} artifacts={artifacts} />
            </div>
          ) : (
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-xl border border-slate-200 bg-white p-4 text-sm text-emerald-800">{markdown}</pre>
          )}
        </section>
      </div>
      {webhookDialogOpen && (
        <WebhookSendDialog
          job={{ id: job.id, label: job.original_filename }}
          onClose={() => setWebhookDialogOpen(false)}
        />
      )}
      {restartProfileDialogOpen && (
        <RestartWithProfileDialog
          jobId={job.id}
          jobLabel={job.original_filename}
          onClose={() => setRestartProfileDialogOpen(false)}
          onRestarted={async () => {
            const refreshed = await apiFetch(`/api/v1/jobs/${job.id}`, { cache: 'no-store' });
            if (refreshed.ok) {
              const jobData = await refreshed.json();
              setJob(jobData);
              setCached(`/api/v1/jobs/${job.id}`, jobData);
            }
          }}
        />
      )}
    </main>
  );
}

/**
 * Profile picker for the "Re-run with profile…" action, mirroring
 * document-browser.tsx's dialog of the same name (duplicated rather than
 * imported — that component owns its own copy of this UI, and this page
 * shows inline errors instead of alert() to match its existing patterns
 * like retryWithLowerProfile above).
 */
function RestartWithProfileDialog({
  jobId,
  jobLabel,
  onClose,
  onRestarted,
}: {
  jobId: string;
  jobLabel: string;
  onClose: () => void;
  onRestarted: () => void;
}) {
  const { t } = useI18n();
  const [capabilities, setCapabilities] = useState<PaddleCapabilities | null>(
    () => peekCached<PaddleCapabilities>(CAPABILITIES_PATH) ?? null
  );
  const [profileId, setProfileId] = useState(
    () => peekCached<PaddleCapabilities>(CAPABILITIES_PATH)?.profiles[0]?.value ?? ''
  );
  const [loading, setLoading] = useState(!capabilities);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (capabilities) {
      return;
    }
    let active = true;
    (async () => {
      const response = await apiFetch(CAPABILITIES_PATH, { cache: 'no-store' });
      if (!active) {
        return;
      }
      if (response.ok) {
        const data = (await response.json()) as PaddleCapabilities;
        if (!active) {
          return;
        }
        setCapabilities(data);
        setCached(CAPABILITIES_PATH, data);
        setProfileId((current) => current || data.profiles[0]?.value || '');
      }
      setLoading(false);
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedDescription = capabilities?.profiles.find((option) => option.value === profileId)?.description;

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    const response = await apiFetch(`/api/v1/jobs/${jobId}/restart`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = typeof payload?.detail === 'string' ? payload.detail : t('portal.jobs.browser.alert.restartFailed');
      setError(detail);
      setStarting(false);
      return;
    }
    onRestarted();
    onClose();
  };

  return (
    <Modal title={t('portal.jobs.browser.dialog.rerunTitle', { filename: jobLabel })} onClose={onClose}>
      <div className="space-y-4">
        {loading && <LoadingState label={t('portal.jobs.browser.dialog.loadingProfiles')} />}
        {!loading && capabilities && capabilities.profiles.length > 0 && (
          <Field label={t('portal.jobs.browser.dialog.profileLabel')}>
            <select className={inputClass} value={profileId} onChange={(event) => setProfileId(event.target.value)}>
              {capabilities.profiles.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {selectedDescription && (
              <span className="mt-1 block text-xs font-normal text-slate-400">{selectedDescription}</span>
            )}
          </Field>
        )}
        {!loading && (!capabilities || capabilities.profiles.length === 0) && (
          <p className="text-sm text-slate-600">{t('portal.jobs.browser.dialog.noProfiles')}</p>
        )}
        <ErrorNotice message={error} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={starting}>
            {t('common.cancel')}
          </Button>
          <Button type="button" size="sm" disabled={starting || !profileId} onClick={() => void handleStart()}>
            {starting ? t('common.starting') : t('common.start')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
