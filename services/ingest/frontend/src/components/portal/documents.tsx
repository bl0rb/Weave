'use client';

import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Trash2 } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { buttonVariants } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/admin/admin-shared';
import { spaceColorVar } from '@/lib/space-color';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import type { IndexingItem } from '@/lib/indexing-status';
import {
  bulkPortalAction,
  dateLabel,
  documentState,
  documentUrl,
  loadDocuments,
  pipelineStage,
  portalError,
  type DocumentSourceFilter,
  type KnowledgeSpace,
  type PipelineStage,
  type PortalDocument,
  type QualityGradeFilter,
  type ReviewStateFilter,
} from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { BulkActionBar, EmptyState, InfoTip, Notice, Pagination, PortalPage, useBulkSelection } from './shared';

/** Bounds the list to the most recent N documents matching the server-side filters (view, space, search, grade, source); only "Stand" is filtered client-side, because it depends on the live indexing status. */
const DOCUMENTS_FETCH_LIMIT = 200;
/** 0 = all filtered documents on one page. */
const PAGE_SIZES = [20, 50, 100, 0];

/** View presets at the top of the page; each maps to one server-side review_state. */
export type DocumentView = 'review' | 'all' | 'parked' | 'failed' | 'mine';
const VIEWS: DocumentView[] = ['review', 'all', 'parked', 'failed', 'mine'];
const VIEW_STATE: Record<DocumentView, ReviewStateFilter> = { review: 'review', all: 'all', parked: 'parked', failed: 'failed', mine: 'all' };
const VIEW_LABEL: Record<DocumentView, MessageKey> = {
  review: 'portal.home.tile.review.label',
  all: 'common.all',
  parked: 'portal.documents.state.parked',
  failed: 'common.error',
  mine: 'portal.documents.view.mine',
};
const VIEW_EMPTY: Record<DocumentView, MessageKey> = {
  review: 'portal.tasks.reviewEmpty',
  all: 'portal.documents.emptyBody',
  parked: 'portal.reviews.emptyParkedTitle',
  failed: 'portal.tasks.helpEmpty',
  mine: 'portal.tasks.releasedByMeEmpty',
};

/** "Stand" of one document: the pipeline stage, with parked and skipped told apart. */
type Stand = Exclude<PipelineStage, 'decided'> | 'parked' | 'skipped';
const STANDS: Stand[] = ['processing', 'review', 'indexing', 'ready', 'parked', 'skipped', 'error'];
const STAND_LABEL: Record<Stand, MessageKey> = {
  processing: 'portal.pipeline.processing.label',
  review: 'portal.pipeline.review.label',
  indexing: 'portal.pipeline.indexing.label',
  ready: 'portal.pipeline.ready.label',
  parked: 'portal.documents.state.parked',
  skipped: 'portal.documents.state.skipped',
  error: 'common.error',
};
const GRADES: QualityGradeFilter[] = ['', 'A', 'B', 'C', 'none'];
const SOURCES: DocumentSourceFilter[] = ['', 'upload', 'confluence', 'mail'];
const SOURCE_LABEL: Record<Exclude<DocumentSourceFilter, ''>, MessageKey> = {
  upload: 'portal.documents.source.upload',
  confluence: 'portal.documents.source.confluence',
  mail: 'portal.documents.source.mail',
};

const isView = (value?: string): value is DocumentView => VIEWS.includes(value as DocumentView);
const isStand = (value?: string): value is Stand => STANDS.includes(value as Stand);
const isGrade = (value?: string): value is QualityGradeFilter => GRADES.includes(value as QualityGradeFilter);
const isSource = (value?: string): value is DocumentSourceFilter => SOURCES.includes(value as DocumentSourceFilter);

function standOf(document: PortalDocument, live?: IndexingItem): Stand {
  const stage = pipelineStage(document, live);
  if (stage !== 'decided') return stage;
  return document.review_decision === 'skipped' ? 'skipped' : 'parked';
}

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

function actionFor(document: PortalDocument, stand: Stand, t: (key: MessageKey) => string) {
  switch (stand) {
    case 'review': case 'parked': case 'skipped': return <Link className={buttonVariants({ size: 'sm' })} href={`/reviews/${document.id}`}>{t('portal.tasks.reviewAction')}</Link>;
    case 'ready': case 'indexing': return <Link className={buttonVariants({ size: 'sm', variant: 'outline' })} href={documentUrl(document)}>{t('common.view')}</Link>;
    default: return null;
  }
}

export type DocumentsInitialFilters = { q?: string; stand?: string; bereich?: string; bewertung?: string; quelle?: string; ansicht?: string };

/**
 * /documents — every document the person can maintain, in one list:
 * view presets (Zu prüfen, Alle, Geparkt, Fehler, Von mir freigegeben),
 * one filter bar and the bulk actions. The filters live in the URL
 * (?ansicht=&bereich=&stand=&bewertung=&quelle=&q=), so links from the
 * Übersicht and the Wissensbereiche open the matching view.
 */
export function PortalDocuments({ initial = {} }: { initial?: DocumentsInitialFilters }) {
  const pathname = usePathname();
  const { t, locale } = useI18n();
  const [spaces, setSpaces] = useState<KnowledgeSpace[] | null>(null);
  const [documents, setDocuments] = useState<PortalDocument[] | null>(null);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<{ review: number; parked: number; failed: number } | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState(initial.q ?? '');
  const [debouncedQuery, setDebouncedQuery] = useState(initial.q ?? '');
  const [stand, setStand] = useState<Stand | ''>(isStand(initial.stand) ? initial.stand : '');
  const [bereichSlug, setBereichSlug] = useState(initial.bereich ?? '');
  const [grade, setGrade] = useState<QualityGradeFilter>(isGrade(initial.bewertung) ? initial.bewertung : '');
  const [source, setSource] = useState<DocumentSourceFilter>(isSource(initial.quelle) ? initial.quelle : '');
  // "Zu prüfen" is the default on a plain open; a link with filters (e.g. "N Dokumente ansehen") opens "Alle".
  const hasInitialFilters = Boolean(initial.q || initial.stand || initial.bereich || initial.bewertung || initial.quelle);
  const [view, setView] = useState<DocumentView>(isView(initial.ansicht) ? initial.ansicht : hasInitialFilters ? 'all' : 'review');
  // Falls back to "Alle" once when nothing waits for review.
  const autoView = useRef(!isView(initial.ansicht) && !hasInitialFilters);
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState('');
  const [pageSize, setPageSize] = useState(20);
  const [rowDeleting, setRowDeleting] = useState<PortalDocument | null>(null);
  const ids = { space: useId(), search: useId(), stand: useId(), grade: useId(), source: useId(), pageSize: useId() };

  useEffect(() => { apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections').then((page) => setSpaces(page.items)).catch(() => setSpaces([])); }, []);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 250);
    return () => clearTimeout(timer);
  }, [query]);

  const selectedSpace = useMemo(() => spaces?.find((space) => space.slug === bereichSlug || space.collection_id === bereichSlug), [spaces, bereichSlug]);
  const collectionId = selectedSpace?.collection_id;
  // A ?bereich= slug can only be resolved once the spaces are loaded;
  // fetching before that would briefly show every space's documents.
  const waitingForSpace = Boolean(bereichSlug) && spaces === null;

  // Only the newest request may write state, so a slower earlier response
  // (e.g. unfiltered vs. filtered) can never overwrite a newer one.
  const requestSeq = useRef(0);
  const load = useCallback(() => {
    if (waitingForSpace) return;
    const seq = ++requestSeq.current;
    const filters = { q: debouncedQuery, source };
    loadDocuments(collectionId, 0, VIEW_STATE[view], grade || undefined, DOCUMENTS_FETCH_LIMIT, undefined, { ...filters, releasedByMe: view === 'mine' })
      .then((page) => { if (seq === requestSeq.current) { setDocuments(page.items); setTotal(page.total); setError(''); } })
      .catch((err) => { if (seq === requestSeq.current) setError(portalError(err, locale)); });
    // The preset counts follow the same filters (only `total` is read).
    Promise.all((['review', 'parked', 'failed'] as const).map((state) => loadDocuments(collectionId, 0, state, grade || undefined, 1, undefined, filters)))
      .then(([review, parked, failed]) => {
        if (seq !== requestSeq.current) return;
        setCounts({ review: review.total, parked: parked.total, failed: failed.total });
        if (autoView.current) {
          autoView.current = false;
          if (review.total === 0) setView('all');
        }
      })
      .catch(() => { if (seq === requestSeq.current) setCounts(null); });
  }, [waitingForSpace, collectionId, view, grade, source, debouncedQuery, locale]);
  useEffect(() => { void load(); }, [load]);

  // Reflect the current filters in the URL (shareable) without a server
  // round trip: history.replaceState keeps the page's searchParams props
  // unchanged, so only an external navigation (e.g. the topbar search)
  // remounts this component via the key in app/documents/page.tsx.
  useEffect(() => {
    const params = new URLSearchParams();
    if (bereichSlug) params.set('bereich', bereichSlug);
    if (stand) params.set('stand', stand);
    if (grade) params.set('bewertung', grade);
    if (source) params.set('quelle', source);
    if (query.trim()) params.set('q', query.trim());
    if (view !== 'review' || params.toString()) params.set('ansicht', view);
    const search = params.toString();
    window.history.replaceState(null, '', search ? `${pathname}?${search}` : pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, stand, bereichSlug, grade, source, view]);

  // Filter changes reset pagination and the selection inline rather than via a separate effect.
  function resetPaging() { setOffset(0); bulk.clear(); }
  function updateView(value: DocumentView) { if (value === view) return; autoView.current = false; setView(value); setDocuments(null); resetPaging(); }
  function updateQuery(value: string) { setQuery(value); resetPaging(); }
  function updateStand(value: string) { setStand(isStand(value) ? value : ''); resetPaging(); }
  function updateBereich(value: string) { setBereichSlug(value); resetPaging(); }
  function updateGrade(value: string) { setGrade(isGrade(value) ? value : ''); resetPaging(); }
  function updateSource(value: string) { setSource(isSource(value) ? value : ''); resetPaging(); }
  // The selection spans pages ("alle auswählen" selects every filtered document), so paging keeps it.
  function updateOffset(value: number) { setOffset(value); }
  function updatePageSize(value: number) { setPageSize(value); setOffset(0); }

  const releasedIds = useMemo(() => (documents ?? []).filter((document) => document.release).map((document) => document.id), [documents]);
  const { items: live } = useIndexingStatus(releasedIds);
  const filtered = useMemo(() => (documents ?? []).filter((document) => !stand || standOf(document, live[document.id]) === stand), [documents, stand, live]);
  const pageItems = pageSize ? filtered.slice(offset, offset + pageSize) : filtered;
  const hasFilters = Boolean(query.trim() || stand || bereichSlug || grade || source);

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
  const viewCount = (value: DocumentView) => value === 'review' || value === 'parked' || value === 'failed' ? counts?.[value] : undefined;

  return (
    <PortalPage title={t('portal.nav.documents')}>
      {error && <Notice error action={load}>{error}</Notice>}
      {notice && <Notice>{notice}</Notice>}

      <div className="portal-filter-row portal-view-row" role="group" aria-label={t('portal.documents.viewsAria')}>
        {VIEWS.map((value) => {
          const count = viewCount(value);
          return <button key={value} type="button" aria-pressed={view === value} onClick={() => updateView(value)}>{t(VIEW_LABEL[value])}{count !== undefined && <b>{count}</b>}</button>;
        })}
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-3" role="search">
        <div className="min-w-[220px] flex-[2] text-sm font-semibold text-[var(--ink-2)]">
          <label htmlFor={ids.search}>{t('portal.documents.searchLabel')}</label>
          <input id={ids.search} type="search" value={query} onChange={(event) => updateQuery(event.target.value)} placeholder={t('portal.documents.filenamePlaceholder')} />
        </div>
        <div className="min-w-[180px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
          <label htmlFor={ids.space}>{t('portal.documents.columnSpace')}</label>
          <select id={ids.space} value={selectedSpace?.slug ?? bereichSlug} onChange={(event) => updateBereich(event.target.value)}>
            <option value="">{t('portal.documents.allSpaces')}</option>
            {spaces?.map((space) => <option key={space.collection_id} value={space.slug}>{space.name}</option>)}
          </select>
        </div>
        <div className="min-w-[150px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
          <span className="flex items-center gap-1"><label htmlFor={ids.stand}>{t('portal.documents.columnStatus')}</label>
            <InfoTip label={t('portal.documents.standHelp.aria')}><dl>{STANDS.map((value) => <div key={value}><dt>{t(STAND_LABEL[value])}</dt><dd>{t(`portal.documents.standHelp.${value}` as MessageKey)}</dd></div>)}</dl></InfoTip>
          </span>
          <select id={ids.stand} value={stand} onChange={(event) => updateStand(event.target.value)}>
            <option value="">{t('common.all')}</option>
            {STANDS.map((value) => <option key={value} value={value}>{t(STAND_LABEL[value])}</option>)}
          </select>
        </div>
        <div className="min-w-[140px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
          <span className="flex items-center gap-1"><label htmlFor={ids.grade}>{t('portal.documents.columnGrade')}</label>
            <InfoTip label={t('portal.documents.qualityLegend.summary')}><dl>
              {(['A', 'B', 'C'] as const).map((value) => <div key={value}><dt>{t('portal.documents.grade', { grade: value })}</dt><dd>{t(`portal.documents.qualityLegend.${value.toLowerCase()}` as MessageKey)}</dd></div>)}
              <dt>{t('portal.documents.noGrade')}</dt><dd>{t('portal.documents.qualityLegend.none')}</dd>
            </dl></InfoTip>
          </span>
          <select id={ids.grade} value={grade} onChange={(event) => updateGrade(event.target.value)}>
            {GRADES.map((value) => <option key={value || 'all'} value={value}>{value === '' ? t('common.all') : value === 'none' ? t('portal.documents.noGrade') : value}</option>)}
          </select>
        </div>
        <div className="min-w-[140px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
          <label htmlFor={ids.source}>{t('portal.documents.columnSource')}</label>
          <select id={ids.source} value={source} onChange={(event) => updateSource(event.target.value)}>
            <option value="">{t('common.all')}</option>
            {SOURCES.filter((value): value is Exclude<DocumentSourceFilter, ''> => value !== '').map((value) => <option key={value} value={value}>{t(SOURCE_LABEL[value])}</option>)}
          </select>
        </div>
        <div className="text-sm font-semibold text-[var(--ink-2)]">
          <label htmlFor={ids.pageSize}>{t('portal.documents.pageSize')}</label>
          <select id={ids.pageSize} value={pageSize} onChange={(event) => updatePageSize(Number(event.target.value))}>
            {PAGE_SIZES.map((size) => <option key={size} value={size}>{size || t('common.all')}</option>)}
          </select>
        </div>
      </div>
      {documents && total > documents.length && <p className="text-xs text-[var(--muted)]">{t('portal.documents.truncatedHint', { count: documents.length, total })}</p>}

      <section className="portal-panel" aria-labelledby="docs-title">
        <h2 className="sr-only" id="docs-title">{t('portal.documents.listHeading')}</h2>
        {/* Several documents at once: select them, the bulk bar asks for the review confirmation (and grade C explicitly). */}
        <BulkActionBar {...bulk.barProps} />

        {documents === null && !error ? <p role="status" className="portal-loading">{t('portal.tasks.documentsLoading')}</p> : pageItems.length ? (
          <>
            <div className="portal-table-scroll">
              <table className="portal-table portal-doc-table">
                <caption className="sr-only">{t('portal.documents.tableCaption')}</caption>
                <thead><tr><th scope="col"><div className="flex items-center gap-[10px]"><input type="checkbox" aria-label={t('portal.documents.selectAllFiltered', { count: filtered.length })} checked={allSelected} onChange={(event) => bulk.selectAll(filtered.map((document) => document.id), event.target.checked)} />{t('portal.documents.columnDocument')}</div></th><th scope="col">{t('portal.documents.columnSpace')}</th><th scope="col">{t('portal.documents.columnStatus')}</th><th scope="col">{t('portal.documents.columnGrade')}</th><th scope="col">{t('portal.documents.columnLast')}</th><th scope="col"><span className="sr-only">{t('portal.documents.columnAction')}</span></th></tr></thead>
                <tbody>
                  {pageItems.map((document) => {
                    const rowStand = standOf(document, live[document.id]);
                    const state = documentState(document, live[document.id], locale);
                    return (
                      <tr key={document.id}>
                        <td><div className="portal-document-link"><input type="checkbox" aria-label={t('portal.documents.selectRow', { filename: document.original_filename })} checked={bulk.selectedIds.has(document.id)} onChange={() => bulk.toggle(document.id)} /><span className="portal-filetype">{fileTypeLabel(document)}</span><span>{document.original_filename}{document.source?.label && <small className="block text-xs text-[var(--muted)]">{document.source.url ? <a href={document.source.url} target="_blank" rel="noopener noreferrer">{document.source.label}</a> : `${document.source.label}${document.source.path ? `: ${document.source.path}` : ''}`}</small>}</span></div></td>
                        <td><span className="portal-space-tag" style={cssVar(spaceColorVar(document.collection_id))}>{document.collection_name}</span></td>
                        <td><span aria-live="polite" className={`portal-badge portal-badge-${state.tone}`}>{state.label}</span>{state.hint && <span className="mt-1 block max-w-[280px] text-xs leading-5 text-[var(--muted)]">{state.hint}</span>}</td>
                        <td>{document.quality_grade ? document.quality_grade.toUpperCase() : <span className="text-[var(--muted)]" title={t('portal.documents.noGrade')}>–</span>}</td>
                        <td className="portal-date">{dateLabel(document.release?.created_at ?? document.created_at, locale)}</td>
                        <td><div className="portal-row-actions">{actionFor(document, rowStand, t)}<Link className="portal-inline-link whitespace-nowrap text-xs" href={`/jobs/${document.id}`}>{t('portal.documents.technicalDetails')}</Link><button type="button" className="portal-open" onClick={() => setRowDeleting(document)} aria-label={t('portal.documents.deleteAria', { filename: document.original_filename })} title={t('common.delete')}><Trash2 size={16} aria-hidden="true" /></button></div></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {pageSize > 0 && <Pagination offset={offset} total={filtered.length} pageSize={pageSize} onChange={updateOffset} />}
          </>
        ) : (
          <EmptyState title={hasFilters ? t('portal.documents.noMatchTitle') : view === 'all' ? t('portal.documents.emptyTitle') : t(VIEW_LABEL[view])}>
            {hasFilters ? t('portal.documents.noMatchBody') : t(VIEW_EMPTY[view])}
          </EmptyState>
        )}
      </section>
      {rowDeleting && <ConfirmDialog title={t('portal.spaces.deleteDocumentsTitle')} body={<><p>{t('portal.spaces.deleteDocumentsBody', { count: 1 })} <strong>{rowDeleting.original_filename}</strong></p>{rowDeleting.release && <p className="mt-3 text-sm">{t('portal.spaces.withdrawReleased')}</p>}</>} confirmLabel={t('common.delete')} onClose={() => setRowDeleting(null)} onConfirm={deleteRow} />}
      {bulk.bulkDeleting && <ConfirmDialog title={t('portal.spaces.deleteDocumentsTitle')} body={<><p>{t('portal.spaces.deleteDocumentsBody', { count: bulk.count })}</p><label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={bulk.withdrawReleased} onChange={(event) => bulk.setWithdrawReleased(event.target.checked)} />{t('portal.spaces.withdrawReleased')}</label></>} confirmLabel={t('portal.spaces.deleteDocumentsTitle')} onClose={bulk.closeDelete} onConfirm={() => bulk.runBulk('delete')} />}
    </PortalPage>
  );
}
