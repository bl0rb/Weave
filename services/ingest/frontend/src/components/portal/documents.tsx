'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { AlertTriangle, ArrowRight, CheckCheck, Info, Trash2 } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/admin/admin-shared';
import { spaceColorVar } from '@/lib/space-color';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import {
  bulkPortalAction,
  jsonBody,
  pipelineSteps,
  dateLabel,
  documentState,
  documentUrl,
  loadDocuments,
  pipelineStage,
  portalError,
  summarizePipeline,
  type KnowledgeSpace,
  type PipelineStage,
  type PortalDocument,
} from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { BulkActionBar, EmptyState, Notice, Pagination, PortalPage, useBulkSelection } from './shared';

/** Bounds the whole filterable/searchable list to the most recent N documents visible to the user — the backend has no full-text search for portal documents yet, so filtering happens client-side over this batch. */
const DOCUMENTS_FETCH_LIMIT = 200;
/** 0 = all filtered documents on one page. */
const PAGE_SIZES = [20, 50, 100, 0];
const STEP_COLOR: Record<Exclude<PipelineStage, 'error' | 'decided'>, string> = {
  processing: 'var(--proc)',
  review: 'var(--warn)',
  indexing: 'var(--idx)',
  ready: 'var(--ok)',
};

function cssVar(color: string): CSSProperties {
  return { '--c': color } as CSSProperties;
}

function fileTypeLabel(document: PortalDocument): string {
  if (document.source?.kind === 'confluence') return 'WIKI';
  if (document.source?.kind === 'mail') return 'MAIL';
  const name = document.original_filename;
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toUpperCase().slice(0, 4) : 'DOC';
}

function actionFor(document: PortalDocument, stage: PipelineStage, t: (key: MessageKey) => string) {
  switch (stage) {
    case 'review': case 'decided': return <Link className={buttonVariants({ size: 'sm' })} href={`/reviews/${document.id}`}>{t('portal.tasks.reviewAction')}</Link>;
    case 'ready': return <Link className={buttonVariants({ size: 'sm', variant: 'outline' })} href={documentUrl(document)}>{t('common.open')}</Link>;
    case 'error': return <Link className={buttonVariants({ size: 'sm', variant: 'outline' })} href={`/jobs/${document.id}`}>{t('common.retry')}</Link>;
    default: return <Link className={buttonVariants({ size: 'sm', variant: 'ghost' })} href={`/jobs/${document.id}`}>{t('portal.documents.statusAction')}</Link>;
  }
}

const isPipelineStage = (value: string): value is PipelineStage => ['processing', 'review', 'indexing', 'ready', 'error'].includes(value); // not 'decided': it has no filter chip

export function PortalDocuments({ initialQuery, initialStand, initialBereich }: { initialQuery?: string; initialStand?: string; initialBereich?: string }) {
  const pathname = usePathname();
  const { t, locale } = useI18n();
  const [spaces, setSpaces] = useState<KnowledgeSpace[] | null>(null);
  const [documents, setDocuments] = useState<PortalDocument[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const [query, setQuery] = useState(initialQuery ?? '');
  const [stand, setStand] = useState<PipelineStage | ''>(initialStand && isPipelineStage(initialStand) ? initialStand : '');
  const [bereichSlug, setBereichSlug] = useState(initialBereich ?? '');
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState('');
  const [confirmReleaseAll, setConfirmReleaseAll] = useState(false);
  const [releasingAll, setReleasingAll] = useState(false);
  const [pageSize, setPageSize] = useState(20);
  const [rowDeleting, setRowDeleting] = useState<PortalDocument | null>(null);

  useEffect(() => { apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections').then((page) => setSpaces(page.items)).catch(() => setSpaces([])); }, []);

  const selectedSpace = useMemo(() => spaces?.find((space) => space.slug === bereichSlug), [spaces, bereichSlug]);
  const collectionId = selectedSpace?.collection_id;

  // Only the newest request may write state, so a slower earlier response
  // (e.g. unfiltered vs. filtered) can never overwrite a newer one.
  const requestSeq = useRef(0);
  const load = useCallback(() => {
    // A ?bereich= slug can only be resolved once the spaces are loaded;
    // fetching before that would briefly show every space's documents.
    if (bereichSlug && spaces === null) return;
    const seq = ++requestSeq.current;
    loadDocuments(collectionId, 0, 'all', undefined, DOCUMENTS_FETCH_LIMIT)
      .then((page) => { if (seq === requestSeq.current) { setDocuments(page.items); setTotal(page.total); setError(''); } })
      .catch((err) => { if (seq === requestSeq.current) setError(portalError(err, locale)); });
  }, [bereichSlug, spaces, collectionId, locale]);
  useEffect(() => { void load(); }, [load]);

  // Reflect the current filters in the URL (shareable) without a server
  // round trip: history.replaceState keeps the page's searchParams props
  // unchanged, so only an external navigation (e.g. the topbar search)
  // remounts this component via the key in app/documents/page.tsx.
  useEffect(() => {
    const params = new URLSearchParams();
    if (query.trim()) params.set('q', query.trim());
    if (stand) params.set('stand', stand);
    if (bereichSlug) params.set('bereich', bereichSlug);
    const search = params.toString();
    window.history.replaceState(null, '', search ? `${pathname}?${search}` : pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, stand, bereichSlug]);

  // Filter changes reset pagination inline (in the setters below) rather than via a separate effect.
  function updateQuery(value: string) { setQuery(value); setOffset(0); bulk.clear(); }
  function updateStand(value: PipelineStage | '') { setStand(value); setOffset(0); bulk.clear(); }
  function updateBereich(value: string) { setBereichSlug(value); setOffset(0); bulk.clear(); setConfirmReleaseAll(false); }
  // The selection spans pages ("alle auswählen" selects every filtered document), so paging keeps it.
  function updateOffset(value: number) { setOffset(value); }
  function updatePageSize(value: number) { setPageSize(value); setOffset(0); }

  const releasedIds = useMemo(() => (documents ?? []).filter((document) => document.release).map((document) => document.id), [documents]);
  const { items: live } = useIndexingStatus(releasedIds);
  const stageOf = useCallback((document: PortalDocument) => pipelineStage(document, live[document.id]), [live]);
  const counts = useMemo(() => summarizePipeline(documents ?? [], live), [documents, live]);
  const steps = useMemo(() => pipelineSteps(locale), [locale]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('de');
    return (documents ?? []).filter((document) => {
      if (stand && stageOf(document) !== stand) return false;
      if (needle && !document.original_filename.toLocaleLowerCase('de').includes(needle)) return false;
      return true;
    });
  }, [documents, stand, query, stageOf]);
  const pageItems = pageSize ? filtered.slice(offset, offset + pageSize) : filtered;

  // Only what still matches the filter counts: a live status change can move a selected document out of it.
  const bulk = useBulkSelection({ documents: filtered, onNotice: setNotice, onError: setError, onDone: () => { setDocuments(null); load(); } });
  const allSelected = filtered.length > 0 && filtered.every((document) => bulk.selectedIds.has(document.id));
  async function deleteRow() {
    if (!rowDeleting) return;
    // A released document is withdrawn from the knowledge index first (the dialog says so).
    const result = await bulkPortalAction([rowDeleting.id], 'delete', false, Boolean(rowDeleting.release));
    if (result.errors.length) throw new Error(result.errors[0].reason);
    setRowDeleting(null);
    setNotice(t('portal.spaces.bulkDoneNotice', { count: result.done }));
    bulk.deselect(rowDeleting.id);
    load();
  }
  // Same endpoint and confirm flow as the knowledge space detail page ("Dokumente freigeben").
  async function releaseAll() {
    if (!collectionId || !confirmReleaseAll || releasingAll) return;
    setReleasingAll(true); setError(''); setNotice('');
    try {
      const result = await apiJson<{ released: number; skipped: number }>(`/api/v1/portal/collections/${encodeURIComponent(collectionId)}/release-all`, jsonBody({ accept_quality_warnings: true }));
      setNotice(`${t('portal.spaces.releasedNotice', { count: result.released })}${result.skipped ? t('portal.spaces.releasedSkippedSuffix', { count: result.skipped }) : ''}.`);
      setConfirmReleaseAll(false);
      bulk.clear();
      load();
    } catch (err) { setError(portalError(err, locale)); }
    finally { setReleasingAll(false); }
  }
  const canReleaseAll = Boolean(selectedSpace && (selectedSpace.can_upload ?? selectedSpace.can_manage) && documents?.length);
  // Reached from the navigation ("Alle Bereiche"): show the action, but it needs one space to act on.
  const releaseAllNeedsSpace = !selectedSpace && Boolean(spaces?.some((space) => space.can_upload ?? space.can_manage) && documents?.length);

  return (
    <PortalPage
      title={t('portal.nav.documents')}
      actions={<div className="flex flex-wrap items-center gap-4">
        <Link className="portal-inline-link" href="/processing">{t('portal.chrome.breadcrumb.processing')} <ArrowRight size={14} aria-hidden="true" /></Link>
        <Link className="portal-inline-link" href="/imports">{t('portal.documents.importsLink')} <ArrowRight size={14} aria-hidden="true" /></Link>
      </div>}
    >
      {error && <Notice error action={load}>{error}</Notice>}
      {notice && <Notice>{notice}</Notice>}

      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-[200px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
          {t('portal.documents.columnSpace')}
          <select value={bereichSlug} onChange={(event) => updateBereich(event.target.value)}>
            <option value="">{t('portal.documents.allSpaces')}</option>
            {spaces?.map((space) => <option key={space.collection_id} value={space.slug}>{space.name}</option>)}
          </select>
        </label>
        <label className="min-w-[220px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
          {t('portal.documents.searchLabel')}
          <input type="search" value={query} onChange={(event) => updateQuery(event.target.value)} placeholder={t('portal.documents.filenamePlaceholder')} aria-label={t('portal.chrome.searchDocuments')} />
        </label>
        <label className="text-sm font-semibold text-[var(--ink-2)]">
          {t('portal.documents.pageSize')}
          <select value={pageSize} onChange={(event) => updatePageSize(Number(event.target.value))}>
            {PAGE_SIZES.map((size) => <option key={size} value={size}>{size || t('common.all')}</option>)}
          </select>
        </label>
      </div>
      {documents && total > documents.length && <p className="text-xs text-[var(--muted)]">{t('portal.documents.truncatedHint', { count: documents.length, total })}</p>}

      <section className="portal-panel" aria-labelledby="docs-title">
        <h2 className="sr-only" id="docs-title">{t('portal.documents.listHeading')}</h2>
        <div className="portal-pipeline" role="group" aria-label={t('portal.documents.filterByStageAria')}>
          <button type="button" className="portal-pipe-all" aria-pressed={stand === ''} onClick={() => updateStand('')}>{t('common.all')}</button>
          <ol className="portal-pipe-steps">
            {steps.map((step) => (
              <li key={step.value}>
                <button
                  type="button"
                  className={step.value === 'review' ? 'portal-pipe-human' : ''}
                  style={cssVar(STEP_COLOR[step.value])}
                  aria-pressed={stand === step.value}
                  onClick={() => updateStand(step.value)}
                >
                  <span className="portal-pipe-n">{step.step}</span>
                  <span className="portal-pipe-label">{step.label}<small>{step.hint}</small></span>
                  <b>{documents === null ? '–' : counts[step.value]}</b>
                </button>
              </li>
            ))}
          </ol>
          <button type="button" className="portal-pipe-error" aria-pressed={stand === 'error'} onClick={() => updateStand('error')}>
            <AlertTriangle size={16} aria-hidden="true" />{t('common.error')}<b>{documents === null ? '–' : counts.error}</b>
          </button>
        </div>
        <p className="portal-pipe-note"><Info size={16} aria-hidden="true" /><span>{t('portal.documents.pipelineNote.part1')} <em>{t('common.and')}</em> {t('portal.documents.pipelineNote.part2')}</span></p>

        {(canReleaseAll || releaseAllNeedsSpace) && <div className="portal-release-all">
          {canReleaseAll && confirmReleaseAll ? <>
            <Button variant="outline" disabled={releasingAll} onClick={() => setConfirmReleaseAll(false)}>{t('common.cancel')}</Button>
            <Button variant="danger" disabled={releasingAll} onClick={() => void releaseAll()}><CheckCheck size={15} />{releasingAll ? t('portal.spaces.releasingAll') : t('portal.spaces.confirmReleaseAll')}</Button>
          </> : <Button variant="outline" disabled={!canReleaseAll} aria-describedby={releaseAllNeedsSpace ? 'release-all-hint' : undefined} onClick={() => setConfirmReleaseAll(true)}><CheckCheck size={15} />{t('portal.spaces.releaseCollection')}</Button>}
          {releaseAllNeedsSpace && <span id="release-all-hint" className="text-xs text-[var(--muted)]">{t('portal.spaces.releaseAllNeedsSpace')}</span>}
        </div>}

        <BulkActionBar {...bulk.barProps} />

        {documents === null && !error ? <p role="status" className="portal-loading">{t('portal.tasks.documentsLoading')}</p> : pageItems.length ? (
          <>
            <div className="portal-table-scroll">
              <table className="portal-table portal-doc-table">
                <caption className="sr-only">{t('portal.documents.tableCaption')}</caption>
                <thead><tr><th scope="col"><div className="flex items-center gap-[10px]"><input type="checkbox" aria-label={t('portal.documents.selectAllFiltered', { count: filtered.length })} checked={allSelected} onChange={(event) => bulk.selectAll(filtered.map((document) => document.id), event.target.checked)} />{t('portal.documents.columnDocument')}</div></th><th scope="col">{t('portal.documents.columnSpace')}</th><th scope="col">{t('portal.documents.columnStatus')}</th><th scope="col">{t('portal.documents.columnLast')}</th><th scope="col"><span className="sr-only">{t('portal.documents.columnAction')}</span></th></tr></thead>
                <tbody>
                  {pageItems.map((document) => {
                    const stage = stageOf(document);
                    const state = documentState(document, live[document.id], locale);
                    return (
                      <tr key={document.id}>
                        <td><div className="portal-document-link"><input type="checkbox" aria-label={t('portal.documents.selectRow', { filename: document.original_filename })} checked={bulk.selectedIds.has(document.id)} onChange={() => bulk.toggle(document.id)} /><span className="portal-filetype">{fileTypeLabel(document)}</span><span>{document.original_filename}{document.source?.label && <small className="block text-xs text-[var(--muted)]">{document.source.label}{document.source.path ? `: ${document.source.path}` : ''}</small>}</span></div></td>
                        <td><span className="portal-space-tag" style={cssVar(spaceColorVar(document.collection_id))}>{document.collection_name}</span></td>
                        <td><span aria-live="polite" className={`portal-badge portal-badge-${state.tone}`}>{state.label}</span>{state.hint && <span className="mt-1 block max-w-[280px] text-xs leading-5 text-[var(--muted)]">{state.hint}</span>}</td>
                        <td className="portal-date">{dateLabel(document.release?.created_at ?? document.created_at, locale)}</td>
                        <td><div className="portal-row-actions">{actionFor(document, stage, t)}<button type="button" className="portal-open" onClick={() => setRowDeleting(document)} aria-label={t('portal.documents.deleteAria', { filename: document.original_filename })} title={t('common.delete')}><Trash2 size={16} aria-hidden="true" /></button></div></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {pageSize > 0 && <Pagination offset={offset} total={filtered.length} pageSize={pageSize} onChange={updateOffset} />}
          </>
        ) : (
          <EmptyState title={query || stand ? t('portal.documents.noMatchTitle') : t('portal.documents.emptyTitle')}>
            {query || stand ? t('portal.documents.noMatchBody') : t('portal.documents.emptyBody')}
          </EmptyState>
        )}
      </section>
      {rowDeleting && <ConfirmDialog title={t('portal.spaces.deleteDocumentsTitle')} body={<><p>{t('portal.spaces.deleteDocumentsBody', { count: 1 })} <strong>{rowDeleting.original_filename}</strong></p>{rowDeleting.release && <p className="mt-3 text-sm">{t('portal.spaces.withdrawReleased')}</p>}</>} confirmLabel={t('common.delete')} onClose={() => setRowDeleting(null)} onConfirm={deleteRow} />}
      {bulk.bulkDeleting && <ConfirmDialog title={t('portal.spaces.deleteDocumentsTitle')} body={<><p>{t('portal.spaces.deleteDocumentsBody', { count: bulk.count })}</p><label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={bulk.withdrawReleased} onChange={(event) => bulk.setWithdrawReleased(event.target.checked)} />{t('portal.spaces.withdrawReleased')}</label></>} confirmLabel={t('portal.spaces.deleteDocumentsTitle')} onClose={bulk.closeDelete} onConfirm={() => bulk.runBulk('delete')} />}
    </PortalPage>
  );
}
