'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { CalendarCheck, GitCompare } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { ApiError, type JobVersionEntry } from '@/lib/api';
import { compareLink, confirmDocumentReview, dateInputValue, hasNewerRelease, loadDocumentVersions, setDocumentReviewDue, type DocumentReview } from '@/lib/document-freshness';
import { portalError } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { Notice } from './shared';

/** Review dates are UTC midnights (app/services/document_freshness.py): format them in UTC so every time zone shows the chosen day. */
const DUE_DATE_FORMAT: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeZone: 'UTC' };

/**
 * 'Prüfung & Aktualität' on the document view (/reviews/[id], reviews.tsx):
 * when this version is next due for review, when and by whom it was last
 * reviewed, an overdue badge and -- for people who control the document --
 * setting the date and 'Als geprüft bestätigen'
 * (PUT/POST /api/v1/portal/documents/{id}/review[/confirm]). Also links to
 * the version comparison (/reviews/[id]/changes) when the caller sees more
 * than one FINISHED version of the document.
 *
 * A version superseded by a newer release is no longer reviewed (the
 * writers answer 409, `has_newer_release` in app/services/document_freshness.py):
 * then the actions and the overdue badge are hidden and a notice points to
 * the current version. Detected up front from the visible newer versions,
 * and from a 409 for newer versions the caller cannot see.
 */
export function DocumentReviewPanel({ jobId, review: initialReview, released, canManage }: {
  jobId: string;
  review: DocumentReview | null | undefined;
  /** Review dates only apply to released versions; unreleased ones show just the comparison link. */
  released: boolean;
  /** The caller controls the document (owner, space owner, admin); the backend decides finally (403). */
  canManage: boolean;
}) {
  const { t, locale, formatDate } = useI18n();
  const [review, setReview] = useState<DocumentReview | null>(initialReview ?? null);
  const [dueDate, setDueDate] = useState(() => dateInputValue(initialReview?.review_due_at));
  const [versions, setVersions] = useState<JobVersionEntry[]>([]);
  const [superseded, setSuperseded] = useState(false);
  const [busy, setBusy] = useState<'save' | 'clear' | 'confirm' | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  // Optional extra: an older backend or a protected document simply hides the comparison link.
  useEffect(() => {
    const controller = new AbortController();
    loadDocumentVersions(jobId, controller.signal).then(loaded => {
      setVersions(loaded);
      if (!released) return;
      void hasNewerRelease(loaded, jobId, controller.signal).then(value => { if (value && !controller.signal.aborted) setSuperseded(true); });
    }).catch(() => setVersions([]));
    return () => controller.abort();
  }, [jobId, released]);

  async function run(action: 'save' | 'clear' | 'confirm') {
    if (busy) return;
    setBusy(action); setError(''); setNotice('');
    try {
      const updated = action === 'confirm' ? await confirmDocumentReview(jobId)
        : await setDocumentReviewDue(jobId, action === 'clear' ? null : dueDate);
      setReview(updated);
      setDueDate(dateInputValue(updated.review_due_at));
      setNotice(action === 'confirm' ? t('portal.freshness.confirmedNotice') : action === 'clear' ? t('portal.freshness.clearedNotice') : t('portal.freshness.savedNotice'));
    } catch (err) {
      // The only 409 of these writers: a newer version has been released meanwhile.
      if (err instanceof ApiError && err.status === 409) setSuperseded(true);
      else setError(portalError(err, locale));
    } finally {
      setBusy(null);
    }
  }

  const link = compareLink(versions, jobId);
  const compareHref = link ? `/reviews/${encodeURIComponent(link.target)}/changes?${new URLSearchParams({ base: link.base })}` : null;
  // The newest version the caller sees, for the 'superseded' notice.
  const current = versions.find(version => version.job_id !== jobId && version.is_current);
  if (!released && !review && !compareHref) return null;
  const savedDate = dateInputValue(review?.review_due_at);

  return <section className="portal-freshness" aria-labelledby={`freshness-${jobId}`}>
    <h3 id={`freshness-${jobId}`}><CalendarCheck size={17} aria-hidden="true" />{t('portal.freshness.heading')}</h3>
    {(released || review) && <>
      <dl>
        <dt>{t('portal.freshness.dueLabel')}</dt>
        <dd>
          {review?.review_due_at ? formatDate(review.review_due_at, DUE_DATE_FORMAT) : t('portal.freshness.noDueDate')}
          {review?.overdue && !superseded && <> <span className="portal-badge portal-badge-error">{t('portal.freshness.overdue')}</span></>}
        </dd>
        <dt>{t('portal.freshness.lastReviewedLabel')}</dt>
        <dd>{review?.last_reviewed_at
          ? review.last_reviewed_by
            ? t('portal.freshness.lastReviewedBy', { date: formatDate(review.last_reviewed_at), name: review.last_reviewed_by })
            : formatDate(review.last_reviewed_at)
          : t('portal.freshness.neverReviewed')}</dd>
      </dl>
      {error && <Notice error>{error}</Notice>}
      {notice && <Notice>{notice}</Notice>}
      {superseded && <Notice>{t('portal.freshness.superseded')}{current && <> <Link href={`/reviews/${encodeURIComponent(current.job_id)}`}>{t('portal.freshness.openCurrent')}</Link></>}</Notice>}
      {canManage && released && !superseded && <div className="portal-freshness-actions">
        <label>{t('portal.freshness.dateLabel')}
          <input type="date" value={dueDate} disabled={Boolean(busy)} onChange={event => setDueDate(event.target.value)} />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" disabled={Boolean(busy) || !dueDate || dueDate === savedDate} onClick={() => void run('save')}>{busy === 'save' ? t('portal.freshness.saving') : t('portal.freshness.saveDate')}</Button>
          {savedDate && <Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={() => void run('clear')}>{t('portal.freshness.clearDate')}</Button>}
        </div>
        <Button className="w-full" disabled={Boolean(busy)} onClick={() => void run('confirm')}><CalendarCheck size={16} aria-hidden="true" />{busy === 'confirm' ? t('portal.freshness.confirming') : t('portal.freshness.confirm')}</Button>
        <p className="portal-field-hint">{t('portal.freshness.confirmHint')}</p>
      </div>}
    </>}
    {compareHref && <Link className={buttonVariants({ variant: 'outline', size: 'sm', className: 'w-full' })} href={compareHref}><GitCompare size={15} aria-hidden="true" />{t('portal.freshness.compareVersions')}</Link>}
  </section>;
}
