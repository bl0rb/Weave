'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, CheckCheck, CircleQuestionMark, Clock, FileText, GitCompare, MessageSquareX, RefreshCw, UserCheck, X, CircleX } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/auth-context';
import { loadWritableSpaces, portalError, type KnowledgeSpace } from '@/lib/portal';
import {
  REVIEW_TASK_KINDS, REVIEW_TASK_PAGE_SIZE, REVIEW_TASK_STATUSES, REVIEW_TASKS_CHANGED_EVENT,
  contradictionDetails, feedbackDetails, gapDetails, isActiveTask, loadQualitySummary, loadReviewTask, loadReviewTasks, reviewDueDetails, updateReviewTask,
  type QualitySummary, type ReviewTask, type ReviewTaskDocumentRef, type ReviewTaskKind, type ReviewTaskPage, type ReviewTaskStatus, type ReviewTaskStatusFilter, type ReviewTaskUpdate,
} from '@/lib/review-tasks';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { EmptyState, Notice, PortalPage } from './shared';

/**
 * 'Prüfaufgaben' (/pruefaufgaben): the knowledge owners' inbox of review
 * tasks -- knowledge gaps from unanswered chat questions, negative answer
 * feedback, due document reviews and possible contradictions between two
 * documents (lib/review-tasks.ts, backend app/api/review_tasks.py). A quality
 * summary strip over the last 90 days sits on top; the list opens a detail
 * panel with kind-specific details and the actions Übernehmen / Erledigt /
 * Verwerfen. The chat side is anonymous: no detail names a chat user.
 */

type T = (key: MessageKey, vars?: Record<string, string | number>) => string;

const KIND_LABEL: Record<ReviewTaskKind, MessageKey> = {
  knowledge_gap: 'portal.reviewTasks.kind.knowledge_gap',
  answer_feedback: 'portal.reviewTasks.kind.answer_feedback',
  review_due: 'portal.reviewTasks.kind.review_due',
  contradiction: 'portal.reviewTasks.kind.contradiction',
};
const STATUS_LABEL: Record<ReviewTaskStatus, MessageKey> = {
  open: 'portal.reviewTasks.status.open',
  in_progress: 'portal.reviewTasks.status.in_progress',
  done: 'portal.reviewTasks.status.done',
  dismissed: 'portal.reviewTasks.status.dismissed',
};
const STATUS_TONE: Record<ReviewTaskStatus, string> = { open: 'warning', in_progress: 'working', done: 'success', dismissed: 'neutral' };
const KIND_ICON: Record<ReviewTaskKind, typeof CircleQuestionMark> = { knowledge_gap: CircleQuestionMark, answer_feedback: MessageSquareX, review_due: Clock, contradiction: GitCompare };

const kindLabel = (t: T, kind: string) => (kind in KIND_LABEL ? t(KIND_LABEL[kind as ReviewTaskKind]) : kind);
const statusLabel = (t: T, status: string) => (status in STATUS_LABEL ? t(STATUS_LABEL[status as ReviewTaskStatus]) : status);
const statusTone = (status: string) => STATUS_TONE[status as ReviewTaskStatus] ?? 'neutral';
const documentHref = (document: ReviewTaskDocumentRef) => `/reviews/${encodeURIComponent(document.job_id)}`;

function notifyChanged() {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(REVIEW_TASKS_CHANGED_EVENT));
}

export type ReviewTasksInitialFilters = { aufgabe?: string; art?: string; status?: string; bereich?: string };

function initialStatus(value?: string): ReviewTaskStatusFilter {
  return value === 'active' || REVIEW_TASK_STATUSES.includes(value as ReviewTaskStatus) ? value as ReviewTaskStatusFilter : 'active';
}
function initialKind(value?: string): ReviewTaskKind | '' {
  return REVIEW_TASK_KINDS.includes(value as ReviewTaskKind) ? value as ReviewTaskKind : '';
}

export function ReviewTasks({ initial = {} }: { initial?: ReviewTasksInitialFilters }) {
  const { t, locale } = useI18n();
  const [status, setStatus] = useState<ReviewTaskStatusFilter>(() => initialStatus(initial.status));
  const [kind, setKind] = useState<ReviewTaskKind | ''>(() => initialKind(initial.art));
  const [collectionId, setCollectionId] = useState(initial.bereich ?? '');
  const [page, setPage] = useState(1);
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [tasks, setTasks] = useState<ReviewTaskPage | null>(null);
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(initial.aufgabe ?? null);
  const requestId = useRef(0);
  const summaryRequestId = useRef(0);
  const listRef = useRef<HTMLUListElement>(null);
  const listHeadingRef = useRef<HTMLHeadingElement>(null);

  // Only the newest request may update the list (filters can change faster than responses arrive).
  const load = useCallback(() => {
    const current = ++requestId.current;
    return loadReviewTasks({ status, kind, collectionId: collectionId || undefined, page })
      .then(result => {
        if (current !== requestId.current) return;
        setTasks(result);
        setError('');
        // A page past the end (tasks closed meanwhile): step back to the last page that has tasks.
        if (!result.items.length && page > 1) setPage(Math.max(1, Math.min(page - 1, Math.ceil(result.total / REVIEW_TASK_PAGE_SIZE))));
      })
      .catch(reason => { if (current === requestId.current) setError(portalError(reason, locale)); });
  }, [status, kind, collectionId, page, locale]);

  // Same guard for the summary: a slow response for a previous space must not overwrite the current one.
  const loadSummary = useCallback(() => {
    const current = ++summaryRequestId.current;
    loadQualitySummary(collectionId || undefined)
      .then(result => { if (current === summaryRequestId.current) setSummary(result); })
      .catch(() => { if (current === summaryRequestId.current) setSummary(null); });
  }, [collectionId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => {
    loadWritableSpaces()
      .then(result => {
        setSpaces(result);
        // A deep-linked space (?bereich=) the caller does not maintain, or a stale id, is ignored.
        setCollectionId(current => (current && !result.some(space => space.collection_id === current) ? '' : current));
      })
      .catch(() => setSpaces([]));
  }, []);

  function changeFilter(update: () => void) {
    update();
    setPage(1);
  }

  /** A changed task replaces its row, or leaves the list when it no longer matches the status filter. */
  function onTaskChanged(task: ReviewTask) {
    const matches = status === 'active' ? isActiveTask(task) : task.status === status;
    setTasks(previous => {
      if (!previous) return previous;
      if (matches) return { ...previous, items: previous.items.map(item => (item.id === task.id ? task : item)) };
      return { ...previous, items: previous.items.filter(item => item.id !== task.id), total: Math.max(0, previous.total - 1) };
    });
    // The last row of a page left: show the previous page, or reload so tasks from later pages move up.
    if (!matches && tasks && tasks.items.length > 0 && tasks.items.every(item => item.id === task.id)) {
      if (page > 1) setPage(page - 1);
      else void load();
    }
    loadSummary();
    notifyChanged();
  }

  /** Closes the detail panel and returns focus to the row that opened it (or the list heading when that row has left). */
  function closeDetail() {
    const id = selectedId;
    setSelectedId(null);
    const row = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('.portal-review-task-open') ?? []).find(button => button.dataset.taskId === id);
    (row ?? listHeadingRef.current)?.focus();
  }

  const totalPages = tasks ? Math.max(1, Math.ceil(tasks.total / REVIEW_TASK_PAGE_SIZE)) : 1;
  const hasFilters = Boolean(kind || collectionId || status !== 'active');
  const selectedFromList = tasks?.items.find(item => item.id === selectedId) ?? null;

  return (
    <PortalPage title={t('portal.reviewTasks.title')} description={t('portal.reviewTasks.description')} eyebrow={t('portal.reviewTasks.eyebrow')}>
      <QualitySummaryStrip summary={summary} />

      {error && <Notice error action={() => void load()}>{error}</Notice>}

      <div className={`portal-tasks-grid ${selectedId ? 'portal-tasks-grid-open' : ''}`}>
        <section className="portal-panel" aria-labelledby="review-tasks-list-title">
          <div className="portal-section-heading">
            <div>
              <p className="portal-eyebrow">{t('portal.reviewTasks.listEyebrow')}</p>
              <h2 id="review-tasks-list-title" ref={listHeadingRef} tabIndex={-1}>{t('portal.reviewTasks.listHeading')}{tasks ? ` · ${tasks.total}` : ''}</h2>
            </div>
            <Button variant="ghost" size="sm" onClick={() => { void load(); loadSummary(); }}>
              <RefreshCw size={15} aria-hidden="true" /> {t('common.refresh')}
            </Button>
          </div>

          <div className="flex flex-wrap items-end gap-3 border-b border-[var(--line)] px-5 py-4">
            <label className="min-w-[160px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
              {t('portal.reviewTasks.filter.status')}
              <select value={status} onChange={event => changeFilter(() => setStatus(event.target.value as ReviewTaskStatusFilter))}>
                <option value="active">{t('portal.reviewTasks.status.active')}</option>
                {REVIEW_TASK_STATUSES.map(value => <option key={value} value={value}>{statusLabel(t, value)}</option>)}
              </select>
            </label>
            <label className="min-w-[160px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
              {t('portal.reviewTasks.filter.kind')}
              <select value={kind} onChange={event => changeFilter(() => setKind(event.target.value as ReviewTaskKind | ''))}>
                <option value="">{t('portal.reviewTasks.filter.allKinds')}</option>
                {REVIEW_TASK_KINDS.map(value => <option key={value} value={value}>{kindLabel(t, value)}</option>)}
              </select>
            </label>
            <label className="min-w-[160px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
              {t('portal.reviewTasks.filter.space')}
              <select value={collectionId} onChange={event => changeFilter(() => { setCollectionId(event.target.value); setSummary(null); })}>
                <option value="">{t('portal.reviewTasks.filter.allSpaces')}</option>
                {spaces.map(space => <option key={space.collection_id} value={space.collection_id}>{space.name || space.slug}</option>)}
              </select>
            </label>
          </div>

          {tasks === null && !error ? (
            <Notice>{t('portal.reviewTasks.loading')}</Notice>
          ) : tasks?.items.length ? (
            <ul ref={listRef} className="portal-task-list" aria-label={t('portal.reviewTasks.listHeading')}>
              {tasks.items.map(task => (
                <TaskRow key={task.id} task={task} selected={task.id === selectedId} onSelect={() => setSelectedId(task.id)} />
              ))}
            </ul>
          ) : tasks ? (
            <EmptyState title={hasFilters ? t('portal.reviewTasks.empty.filteredTitle') : t('portal.reviewTasks.empty.title')}>
              {hasFilters ? t('portal.reviewTasks.empty.filteredBody') : t('portal.reviewTasks.empty.body')}
            </EmptyState>
          ) : null}
          {/* Also on an emptied page, so the user can always get back to the other pages. */}
          {tasks && (totalPages > 1 || page > 1) && (
            <nav className="portal-pagination" aria-label={t('portal.reviewTasks.paginationAria')}>
              <span>{t('portal.reviewTasks.pageOf', { page, totalPages: Math.max(page, totalPages) })}</span>
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>{t('common.back')}</Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>{t('common.next')}</Button>
            </nav>
          )}
        </section>

        {selectedId && (
          <TaskDetail
            key={selectedId}
            taskId={selectedId}
            fallback={selectedFromList}
            onClose={closeDetail}
            onChanged={onTaskChanged}
          />
        )}
      </div>
    </PortalPage>
  );
}

function QualitySummaryStrip({ summary }: { summary: QualitySummary | null }) {
  const { t, formatNumber } = useI18n();
  if (!summary) return null;
  const tiles: { label: string; value: number; color: string; hint?: string }[] = [
    { label: t('portal.reviewTasks.summary.helpful'), value: summary.feedback.helpful, color: 'var(--ok)' },
    { label: t('portal.reviewTasks.summary.wrong'), value: summary.feedback.wrong, color: 'var(--err)' },
    { label: t('portal.reviewTasks.summary.outdated'), value: summary.feedback.outdated, color: 'var(--warn)' },
    { label: t('portal.reviewTasks.summary.unanswered'), value: summary.unanswered, color: 'var(--warn)' },
    {
      label: t('portal.reviewTasks.summary.tasks'), value: summary.tasks.open + summary.tasks.in_progress, color: 'var(--accent)',
      hint: t('portal.reviewTasks.summary.tasksHint', { open: summary.tasks.open, inProgress: summary.tasks.in_progress }),
    },
  ];
  return (
    <section aria-labelledby="quality-summary-title" className="portal-quality-summary">
      <h2 id="quality-summary-title" className="portal-eyebrow">{t('portal.reviewTasks.summary.heading')}</h2>
      <div className="portal-tiles">
        {tiles.map(tile => (
          <div key={tile.label} className="portal-tile" style={{ '--c': tile.color } as React.CSSProperties}>
            <span className="portal-tile-label">{tile.label}</span>
            <strong>{formatNumber(tile.value)}</strong>
            {tile.hint && <small>{tile.hint}</small>}
          </div>
        ))}
      </div>
      {summary.documents.length > 0 && (
        <details className="portal-quality-legend portal-quality-documents">
          <summary>{t('portal.reviewTasks.summary.documents', { count: summary.documents.length })}</summary>
          <ol>
            {summary.documents.map(document => (
              <li key={document.job_id}>
                <Link href={`/reviews/${encodeURIComponent(document.job_id)}`}>{document.title}</Link>
                <span>{t('portal.reviewTasks.summary.documentCounts', { wrong: document.wrong, outdated: document.outdated, helpful: document.helpful })}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}

function TaskRow({ task, selected, onSelect }: { task: ReviewTask; selected: boolean; onSelect: () => void }) {
  const { t, formatDate } = useI18n();
  const Icon = KIND_ICON[task.kind as ReviewTaskKind] ?? CircleQuestionMark;
  const iconTone = task.kind === 'answer_feedback' || task.kind === 'contradiction' ? 'portal-task-icon-err' : '';
  return (
    <li className={`portal-task portal-review-task ${selected ? 'portal-review-task-selected' : ''}`}>
      <span className={`portal-task-icon ${iconTone}`} aria-hidden="true"><Icon size={18} /></span>
      <span className="portal-task-text">
        <button type="button" className="portal-review-task-open" data-task-id={task.id} aria-current={selected ? 'true' : undefined} onClick={onSelect}>
          <strong>{task.title}</strong>
        </button>
        <small>
          {kindLabel(t, task.kind)} · {task.collection.name || task.collection.slug}
          {task.document ? ` · ${task.document.title}` : ''} · {t('portal.reviewTasks.lastSeen', { date: formatDate(task.last_seen_at) })}
          {task.assignee ? ` · ${t('portal.reviewTasks.assignedTo', { name: task.assignee.username })}` : ''}
        </small>
      </span>
      <span className="portal-review-task-meta">
        <span className={`portal-badge portal-badge-${statusTone(task.status)}`}>{statusLabel(t, task.status)}</span>
        {task.occurrence_count > 1 && (
          <span className="portal-pill" title={t('portal.reviewTasks.occurrences', { count: task.occurrence_count })}>
            <span aria-hidden="true">×{task.occurrence_count}</span>
            <span className="sr-only">{t('portal.reviewTasks.occurrences', { count: task.occurrence_count })}</span>
          </span>
        )}
      </span>
    </li>
  );
}

function TaskDetail({ taskId, fallback, onClose, onChanged }: {
  taskId: string; fallback: ReviewTask | null; onClose: () => void; onChanged: (task: ReviewTask) => void;
}) {
  const { t, locale, formatDate } = useI18n();
  const { user } = useAuth();
  const [task, setTask] = useState<ReviewTask | null>(fallback);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadReviewTask(taskId, controller.signal)
      .then(result => { setTask(result); setLoadError(''); })
      .catch(reason => { if (!controller.signal.aborted) setLoadError(portalError(reason, locale)); });
    return () => controller.abort();
  }, [taskId, locale]);

  // Move focus into the panel when it opens, so keyboard and screen-reader users land on it.
  useEffect(() => { headingRef.current?.focus(); }, []);

  async function apply(update: ReviewTaskUpdate) {
    if (!task || busy) return;
    setBusy(true);
    setActionError('');
    try {
      const updated = await updateReviewTask(task.id, update);
      setTask(updated);
      // Only done/dismiss send the note; after Übernehmen it is kept for the closing step.
      if (update.status === 'done' || update.status === 'dismissed') setNote('');
      onChanged(updated);
    } catch (reason) {
      setActionError(portalError(reason, locale));
    } finally {
      setBusy(false);
    }
  }

  const active = task ? isActiveTask(task) : false;
  const assignedToMe = Boolean(task?.assignee && user && task.assignee.id === user.id);
  const trimmedNote = note.trim();

  function onKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key !== 'Escape' || event.defaultPrevented || event.nativeEvent.isComposing) return;
    // Escape inside a filled text field must not close the panel and discard what was typed.
    const target = event.target as HTMLElement;
    if ((target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) && target.value) return;
    onClose();
  }

  return (
    <section className="portal-panel portal-review-task-detail" aria-labelledby="review-task-detail-title" onKeyDown={onKeyDown}>
      <div className="portal-section-heading">
        <div className="min-w-0">
          <p className="portal-eyebrow">{task ? kindLabel(t, task.kind) : t('portal.reviewTasks.detail.eyebrow')}</p>
          <h2 id="review-task-detail-title" ref={headingRef} tabIndex={-1}>{task?.title ?? t('portal.reviewTasks.loading')}</h2>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('portal.reviewTasks.detail.close')}><X size={18} aria-hidden="true" /></Button>
      </div>

      {loadError && !task && <Notice error>{loadError}</Notice>}

      {task && (
        <div className="portal-review-task-body">
          <dl className="portal-review-task-facts">
            <div><dt>{t('portal.reviewTasks.detail.status')}</dt><dd><span className={`portal-badge portal-badge-${statusTone(task.status)}`}>{statusLabel(t, task.status)}</span></dd></div>
            <div><dt>{t('portal.reviewTasks.detail.space')}</dt><dd><Link href={`/knowledge/${encodeURIComponent(task.collection.id)}`}>{task.collection.name || task.collection.slug}</Link></dd></div>
            <div><dt>{t('portal.reviewTasks.detail.occurrences')}</dt><dd>{task.occurrence_count}</dd></div>
            <div><dt>{t('portal.reviewTasks.detail.firstSeen')}</dt><dd>{formatDate(task.first_seen_at)}</dd></div>
            <div><dt>{t('portal.reviewTasks.detail.lastSeen')}</dt><dd>{formatDate(task.last_seen_at)}</dd></div>
            <div><dt>{t('portal.reviewTasks.detail.assignee')}</dt><dd>{task.assignee?.username ?? t('portal.reviewTasks.detail.unassigned')}</dd></div>
            {task.resolved_at && <div><dt>{t('portal.reviewTasks.detail.resolvedAt')}</dt><dd>{formatDate(task.resolved_at)}</dd></div>}
          </dl>
          {task.resolution_note && <p className="portal-review-task-note"><strong>{t('portal.reviewTasks.detail.resolutionNote')}</strong> {task.resolution_note}</p>}

          {task.document && task.kind !== 'contradiction' && (
            <p className="portal-review-task-document">
              <FileText size={16} aria-hidden="true" />
              <Link href={documentHref(task.document)}>{task.document.title}</Link>
              <span>{t('portal.reviewTasks.detail.version', { version: task.document.document_version })}</span>
            </p>
          )}

          <KindDetails task={task} />

          {active && (
            <div className="portal-review-task-actions">
              <label className="text-sm font-semibold text-[var(--ink-2)]">
                {t('portal.reviewTasks.action.noteLabel')}
                <textarea rows={2} maxLength={2000} value={note} onChange={event => setNote(event.target.value)} placeholder={t('portal.reviewTasks.action.notePlaceholder')} />
              </label>
              {actionError && <Notice error>{actionError}</Notice>}
              <div className="flex flex-wrap gap-2">
                {user && !(task.status === 'in_progress' && assignedToMe) && (
                  <Button variant="outline" size="sm" disabled={busy} onClick={() => void apply({ status: 'in_progress', assignee_id: user.id })}>
                    <UserCheck size={15} aria-hidden="true" /> {t('portal.reviewTasks.action.take')}
                  </Button>
                )}
                <Button size="sm" disabled={busy} onClick={() => void apply({ status: 'done', ...(trimmedNote ? { resolution_note: trimmedNote } : {}) })}>
                  <CheckCheck size={15} aria-hidden="true" /> {t('portal.reviewTasks.action.done')}
                </Button>
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => void apply({ status: 'dismissed', ...(trimmedNote ? { resolution_note: trimmedNote } : {}) })}>
                  <CircleX size={15} aria-hidden="true" /> {t('portal.reviewTasks.action.dismiss')}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function KindDetails({ task }: { task: ReviewTask }) {
  switch (task.kind) {
    case 'knowledge_gap': return <GapDetails task={task} />;
    case 'answer_feedback': return <FeedbackDetails task={task} />;
    case 'review_due': return <ReviewDueDetails task={task} />;
    case 'contradiction': return <ContradictionDetails task={task} />;
    default: return null;
  }
}

function GapDetails({ task }: { task: ReviewTask }) {
  const { t, formatDate } = useI18n();
  const { questions, rewrittenQueries } = gapDetails(task.details);
  return (
    <div className="portal-review-task-section">
      <p className="portal-field-hint">{t('portal.reviewTasks.gap.hint')}</p>
      <h3>{t('portal.reviewTasks.gap.questions')}</h3>
      {questions.length ? (
        <ul className="portal-review-task-items">
          {questions.map(question => (
            <li key={question.text}>
              <span>{question.text}</span>
              <small>
                {t('portal.reviewTasks.gap.asked', { count: question.count })}
                {question.created_at ? ` · ${formatDate(question.created_at)}` : ''}
              </small>
            </li>
          ))}
        </ul>
      ) : <p className="portal-field-hint">{t('portal.reviewTasks.gap.noQuestions')}</p>}
      {rewrittenQueries.length > 0 && (
        <>
          <h3>{t('portal.reviewTasks.gap.rewrittenQueries')}</h3>
          <ul className="portal-review-task-items">{rewrittenQueries.map(query => <li key={query}><span>{query}</span></li>)}</ul>
        </>
      )}
      <Link className="portal-inline-link" href="/sources/new">{t('portal.reviewTasks.gap.addSource')} <ArrowRight size={14} aria-hidden="true" /></Link>
    </div>
  );
}

function FeedbackDetails({ task }: { task: ReviewTask }) {
  const { t, formatDate } = useI18n();
  const { wrong, outdated, samples } = feedbackDetails(task.details);
  const ratingLabel = (rating: string) => (rating === 'wrong' ? t('portal.reviewTasks.feedback.wrong') : rating === 'outdated' ? t('portal.reviewTasks.feedback.outdated') : rating);
  return (
    <div className="portal-review-task-section">
      <p className="portal-review-task-counts">
        <span className="portal-pill portal-pill-err">{t('portal.reviewTasks.feedback.wrongCount', { count: wrong })}</span>
        <span className="portal-pill">{t('portal.reviewTasks.feedback.outdatedCount', { count: outdated })}</span>
      </p>
      <h3>{t('portal.reviewTasks.feedback.samples')}</h3>
      {samples.length ? (
        <ul className="portal-review-task-items">
          {samples.map((sample, index) => (
            <li key={`${sample.created_at ?? ''}-${index}`}>
              <small>
                <span className={`portal-badge portal-badge-${sample.rating === 'wrong' ? 'error' : 'warning'}`}>{ratingLabel(sample.rating)}</span>
                {sample.created_at ? ` ${formatDate(sample.created_at)}` : ''}
                {sample.heading_path ? ` · ${sample.heading_path}` : ''}
              </small>
              {sample.question && <span><strong>{t('portal.reviewTasks.feedback.question')}</strong> {sample.question}</span>}
              {sample.answer_excerpt && <blockquote>{sample.answer_excerpt}</blockquote>}
              {sample.comment
                ? <span><strong>{t('portal.reviewTasks.feedback.comment')}</strong> {sample.comment}</span>
                : <small>{t('portal.reviewTasks.feedback.noComment')}</small>}
            </li>
          ))}
        </ul>
      ) : <p className="portal-field-hint">{t('portal.reviewTasks.feedback.noSamples')}</p>}
    </div>
  );
}

function ReviewDueDetails({ task }: { task: ReviewTask }) {
  const { t, formatDate } = useI18n();
  const { reviewDueAt, overdue } = reviewDueDetails(task.details);
  return (
    <div className="portal-review-task-section">
      <p>
        {t('portal.reviewTasks.reviewDue.dueAt', { date: formatDate(reviewDueAt) })}{' '}
        {overdue && <span className="portal-badge portal-badge-error">{t('portal.reviewTasks.reviewDue.overdue')}</span>}
      </p>
      <p className="portal-field-hint">{t('portal.reviewTasks.reviewDue.hint')}</p>
      {task.document && <Link className="portal-inline-link" href={documentHref(task.document)}>{t('portal.reviewTasks.reviewDue.open')} <ArrowRight size={14} aria-hidden="true" /></Link>}
    </div>
  );
}

function ContradictionDetails({ task }: { task: ReviewTask }) {
  const { t } = useI18n();
  const pairs = contradictionDetails(task.details);
  const documentHeader = (document: ReviewTaskDocumentRef | null) => document
    ? <Link href={documentHref(document)}>{document.title} <span className="portal-field-hint">{t('portal.reviewTasks.detail.version', { version: document.document_version })}</span></Link>
    : <span>{t('portal.reviewTasks.contradiction.unknownDocument')}</span>;
  return (
    <div className="portal-review-task-section">
      <p className="portal-field-hint">{t('portal.reviewTasks.contradiction.hint')}</p>
      <div className="portal-contradiction-docs">
        <div><span className="portal-field-hint">{t('portal.reviewTasks.contradiction.documentA')}</span>{documentHeader(task.document)}</div>
        <div><span className="portal-field-hint">{t('portal.reviewTasks.contradiction.documentB')}</span>{documentHeader(task.related_document)}</div>
      </div>
      {pairs.length ? pairs.map((pair, index) => (
        <div key={index} className="portal-contradiction-pair" role="group" aria-label={t('portal.reviewTasks.contradiction.pair', { number: index + 1 })}>
          <ContradictionSide heading={pair.heading_path} excerpt={pair.excerpt} facts={pair.facts} />
          <ContradictionSide heading={pair.other_heading_path} excerpt={pair.other_excerpt} facts={pair.other_facts} />
        </div>
      )) : <p className="portal-field-hint">{t('portal.reviewTasks.contradiction.noExcerpts')}</p>}
    </div>
  );
}

function ContradictionSide({ heading, excerpt, facts }: { heading: string; excerpt: string; facts: string[] }) {
  const { t } = useI18n();
  return (
    <div className="portal-contradiction-side">
      {heading && <small>{heading}</small>}
      <blockquote>{excerpt || '–'}</blockquote>
      {facts.length > 0 && <small>{t('portal.reviewTasks.contradiction.facts', { facts: facts.join(', ') })}</small>}
    </div>
  );
}
