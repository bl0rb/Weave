import { ApiError, apiFetch, apiJson } from '@/lib/api';
import type { AccessUserDetail } from '@/lib/access-summary';
import { DEFAULT_LOCALE, INTL_LOCALE, type Locale } from '@/i18n/config';
import { translate } from '@/i18n/messages';
import { currentReleaseStatus, isIndexReady, publicationState, type IndexingItem } from './indexing-status';

export type KnowledgeSpace = {
  collection_id: string; slug: string; name: string; description: string | null; read_teams: string[]; can_manage: boolean; can_upload: boolean;
  // Landing concurrently on the backend (see services/ingest/backend's
  // CollectionResponse) — optional here until every deployment is on the
  // new schema. accessSummary()/<AccessLine> already understand them.
  visibility?: 'public' | 'restricted'; read_users?: string[]; read_user_details?: AccessUserDetail[];
};
export type Publication = { id: string; created_at: string; status: 'pending' | 'sent' | 'failed'; error_message: string | null; released_by: string | null };
export type PortalSource = { kind: 'upload' | 'confluence' | 'mail' | 'unknown'; label: string; path: string | null; url: string | null };
export type PortalDocument = {
  id: string; original_filename: string; status: 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
  collection_id: string; collection_name: string; created_at: string;
  quality_grade: string | null; quality_recommendation: string | null; can_release: boolean; release: Publication | null;
  source: PortalSource; review_decision: string | null;
};
export type DocumentPage = { items: PortalDocument[]; total: number };
export type ReviewStateFilter = 'review' | 'all' | 'skipped';
export type BulkAction = 'release' | 'skip' | 'unskip' | 'delete';
export type BulkActionResult = { done: number; errors: { job_id: string; reason: string }[] };
export type QualityGradeFilter = '' | 'A' | 'B' | 'C' | 'none';
export type QualitySignals = {
  ocr_confidence: number | null; confidence_sample_size: number;
  structure_quality: number; noise_penalty: number; text_quality: number;
  field_validation: Record<string, number> | null;
};
export type QualityDetail = {
  grade: string | null; score: number | null; recommendation: string | null;
  thresholds: { A: number; B: number }; signals: QualitySignals; issues: string[];
};
export type QualityMissingReason = 'not_finished' | 'failed' | 'legacy' | 'import_without_gate' | 'unknown';
export type DocumentPreview = PortalDocument & {
  markdown: string; markdown_sha256: string; profile_id: string | null; can_reprocess: boolean;
  quality: QualityDetail | null; quality_missing_reason: QualityMissingReason | null;
};
export type PortalConfig = { publication_configured: boolean; team_name: string | null; team_names: string[] };
export const jsonBody = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function portalError(error: unknown, locale: Locale = DEFAULT_LOCALE): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return translate(locale, 'portal.error.unauthorized');
    if (error.status === 403) return translate(locale, 'portal.error.forbidden');
    if (error.status === 404) return translate(locale, 'portal.error.notFound');
    if (error.status === 409) return translate(locale, 'portal.error.conflict');
    if (error.status === 413) return translate(locale, 'portal.error.payloadTooLarge');
    if (error.status === 422) return error.detail || translate(locale, 'portal.error.validation');
    if (error.status === 429) return translate(locale, 'portal.error.rateLimited');
    if (error.status === 503) return translate(locale, 'portal.error.serviceUnavailable');
  }
  return translate(locale, 'portal.error.generic');
}

export function portalDownloadError(error: unknown, locale: Locale = DEFAULT_LOCALE): string {
  if (error instanceof ApiError && error.status === 404) {
    return translate(locale, 'portal.error.downloadMissing');
  }
  return portalError(error, locale);
}

/* -------------------------------------------------------------------------
 * Zugriffsdialog (AccessDialog, src/components/portal/access-dialog.tsx) --
 * the person-picker/team-chip directory search and the access-only PATCH.
 * ---------------------------------------------------------------------- */

/** One row of GET /api/v1/directory/users -- the access dialog's person-picker search result. */
export type DirectoryUser = { id: string; username: string; display_name: string | null; team: string | null };
/** One row of GET /api/v1/directory/teams -- every team plus its member_count, for the access dialog's team chips. */
export type DirectoryTeam = { name: string; member_count: number };

/** Person-picker search behind the access dialog -- 403 unless the caller is an admin or manages at least one collection. */
export function searchDirectoryUsers(query: string, signal?: AbortSignal): Promise<{ items: DirectoryUser[] }> {
  return apiJson(`/api/v1/directory/users?${new URLSearchParams({ q: query, limit: '20' })}`, { signal });
}

/** Team-chip listing behind the access dialog -- same authorization gate as {@link searchDirectoryUsers}. */
export function loadDirectoryTeams(signal?: AbortSignal): Promise<{ items: DirectoryTeam[] }> {
  return apiJson('/api/v1/directory/teams', { signal });
}

/** PATCH /api/v1/collections/{id} scoped to the access dialog's three fields -- 422 on an unknown team/user id, 403 unless the caller can manage the collection. */
export function updateCollectionAccess(collectionId: string, access: { visibility: 'public' | 'restricted'; read_teams: string[]; read_users: string[] }): Promise<KnowledgeSpace> {
  return apiJson(`/api/v1/collections/${encodeURIComponent(collectionId)}`, { ...jsonBody(access), method: 'PATCH' });
}

export function documentState(document: PortalDocument, live?: IndexingItem, locale: Locale = DEFAULT_LOCALE): { label: string; tone: 'neutral' | 'working' | 'warning' | 'success' | 'error'; hint?: string } {
  if (document.release) {
    const { delivery, indexing } = currentReleaseStatus(document.release, live);
    return publicationState(delivery, indexing, locale);
  }
  if (document.review_decision === 'skipped') return { label: translate(locale, 'portal.documents.state.skipped'), tone: 'neutral' };
  if (document.status === 'FAILED') return { label: translate(locale, 'portal.documents.state.failed'), tone: 'error' };
  if (document.status === 'RUNNING') return { label: translate(locale, 'portal.documents.state.running'), tone: 'working' };
  if (document.status === 'PENDING') return { label: translate(locale, 'portal.documents.state.pending'), tone: 'neutral' };
  if (document.quality_grade?.toUpperCase() === 'C') return { label: translate(locale, 'portal.documents.state.qualityC'), tone: 'warning' };
  if (document.quality_recommendation?.trim().toLowerCase() === 'block') return { label: translate(locale, 'portal.documents.state.qualityBlocked'), tone: 'error' };
  return { label: translate(locale, 'portal.documents.state.readyForReview'), tone: 'warning' };
}
export const documentUrl = (document: PortalDocument) => document.status === 'FINISHED' ? `/reviews/${document.id}` : `/jobs/${document.id}`;
export const dateLabel = (value: string, locale: Locale = DEFAULT_LOCALE) => new Intl.DateTimeFormat(INTL_LOCALE[locale], { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(value));
export function loadDocuments(collectionId?: string, offset = 0, reviewState: ReviewStateFilter = 'all', qualityGrade?: QualityGradeFilter, limit = 20): Promise<DocumentPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit), review_state: reviewState });
  if (collectionId) params.set('collection_id', collectionId);
  if (qualityGrade) params.set('quality_grade', qualityGrade);
  return apiJson(`/api/v1/portal/documents?${params}`);
}

/**
 * The four automatic/manual stops a document's journey to the chat can be
 * in, plus a cross-cutting 'error' bucket — mirrors the "Dokumentweg" the
 * 06-loom-rc design prototype (docs/design-proposals/06-loom-rc) shows as a
 * numbered pipeline: 1 Verarbeitung -> 2 Prüfung -> 3 Indexierung -> 4 Im
 * Chat, with Fehler called out separately rather than as a fifth step.
 */
export type PipelineStage = 'processing' | 'review' | 'indexing' | 'ready' | 'error';

/** Numbered steps only — 'error' is shown as a separate, unnumbered chip (see the design reference above). */
export function pipelineSteps(locale: Locale = DEFAULT_LOCALE): { value: Exclude<PipelineStage, 'error'>; step: number; label: string; hint: string }[] {
  const automatic = translate(locale, 'portal.pipeline.hint.automatic');
  return [
    { value: 'processing', step: 1, label: translate(locale, 'portal.pipeline.processing.label'), hint: automatic },
    { value: 'review', step: 2, label: translate(locale, 'portal.pipeline.review.label'), hint: translate(locale, 'portal.pipeline.review.hint') },
    { value: 'indexing', step: 3, label: translate(locale, 'portal.pipeline.indexing.label'), hint: automatic },
    { value: 'ready', step: 4, label: translate(locale, 'portal.pipeline.ready.label'), hint: translate(locale, 'portal.pipeline.ready.hint') },
  ];
}

/**
 * Buckets one document into the pipeline stage a person actually cares
 * about, independent of the more granular {@link documentState} label.
 * Without a `live` indexing snapshot a released document can only be
 * placed in 'indexing' (not 'ready') — the caller can pass one from
 * {@link useIndexingStatus} for an accurate 'ready' vs 'indexing' split.
 */
export function pipelineStage(document: PortalDocument, live?: IndexingItem): PipelineStage {
  if (document.status === 'PENDING' || document.status === 'RUNNING') return 'processing';
  if (document.status === 'FAILED') return 'error';
  if (!document.release) return 'review';
  const { delivery, indexing } = currentReleaseStatus(document.release, live);
  if (isIndexReady(indexing)) return 'ready';
  if (delivery === 'failed' || indexing?.state === 'failed' || indexing?.state === 'blocked' || indexing?.state === 'incomplete' || indexing?.state === 'mismatch') return 'error';
  return 'indexing';
}

/** Tallies documents per {@link PipelineStage} — shared by the Übersicht stat tiles, the Wissensbereiche cards and the Dokumente filter bar. */
export function summarizePipeline(documents: PortalDocument[], live: Record<string, IndexingItem> = {}): Record<PipelineStage, number> {
  const counts: Record<PipelineStage, number> = { processing: 0, review: 0, indexing: 0, ready: 0, error: 0 };
  for (const document of documents) counts[pipelineStage(document, live[document.id])] += 1;
  return counts;
}

export function bulkPortalAction(jobIds: string[], action: BulkAction, acceptQualityWarnings = false): Promise<BulkActionResult> {
  return apiJson('/api/v1/portal/documents/bulk', jsonBody({ job_ids: jobIds, action, accept_quality_warnings: acceptQualityWarnings }));
}

export function skipPortalDocument(jobId: string): Promise<PortalDocument> {
  return apiJson(`/api/v1/portal/documents/${encodeURIComponent(jobId)}/skip`, { method: 'POST' });
}

export function unskipPortalDocument(jobId: string): Promise<PortalDocument> {
  return apiJson(`/api/v1/portal/documents/${encodeURIComponent(jobId)}/unskip`, { method: 'POST' });
}

export function reindexPortalDocument(jobId: string): Promise<Publication> {
  return apiJson(`/api/v1/portal/documents/${encodeURIComponent(jobId)}/reindex`, { method: 'POST' });
}

export function reindexKnowledgeSpace(collectionId: string): Promise<{ requeued: number }> {
  return apiJson(`/api/v1/portal/collections/${encodeURIComponent(collectionId)}/reindex`, { method: 'POST' });
}

export function markdownDownloadName(originalFilename: string): string {
  const basename = originalFilename.replaceAll('\\', '/').split('/').at(-1) || 'dokument';
  const dot = basename.lastIndexOf('.');
  const stem = (dot > 0 ? basename.slice(0, dot) : basename)
    .replace(/[^\p{L}\p{N} .()_-]+/gu, '_')
    .replace(/^[ .]+|[ .]+$/g, '') || 'dokument';
  return `${stem}.md`;
}

export function collectionDownloadName(space: Pick<KnowledgeSpace, 'name' | 'slug'>): string {
  return `${markdownDownloadName(`${space.name || space.slug}.md`).slice(0, -3)}-markdown.zip`;
}

export async function downloadPortalFile(path: string, filename: string, locale: Locale = DEFAULT_LOCALE): Promise<void> {
  const response = await apiFetch(path);
  if (!response.ok) {
    let detail = translate(locale, 'portal.error.downloadFailed', { status: response.status });
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // Keep the stable user-facing fallback for non-JSON error bodies.
    }
    throw new ApiError(response.status, detail);
  }
  const blob = await response.blob();
  const href = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(href);
}
