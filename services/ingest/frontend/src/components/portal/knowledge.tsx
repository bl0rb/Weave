'use client';

import { useCallback, useEffect, useMemo, useState, type CSSProperties, type FormEvent } from 'react';
import Link from 'next/link';
import { AlertTriangle, Archive, CheckCheck, Pencil, Plus, RefreshCw, Trash2, Users } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { apiSend, ConfirmDialog, Modal, inputClass } from '@/components/admin/admin-shared';
import { AccessDialog } from './access-dialog';
import { AccessLine } from './access-line';
import { accessSummary, ownerSummary } from '@/lib/access-summary';
import { spaceColorVar, spaceMark } from '@/lib/space-color';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { bulkPortalAction, collectionDownloadName, downloadPortalFile, jsonBody, loadDirectoryTeams, loadDocuments, loadImportScopes, markdownDownloadName, pipelineStage, portalDownloadError, portalError, reindexKnowledgeSpace, type DirectoryTeam, type DocumentPage, type ImportScopesResponse, type KnowledgeSpace, type PortalDocument, type QualityGradeFilter } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { BulkActionBar, DocumentTable, EmptyState, Notice, Pagination, PortalPage, QualityGradeFilterRow, QualityGradeLegend } from './shared';

/** Bounds the "Im Chat verfügbar" counts and state chips below to the most recent N documents visible to the user. */
const SPACE_STATS_SCAN_LIMIT = 200;

function cssVar(color: string): CSSProperties {
  return { '--c': color } as CSSProperties;
}

export function KnowledgeSpaces() {
  const { t, locale } = useI18n();
  const [spaces, setSpaces] = useState<KnowledgeSpace[] | null>(null);
  const [documents, setDocuments] = useState<PortalDocument[]>([]);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<KnowledgeSpace | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeSpace | null>(null);
  const [accessEditing, setAccessEditing] = useState<KnowledgeSpace | null>(null);
  const [notice, setNotice] = useState('');
  const load = useCallback(() => apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections')
    .then(areas => { setSpaces(areas.items); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);
  // Best-effort per-space stats (ready count + state chips) — a separate,
  // bounded fetch so a failure here never blocks the space list itself.
  useEffect(() => {
    loadDocuments(undefined, 0, 'all', undefined, SPACE_STATS_SCAN_LIMIT)
      .then(page => setDocuments(page.items))
      .catch(() => setDocuments([]));
  }, []);
  const releasedIds = useMemo(() => documents.filter(document => document.release).map(document => document.id), [documents]);
  const { items: live } = useIndexingStatus(releasedIds);
  const statsFor = useCallback((collectionId: string) => {
    const own = documents.filter(document => document.collection_id === collectionId);
    const counts = { review: 0, error: 0, working: 0, ready: 0 };
    for (const document of own) {
      switch (pipelineStage(document, live[document.id])) {
        case 'review': counts.review += 1; break;
        case 'error': counts.error += 1; break;
        case 'processing': case 'indexing': counts.working += 1; break;
        case 'ready': counts.ready += 1; break;
      }
    }
    return counts;
  }, [documents, live]);
  const visible = spaces?.filter(space => `${space.name} ${space.description || ''}`.toLocaleLowerCase('de').includes(search.toLocaleLowerCase('de')));
  return <PortalPage eyebrow={null} title={t('portal.nav.knowledgeSpaces')} description={t('portal.spaces.description')} actions={Boolean(spaces?.length) && <Link href="/knowledge/new" className={buttonVariants()}><Plus size={17} />{t('portal.chrome.breadcrumb.knowledgeNew')}</Link>}>
    {notice && <Notice>{notice}</Notice>}
    {error && <Notice error action={load}>{error}</Notice>}
    {spaces === null && !error ? <Notice>{t('portal.spaces.loading')}</Notice> : spaces?.length ? <><label className="portal-search">{t('portal.spaces.searchLabel')}<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder={t('portal.spaces.searchPlaceholder')} /></label><div className="portal-space-grid">
      {visible?.map(space => {
        const stats = statsFor(space.collection_id);
        const chips = [
          stats.review > 0 && <span className="portal-chip portal-chip-warn" key="review">{t('portal.spaces.chipReview', { count: stats.review })}</span>,
          stats.error > 0 && <span className="portal-chip portal-chip-err" key="error"><AlertTriangle aria-hidden="true" />{t('portal.spaces.chipError', { count: stats.error })}</span>,
          stats.working > 0 && <span className="portal-chip portal-chip-proc" key="working">{t('portal.spaces.chipWorking', { count: stats.working })}</span>,
        ].filter(Boolean);
        return <article className="portal-panel portal-space-card" key={space.collection_id} style={cssVar(spaceColorVar(space.collection_id))}>
          <div className="portal-space-top">
            <span className="portal-space-mark" aria-hidden="true">{spaceMark(space.name)}</span>
            <div><h2><Link href={`/knowledge/${space.collection_id}`}>{space.name}</Link></h2><p>{space.description || t('portal.spaces.defaultDescription')}</p></div>
          </div>
          <p className="text-sm font-semibold text-[var(--ink-2)]">{t('portal.spaces.readyCount', { count: stats.ready })}</p>
          <p className="text-xs text-[var(--muted)]">{t('portal.spaces.ownersLine', { owners: ownerSummary(space.grants, locale) })}{space.responsible_team && ` · ${t('portal.spaces.responsibleLine', { team: space.responsible_team.name })}`}</p>
          <AccessLine collection={space} name={space.name} canManage={space.can_manage} onChangeAccess={() => setAccessEditing(space)} />
          <p className="portal-space-state">{chips.length ? chips : <span className="portal-chip portal-chip-ok"><CheckCheck aria-hidden="true" />{t('portal.spaces.allCurrent')}</span>}</p>
          <div className="portal-space-actions">
            <div className="flex flex-wrap gap-2">
              <Link className={buttonVariants({ variant: 'outline', size: 'sm' })} href={`/documents?bereich=${encodeURIComponent(space.slug)}`}>{t('portal.spaces.viewDocuments')}</Link>
              <Link className={buttonVariants({ variant: 'outline', size: 'sm' })} href={`/sources/new?collection=${encodeURIComponent(space.collection_id)}`}><Plus size={15} aria-hidden="true" />{t('portal.spaces.addSourceShort')}</Link>
            </div>
            {space.can_manage && <div className="portal-space-management"><Button variant="ghost" size="sm" onClick={() => { setEditing(space); setNotice(''); }} aria-label={t('portal.spaces.renameAria', { name: space.name })}><Pencil size={14} aria-hidden="true" />{t('common.edit')}</Button><Button variant="ghost" size="sm" onClick={() => { setDeleting(space); setNotice(''); }} aria-label={t('portal.spaces.deleteAria', { name: space.name })}><Trash2 size={14} aria-hidden="true" />{t('common.delete')}</Button></div>}
          </div>
        </article>;
      })}
      <Link href="/knowledge/new" className="portal-panel portal-space-card portal-space-new"><Plus aria-hidden="true" /><strong>{t('portal.chrome.breadcrumb.knowledgeNew')}</strong><small>{t('portal.spaces.newCardHint')}</small></Link>
    </div>{visible?.length === 0 && <EmptyState title={t('portal.spaces.noMatchTitle')}>{t('portal.spaces.noMatchBody')}</EmptyState>}</> : !error && <EmptyState title={t('portal.spaces.emptyTitle')} href="/knowledge/new" action={t('portal.chrome.breadcrumb.knowledgeNew')}>{t('portal.spaces.emptyBody')}</EmptyState>}
    {editing && <KnowledgeSpaceEditor space={editing} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); setNotice(t('portal.spaces.savedNotice')); await load(); }} />}
    {deleting && <DeleteSpaceDialog space={deleting} onClose={() => setDeleting(null)} onDeleted={async () => { setDeleting(null); setNotice(t('portal.spaces.deletedNotice')); await load(); }} />}
    {accessEditing && <AccessDialog collection={accessEditing} onClose={() => setAccessEditing(null)} onSaved={updated => {
      setSpaces(current => current?.map(space => space.collection_id === updated.collection_id ? { ...space, ...updated } : space) ?? current);
      setAccessEditing(null);
      setNotice(t('portal.spaces.accessSavedNotice'));
    }} />}
  </PortalPage>;
}

/** Deletes an empty space, or -- after typing its name -- one with all its
 * documents; released ones are withdrawn from the knowledge index (ADR 0008). */
function DeleteSpaceDialog({ space, onClose, onDeleted }: { space: KnowledgeSpace; onClose: () => void; onDeleted: () => Promise<void> }) {
  const { t } = useI18n();
  const [withContent, setWithContent] = useState(false);
  const [confirmName, setConfirmName] = useState('');
  const params = withContent ? `?${new URLSearchParams({ with_content: 'true', confirm_name: confirmName })}` : '';
  return <ConfirmDialog title={t('portal.spaces.deleteDialogTitle')} confirmLabel={t('portal.spaces.deleteDialogTitle')}
    confirmDisabled={withContent && confirmName !== space.name}
    body={<>
      <p>{t('portal.spaces.deleteDialogBodyPrefix')} <strong className="text-slate-950">{space.name}</strong>{t('portal.spaces.deleteDialogBodySuffix')}</p>
      <label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={withContent} onChange={event => { setWithContent(event.target.checked); setConfirmName(''); }} />{t('portal.spaces.deleteWithContent')}</label>
      {withContent && <label className="mt-3 block text-sm font-medium text-[var(--ink-2)]">{t('portal.spaces.deleteConfirmName', { name: space.name })}<input className={inputClass} value={confirmName} autoComplete="off" onChange={event => setConfirmName(event.target.value)} /></label>}
    </>}
    onClose={onClose}
    onConfirm={async () => { await apiSend(`/api/v1/collections/${encodeURIComponent(space.collection_id)}${params}`, { method: 'DELETE' }); await onDeleted(); }} />;
}

function KnowledgeSpaceEditor({ space, onClose, onSaved }: { space: KnowledgeSpace; onClose: () => void; onSaved: () => Promise<void> }) {
  const { t, locale } = useI18n();
  const [name, setName] = useState(space.name);
  const [purpose, setPurpose] = useState(space.description || '');
  const [responsibleTeam, setResponsibleTeam] = useState(space.responsible_team?.id ?? '');
  const [teams, setTeams] = useState<DirectoryTeam[]>(space.responsible_team ? [{ ...space.responsible_team, member_count: 0 }] : []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    loadDirectoryTeams(controller.signal).then(data => setTeams(data.items)).catch(() => { /* keeps the current team only */ });
    return () => controller.abort();
  }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !purpose.trim()) return;
    setSaving(true); setError('');
    try {
      await apiJson(`/api/v1/collections/${encodeURIComponent(space.collection_id)}`, {
        ...jsonBody({ name: name.trim(), description: purpose.trim(), responsible_team_id: responsibleTeam }), method: 'PATCH',
      });
      await onSaved();
    } catch (err) { setError(portalError(err, locale)); setSaving(false); }
  }
  return <Modal title={t('portal.spaces.editDialogTitle')} onClose={onClose}>
    {error && <Notice error>{error}</Notice>}
    <form className="space-y-4" onSubmit={save}>
      <label className="block text-sm font-medium text-slate-700">{t('common.name')}<input autoFocus required maxLength={255} className={inputClass} value={name} onChange={event => setName(event.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">{t('portal.newSpace.purposeLabel')}<textarea required rows={4} maxLength={4000} className={inputClass} value={purpose} onChange={event => setPurpose(event.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">{t('portal.newSpace.responsibleTeam')}<select className={inputClass} value={responsibleTeam} onChange={event => setResponsibleTeam(event.target.value)}>
        <option value="">{t('portal.newSpace.noResponsibleTeam')}</option>
        {teams.map(team => <option key={team.id} value={team.id}>{team.name}</option>)}
      </select></label>
      <div className="flex flex-wrap justify-end gap-2"><Button type="button" variant="outline" onClick={onClose} disabled={saving}>{t('common.cancel')}</Button><Button type="submit" disabled={saving || !name.trim() || !purpose.trim()}>{saving ? t('portal.access.dialog.saving') : t('common.save')}</Button></div>
    </form>
  </Modal>;
}

export function KnowledgeDetail({ id }: { id: string }) {
  const { t, locale } = useI18n();
  const [space, setSpace] = useState<KnowledgeSpace | null>(null);
  const [documents, setDocuments] = useState<DocumentPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [qualityGrade, setQualityGrade] = useState<QualityGradeFilter>('');
  const [importScope, setImportScope] = useState('');
  const [importScopes, setImportScopes] = useState<ImportScopesResponse | null>(null);
  const [error, setError] = useState('');
  const [downloadError, setDownloadError] = useState('');
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [confirmReleaseAll, setConfirmReleaseAll] = useState(false);
  const [releasingAll, setReleasingAll] = useState(false);
  const [notice, setNotice] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [acceptQualityWarnings, setAcceptQualityWarnings] = useState(false);
  const [releaseConfirmed, setReleaseConfirmed] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [withdrawReleased, setWithdrawReleased] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const load = useCallback(() => Promise.all([
    apiJson<KnowledgeSpace>(`/api/v1/collections/${encodeURIComponent(id)}`),
    loadDocuments(id, offset, 'all', qualityGrade || undefined, undefined, importScope || undefined),
    loadImportScopes(id),
  ])
    .then(([area, docs, scopes]) => {
      setSpace(area); setDocuments(docs); setImportScopes(scopes); setError('');
      // The filtered scope vanished (e.g. its documents were deleted): drop the filter instead of showing an empty list.
      if (importScope && !(importScope === 'none' ? scopes.other_count > 0 : scopes.items.some(scope => scope.value === importScope))) { setImportScope(''); setOffset(0); }
    })
    .catch(err => setError(portalError(err, locale))), [id, offset, qualityGrade, importScope, locale]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void load(); }, 15000);
    return () => clearInterval(timer);
  }, [load]);
  const downloadAll = async () => {
    if (!space) return;
    setDownloadingId('collection');
    setDownloadError('');
    try {
      await downloadPortalFile(`/api/v1/portal/collections/${encodeURIComponent(id)}/markdown.zip`, collectionDownloadName(space), locale);
    } catch (err) {
      setDownloadError(portalDownloadError(err, locale));
    } finally {
      setDownloadingId(null);
    }
  };
  const downloadDocument = async (document: PortalDocument) => {
    setDownloadingId(document.id);
    setDownloadError('');
    try {
      await downloadPortalFile(`/api/v1/portal/documents/${encodeURIComponent(document.id)}/markdown`, markdownDownloadName(document.original_filename), locale);
    } catch (err) {
      setDownloadError(portalDownloadError(err, locale));
    } finally {
      setDownloadingId(null);
    }
  };
  const releaseAll = async () => {
    if (!confirmReleaseAll || releasingAll) return;
    setReleasingAll(true); setError(''); setNotice('');
    try {
      const result = await apiJson<{ released: number; skipped: number }>(`/api/v1/portal/collections/${encodeURIComponent(id)}/release-all`, jsonBody({ accept_quality_warnings: true }));
      setNotice(`${t('portal.spaces.releasedNotice', { count: result.released })}${result.skipped ? t('portal.spaces.releasedSkippedSuffix', { count: result.skipped }) : ''}.`);
      setConfirmReleaseAll(false);
      await load();
    } catch (err) { setError(portalError(err, locale)); }
    finally { setReleasingAll(false); }
  };
  const selectedScope = importScopes?.items?.find(scope => scope.value === importScope);
  const editableScopes = importScopes?.items?.filter(scope => scope.can_edit && scope.edit_run_id) ?? [];
  const selectedDocuments = documents?.items.filter(document => selectedIds.has(document.id)) ?? [];
  const gradeCCount = selectedDocuments.filter(document => document.quality_grade?.toUpperCase() === 'C').length;
  function clearSelection() { setSelectedIds(new Set()); setAcceptQualityWarnings(false); setReleaseConfirmed(false); }
  async function runBulk(action: 'release' | 'skip' | 'delete') {
    if (bulkBusy || selectedIds.size === 0) return;
    setBulkBusy(true); setNotice('');
    try {
      const result = await bulkPortalAction([...selectedIds], action, acceptQualityWarnings, action === 'delete' && withdrawReleased);
      setNotice(`${t('portal.spaces.bulkDoneNotice', { count: result.done })}${result.errors.length ? t('portal.spaces.bulkErrorSuffix', { count: result.errors.length }) : ''}.`);
      clearSelection();
      setDocuments(null);
      await load();
    } catch (err) { setError(portalError(err, locale)); } finally { setBulkBusy(false); setBulkDeleting(false); setWithdrawReleased(false); }
  }
  return <PortalPage title={space?.name || t('portal.documents.columnSpace')} description={space?.description || t('portal.spaces.detailDefaultDescription')} eyebrow={t('portal.spaces.detailEyebrow')}>
    <Link className="portal-back" href="/knowledge">{t('portal.newSpace.backLink')}</Link>
    {error && <Notice error action={load}>{error}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    {downloadError && <Notice error>{downloadError}</Notice>}
    {space && <div className="portal-context-bar"><span><Users size={17} />{t('portal.spaces.authorizedLabel')} {accessSummary(space, locale)}</span><span>{t('portal.spaces.publicationHint')}</span></div>}
    <section className="portal-panel"><div className="portal-section-heading"><div><p className="portal-eyebrow">{t('portal.spaces.contentsEyebrow')}</p><h2>{t('portal.nav.documents')}{documents ? ` · ${documents.total}` : ''}</h2><QualityGradeFilterRow value={qualityGrade} onChange={value => { setQualityGrade(value); setOffset(0); setDocuments(null); }} /><QualityGradeLegend /></div><div className="flex flex-wrap items-center gap-2">{(space?.can_upload ?? space?.can_manage) && Boolean(documents?.total) && (confirmReleaseAll ? <><Button variant="outline" size="sm" disabled={releasingAll} onClick={() => setConfirmReleaseAll(false)}>{t('common.cancel')}</Button><Button variant="danger" size="sm" disabled={releasingAll} onClick={() => void releaseAll()}><CheckCheck size={15} />{releasingAll ? t('portal.spaces.releasingAll') : t('portal.spaces.confirmReleaseAll')}</Button></> : <Button variant="outline" size="sm" onClick={() => setConfirmReleaseAll(true)}><CheckCheck size={15} />{t('portal.spaces.releaseCollection')}</Button>)}{Boolean(documents?.total) && <Button variant="outline" size="sm" disabled={downloadingId !== null} onClick={() => void downloadAll()}><Archive size={15} />{downloadingId === 'collection' ? t('portal.spaces.zipCreating') : t('portal.spaces.zipAll')}</Button>}{(space?.can_upload ?? space?.can_manage) && Boolean(documents?.total) && <Link href={`/sources/new?collection=${encodeURIComponent(id)}`} className={buttonVariants({ variant: 'outline', size: 'sm' })}><Plus size={15} />{t('portal.chrome.addSource')}</Link>}{(space?.can_upload ?? space?.can_manage) && Boolean(documents?.total) && <Button variant="outline" size="sm" onClick={() => setReindexing(true)}><RefreshCw size={15} />{t('portal.spaces.reindexSpace')}</Button>}<Button variant="ghost" size="sm" onClick={load}>{t('common.refresh')}</Button></div></div>
      <BulkActionBar
        count={selectedIds.size}
        gradeCCount={gradeCCount}
        acceptQualityWarnings={acceptQualityWarnings}
        onAcceptQualityWarningsChange={setAcceptQualityWarnings}
        releaseConfirmed={releaseConfirmed}
        onReleaseConfirmedChange={setReleaseConfirmed}
        busy={bulkBusy}
        onRelease={() => void runBulk('release')}
        onSkip={() => void runBulk('skip')}
        onDelete={() => setBulkDeleting(true)}
        onClear={clearSelection}
      />
      {Boolean(importScopes?.items?.length) && <div className="flex flex-wrap items-end gap-3 border-b border-[var(--line)] px-5 py-4">
        <label className="min-w-[200px] flex-1 text-sm font-semibold text-[var(--ink-2)]">
          {t('portal.spaces.importScopeLabel')}
          <select value={importScope} onChange={event => { setImportScope(event.target.value); setOffset(0); setDocuments(null); clearSelection(); }}>
            <option value="">{t('portal.spaces.importScopeAll')}</option>
            {importScopes?.items.map(scope => <option key={scope.value} value={scope.value}>{scope.scope_type === 'space' && scope.label !== scope.scope_value ? `${scope.label} · ${scope.scope_value} (${scope.count})` : `${scope.label} (${scope.count})`}</option>)}
            {Boolean(importScopes?.other_count) && <option value="none">{t('portal.spaces.importScopeNone')}</option>}
          </select>
        </label>
        {selectedScope && <div className="flex flex-wrap items-center gap-4 pb-2 text-sm">
          <Link className="portal-inline-link" href={`/imports/${encodeURIComponent(selectedScope.latest_run_id)}`}>{t('portal.spaces.importView')}</Link>
          {selectedScope.can_edit && selectedScope.edit_run_id && <Link className="portal-inline-link" href={`/imports/new?from=${encodeURIComponent(selectedScope.edit_run_id)}`}>{t('portal.spaces.importEdit')}</Link>}
        </div>}
        {!selectedScope && editableScopes.length > 0 && <p className="basis-full text-xs text-[var(--muted)]">{t('portal.spaces.importEditHint')} {editableScopes.map((scope, index) => <span key={scope.value}>{index > 0 && ' · '}<Link className="portal-inline-link" href={`/imports/new?from=${encodeURIComponent(scope.edit_run_id ?? '')}`}>{scope.label}</Link></span>)}</p>}
      </div>}
      {!documents && !error ? <p className="portal-loading" role="status">{t('portal.tasks.documentsLoading')}</p> : documents?.items.length ? <><DocumentTable documents={documents.items} onDownloadMarkdown={document => void downloadDocument(document)} downloadingId={downloadingId} selectedIds={selectedIds} onToggle={docId => setSelectedIds(previous => { const next = new Set(previous); if (next.has(docId)) next.delete(docId); else next.add(docId); return next; })} onToggleAll={checked => setSelectedIds(checked ? new Set(documents.items.map(document => document.id)) : new Set())} /><Pagination offset={offset} total={documents.total} onChange={value => { setDocuments(null); setOffset(value); }} /></> : !error && <EmptyState title={t('portal.spaces.emptyContentTitle')} href={(space?.can_upload ?? space?.can_manage) ? `/sources/new?collection=${encodeURIComponent(id)}` : undefined} action={(space?.can_upload ?? space?.can_manage) ? t('portal.chrome.addSource') : undefined}>{t('portal.spaces.emptyContentBody')}</EmptyState>}
    </section>
    {bulkDeleting && <ConfirmDialog title={t('portal.spaces.deleteDocumentsTitle')} body={<><p>{t('portal.spaces.deleteDocumentsBody', { count: selectedIds.size })}</p><label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={withdrawReleased} onChange={event => setWithdrawReleased(event.target.checked)} />{t('portal.spaces.withdrawReleased')}</label></>} confirmLabel={t('portal.spaces.deleteDocumentsTitle')} onClose={() => { setBulkDeleting(false); setWithdrawReleased(false); }} onConfirm={() => runBulk('delete')} />}
    {reindexing && <ConfirmDialog title={t('portal.spaces.reindexSpace')} body={<p>{t('portal.spaces.reindexBodyPrefix')} <strong className="text-slate-950">{space?.name}</strong> {t('portal.spaces.reindexBodySuffix')}</p>} confirmLabel={t('portal.spaces.reindexConfirm')} onClose={() => setReindexing(false)} onConfirm={async () => {
      const result = await reindexKnowledgeSpace(id);
      setReindexing(false);
      setNotice(t('portal.spaces.reindexedNotice', { count: result.requeued }));
    }} />}
  </PortalPage>;
}
