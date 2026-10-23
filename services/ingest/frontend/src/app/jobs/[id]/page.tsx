'use client';

import { Suspense, useEffect, useEffectEvent, useState } from 'react';
import Link from 'next/link';
import dynamic from 'next/dynamic';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { Settings2 } from 'lucide-react';

import { inputClass } from '@/components/admin/admin-shared';
import { BackLink } from '@/components/back-link';
import { Notice, PortalPage } from '@/components/portal/shared';
import { RestartWithProfileDialog, TechnicalDetailsButton } from '@/components/portal/technical-details';
import { Button, buttonVariants } from '@/components/ui/button';
import type { JobArtifact } from '@/components/markdown/markdown-view';
import { apiFetch, redirectIfSessionExpired, type QualityFinding, type QualityFindingKind } from '@/lib/api';
import { API_BASE_URL } from '@/lib/api-base';
import { useAuth } from '@/lib/auth-context';
import { peekCached, setCached } from '@/lib/data-cache';
import { useI18n } from '@/i18n/provider';
import { translate, type MessageKey } from '@/i18n/messages';
import { DEFAULT_LOCALE } from '@/i18n/config';

// react-markdown + remark-gfm + rehype-sanitize are only needed for the
// "Rendered" tab (the default tab is "Raw" for everything but Confluence
// imports) -- loading them eagerly would tax every job-detail page visit
// with a parser bundle most visits never use. Deferred + client-only: the
// server-rendered shell never waits on it, and the chunk is fetched on
// first actual need.
const MarkdownView = dynamic(() => import('@/components/markdown/markdown-view').then((mod) => mod.MarkdownView), {
  ssr: false,
  loading: () => (
    <div className="animate-pulse space-y-3" role="status" aria-label={translate(DEFAULT_LOCALE, 'portal.jobDetail.loadingPreview')}>
      <div className="h-4 w-3/4 rounded bg-[var(--surface-2)]" />
      <div className="h-4 w-full rounded bg-[var(--surface-2)]" />
      <div className="h-4 w-5/6 rounded bg-[var(--surface-2)]" />
      <div className="h-4 w-2/3 rounded bg-[var(--surface-2)]" />
    </div>
  ),
});

/** Readable status for the badge; unknown values fall back to the raw status. */
const JOB_STATUS_LABEL: Record<string, MessageKey> = {
  PENDING: 'portal.documents.state.pending',
  RUNNING: 'portal.documents.state.running',
  FINISHED: 'portal.activity.filterFinished',
  FAILED: 'portal.documents.state.failed',
};
const JOB_STATUS_TONE: Record<string, string> = {
  PENDING: 'neutral',
  RUNNING: 'working',
  FINISHED: 'success',
  FAILED: 'error',
};
const JOB_STATUS_DESC: Record<string, MessageKey> = {
  PENDING: 'portal.jobDetail.statusDesc.pending',
  RUNNING: 'portal.jobDetail.statusDesc.running',
  FINISHED: 'portal.jobDetail.statusDesc.finished',
  FAILED: 'portal.jobDetail.statusDesc.failed',
};

const LOWER_PROFILE_RETRY_MAP: Record<string, string> = {
  ppocrv6_medium_structurev3: 'ppocrv6_small_structurev3',
  ppocrv6_small_structurev3: 'ppocrv6_tiny_structurev3',
  ppocrv6_medium: 'ppocrv6_tiny',
  ppocrv6_small: 'ppocrv6_tiny',
};

type Job = {
  id: string;
  original_filename: string;
  status: 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
  processing_info?: {
    settings?: Record<string, unknown>;
    execution?: Record<string, unknown>;
  } | null;
  created_at: string;
  document_version?: number;
};

const API = API_BASE_URL;

export default function JobDetailsPage() {
  // useSearchParams (used below to read ?edit=1) requires a Suspense
  // boundary around it, same as app/connections/page.tsx -- omitting this
  // breaks `next build`.
  return (
    <Suspense fallback={<main id="main-content" className="portal-page" />}>
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
  // The Versions table (in the technical details) links between jobs on this
  // same dynamic route. Keying the details component by id remounts it on every
  // id change, so all per-job state starts fresh instead of leaking.
  return <JobDetails key={params.id} jobId={params.id} openEditOnLoad={openEditOnLoad} />;
}

function JobDetails({ jobId, openEditOnLoad }: { jobId: string; openEditOnLoad: boolean }) {
  const { t } = useI18n();
  const router = useRouter();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  // Job metadata (unlike the markdown preview) isn't gated behind the
  // document password, so it's safe to reuse: re-opening a job you already
  // viewed this session paints its header instantly.
  const [job, setJob] = useState<Job | null>(
    () => peekCached<Job>(`/api/v1/jobs/${jobId}`) ?? null
  );
  const [markdown, setMarkdown] = useState('');
  const [draftMarkdown, setDraftMarkdown] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [requirePassword, setRequirePassword] = useState(false);
  // True once a password-protected document was unlocked: it then stays on this
  // page (the review view cannot read it without the password).
  const [unlocked, setUnlocked] = useState(false);
  const [password, setPassword] = useState('');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isRetryingLower, setIsRetryingLower] = useState(false);
  const [restartOpen, setRestartOpen] = useState(false);
  const [viewTab, setViewTab] = useState<'rendered' | 'raw'>('raw');
  const [highlightWeaknesses, setHighlightWeaknesses] = useState(false);
  // null = artifact list not fetched yet (images render skeletons); [] after
  // a completed fetch that found nothing or failed (broken-image placeholder).
  const [artifacts, setArtifacts] = useState<JobArtifact[] | null>(null);

  // `isActive` lets the load effect discard results that finish after the
  // component was left; direct user actions pass the always-true default.
  const loadArtifacts = async (id: string, pw: string, isActive: () => boolean = () => true) => {
    try {
      const url = new URL(`${API}/api/v1/jobs/${id}/artifacts`);
      if (pw) {
        url.searchParams.set('password', pw);
      }
      // skipAuthRedirect: a 401 here means the document password, same as
      // the sibling /preview fetches -- never bounce to /login for it.
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

  // Reads the current language without making `t` an effect dependency: a
  // language switch must not re-run the load and discard an unsaved edit.
  const loadFailedMessage = useEffectEvent(() => t('portal.jobDetail.loadFailed'));

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
        setLoadError(loadFailedMessage());
        return;
      }
      const jobData = await jobResp.json();
      if (!active) {
        return;
      }
      setJob(jobData);
      setCached(`/api/v1/jobs/${id}`, jobData);
      const jobSettings = jobData.processing_info?.settings as Record<string, unknown> | undefined;
      setViewTab(jobSettings?.mode === 'import' ? 'rendered' : 'raw');
      if (jobData.status === 'FINISHED') {
        const previewResp = await apiFetch(`/api/v1/jobs/${id}/preview`, { cache: 'no-store', skipAuthRedirect: true });
        if (!active) {
          return;
        }
        if (previewResp.status === 401) {
          // Could also be an expired session -- redirect instead of
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
        // Finished documents in a knowledge space are handled only in the
        // review view; replace (not push) so Back skips this redirect page.
        // Only when the portal can actually open it: a deleted space or lost
        // space access must keep this page as the fallback, not a dead end.
        if (typeof jobSettings?.collection_id === 'string' && jobSettings.collection_id) {
          const portalResp = await apiFetch(`/api/v1/portal/documents/${id}`, { cache: 'no-store', skipAuthRedirect: true });
          if (!active) {
            return;
          }
          if (portalResp.ok) {
            router.replace(`/reviews/${id}${openEditOnLoad ? '?edit=1' : ''}`);
            return;
          }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, openEditOnLoad]);

  const refreshJob = async () => {
    const refreshed = await apiFetch(`/api/v1/jobs/${jobId}`, { cache: 'no-store' });
    if (refreshed.ok) {
      const jobData = await refreshed.json();
      setJob(jobData);
      setCached(`/api/v1/jobs/${jobId}`, jobData);
    }
  };

  const loadMarkdownWithPassword = async () => {
    const url = new URL(`${API}/api/v1/jobs/${jobId}/preview`);
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
      setUnlocked(true);
      setLoadError(null);
      if (openEditOnLoad) {
        setIsEditing(true);
      }
      void loadArtifacts(jobId, password);
    }
  };

  const back = (
    // Members come from Dokumente; the job list is an admin view.
    <BackLink
      fallbackHref={isAdmin ? '/jobs' : '/documents'}
      fallbackLabel={isAdmin ? t('portal.jobDetail.backToJobs') : t('portal.jobDetail.backToDocuments')}
      className="portal-back"
    />
  );
  const eyebrow = t('portal.jobDetail.eyebrow');

  if (!job) {
    return (
      <PortalPage title={eyebrow}>
        {back}
        {loadError ? (
          <Notice error>{loadError}</Notice>
        ) : (
          <div className="portal-panel animate-pulse space-y-3 p-5" role="status" aria-label={t('portal.jobDetail.loadingJobAria')}>
            <div className="h-4 w-2/3 rounded bg-[var(--surface-2)]" />
            <div className="h-4 w-1/3 rounded bg-[var(--surface-2)]" />
            <div className="h-48 rounded bg-[var(--surface-2)]" />
          </div>
        )}
      </PortalPage>
    );
  }

  const settings = job.processing_info?.settings as Record<string, unknown> | undefined;
  const execution = job.processing_info?.execution as Record<string, unknown> | undefined;
  const collectionId = typeof settings?.collection_id === 'string' && settings.collection_id ? settings.collection_id : null;
  const technical = <TechnicalDetailsButton jobId={job.id} password={password || undefined} onJobChanged={() => void refreshJob()} />;

  // Shown until the preview answers; a finished document with a knowledge space
  // never renders this page's own UI (no flash before the redirect).
  if (job.status === 'FINISHED' && collectionId && !requirePassword && !unlocked) {
    return (
      <PortalPage title={job.original_filename} eyebrow={eyebrow}>
        {back}
        <div className="portal-notice" role="status">{t('portal.jobDetail.redirecting')}</div>
      </PortalPage>
    );
  }

  const statusBadge = (
    <span className={`portal-badge portal-badge-${JOB_STATUS_TONE[job.status] ?? 'neutral'}`}>
      {JOB_STATUS_LABEL[job.status] ? t(JOB_STATUS_LABEL[job.status]) : job.status}
    </span>
  );
  const header = (description: string) => ({
    title: job.original_filename,
    eyebrow,
    description,
    actions: <>{statusBadge}{technical}</>,
  });

  if (requirePassword) {
    return (
      <PortalPage {...header(t('portal.jobDetail.statusDesc.passwordProtected'))}>
        {back}
        <section className="portal-panel max-w-md space-y-4 p-5">
          <h2>{t('portal.jobs.browser.passwordRequired')}</h2>
          <p className="text-sm text-[var(--muted)]">{t('portal.jobs.browser.passwordProtected')}</p>
          {loadError && <Notice error>{loadError}</Notice>}
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void loadMarkdownWithPassword()}
            placeholder={t('portal.jobs.browser.enterPassword')}
            aria-label={t('portal.jobs.browser.enterPassword')}
            className={inputClass}
          />
          <Button onClick={loadMarkdownWithPassword}>{t('portal.jobDetail.unlock')}</Button>
        </section>
      </PortalPage>
    );
  }

  const selectedProfileId = typeof settings?.profile_id === 'string' ? settings.profile_id : null;
  const warning = typeof execution?.warning === 'string' ? execution.warning : null;
  const suggestedLowerProfile =
    (typeof execution?.suggested_profile_id === 'string' ? execution.suggested_profile_id : null) ||
    (selectedProfileId ? LOWER_PROFILE_RETRY_MAP[selectedProfileId] ?? null : null);
  // Same derivation as document-browser.tsx's isImportJob.
  const canRestartWithProfile = settings?.mode !== 'import' && (job.status === 'FINISHED' || job.status === 'FAILED');

  const retryWithLowerProfile = async () => {
    setIsRetryingLower(true);
    setLoadError(null);
    try {
      const response = await apiFetch(`/api/v1/jobs/${job.id}/retry-lower-profile`, { method: 'POST' });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        setLoadError(typeof payload?.detail === 'string' ? payload.detail : t('portal.jobs.browser.alert.retryLowerFailed'));
        return;
      }
      await refreshJob();
    } finally {
      setIsRetryingLower(false);
    }
  };

  const qualityGate = execution?.quality_gate as Record<string, unknown> | undefined;
  const qualityFindings = (Array.isArray(qualityGate?.findings) ? qualityGate.findings : []) as QualityFinding[];
  const findingsByLine = new Map<number, QualityFinding[]>();
  const findingCounts: Partial<Record<QualityFindingKind, number>> = {};
  for (const finding of qualityFindings) {
    findingsByLine.set(finding.line, [...(findingsByLine.get(finding.line) ?? []), finding]);
    findingCounts[finding.kind] = (findingCounts[finding.kind] ?? 0) + 1;
  }

  const saveMarkdown = async () => {
    setIsSaving(true);
    setSaveMessage(null);
    const url = new URL(`${API}/api/v1/jobs/${job.id}/save`);
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

  const showPreview = job.status === 'FINISHED';
  return (
    <PortalPage {...header(t(JOB_STATUS_DESC[job.status] ?? 'portal.jobDetail.statusDesc.finished'))}>
      {back}
      {loadError && <Notice error>{loadError}</Notice>}
      {job.status === 'FAILED' && (
        <Notice error>
          <span className="block">{warning || t('portal.jobDetail.processingStoppedManual')}</span>
          {/* Recovery stays available to the owner, not only to admins. */}
          {(suggestedLowerProfile || canRestartWithProfile) && (
            <span className="mt-3 flex flex-wrap gap-2">
              {suggestedLowerProfile && (
                <Button size="sm" disabled={isRetryingLower} onClick={retryWithLowerProfile}>
                  {isRetryingLower ? t('portal.jobDetail.retrying') : t('portal.jobDetail.retryWith', { profile: suggestedLowerProfile })}
                </Button>
              )}
              {canRestartWithProfile && (
                <Button size="sm" variant={suggestedLowerProfile ? 'outline' : 'default'} onClick={() => setRestartOpen(true)}>
                  <Settings2 className="h-4 w-4" aria-hidden="true" />
                  {t('portal.jobs.browser.rerunWithProfile')}
                </Button>
              )}
            </span>
          )}
        </Notice>
      )}
      {showPreview && !collectionId && (
        <section className="portal-notice flex-wrap" role="status">
          <div>
            <h2 className="font-semibold">{t('portal.jobDetail.noSpaceTitle')}</h2>
            <p className="mt-1 text-sm">{t('portal.jobDetail.noSpaceBody')}</p>
          </div>
          <Link href="/sources/new" className={buttonVariants({ variant: 'outline' })}>{t('portal.jobDetail.reimportSource')}</Link>
        </section>
      )}
      {showPreview && (
        <section className="portal-panel space-y-3 p-5">
          <h2>{t('portal.jobDetail.markdownPreviewHeading')}</h2>
          {/* Plain toggle buttons (aria-pressed), not an ARIA tabs widget:
              the full tablist pattern needs panel wiring + arrow-key focus. */}
          <div className="portal-filter-row" role="group" aria-label={t('portal.jobDetail.markdownPreviewHeading')}>
            <button type="button" aria-pressed={!isEditing && viewTab === 'rendered'} onClick={() => { setIsEditing(false); setViewTab('rendered'); }}>
              {t('portal.jobDetail.renderedTab')}
            </button>
            <button type="button" aria-pressed={!isEditing && viewTab === 'raw'} onClick={() => { setIsEditing(false); setViewTab('raw'); }}>
              {t('portal.jobDetail.rawTab')}
            </button>
            <button type="button" aria-pressed={isEditing} onClick={() => setIsEditing(true)}>
              {t('common.edit')}
            </button>
            {!isEditing && viewTab === 'raw' && qualityFindings.length > 0 && (
              <label className="ml-2 flex items-center gap-2 text-sm text-[var(--muted)]">
                <input type="checkbox" checked={highlightWeaknesses} onChange={(event) => setHighlightWeaknesses(event.target.checked)} />
                {t('portal.jobDetail.highlightWeaknesses')}
              </label>
            )}
          </div>
          {isEditing ? (
            <div className="space-y-2">
              <textarea
                className="min-h-[380px] w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-4 text-sm text-[var(--ink)]"
                aria-label={t('portal.jobs.browser.editMarkdown')}
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
            </div>
          ) : viewTab === 'rendered' ? (
            <div className="rounded-lg border border-[var(--line)] p-4">
              <MarkdownView markdown={markdown} jobId={job.id} password={password || undefined} artifacts={artifacts} />
            </div>
          ) : highlightWeaknesses && qualityFindings.length > 0 ? (
            <>
              <div className="flex flex-wrap gap-3 text-xs text-[var(--muted)]">
                {(Object.keys(findingCounts) as QualityFindingKind[]).map((kind) => (
                  <span key={kind} className="portal-badge portal-badge-warning">
                    {t(`portal.jobDetail.finding.${kind}` as MessageKey)}: {findingCounts[kind]}
                  </span>
                ))}
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg border border-[var(--line)] p-4 text-sm text-[var(--ink)]">
                {markdown.split('\n').map((line, index) => {
                  const lineFindings = findingsByLine.get(index + 1);
                  return (
                    <span
                      key={index}
                      className={lineFindings ? 'block bg-[var(--warn-bg)]' : 'block'}
                      title={lineFindings?.map((finding) => `${finding.kind}: ${finding.text}`).join('\n')}
                    >
                      {line || ' '}
                    </span>
                  );
                })}
              </pre>
            </>
          ) : (
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg border border-[var(--line)] p-4 text-sm text-[var(--ink)]">{markdown}</pre>
          )}
          <div aria-live="polite">{saveMessage && <p className="text-sm text-[var(--muted)]">{saveMessage}</p>}</div>
        </section>
      )}
      {restartOpen && (
        <RestartWithProfileDialog
          jobId={job.id}
          jobLabel={job.original_filename}
          onClose={() => setRestartOpen(false)}
          onRestarted={() => void refreshJob()}
        />
      )}
    </PortalPage>
  );
}
