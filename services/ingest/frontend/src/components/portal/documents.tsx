'use client';

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { AlertTriangle, ArrowRight, Info } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { buttonVariants } from '@/components/ui/button';
import { spaceColorVar } from '@/lib/space-color';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import {
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
import { EmptyState, Notice, Pagination, PortalPage } from './shared';

/** Bounds the whole filterable/searchable list to the most recent N documents visible to the user — the backend has no full-text search for portal documents yet, so filtering happens client-side over this batch. */
const DOCUMENTS_FETCH_LIMIT = 200;
const PAGE_SIZE = 20;
const STEP_COLOR: Record<Exclude<PipelineStage, 'error'>, string> = {
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
    case 'review': return <Link className={buttonVariants({ size: 'sm' })} href={`/reviews/${document.id}`}>{t('portal.tasks.reviewAction')}</Link>;
    case 'ready': return <Link className={buttonVariants({ size: 'sm', variant: 'outline' })} href={documentUrl(document)}>{t('common.open')}</Link>;
    case 'error': return <Link className={buttonVariants({ size: 'sm', variant: 'outline' })} href={`/jobs/${document.id}`}>{t('common.retry')}</Link>;
    default: return <Link className={buttonVariants({ size: 'sm', variant: 'ghost' })} href={`/jobs/${document.id}`}>{t('portal.documents.statusAction')}</Link>;
  }
}

const isPipelineStage = (value: string): value is PipelineStage => ['processing', 'review', 'indexing', 'ready', 'error'].includes(value);

export function PortalDocuments({ initialQuery, initialStand, initialBereich }: { initialQuery?: string; initialStand?: string; initialBereich?: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const { t, locale } = useI18n();
  const [spaces, setSpaces] = useState<KnowledgeSpace[] | null>(null);
  const [documents, setDocuments] = useState<PortalDocument[] | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState(initialQuery ?? '');
  const [stand, setStand] = useState<PipelineStage | ''>(initialStand && isPipelineStage(initialStand) ? initialStand : '');
  const [bereichSlug, setBereichSlug] = useState(initialBereich ?? '');
  const [offset, setOffset] = useState(0);

  useEffect(() => { apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections').then((page) => setSpaces(page.items)).catch(() => setSpaces([])); }, []);

  const collectionId = useMemo(() => spaces?.find((space) => space.slug === bereichSlug)?.collection_id, [spaces, bereichSlug]);

  const load = useCallback(() => {
    loadDocuments(collectionId, 0, 'all', undefined, DOCUMENTS_FETCH_LIMIT)
      .then((page) => { setDocuments(page.items); setError(''); })
      .catch((err) => setError(portalError(err, locale)));
  }, [collectionId, locale]);
  useEffect(() => { void load(); }, [load]);

  // Reflect the current filters in the URL (shareable / back-button friendly) without remounting this component — the fetch above only depends on collectionId.
  useEffect(() => {
    const params = new URLSearchParams();
    if (query.trim()) params.set('q', query.trim());
    if (stand) params.set('stand', stand);
    if (bereichSlug) params.set('bereich', bereichSlug);
    const search = params.toString();
    router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, stand, bereichSlug]);

  // Filter changes reset pagination inline (in the setters below) rather than via a separate effect.
  function updateQuery(value: string) { setQuery(value); setOffset(0); }
  function updateStand(value: PipelineStage | '') { setStand(value); setOffset(0); }
  function updateBereich(value: string) { setBereichSlug(value); setOffset(0); }

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
  const pageItems = filtered.slice(offset, offset + PAGE_SIZE);

  return (
    <PortalPage
      title={t('portal.nav.documents')}
      description={t('portal.documents.pageDescription')}
      actions={<div className="flex flex-wrap items-center gap-4">
        <Link className="portal-inline-link" href="/processing">{t('portal.chrome.breadcrumb.processing')} <ArrowRight size={14} aria-hidden="true" /></Link>
        <Link className="portal-inline-link" href="/imports">{t('portal.documents.importsLink')} <ArrowRight size={14} aria-hidden="true" /></Link>
      </div>}
    >
      {error && <Notice error action={load}>{error}</Notice>}

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
      </div>

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

        {documents === null && !error ? <p role="status" className="portal-loading">{t('portal.tasks.documentsLoading')}</p> : pageItems.length ? (
          <>
            <div className="portal-table-scroll">
              <table className="portal-table portal-doc-table">
                <caption className="sr-only">{t('portal.documents.tableCaption')}</caption>
                <thead><tr><th scope="col">{t('portal.documents.columnDocument')}</th><th scope="col">{t('portal.documents.columnSpace')}</th><th scope="col">{t('portal.documents.columnStatus')}</th><th scope="col">{t('portal.documents.columnLast')}</th><th scope="col"><span className="sr-only">{t('portal.documents.columnAction')}</span></th></tr></thead>
                <tbody>
                  {pageItems.map((document) => {
                    const stage = stageOf(document);
                    const state = documentState(document, live[document.id], locale);
                    return (
                      <tr key={document.id}>
                        <td><div className="portal-document-link"><span className="portal-filetype">{fileTypeLabel(document)}</span><span><strong>{document.original_filename}</strong></span></div></td>
                        <td><span className="portal-space-tag" style={cssVar(spaceColorVar(document.collection_id))}>{document.collection_name}</span></td>
                        <td><span aria-live="polite" className={`portal-badge portal-badge-${state.tone}`}>{state.label}</span>{state.hint && <span className="mt-1 block max-w-[280px] text-xs leading-5 text-[var(--muted)]">{state.hint}</span>}</td>
                        <td className="portal-date">{dateLabel(document.release?.created_at ?? document.created_at, locale)}</td>
                        <td>{actionFor(document, stage, t)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination offset={offset} total={filtered.length} onChange={setOffset} />
          </>
        ) : (
          <EmptyState title={query || stand ? t('portal.documents.noMatchTitle') : t('portal.documents.emptyTitle')}>
            {query || stand ? t('portal.documents.noMatchBody') : t('portal.documents.emptyBody')}
          </EmptyState>
        )}
      </section>
    </PortalPage>
  );
}
