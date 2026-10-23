'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Mail, Settings2, Webhook, Wrench } from 'lucide-react';

import { ErrorNotice, Field, inputClass, LoadingState, Modal } from '@/components/admin/admin-shared';
import { QualityGradeLegend } from '@/components/portal/shared';
import { Button, buttonVariants } from '@/components/ui/button';
import type { PaddleCapabilities } from '@/components/dashboard/shared';
import { WebhookSendDialog } from '@/components/webhook-send-dialog';
import { apiFetch, type JobVersionEntry, type JobVersionsResponse } from '@/lib/api';
import { API_BASE_URL } from '@/lib/api-base';
import { useAuth } from '@/lib/auth-context';
import { peekCached, setCached } from '@/lib/data-cache';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';

const CAPABILITIES_PATH = '/api/v1/paddle/capabilities';
const API = API_BASE_URL;

export type TechnicalJob = {
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

const JOB_STATUS_LABEL: Record<string, MessageKey> = {
  PENDING: 'portal.documents.state.pending',
  RUNNING: 'portal.documents.state.running',
  FINISHED: 'portal.activity.filterFinished',
  FAILED: 'portal.documents.state.failed',
};

const qualityRecommendationKeys: Record<string, MessageKey> = {
  allow: 'portal.jobDetail.recommendation.allow',
  warn: 'portal.jobDetail.recommendation.warn',
  block: 'portal.jobDetail.recommendation.block',
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

function AdminBadge() {
  const { t } = useI18n();
  return <span className="portal-badge portal-badge-neutral ml-2 align-middle">{t('portal.jobDetail.adminBadge')}</span>;
}

/**
 * The only place technical processing data is shown for a document. A separate
 * button (not inline) keeps the document views readable for non-technical users;
 * the job is fetched on first open, not on page load.
 */
export function TechnicalDetailsButton({ jobId, className, extraActions, onJobChanged, password }: {
  jobId: string;
  className?: string;
  extraActions?: React.ReactNode;
  onJobChanged?: () => void;
  /** Document password, only needed so downloads of an unlocked protected document work. */
  password?: string;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState<TechnicalJob | null>(null);
  const [versions, setVersions] = useState<JobVersionEntry[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [webhookOpen, setWebhookOpen] = useState(false);
  const [restartOpen, setRestartOpen] = useState(false);

  const load = async () => {
    try {
      const resp = await apiFetch(`/api/v1/jobs/${jobId}`, { cache: 'no-store', skipAuthRedirect: true });
      if (!resp.ok) {
        setFailed(true);
        return;
      }
      const data = (await resp.json()) as TechnicalJob;
      setJob(data);
      setCached(`/api/v1/jobs/${jobId}`, data);
      setFailed(false);
    } catch {
      setFailed(true);
      return;
    }
    try {
      const resp = await apiFetch(`/api/v1/jobs/${jobId}/versions`, { cache: 'no-store', skipAuthRedirect: true });
      if (!resp.ok) {
        setVersions(null);
        return;
      }
      const payload = (await resp.json()) as JobVersionsResponse;
      setVersions(Array.isArray(payload?.items) ? payload.items : null);
    } catch {
      // Non-fatal: an older backend without this endpoint simply hides Versions.
      setVersions(null);
    }
  };

  const settings = job?.processing_info?.settings as Record<string, unknown> | undefined;
  const execution = job?.processing_info?.execution as Record<string, unknown> | undefined;
  const selectedProfileId = typeof settings?.profile_id === 'string' ? settings.profile_id : null;
  const selectedProfileLabel = typeof execution?.profile_label === 'string' ? execution.profile_label : null;
  const converter = typeof execution?.converter === 'string' ? execution.converter : null;
  const structure = execution?.structure as Record<string, unknown> | undefined;
  const blockCount = typeof structure?.block_count === 'number' ? structure.block_count : null;
  const pageCount = typeof structure?.page_count === 'number' ? structure.page_count : null;
  // Requeue paths keep the previous run's execution fields until a worker claims
  // the job, so the banners must only reflect a finished run.
  const usedFallback = job?.status === 'FINISHED' && execution?.used_fallback === true;
  const fallbackReason = typeof execution?.fallback_reason === 'string' ? execution.fallback_reason : null;
  const engine = typeof execution?.engine === 'string' ? execution.engine : null;
  const resolvedProfileId = typeof execution?.profile_id === 'string' ? execution.profile_id : null;
  // A vl:<connection-id> request always resolves to the openai_vision pipeline
  // internally -- expected, not the worker falling back from an unknown profile.
  const profileMismatch = Boolean(
    job?.status === 'FINISHED' &&
      selectedProfileId &&
      !selectedProfileId.startsWith('vl:') &&
      resolvedProfileId &&
      selectedProfileId !== resolvedProfileId
  );
  // Same derivation as document-browser.tsx's isImportJob.
  const isImportJob = settings?.mode === 'import';
  const canRestartWithProfile = !isImportJob && (job?.status === 'FINISHED' || job?.status === 'FAILED');
  const qualityGate = execution?.quality_gate as Record<string, unknown> | undefined;
  const qualityGrade = typeof qualityGate?.grade === 'string' ? qualityGate.grade : null;
  const qualityScore = typeof qualityGate?.score === 'number' ? qualityGate.score : null;
  const qualityRecommendation = typeof qualityGate?.recommendation === 'string' ? qualityGate.recommendation : null;
  const pwQuery = password ? `?password=${encodeURIComponent(password)}` : '';

  const nestedOpen = webhookOpen || restartOpen;
  return (
    <>
      <Button type="button" variant="outline" className={className} onClick={() => {
        setOpen(true);
        // Fetched on first open only; reopening reuses what is already loaded.
        if (!job) void load();
      }}>
        <Wrench className="h-4 w-4" aria-hidden="true" />
        {t('portal.jobDetail.technicalButton')}
      </Button>
      {/* Hidden (not stacked) while a nested dialog is open so Escape closes only the top one. */}
      {open && !nestedOpen && (
        <Modal title={t('portal.jobDetail.technicalTitle', { filename: job?.original_filename ?? '' })} onClose={() => setOpen(false)} size="lg">
          {!job && !failed && <LoadingState label={t('portal.jobDetail.loadingJobAria')} />}
          {failed && <ErrorNotice message={t('portal.jobDetail.detailsLoadFailed')} />}
          {job && (
            <div className="space-y-4 text-sm text-[var(--ink)]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="portal-badge portal-badge-neutral">{JOB_STATUS_LABEL[job.status] ? t(JOB_STATUS_LABEL[job.status]) : job.status}</span>
                {typeof job.document_version === 'number' && <span className="portal-badge portal-badge-neutral">v{job.document_version}</span>}
                {settings?.mode === 'mail_attachment' && mailMessageIdFromSettings(settings) && (
                  <span className="portal-badge portal-badge-success gap-1"><Mail className="h-3 w-3" aria-hidden="true" /> {t('portal.jobs.browser.mailAttachment')}</span>
                )}
                <span className="text-[var(--muted)]">{t('portal.jobDetail.createdLabel', { date: new Date(job.created_at).toLocaleString() })}</span>
              </div>
              {isAdmin && (selectedProfileId || selectedProfileLabel || converter || (pageCount !== null && blockCount !== null)) && (
                <section className="space-y-1">
                  <AdminBadge />
                  {selectedProfileId && <p>{t('portal.jobDetail.profileLabel', { name: profileIdDisplay(selectedProfileId, settings) })}</p>}
                  {selectedProfileLabel && <p>{t('portal.jobDetail.profileNameLabel', { name: selectedProfileLabel })}</p>}
                  {converter && <p>{t('portal.jobDetail.converterLabel', { name: converter })}</p>}
                  {pageCount !== null && blockCount !== null && <p>{t('portal.jobDetail.structureLabel', { pages: pageCount, blocks: blockCount })}</p>}
                </section>
              )}
              {isAdmin && usedFallback && (
                <div className="portal-notice portal-notice-error" role="alert">
                  <div>
                    <p className="font-semibold">{t('portal.jobDetail.ocrFallbackTitle', { engine: engine ?? 'plain-text' })}</p>
                    <p className="mt-1">{t('portal.jobDetail.ocrFallbackBody', { label: selectedProfileLabel ? ` (${selectedProfileLabel})` : '' })}</p>
                    {fallbackReason && <p className="mt-1 break-words">{t('portal.jobDetail.reasonLabel', { reason: fallbackReason })}</p>}
                  </div>
                </div>
              )}
              {isAdmin && !usedFallback && profileMismatch && (
                <div className="portal-notice" role="status">
                  <span>{t('portal.jobDetail.profileMismatch', { requested: selectedProfileId ?? '', resolved: resolvedProfileId ?? '' })}</span>
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
              {isAdmin && versions && versions.length > 1 && (
                <section>
                  <h3 className="mb-2 font-semibold">{t('portal.jobDetail.versionsHeading')}<AdminBadge /></h3>
                  <div className="portal-table-scroll">
                    <table className="portal-table">
                      <thead>
                        <tr>
                          <th scope="col">{t('portal.jobDetail.colVersion')}</th>
                          <th scope="col">{t('portal.jobDetail.colSha')}</th>
                          <th scope="col">{t('common.status')}</th>
                          <th scope="col">{t('portal.jobDetail.colCreated')}</th>
                          <th scope="col">{t('portal.jobDetail.colUploadedBy')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {versions.map((entry) => (
                          <tr key={entry.job_id}>
                            <td>
                              {entry.job_id === job.id ? (
                                <span className="font-medium">v{entry.document_version}</span>
                              ) : (
                                <Link href={`/jobs/${entry.job_id}`} className="font-medium">v{entry.document_version}</Link>
                              )}
                              {entry.is_current && <span className="ml-2 text-xs font-medium text-[var(--muted)]">{t('portal.jobDetail.current')}</span>}
                            </td>
                            <td className="font-mono text-xs" title={entry.content_sha256 ?? ''}>
                              {entry.content_sha256 ? entry.content_sha256.slice(0, 12) : '-'}
                            </td>
                            <td><span className="portal-badge portal-badge-neutral">{entry.status}</span></td>
                            <td>{new Date(entry.created_at).toLocaleString()}</td>
                            <td>{entry.uploaded_by ?? '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
              <section className="border-t border-[var(--line)] pt-4">
                <h3 className="mb-2 font-semibold">{t('portal.jobDetail.actionsHeading')}</h3>
                <div className="flex flex-wrap gap-2">
                  {job.status === 'FINISHED' && (
                    <a className={buttonVariants({ variant: 'outline' })} href={`${API}/api/v1/jobs/${job.id}/download${pwQuery}`}>
                      {t('portal.jobDetail.downloadMarkdown')}
                    </a>
                  )}
                  {job.status === 'FINISHED' && isAdmin && (
                    <a className={buttonVariants({ variant: 'outline' })} href={`${API}/api/v1/jobs/${job.id}/export.json${pwQuery}`}>
                      {t('portal.jobDetail.downloadJson')}<AdminBadge />
                    </a>
                  )}
                  {job.status === 'FINISHED' && isAdmin && (
                    <Button type="button" variant="outline" onClick={() => setWebhookOpen(true)}>
                      <Webhook className="h-4 w-4" aria-hidden="true" />
                      {t('portal.jobs.browser.sendToWebhook')}<AdminBadge />
                    </Button>
                  )}
                  {isAdmin && canRestartWithProfile && (
                    <Button type="button" variant="outline" onClick={() => setRestartOpen(true)}>
                      <Settings2 className="h-4 w-4" aria-hidden="true" />
                      {t('portal.jobs.browser.rerunWithProfile')}<AdminBadge />
                    </Button>
                  )}
                  {extraActions}
                </div>
              </section>
              {isAdmin && (
                <details>
                  <summary className="cursor-pointer font-semibold">{t('portal.jobDetail.technicalDetails')}<AdminBadge /></summary>
                  <pre className="mt-2 overflow-x-auto rounded-lg border border-[var(--line)] bg-[var(--surface-2)] p-4 text-xs text-[var(--ink)]">
                    {JSON.stringify(job.processing_info ?? {}, null, 2)}
                  </pre>
                </details>
              )}
            </div>
          )}
        </Modal>
      )}
      {isAdmin && webhookOpen && job && (
        <WebhookSendDialog job={{ id: job.id, label: job.original_filename }} onClose={() => setWebhookOpen(false)} />
      )}
      {restartOpen && job && (
        <RestartWithProfileDialog
          jobId={job.id}
          jobLabel={job.original_filename}
          onClose={() => setRestartOpen(false)}
          onRestarted={() => {
            void load();
            onJobChanged?.();
          }}
        />
      )}
    </>
  );
}

/**
 * Profile picker for the "Re-run with profile…" action, mirroring
 * document-browser.tsx's dialog of the same name (duplicated rather than
 * imported -- that component owns its own copy of this UI).
 */
export function RestartWithProfileDialog({ jobId, jobLabel, onClose, onRestarted }: {
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
