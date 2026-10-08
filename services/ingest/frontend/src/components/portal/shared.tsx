'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Download, FileText, RefreshCw } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { bulkPortalAction, dateLabel, documentState, documentUrl, portalError, type PortalDocument } from '@/lib/portal';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { useI18n } from '@/i18n/provider';

export function PortalPage({ title, description, eyebrow = null, actions, children }: {
  title: string; description?: string; eyebrow?: string | null; actions?: React.ReactNode; children: React.ReactNode;
}) {
  return <main id="main-content" className="portal-page"><header className="portal-header"><div>{eyebrow && <p className="portal-eyebrow">{eyebrow}</p>}<h1>{title}</h1>{description && <p className="portal-description">{description}</p>}</div>{actions && <div className="portal-actions">{actions}</div>}</header>{children}</main>;
}
export function Notice({ children, error = false, action }: { children: React.ReactNode; error?: boolean; action?: () => void }) {
  const { t } = useI18n();
  return <div className={`portal-notice ${error ? 'portal-notice-error' : ''}`} role={error ? 'alert' : 'status'}><span>{children}</span>{action && <Button variant="outline" size="sm" onClick={action}><RefreshCw size={14} />{t('portal.documents.notice.retry')}</Button>}</div>;
}
export function EmptyState({ title, children, href, action }: { title: string; children: React.ReactNode; href?: string; action?: string }) {
  return <div className="portal-empty"><FileText size={28} aria-hidden="true" /><h3>{title}</h3><p>{children}</p>{href && <Link className={buttonVariants({ variant: 'outline' })} href={href}>{action}<ArrowRight size={16} /></Link>}</div>;
}
export function DocumentTable({ documents, onDownloadMarkdown, downloadingId, selectedIds, onToggle, onToggleAll }: {
  documents: PortalDocument[];
  onDownloadMarkdown?: (document: PortalDocument) => void;
  downloadingId?: string | null;
  selectedIds?: Set<string>;
  onToggle?: (id: string) => void;
  onToggleAll?: (checked: boolean) => void;
}) {
  const { t, locale } = useI18n();
  const { items } = useIndexingStatus(documents.filter(document => document.release).map(document => document.id));
  const allSelected = Boolean(onToggle) && documents.length > 0 && documents.every(document => selectedIds?.has(document.id));
  return <div className="portal-table-scroll"><table className="portal-table"><caption className="sr-only">{t('portal.documents.caption')}</caption><thead><tr>{onToggle && <th scope="col"><input type="checkbox" aria-label={t('portal.documents.selectAllOnPage')} checked={allSelected} onChange={event => onToggleAll?.(event.target.checked)} /></th>}<th scope="col">{t('portal.documents.columnDocument')}</th><th scope="col">{t('portal.documents.columnSpace')}</th><th scope="col">{t('portal.documents.columnStatus')}</th><th scope="col">{t('portal.documents.columnAdded')}</th><th scope="col"><span className="sr-only">{t('common.open')}</span></th></tr></thead><tbody>{documents.map(document => {
    const state = documentState(document, items[document.id], locale);
    return <tr key={document.id}>{onToggle && <td><input type="checkbox" aria-label={t('portal.documents.selectRow', { filename: document.original_filename })} checked={selectedIds?.has(document.id) ?? false} onChange={() => onToggle(document.id)} /></td>}<td><Link className="portal-document-link" href={documentUrl(document)}><FileText size={17} aria-hidden="true" /><span>{document.original_filename}</span></Link><span className="mt-1 block max-w-[300px] text-xs leading-5 text-slate-500">{document.source?.label}{document.source?.path ? `: ${document.source.path}` : ''}</span></td><td><Link href={`/knowledge/${document.collection_id}`}>{document.collection_name || t('portal.documents.columnSpace')}</Link></td><td><div aria-live="polite"><span className={`portal-badge portal-badge-${state.tone}`}>{state.label}</span>{state.hint && <span className="mt-2 block max-w-[300px] text-xs leading-5 text-slate-500">{state.hint}</span>}{document.status === 'FINISHED' && !document.release && document.quality_grade && <span className="mt-2 block text-xs text-slate-500">{t('portal.documents.qualityGrade', { grade: document.quality_grade })}</span>}{document.release?.released_by && <span className="mt-2 block max-w-[300px] text-xs leading-5 text-slate-500">{t('portal.documents.releasedBy', { name: document.release.released_by })}</span>}</div></td><td className="portal-date">{dateLabel(document.created_at, locale)}</td><td><div className="portal-row-actions">{onDownloadMarkdown && document.status === 'FINISHED' && <button className="portal-open" type="button" disabled={downloadingId === document.id} onClick={() => onDownloadMarkdown(document)} aria-label={t('portal.documents.downloadMarkdownAria', { filename: document.original_filename })} title={t('portal.documents.downloadMarkdownTitle')}><Download size={17} aria-hidden="true" /></button>}<Link className="portal-open" href={documentUrl(document)} aria-label={t('portal.documents.openAria', { filename: document.original_filename })}><ArrowRight size={17} /></Link></div></td></tr>;
  })}</tbody></table></div>;
}

export function BulkActionBar({ count, onRelease, onPark, onSkip, onDelete, onClear, gradeCCount, acceptQualityWarnings, onAcceptQualityWarningsChange, releaseConfirmed, onReleaseConfirmedChange, busy }: {
  count: number;
  onRelease: () => void;
  onPark?: () => void;
  onSkip: () => void;
  onDelete: () => void;
  onClear: () => void;
  gradeCCount: number;
  acceptQualityWarnings: boolean;
  onAcceptQualityWarningsChange: (value: boolean) => void;
  releaseConfirmed: boolean;
  onReleaseConfirmedChange: (value: boolean) => void;
  busy: boolean;
}) {
  const { t } = useI18n();
  if (count === 0) return null;
  return <div className="portal-bulk-bar" role="toolbar" aria-label={t('portal.documents.bulk.ariaLabel')}>
    <span>{t('portal.documents.bulk.selectedCount', { count })}</span>
    <label className="portal-choice portal-approval"><input type="checkbox" checked={releaseConfirmed} onChange={event => onReleaseConfirmedChange(event.target.checked)} />{t('portal.documents.bulk.confirmReview')}</label>
    {gradeCCount > 0 && <label className="portal-choice"><input type="checkbox" checked={acceptQualityWarnings} onChange={event => onAcceptQualityWarningsChange(event.target.checked)} />{t('portal.documents.bulk.acceptQualityC', { count: gradeCCount })}</label>}
    <Button variant="outline" size="sm" disabled={busy || !releaseConfirmed} onClick={onRelease}>{t('portal.documents.bulk.release')}</Button>
    {onPark && <Button variant="outline" size="sm" disabled={busy} onClick={onPark}>{t('portal.documents.bulk.park')}</Button>}
    <Button variant="outline" size="sm" disabled={busy} onClick={onSkip}>{t('portal.documents.bulk.skip')}</Button>
    <Button variant="outline" size="sm" disabled={busy} onClick={onDelete}>{t('common.delete')}</Button>
    <Button variant="ghost" size="sm" disabled={busy} onClick={onClear}>{t('portal.documents.bulk.clearSelection')}</Button>
  </div>;
}
/**
 * Selection + bulk-action state shared by the pages that show a BulkActionBar.
 * `documents` is what the selection is resolved against (grade-C count, and the ids/count
 * unless `acrossPages`: then every selected id counts, even one that is no longer listed --
 * the pages that keep their selection while paging). `onDone` reloads after a batch.
 * Spread `barProps` into <BulkActionBar>; `withdrawReleased` only matters for the delete dialog.
 */
export function useBulkSelection({ documents, acrossPages = false, onNotice, onError, onDone }: {
  documents: PortalDocument[];
  acrossPages?: boolean;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onDone: () => void | Promise<unknown>;
}) {
  const { t, locale } = useI18n();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [acceptQualityWarnings, setAcceptQualityWarnings] = useState(false);
  const [releaseConfirmed, setReleaseConfirmed] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [withdrawReleased, setWithdrawReleased] = useState(false);
  const selectedDocuments = documents.filter(document => selectedIds.has(document.id));
  const ids = acrossPages ? [...selectedIds] : selectedDocuments.map(document => document.id);
  const gradeCCount = selectedDocuments.filter(document => document.quality_grade?.toUpperCase() === 'C').length;
  function clear() { setSelectedIds(new Set()); setAcceptQualityWarnings(false); setReleaseConfirmed(false); }
  function toggle(id: string) { setSelectedIds(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  function deselect(id: string) { setSelectedIds(previous => { const next = new Set(previous); next.delete(id); return next; }); }
  function selectAll(allIds: string[], checked: boolean) { setSelectedIds(checked ? new Set(allIds) : new Set()); }
  function closeDelete() { setBulkDeleting(false); setWithdrawReleased(false); }
  async function runBulk(action: 'release' | 'park' | 'skip' | 'delete') {
    if (bulkBusy || ids.length === 0) return;
    setBulkBusy(true); onNotice('');
    try {
      const result = await bulkPortalAction(ids, action, acceptQualityWarnings, action === 'delete' && withdrawReleased);
      onNotice(`${t('portal.spaces.bulkDoneNotice', { count: result.done })}${result.errors.length ? t('portal.spaces.bulkErrorSuffix', { count: result.errors.length }) : ''}.`);
      clear();
      await onDone();
    } catch (err) { onError(portalError(err, locale)); } finally { setBulkBusy(false); closeDelete(); }
  }
  return {
    selectedIds, count: ids.length, toggle, deselect, selectAll, clear,
    bulkDeleting, closeDelete, runBulk, withdrawReleased, setWithdrawReleased,
    barProps: {
      count: ids.length, gradeCCount, acceptQualityWarnings, onAcceptQualityWarningsChange: setAcceptQualityWarnings,
      releaseConfirmed, onReleaseConfirmedChange: setReleaseConfirmed, busy: bulkBusy,
      onRelease: () => void runBulk('release'), onPark: () => void runBulk('park'), onSkip: () => void runBulk('skip'),
      onDelete: () => setBulkDeleting(true), onClear: clear,
    },
  };
}
export function Pagination({ offset, total, onChange, pageSize = 20 }: { offset: number; total: number; onChange: (offset: number) => void; pageSize?: number }) {
  const { t } = useI18n();
  if (total <= pageSize) return null;
  return <nav className="portal-pagination" aria-label={t('portal.documents.pagination.ariaLabel')}><span>{t('portal.documents.pagination.range', { from: offset + 1, to: Math.min(offset + pageSize, total), total })}</span><Button variant="outline" size="sm" disabled={!offset} onClick={() => onChange(Math.max(0, offset - pageSize))}>{t('common.back')}</Button><Button variant="outline" size="sm" disabled={offset + pageSize >= total} onClick={() => onChange(offset + pageSize)}>{t('common.next')}</Button></nav>;
}

/** Visual marker for a required field; the control's own `required` attribute is what assistive technology announces. */
export function RequiredMark() {
  const { t } = useI18n();
  return <span className="portal-required" aria-hidden="true" title={t('common.required')}>*</span>;
}

export function QualityGradeFilterRow({ value, onChange }: { value: '' | 'A' | 'B' | 'C' | 'none'; onChange: (value: '' | 'A' | 'B' | 'C' | 'none') => void }) {
  const { t } = useI18n();
  const qualityGradeChips: Array<{ value: '' | 'A' | 'B' | 'C' | 'none'; label: string }> = [
    { value: '', label: t('common.all') },
    { value: 'A', label: 'A' },
    { value: 'B', label: 'B' },
    { value: 'C', label: 'C' },
    { value: 'none', label: t('portal.documents.noGrade') },
  ];
  return <div className="portal-filter-row" role="group" aria-label={t('portal.documents.filterByGrade.ariaLabel')}>{qualityGradeChips.map(chip => <button key={chip.value || 'all'} type="button" aria-pressed={value === chip.value} onClick={() => onChange(chip.value)}>{chip.label}</button>)}</div>;
}

// Plain-language explanation of the automatic quality grades, reused next to
// the grade filter and inside a document's own "Warum Stufe X?" block.
export function QualityGradeLegend() {
  const { t } = useI18n();
  return <details className="portal-quality-legend"><summary>{t('portal.documents.qualityLegend.summary')}</summary>
    <dl>
      <dt>{t('portal.documents.grade', { grade: 'A' })}</dt><dd>{t('portal.documents.qualityLegend.a')}</dd>
      <dt>{t('portal.documents.grade', { grade: 'B' })}</dt><dd>{t('portal.documents.qualityLegend.b')}</dd>
      <dt>{t('portal.documents.grade', { grade: 'C' })}</dt><dd>{t('portal.documents.qualityLegend.c')}</dd>
      <dt>{t('portal.documents.noGrade')}</dt><dd>{t('portal.documents.qualityLegend.none')}</dd>
    </dl>
  </details>;
}
