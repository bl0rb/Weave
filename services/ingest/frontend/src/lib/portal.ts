import { ApiError, apiFetch, apiJson } from '@/lib/api';
import { DEFAULT_LOCALE, INTL_LOCALE, type Locale } from '@/i18n/config';
import { translate } from '@/i18n/messages';
import { currentReleaseStatus, isIndexReady, publicationState, type IndexingItem } from './indexing-status';

/** Roles on a knowledge space (ADR 0008): owner > member > reader. */
export type CollectionRole = 'owner' | 'member' | 'reader';
export type TeamRef = { id: string; name: string };
/** One entry of a space's access list: a person (`user_id`) or a team (`team_id`). */
export type CollectionGrant = {
  user_id: string | null; team_id: string | null; role: CollectionRole; name: string;
  /** A person's primary team, for display. */
  team?: string | null; is_active?: boolean;
};
export type GrantInput = { user_id?: string; team_id?: string; role: CollectionRole };
export type KnowledgeSpace = {
  collection_id: string; slug: string; name: string;
  /** The space's purpose ("Zweck"); required for new spaces, may be empty on older ones. */
  description: string | null;
  can_manage: boolean; can_upload: boolean; role: CollectionRole | null;
  visibility: 'public' | 'restricted'; grants: CollectionGrant[];
  created_by: { id: string; username: string } | null; responsible_team: TeamRef | null;
};
export type Publication = { id: string; created_at: string; status: 'pending' | 'sent' | 'failed'; error_message: string | null; released_by: string | null };
export type PortalSource = {
  kind: 'upload' | 'confluence' | 'mail' | 'unknown'; label: string; path: string | null; url: string | null;
  /** Uploader (for Confluence: who ran the import) and when; kept by an edited version. */
  uploaded_by?: string | null; uploaded_at?: string | null;
  /** Last change made in the portal editor. */
  edited_by?: string | null; edited_at?: string | null;
};
export type PortalDocument = {
  id: string; original_filename: string; status: 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
  collection_id: string; collection_name: string; created_at: string;
  quality_grade: string | null; quality_recommendation: string | null; can_release: boolean; release: Publication | null;
  source: PortalSource; review_decision: string | null;
};
export type DocumentPage = { items: PortalDocument[]; total: number };
/** One entry of GET /api/v1/portal/collections/{id}/import-scopes -- a distinct Confluence import scope among the space's visible documents. */
export type ImportScope = { value: string; scope_type: string; scope_value: string; label: string; count: number; latest_run_id: string; can_edit: boolean };
export type ImportScopesResponse = { items: ImportScope[]; other_count: number };
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
  /** The portal editor may change this document (and releases the change). */
  can_edit?: boolean;
};
export type PortalEditResult = { document_id: string; document_version: number; release: Publication };
export type PortalConfig = { publication_configured: boolean; team_name: string | null; team_names: string[]; teams: TeamRef[] };
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
export type DirectoryTeam = { id: string; name: string; member_count: number };

/** Person-picker search behind the access dialog -- 403 unless the caller is an admin or owns at least one collection. */
export function searchDirectoryUsers(query: string, signal?: AbortSignal): Promise<{ items: DirectoryUser[] }> {
  return apiJson(`/api/v1/directory/users?${new URLSearchParams({ q: query, limit: '20' })}`, { signal });
}

/** Team-chip listing behind the access dialog -- same authorization gate as {@link searchDirectoryUsers}. */
export function loadDirectoryTeams(signal?: AbortSignal): Promise<{ items: DirectoryTeam[] }> {
  return apiJson('/api/v1/directory/teams', { signal });
}

/** PATCH /api/v1/collections/{id} scoped to the access dialog's fields -- `grants` replaces the whole list and needs an owner; 422 on an unknown team/user id, 403 unless the caller owns the collection. */
export function updateCollectionAccess(collectionId: string, access: { visibility: 'public' | 'restricted'; grants: GrantInput[] }): Promise<KnowledgeSpace> {
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
export function loadDocuments(collectionId?: string, offset = 0, reviewState: ReviewStateFilter = 'all', qualityGrade?: QualityGradeFilter, limit = 20, importScope?: string): Promise<DocumentPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit), review_state: reviewState });
  if (collectionId) params.set('collection_id', collectionId);
  if (qualityGrade) params.set('quality_grade', qualityGrade);
  if (importScope) params.set('import_scope', importScope);
  return apiJson(`/api/v1/portal/documents?${params}`);
}

/** Distinct Confluence import scopes among a space's visible documents -- backs the "Confluence-Bereich" filter on /knowledge/[id]. */
export function loadImportScopes(collectionId: string): Promise<ImportScopesResponse> {
  return apiJson(`/api/v1/portal/collections/${encodeURIComponent(collectionId)}/import-scopes`);
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

/** `withdrawReleased` (delete only): released documents are withdrawn from the knowledge index and deleted too. */
export function bulkPortalAction(jobIds: string[], action: BulkAction, acceptQualityWarnings = false, withdrawReleased = false): Promise<BulkActionResult> {
  return apiJson('/api/v1/portal/documents/bulk', jsonBody({ job_ids: jobIds, action, accept_quality_warnings: acceptQualityWarnings, withdraw_released: withdrawReleased }));
}

export function skipPortalDocument(jobId: string): Promise<PortalDocument> {
  return apiJson(`/api/v1/portal/documents/${encodeURIComponent(jobId)}/skip`, { method: 'POST' });
}

export function unskipPortalDocument(jobId: string): Promise<PortalDocument> {
  return apiJson(`/api/v1/portal/documents/${encodeURIComponent(jobId)}/unskip`, { method: 'POST' });
}

/**
 * Save an edited Markdown and release it at once. A released document gets a
 * new version (``document_id`` differs); the change applies until a changed
 * source (new upload or sync) is released.
 */
export function editPortalDocument(jobId: string, markdown: string, markdownSha256: string, acceptQualityWarning = false): Promise<PortalEditResult> {
  return apiJson(`/api/v1/portal/documents/${encodeURIComponent(jobId)}/edit`, jsonBody({
    markdown, markdown_sha256: markdownSha256, ...(acceptQualityWarning ? { accept_quality_warning: true } : {}),
  }));
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
