'use client';

import { Fragment, useEffect, useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, GitCompare } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ApiError, type JobVersionEntry } from '@/lib/api';
import { comparableVersions, defaultBaseVersion, loadDocumentDiff, loadDocumentVersions, type DocumentDiff, type DocumentDiffHunk, type DocumentDiffSection } from '@/lib/document-freshness';
import { portalError } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { BackLink } from '@/components/back-link';
import { EmptyState, Notice, PortalPage } from './shared';

type T = (key: MessageKey, vars?: Record<string, string | number>) => string;

const CHANGE_LABEL: Record<DocumentDiffSection['change'], MessageKey> = {
  added: 'portal.diff.change.added',
  removed: 'portal.diff.change.removed',
  changed: 'portal.diff.change.changed',
};
const CHANGE_TONE: Record<DocumentDiffSection['change'], string> = { added: 'success', removed: 'error', changed: 'warning' };

export const changesHref = (targetId: string, baseId?: string | null) =>
  `/reviews/${encodeURIComponent(targetId)}/changes${baseId ? `?${new URLSearchParams({ base: baseId })}` : ''}`;

/**
 * 'Versionen vergleichen' (/reviews/[id]/changes?base=): the section-aware
 * comparison of two versions of one document (GET
 * /api/v1/portal/documents/{id}/diff, app/services/document_diff.py) --
 * German summary lines, then every changed section with its hunks. The two
 * selectors offer the caller's visible FINISHED versions (GET
 * /api/v1/jobs/{id}/versions), the older one only versions before the newer
 * one; the choice applies with 'Vergleichen' (no navigation while an option
 * is merely moved to, WCAG 3.2.2). Without `base` the next older visible
 * version is compared; an inverted pair in the URL is swapped.
 */
export function DocumentChanges({ id, base }: { id: string; base?: string | null }) {
  const { t, locale, formatDate } = useI18n();
  const router = useRouter();
  // null = not loaded yet; [] = unavailable (the diff then uses the backend's default base).
  const [versions, setVersions] = useState<JobVersionEntry[] | null>(null);
  const [diff, setDiff] = useState<DocumentDiff | null>(null);
  const [error, setError] = useState('');
  const [noPrevious, setNoPrevious] = useState(false);
  // null = untouched: the loaded pair applies.
  const [pickedBase, setPickedBase] = useState<string | null>(null);
  const [pickedTarget, setPickedTarget] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadDocumentVersions(id, controller.signal).then(setVersions).catch(() => { if (!controller.signal.aborted) setVersions([]); });
    return () => controller.abort();
  }, [id]);

  const effectiveBase = base || (versions?.length ? defaultBaseVersion(versions, id)?.job_id ?? null : null);
  const waitingForVersions = !base && versions === null;
  // The target is the oldest visible version: nothing to compare it with.
  const oldest = !base && Boolean(versions?.length) && versions!.some(version => version.job_id === id) && !effectiveBase;
  const versionOf = (jobId: string | null) => (jobId ? versions?.find(version => version.job_id === jobId) : undefined);
  // A base newer than the target (an old link or a hand-made URL): compare the other way round.
  const baseEntry = versionOf(base ?? null);
  const targetEntry = versionOf(id);
  const inverted = Boolean(baseEntry && targetEntry && baseEntry.document_version > targetEntry.document_version);

  useEffect(() => {
    if (inverted && base) router.replace(changesHref(base, id));
  }, [inverted, base, id, router]);

  useEffect(() => {
    if (waitingForVersions || oldest || inverted) return;
    const controller = new AbortController();
    loadDocumentDiff(id, effectiveBase, controller.signal)
      .then(result => { setDiff(result); setError(''); setNoPrevious(false); })
      .catch(err => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 409) setNoPrevious(true);
        else setError(portalError(err, locale));
      });
    return () => controller.abort();
  }, [id, effectiveBase, waitingForVersions, oldest, inverted, locale]);

  const versionLabel = (version: JobVersionEntry) => t('portal.diff.versionOption', { version: version.document_version, date: formatDate(version.created_at) });
  const comparable = comparableVersions(versions ?? []);
  const showSelectors = comparable.length > 1;
  const targetValue = pickedTarget ?? id;
  const baseValue = pickedBase ?? effectiveBase ?? '';
  const pickedTargetEntry = versionOf(targetValue);
  // Only FINISHED versions have markdown to compare; the base is always the older one.
  const targetOptions = (versions ?? []).filter(version => version.status === 'FINISHED' || version.job_id === id);
  const baseOptions = comparable.filter(version => version.job_id !== targetValue
    && (!pickedTargetEntry || version.document_version < pickedTargetEntry.document_version));
  const unchanged = targetValue === id && baseValue === (effectiveBase ?? '');

  function pickTarget(jobId: string) {
    setPickedTarget(jobId);
    const entry = versionOf(jobId);
    // Keep the chosen base while it is still older than the new target, else the default for that target.
    const keep = baseValue && baseValue !== jobId && (!entry || (versionOf(baseValue)?.document_version ?? -1) < entry.document_version);
    if (!keep) setPickedBase(defaultBaseVersion(versions ?? [], jobId)?.job_id ?? '');
  }

  function compare(event: FormEvent) {
    event.preventDefault();
    if (!baseValue || unchanged) return;
    router.push(changesHref(targetValue, baseValue));
  }

  return <PortalPage title={t('portal.diff.title')} eyebrow={t('portal.diff.eyebrow')}
    description={diff && !inverted ? t('portal.diff.description', { base: diff.base.document_version, target: diff.target.document_version }) : t('portal.diff.descriptionFallback')}>
    <div className="portal-review-nav"><BackLink fallbackHref={`/reviews/${encodeURIComponent(id)}`} fallbackLabel={t('portal.diff.backLink')} className="portal-back" /></div>
    {showSelectors && <form className="portal-diff-selectors" role="group" aria-label={t('portal.diff.selectorAria')} onSubmit={compare}>
      <label>{t('portal.diff.baseLabel')}
        <select value={baseValue} onChange={event => setPickedBase(event.target.value)}>
          {!baseValue && <option value="">{t('portal.diff.choose')}</option>}
          {baseOptions.map(version => <option key={version.job_id} value={version.job_id}>{versionLabel(version)}</option>)}
        </select>
      </label>
      <ArrowRight size={16} aria-hidden="true" />
      <label>{t('portal.diff.targetLabel')}
        <select value={targetValue} onChange={event => pickTarget(event.target.value)}>
          {targetOptions.map(version => <option key={version.job_id} value={version.job_id}>{versionLabel(version)}</option>)}
        </select>
      </label>
      <Button type="submit" variant="outline" disabled={!baseValue || unchanged}><GitCompare size={15} aria-hidden="true" />{t('portal.diff.compare')}</Button>
    </form>}
    {error && <Notice error>{error}</Notice>}
    {(noPrevious || oldest) && <Notice>{t('portal.diff.noPrevious')}</Notice>}
    {!diff && !error && !noPrevious && !oldest && <Notice>{t('portal.diff.loading')}</Notice>}
    {diff && !inverted && <DiffView diff={diff} t={t} />}
  </PortalPage>;
}

function DiffView({ diff, t }: { diff: DocumentDiff; t: T }) {
  const { stats } = diff;
  return <>
    <section className="portal-panel portal-diff-overview" aria-labelledby="diff-summary-title">
      <div className="portal-section-heading"><h2 id="diff-summary-title">{t('portal.diff.summaryHeading')}</h2>
        <p className="portal-diff-stats">
          <span className="portal-pill portal-diff-pill-ins">{t('portal.diff.addedLines', { count: stats.added_lines })}</span>
          <span className="portal-pill portal-diff-pill-del">{t('portal.diff.removedLines', { count: stats.removed_lines })}</span>
          <span className="portal-pill">{t('portal.diff.sectionStats', { changed: stats.sections_changed, added: stats.sections_added, removed: stats.sections_removed })}</span>
        </p>
      </div>
      {diff.summary.length ? <ul className="portal-diff-summary">{diff.summary.map((line, index) => <li key={index}>{line}</li>)}</ul>
        : <p className="portal-diff-summary portal-field-hint">{t('portal.diff.noSummary')}</p>}
    </section>
    {diff.sections.length ? diff.sections.map((section, index) => <DiffSection key={`${section.heading_path.join('\u0000')}-${index}`} section={section} t={t} />)
      : <EmptyState title={t('portal.diff.identicalTitle')}>{t('portal.diff.identicalBody')}</EmptyState>}
  </>;
}

function DiffSection({ section, t }: { section: DocumentDiffSection; t: T }) {
  const heading = section.heading_path.filter(Boolean).join(' › ') || t('portal.diff.noHeading');
  return <section className="portal-panel portal-diff-section" aria-label={heading}>
    <header className="portal-section-heading">
      <h3>{heading}</h3>
      <span className="portal-diff-section-meta">
        <span className={`portal-badge portal-badge-${CHANGE_TONE[section.change] ?? 'neutral'}`}>{CHANGE_LABEL[section.change] ? t(CHANGE_LABEL[section.change]) : section.change}</span>
        <small>{t('portal.diff.sectionLines', { added: section.added_lines, removed: section.removed_lines })}</small>
      </span>
    </header>
    <div className="portal-diff-hunks">{section.hunks.map((hunk, index) => <DiffHunk key={index} hunk={hunk} t={t} />)}</div>
  </section>;
}

function DiffHunk({ hunk, t }: { hunk: DocumentDiffHunk; t: T }) {
  if (hunk.op === 'equal') return <>{hunk.target.map((line, index) => <DiffLine key={index} kind="equal" line={line} t={t} />)}</>;
  return <Fragment>
    {(hunk.op === 'delete' || hunk.op === 'replace') && hunk.base.map((line, index) => <DiffLine key={`d${index}`} kind="del" line={line} t={t} />)}
    {(hunk.op === 'insert' || hunk.op === 'replace') && hunk.target.map((line, index) => <DiffLine key={`i${index}`} kind="ins" line={line} t={t} />)}
  </Fragment>;
}

function DiffLine({ kind, line, t }: { kind: 'equal' | 'del' | 'ins'; line: string; t: T }) {
  if (kind === 'equal') return <div className="portal-diff-line"><span className="portal-diff-marker" aria-hidden="true"> </span><span>{line || ' '}</span></div>;
  const Tag = kind === 'del' ? 'del' : 'ins';
  return <div className={`portal-diff-line portal-diff-${kind}`}>
    <span className="portal-diff-marker" aria-hidden="true">{kind === 'del' ? '−' : '+'}</span>
    <Tag><span className="sr-only">{kind === 'del' ? t('portal.diff.removedSr') : t('portal.diff.addedSr')} </span>{line || ' '}</Tag>
  </div>;
}

