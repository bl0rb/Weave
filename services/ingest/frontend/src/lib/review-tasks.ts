import { apiJson } from '@/lib/api';
import { jsonBody } from '@/lib/portal';

/**
 * Client of the 'Prüfaufgaben' inbox (backend: app/api/review_tasks.py,
 * schemas in app/schemas/review_tasks.py). Tasks come from anonymous chat
 * signals (unanswered questions, negative answer feedback), from the
 * document review tick (`review_due`) and from Weave-Knowledge's
 * contradiction check (`contradiction`). Nothing here identifies a chat user.
 */

export type ReviewTaskKind = 'knowledge_gap' | 'answer_feedback' | 'review_due' | 'contradiction';
export type ReviewTaskStatus = 'open' | 'in_progress' | 'done' | 'dismissed';
/** `active` = open + in_progress (the backend default). */
export type ReviewTaskStatusFilter = ReviewTaskStatus | 'active';

export const REVIEW_TASK_KINDS: ReviewTaskKind[] = ['knowledge_gap', 'answer_feedback', 'review_due', 'contradiction'];
export const REVIEW_TASK_STATUSES: ReviewTaskStatus[] = ['open', 'in_progress', 'done', 'dismissed'];
export const REVIEW_TASK_PAGE_SIZE = 25;
/** Window event the inbox fires after changing a task, so the sidebar badge refreshes at once. */
export const REVIEW_TASKS_CHANGED_EVENT = 'weave:review-tasks-changed';

export type ReviewTaskDocumentRef = { job_id: string; title: string; document_version: number };
export type ReviewTask = {
  id: string; kind: ReviewTaskKind | string; status: ReviewTaskStatus | string; title: string;
  collection: { id: string; name: string; slug: string };
  document: ReviewTaskDocumentRef | null; related_document: ReviewTaskDocumentRef | null;
  occurrence_count: number; first_seen_at: string; last_seen_at: string;
  assignee: { id: string; username: string } | null;
  resolved_at: string | null; resolution_note: string | null;
  /** Kind-specific; read through the tolerant helpers below. */
  details: Record<string, unknown>;
};
export type ReviewTaskPage = { items: ReviewTask[]; total: number; page: number; page_size: number };
export type ReviewTaskCount = { open: number; by_kind: Record<string, number> };
export type ReviewTaskUpdate = { status?: ReviewTaskStatus; assignee_id?: string | null; resolution_note?: string | null };
export type QualitySummary = {
  feedback: { helpful: number; wrong: number; outdated: number };
  unanswered: number;
  tasks: { open: number; in_progress: number };
  documents: { job_id: string; title: string; helpful: number; wrong: number; outdated: number }[];
};
export type ReviewTaskFilters = { status?: ReviewTaskStatusFilter; kind?: ReviewTaskKind | ''; collectionId?: string; page?: number; pageSize?: number };

export function loadReviewTasks(filters: ReviewTaskFilters = {}, signal?: AbortSignal): Promise<ReviewTaskPage> {
  const params = new URLSearchParams({
    status: filters.status ?? 'active',
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? REVIEW_TASK_PAGE_SIZE),
  });
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.collectionId) params.set('collection_id', filters.collectionId);
  return apiJson(`/api/v1/portal/review-tasks?${params}`, { cache: 'no-store', signal });
}

export function loadReviewTask(id: string, signal?: AbortSignal): Promise<ReviewTask> {
  return apiJson(`/api/v1/portal/review-tasks/${encodeURIComponent(id)}`, { cache: 'no-store', signal });
}

/** Active (open + in_progress) tasks of the caller's spaces -- the sidebar badge. */
export function loadReviewTaskCount(signal?: AbortSignal): Promise<ReviewTaskCount> {
  return apiJson('/api/v1/portal/review-tasks/count', { cache: 'no-store', signal });
}

export function updateReviewTask(id: string, update: ReviewTaskUpdate): Promise<ReviewTask> {
  return apiJson(`/api/v1/portal/review-tasks/${encodeURIComponent(id)}`, { ...jsonBody(update), method: 'PATCH' });
}

/** Last 90 days of chat feedback and unanswered questions over the caller's spaces (or one of them). */
export function loadQualitySummary(collectionId?: string, signal?: AbortSignal): Promise<QualitySummary> {
  const params = collectionId ? `?${new URLSearchParams({ collection_id: collectionId })}` : '';
  return apiJson(`/api/v1/portal/quality/summary${params}`, { cache: 'no-store', signal });
}

export function isActiveTask(task: Pick<ReviewTask, 'status'>): boolean {
  return task.status === 'open' || task.status === 'in_progress';
}

/* -------------------------------------------------------------------------
 * Kind-specific `details`. The backend stores them as free JSON, so every
 * reader tolerates missing or mistyped fields instead of throwing.
 * ---------------------------------------------------------------------- */

const record = (value: unknown): Record<string, unknown> => (value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {});
const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value.trim() : null);
const count = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0);
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

/** Heading paths arrive as a list of headings or a ready string; '' when absent. */
export function headingPathLabel(value: unknown): string {
  if (Array.isArray(value)) return value.filter(part => typeof part === 'string' && part.trim()).join(' › ');
  return text(value) ?? '';
}

export type GapQuestion = { text: string; count: number; created_at: string | null };
/** `knowledge_gap`: {questions:[{text, created_at, count}], rewritten_queries:[...]}. */
export function gapDetails(details: Record<string, unknown>): { questions: GapQuestion[]; rewrittenQueries: string[] } {
  const questions = list(details.questions).flatMap(entry => {
    const item = record(entry);
    const question = text(item.text);
    return question ? [{ text: question, count: Math.max(1, count(item.count)), created_at: text(item.created_at) }] : [];
  });
  const rewrittenQueries = list(details.rewritten_queries).flatMap(entry => (text(entry) ? [text(entry) as string] : []));
  return { questions, rewrittenQueries };
}

export type FeedbackSample = { rating: string; comment: string | null; question: string | null; answer_excerpt: string | null; heading_path: string; created_at: string | null };
/** `answer_feedback`: {counts:{wrong, outdated}, samples:[...] newest first}. */
export function feedbackDetails(details: Record<string, unknown>): { wrong: number; outdated: number; samples: FeedbackSample[] } {
  const counts = record(details.counts);
  const samples = list(details.samples).map(entry => {
    const item = record(entry);
    return {
      rating: text(item.rating) ?? '', comment: text(item.comment), question: text(item.question),
      answer_excerpt: text(item.answer_excerpt), heading_path: headingPathLabel(item.heading_path), created_at: text(item.created_at),
    };
  });
  return { wrong: count(counts.wrong), outdated: count(counts.outdated), samples };
}

/** `review_due`: {review_due_at, overdue}. */
export function reviewDueDetails(details: Record<string, unknown>): { reviewDueAt: string | null; overdue: boolean } {
  return { reviewDueAt: text(details.review_due_at), overdue: details.overdue === true };
}

export type ContradictionPair = {
  excerpt: string; other_excerpt: string; heading_path: string; other_heading_path: string;
  facts: string[]; other_facts: string[]; similarity: number | null;
};
/**
 * `contradiction`: up to 5 excerpt pairs of the two documents (Weave-Knowledge
 * report fields, see POST /api/v1/quality/contradictions). Read from
 * `details.pairs`, or `details.excerpts`, or the report fields directly on
 * `details` (a single pair).
 */
export function contradictionDetails(details: Record<string, unknown>): ContradictionPair[] {
  const raw = Array.isArray(details.pairs) ? details.pairs : Array.isArray(details.excerpts) ? details.excerpts : [details];
  const facts = (value: unknown) => list(value).flatMap(entry => (text(entry) ? [text(entry) as string] : typeof entry === 'number' ? [String(entry)] : []));
  return raw.flatMap(entry => {
    const item = record(entry);
    const excerpt = text(item.excerpt) ?? '';
    const otherExcerpt = text(item.other_excerpt) ?? '';
    if (!excerpt && !otherExcerpt) return [];
    return [{
      excerpt, other_excerpt: otherExcerpt,
      heading_path: headingPathLabel(item.heading_path), other_heading_path: headingPathLabel(item.other_heading_path),
      facts: facts(item.facts), other_facts: facts(item.other_facts),
      similarity: typeof item.similarity === 'number' ? item.similarity : null,
    }];
  });
}
