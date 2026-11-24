'use client';

import { useState, type FormEvent } from 'react';
import { Flag, ThumbsDown, ThumbsUp } from 'lucide-react';
import { cn } from '@/lib/utils';
import { deleteJson, putJson } from '@/lib/api-client';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import type { FeedbackRating, FeedbackResponseBody, MessageFeedback } from '@/types/weave-api';

/** Weave-API's FEEDBACK_COMMENT_MAX_LENGTH (backend/app/schemas/feedback.py). */
const COMMENT_MAX_LENGTH = 2000;

const RATINGS: Array<{ rating: FeedbackRating; label: MessageKey; Icon: typeof ThumbsUp }> = [
  { rating: 'helpful', label: 'chat.feedback.helpful', Icon: ThumbsUp },
  { rating: 'wrong', label: 'chat.feedback.wrong', Icon: ThumbsDown },
  // Stored as `outdated` (Weave-API's rating enum); shown as „Prüfung anfordern“.
  { rating: 'outdated', label: 'chat.feedback.outdated', Icon: Flag },
];

type Notice = 'thanks' | 'removed' | 'failed' | null;

interface AnswerFeedbackProps {
  /** Weave-API's id of the persisted assistant message (`UiMessage.serverId`). */
  messageId: number;
  /** The caller's stored rating, if any (GET /v1/conversations/{id}). */
  feedback: MessageFeedback | null | undefined;
  /** Called after every successful save (`null` = rating withdrawn). */
  onChange?: (feedback: MessageFeedback | null) => void;
}

/**
 * The feedback row under a finished answer: „Hilfreich“, „Falsch“,
 * „Prüfung anfordern“ (stored as `outdated`) as toggle buttons
 * (`aria-pressed`), saved right away via PUT /api/messages/{id}/feedback
 * (Weave-API's
 * PUT /v1/messages/{message_id}/feedback); clicking the active rating
 * again withdraws it (DELETE). After „Falsch“/„Prüfung anfordern“ an optional
 * comment field opens — the rating itself is already stored, so leaving
 * the comment out loses nothing; sending one re-saves the same rating with
 * it. Weave-API forwards the rating anonymously to the review tasks of the
 * cited knowledge spaces, which their owners and members see in the
 * portal — the user's identity never leaves Weave-API.
 */
export function AnswerFeedback({ messageId, feedback, onChange }: AnswerFeedbackProps) {
  const { t } = useI18n();
  const [current, setCurrent] = useState<MessageFeedback | null>(feedback ?? null);
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState(feedback?.comment ?? '');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const path = `/api/messages/${messageId}/feedback`;

  async function save(rating: FeedbackRating, commentText: string | null): Promise<boolean> {
    setBusy(true);
    setNotice(null);
    const result = await putJson<FeedbackResponseBody>(path, { rating, comment: commentText });
    setBusy(false);
    if (!result.ok) {
      setNotice('failed');
      return false;
    }
    const saved: MessageFeedback = { rating: result.data.rating, comment: result.data.comment, updated_at: result.data.updated_at };
    setCurrent(saved);
    setNotice('thanks');
    onChange?.(saved);
    return true;
  }

  async function withdraw() {
    setBusy(true);
    setNotice(null);
    const result = await deleteJson(path);
    setBusy(false);
    if (!result.ok) {
      setNotice('failed');
      return;
    }
    setCurrent(null);
    setCommentOpen(false);
    setComment('');
    setNotice('removed');
    onChange?.(null);
  }

  async function choose(rating: FeedbackRating) {
    if (busy) return;
    if (current?.rating === rating) {
      await withdraw();
      return;
    }
    // A comment typed for one negative rating still fits the other one;
    // switching to „Hilfreich“ drops it.
    const keep = rating !== 'helpful' && comment.trim() ? comment : null;
    if (await save(rating, keep)) {
      setCommentOpen(rating !== 'helpful');
      if (rating === 'helpful') setComment('');
    }
  }

  async function submitComment(event: FormEvent) {
    event.preventDefault();
    if (busy || !current || current.rating === 'helpful') return;
    if (await save(current.rating, comment.trim() ? comment : null)) setCommentOpen(false);
  }

  const negative = current !== null && current.rating !== 'helpful';

  return (
    <div className="mt-3 flex flex-col gap-2" data-testid="answer-feedback">
      <div role="group" aria-label={t('chat.feedback.groupLabel')} className="flex flex-wrap items-center gap-1.5">
        {RATINGS.map(({ rating, label, Icon }) => {
          const pressed = current?.rating === rating;
          return (
            <button
              key={rating}
              type="button"
              aria-pressed={pressed}
              disabled={busy}
              onClick={() => void choose(rating)}
              className={cn(
                'inline-flex min-h-[32px] items-center gap-1 rounded-[var(--radius-control)] border border-[var(--line)] px-2.5 py-1 text-xs font-medium disabled:opacity-60',
                pressed ? 'bg-[var(--accent-soft)] text-[var(--accent)]' : 'text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]'
              )}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              {t(label)}
            </button>
          );
        })}
        {negative && !commentOpen ? (
          <button
            type="button"
            onClick={() => setCommentOpen(true)}
            className="text-xs font-medium text-[var(--accent)] underline-offset-2 hover:underline"
          >
            {t('chat.feedback.commentEdit')}
          </button>
        ) : null}
        {notice === 'thanks' || notice === 'removed' ? (
          <span role="status" className="text-xs text-[var(--muted)]">
            {t(notice === 'thanks' ? 'chat.feedback.thanks' : 'chat.feedback.removed')}
          </span>
        ) : null}
      </div>
      {negative && commentOpen ? (
        <form onSubmit={submitComment} className="flex flex-col gap-1.5">
          <label className="flex flex-col gap-1 text-xs font-medium text-[var(--muted)]">
            {t('chat.feedback.commentLabel')}
            <textarea
              value={comment}
              maxLength={COMMENT_MAX_LENGTH}
              rows={2}
              onChange={(event) => setComment(event.target.value)}
              className="rounded-[var(--radius-control)] border border-[var(--line)] bg-[var(--surface)] px-2.5 py-1.5 text-sm font-normal text-[var(--ink)] outline-none focus-visible:border-[var(--accent)] focus-visible:ring-[3px] focus-visible:ring-[var(--accent-soft)]"
            />
          </label>
          <div>
            <button
              type="submit"
              disabled={busy}
              className="rounded-[var(--radius-control)] border border-[var(--line)] px-2.5 py-1 text-xs font-medium text-[var(--ink)] hover:bg-[var(--surface-2)] disabled:opacity-60"
            >
              {t('chat.feedback.commentSubmit')}
            </button>
          </div>
        </form>
      ) : null}
      {notice === 'failed' ? (
        <p role="alert" className="text-xs text-[var(--err,#e03131)]">
          {t('chat.feedback.failed')}
        </p>
      ) : null}
    </div>
  );
}
