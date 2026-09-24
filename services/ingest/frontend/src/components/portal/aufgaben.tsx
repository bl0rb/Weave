'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, CheckCheck, Clock3, FileText, RefreshCw } from 'lucide-react';
import { useAuth } from '@/lib/auth-context';
import { buttonVariants } from '@/components/ui/button';
import { loadDocuments, portalError, type PortalDocument } from '@/lib/portal';
import { loadFailedJobs, type FailedJob } from '@/lib/jobs-search';
import { Notice, PortalPage } from './shared';

/** How many "released by me" candidates to scan for a match — see the component doc comment below. */
const RELEASED_BY_ME_SCAN_LIMIT = 100;
const RELEASED_BY_ME_SHOWN = 5;

type State = {
  review: PortalDocument[] | null;
  failed: FailedJob[] | null;
  releasedByMe: PortalDocument[] | null;
};

/**
 * /aufgaben — everything that needs the current person's attention before
 * their team can use the knowledge in chat: documents waiting for review,
 * jobs/imports that failed, and (as a light positive-feedback loop) what
 * they released recently.
 *
 * "Von dir freigegeben" is derived from `release.released_by` (a plain
 * username string the portal API already returns — see Publication in
 * lib/portal.ts) matched against the signed-in user; it is scanned across
 * the most recent {@link RELEASED_BY_ME_SCAN_LIMIT} documents visible to
 * the user rather than the full history, to keep this page to one bounded
 * request per section.
 */
export function Aufgaben() {
  const { user } = useAuth();
  const [state, setState] = useState<State>({ review: null, failed: null, releasedByMe: null });
  const [error, setError] = useState('');

  const load = useCallback(() => Promise.all([
    loadDocuments(undefined, 0, 'review', undefined, 20),
    loadFailedJobs(20),
    loadDocuments(undefined, 0, 'all', undefined, RELEASED_BY_ME_SCAN_LIMIT),
  ]).then(([review, failed, all]) => {
    const releasedByMe = all.items
      .filter((document) => document.release && user && document.release.released_by === user.username)
      .sort((a, b) => new Date(b.release!.created_at).getTime() - new Date(a.release!.created_at).getTime())
      .slice(0, RELEASED_BY_ME_SHOWN);
    setState({ review: review.items, failed: failed.items, releasedByMe });
    setError('');
  }).catch((err) => setError(portalError(err))), [user]);

  useEffect(() => { void load(); }, [load]);

  return (
    <PortalPage title="Aufgaben" description="Was auf dich wartet, bevor dein Team das Wissen im Chat nutzen kann." actions={<button type="button" onClick={() => void load()} className={buttonVariants({ variant: 'outline' })}><RefreshCw size={15} aria-hidden="true" />Aktualisieren</button>}>
      {error && <Notice error action={load}>{error}</Notice>}

      <section className="portal-panel portal-tasks-card" aria-labelledby="review-title">
        <header className="portal-section-heading">
          <h2 id="review-title">Zu prüfen</h2>
          {Boolean(state.review?.length) && <span className="portal-pill">{state.review!.length}</span>}
          <span className="ml-auto text-xs text-[var(--muted)]">Du gibst genau den geprüften Stand frei.</span>
        </header>
        {state.review === null ? (
          <p role="status" className="portal-loading">Dokumente werden geladen …</p>
        ) : state.review.length ? (
          <ul className="portal-task-list" aria-live="polite">
            {state.review.map((document) => (
              <li className="portal-task" key={document.id}>
                <span className="portal-task-icon"><FileText size={18} aria-hidden="true" /></span>
                <span className="portal-task-text">
                  <strong>{document.original_filename}</strong>
                  <small>{document.collection_name}{document.quality_grade ? ` · Qualitätsstufe ${document.quality_grade}` : ''}</small>
                </span>
                <Link className={buttonVariants({ size: 'sm' })} href={`/reviews/${document.id}`}>Prüfen</Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="portal-task-empty"><CheckCheck size={26} aria-hidden="true" /><span>Nichts zu prüfen. Neue Dokumente erscheinen hier, sobald ihre Verarbeitung abgeschlossen ist.</span></div>
        )}
      </section>

      <section className="portal-panel" aria-labelledby="error-title">
        <header className="portal-section-heading">
          <h2 id="error-title">Braucht Hilfe</h2>
          {Boolean(state.failed?.length) && <span className="portal-pill portal-pill-err">{state.failed!.length}</span>}
          <span className="ml-auto text-xs text-[var(--muted)]">Importe, die nicht verarbeitet werden konnten.</span>
        </header>
        {state.failed === null ? (
          <p role="status" className="portal-loading">Aufträge werden geladen …</p>
        ) : state.failed.length ? (
          <ul className="portal-task-list">
            {state.failed.map((job) => (
              <li className="portal-task" key={job.id}>
                <span className="portal-task-icon portal-task-icon-err"><AlertTriangle size={18} aria-hidden="true" /></span>
                <span className="portal-task-text">
                  <strong>{job.original_filename}</strong>
                  <small>{job.error_message || 'Verarbeitung fehlgeschlagen'}</small>
                </span>
                <Link className={buttonVariants({ size: 'sm', variant: 'outline' })} href={`/jobs/${job.id}`}>Auftrag ansehen</Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="portal-task-empty"><CheckCheck size={26} aria-hidden="true" /><span>Keine Fehler – alle Importe laufen.</span></div>
        )}
      </section>

      <section className="portal-panel" aria-labelledby="done-title">
        <header className="portal-section-heading">
          <h2 id="done-title">Von dir freigegeben</h2>
        </header>
        {state.releasedByMe === null ? (
          <p role="status" className="portal-loading">Wird geladen …</p>
        ) : state.releasedByMe.length ? (
          <ul className="portal-task-list">
            {state.releasedByMe.map((document) => (
              <li className="portal-task" key={document.id}>
                <span className="portal-task-icon portal-task-icon-ok"><CheckCheck size={18} aria-hidden="true" /></span>
                <span className="portal-task-text">
                  <strong>{document.original_filename}</strong>
                  <small>{document.collection_name}</small>
                </span>
                <Link className={buttonVariants({ size: 'sm', variant: 'ghost' })} href={`/reviews/${document.id}`}>Ansehen</Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="portal-task-empty"><Clock3 size={26} aria-hidden="true" /><span>Du hast noch nichts freigegeben.</span></div>
        )}
      </section>
    </PortalPage>
  );
}
