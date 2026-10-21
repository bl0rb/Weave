'use client';

import { useCallback, useEffect, useMemo, useState, type CSSProperties, type FormEvent } from 'react';
import Link from 'next/link';
import { AlertTriangle, Archive, ArrowRight, CheckCheck, Pencil, Plus, RefreshCw, Trash2, Users } from 'lucide-react';
import { ApiError, apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { apiSend, ConfirmDialog, Modal, inputClass } from '@/components/admin/admin-shared';
import { AccessDialog } from './access-dialog';
import { AccessLine } from './access-line';
import { ownerSummary } from '@/lib/access-summary';
import { spaceColorVar, spaceMark } from '@/lib/space-color';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { collectionDownloadName, downloadPortalFile, jsonBody, loadDirectoryTeams, loadDocuments, loadImportScopes, pipelineStage, portalDownloadError, portalError, reindexKnowledgeSpace, type DirectoryTeam, type ImportScope, type ImportScopesResponse, type KnowledgeSpace, type PortalDocument } from '@/lib/portal';
import { type ImportRun, type ImportSyncListResponse, type ImportSync } from '@/lib/imports';
import { useI18n } from '@/i18n/provider';
import { EmptyState, Notice, PortalPage, RequiredMark } from './shared';
import { ImportSyncSummary } from './import-sync';

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
  // "Wissensbereich anlegen" lives on the last card of the grid (or in the empty state), not in the header too.
  return <PortalPage eyebrow={null} title={t('portal.nav.knowledgeSpaces')} description={t('portal.spaces.description')}>
    {notice && <Notice>{notice}</Notice>}
    {error && <Notice error action={load}>{error}</Notice>}
    {spaces === null && !error ? <Notice>{t('portal.spaces.loading')}</Notice> : spaces?.length ? <><label className="portal-search">{t('portal.spaces.searchLabel')}<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder={t('portal.spaces.searchPlaceholder')} /></label><div className="portal-space-grid">
      {visible?.map(space => {
        const stats = statsFor(space.collection_id);
        // Readers use a space in chat only (ADR 0008): the portal shows them none of its documents.
        const readOnly = !(space.can_upload ?? space.can_manage);
        const documentsHref = (stand: string) => `/documents?${new URLSearchParams({ bereich: space.slug, stand })}`;
        const chips = [
          stats.review > 0 && <Link className="portal-chip portal-chip-warn" key="review" href={documentsHref('review')}>{t('portal.spaces.chipReview', { count: stats.review })}</Link>,
          stats.error > 0 && <Link className="portal-chip portal-chip-err" key="error" href={documentsHref('error')}><AlertTriangle aria-hidden="true" />{t('portal.spaces.chipError', { count: stats.error })}</Link>,
          stats.working > 0 && <span className="portal-chip portal-chip-proc" key="working">{t('portal.spaces.chipWorking', { count: stats.working })}</span>,
        ].filter(Boolean);
        return <article className="portal-panel portal-space-card" key={space.collection_id} style={cssVar(spaceColorVar(space.collection_id))}>
          <div className="portal-space-top">
            <span className="portal-space-mark" aria-hidden="true">{spaceMark(space.name)}</span>
            <div><h2><Link href={`/knowledge/${space.collection_id}`}>{space.name}</Link></h2><p>{space.description || t('portal.spaces.defaultDescription')}</p></div>
          </div>
          {readOnly ? <p className="text-sm text-[var(--ink-2)]">{t('portal.spaces.readerHint')}</p> : <p className="text-sm font-semibold text-[var(--ink-2)]">{t('portal.spaces.readyCount', { count: stats.ready })}</p>}
          <p className="text-xs text-[var(--muted)]">{t('portal.spaces.ownersLine', { owners: ownerSummary(space.grants, locale) })}{space.responsible_team && ` · ${t('portal.spaces.responsibleLine', { team: space.responsible_team.name })}`}</p>
          <AccessLine collection={space} name={space.name} canManage={space.can_manage} onChangeAccess={() => setAccessEditing(space)} />
          {!readOnly && <p className="portal-space-state">{chips.length ? chips : <span className="portal-chip portal-chip-ok"><CheckCheck aria-hidden="true" />{t('portal.spaces.allCurrent')}</span>}</p>}
          <div className="portal-space-actions">
            {!readOnly && <div className="flex flex-wrap gap-2">
              <Link className={buttonVariants({ variant: 'outline', size: 'sm' })} href={`/documents?${new URLSearchParams({ bereich: space.slug })}`}>{t('portal.spaces.viewDocuments')}</Link>
              <Link className={buttonVariants({ size: 'sm' })} href={`/sources/new?collection=${encodeURIComponent(space.collection_id)}`}><Plus size={15} aria-hidden="true" />{t('portal.chrome.addSource')}</Link>
            </div>}
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
    } catch (err) { setError(err instanceof ApiError && err.status === 409 ? t('portal.spaces.nameTaken') : portalError(err, locale)); setSaving(false); }
  }
  return <Modal title={t('portal.spaces.editDialogTitle')} onClose={onClose}>
    {error && <Notice error>{error}</Notice>}
    <form className="space-y-4" onSubmit={save}>
      <label className="block text-sm font-medium text-slate-700">{t('common.name')}<RequiredMark /><input autoFocus required maxLength={255} className={inputClass} value={name} onChange={event => setName(event.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">{t('portal.newSpace.purposeLabel')}<RequiredMark /><textarea required rows={4} maxLength={4000} className={inputClass} value={purpose} onChange={event => setPurpose(event.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">{t('portal.newSpace.responsibleTeam')}<select className={inputClass} value={responsibleTeam} onChange={event => setResponsibleTeam(event.target.value)}>
        <option value="">{t('portal.newSpace.noResponsibleTeam')}</option>
        {teams.map(team => <option key={team.id} value={team.id}>{team.name}</option>)}
      </select></label>
      <div className="flex flex-wrap justify-end gap-2"><Button type="button" variant="outline" onClick={onClose} disabled={saving}>{t('common.cancel')}</Button><Button type="submit" disabled={saving || !name.trim() || !purpose.trim()}>{saving ? t('portal.access.dialog.saving') : t('common.save')}</Button></div>
    </form>
  </Modal>;
}

/** Readable label of a Confluence import scope ("Handbuch · DOCS"). */
function scopeLabel(scope: ImportScope): string {
  return scope.scope_type === 'space' && scope.label !== scope.scope_value ? `${scope.label} · ${scope.scope_value}` : scope.label;
}

/**
 * /knowledge/[id] — managing one space: where its documents come from
 * (Quellen), who may use it (Zugriff) and maintenance (Wartung). The
 * documents themselves live in /documents?bereich=<slug>.
 */
export function KnowledgeDetail({ id }: { id: string }) {
  const { t, locale } = useI18n();
  const [space, setSpace] = useState<KnowledgeSpace | null>(null);
  const [documentTotal, setDocumentTotal] = useState<number | null>(null);
  const [importScopes, setImportScopes] = useState<ImportScopesResponse | null>(null);
  const [error, setError] = useState('');
  const [downloadError, setDownloadError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [notice, setNotice] = useState('');
  const [reindexing, setReindexing] = useState(false);
  const [accessEditing, setAccessEditing] = useState(false);
  // Confluence imports into this space that the current user owns and can therefore sync on demand.
  const [syncs, setSyncs] = useState<ImportSync[]>([]);
  const [syncing, setSyncing] = useState(false);
  const loadSyncs = useCallback(() => apiJson<ImportSyncListResponse>('/api/v1/import/syncs', { cache: 'no-store' })
    .then(response => setSyncs(response.items.filter(sync => sync.collection_id === id)))
    .catch(() => setSyncs([])), [id]);
  const load = useCallback(() => Promise.all([
    apiJson<KnowledgeSpace>(`/api/v1/collections/${encodeURIComponent(id)}`),
    loadDocuments(id, 0, 'all', undefined, 1),
    loadImportScopes(id),
  ])
    .then(([area, docs, scopes]) => { setSpace(area); setDocumentTotal(docs.total); setImportScopes(scopes); setError(''); })
    .catch(err => setError(portalError(err, locale))), [id, locale]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadSyncs(); }, [loadSyncs]);
  const syncNow = async () => {
    setSyncing(true);
    setNotice('');
    setError('');
    try {
      for (const sync of syncs) await apiJson<ImportRun>(`/api/v1/import/syncs/${encodeURIComponent(sync.id)}/run`, { method: 'POST' });
      setNotice(t('portal.connections.confluence.syncNowStarted'));
      await Promise.all([load(), loadSyncs()]);
    } catch (err) {
      setError(portalError(err, locale));
    } finally {
      setSyncing(false);
    }
  };
  const downloadAll = async () => {
    if (!space) return;
    setDownloading(true);
    setDownloadError('');
    try {
      await downloadPortalFile(`/api/v1/portal/collections/${encodeURIComponent(id)}/markdown.zip`, collectionDownloadName(space), locale);
    } catch (err) {
      setDownloadError(portalDownloadError(err, locale));
    } finally {
      setDownloading(false);
    }
  };
  const canUpload = Boolean(space && (space.can_upload ?? space.can_manage));
  const documentsHref = space ? `/documents?${new URLSearchParams({ bereich: space.slug })}` : '/documents';
  return <PortalPage title={space?.name || t('portal.documents.columnSpace')} description={space?.description || t('portal.spaces.detailDefaultDescription')} eyebrow={t('portal.spaces.detailEyebrow')}
    actions={canUpload && documentTotal ? <Link className={buttonVariants({ variant: 'outline' })} href={documentsHref}>{t('portal.spaces.viewDocumentsCount', { count: documentTotal })}<ArrowRight size={16} aria-hidden="true" /></Link> : undefined}>
    <Link className="portal-back" href="/knowledge">{t('portal.newSpace.backLink')}</Link>
    {error && <Notice error action={load}>{error}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    {downloadError && <Notice error>{downloadError}</Notice>}
    {space && <div className="portal-context-bar"><span><Users size={17} />{t('portal.spaces.yourRole')} {space.role ? t(`portal.access.role.${space.role}`) : t('portal.chrome.admin')}</span><span>{t('portal.spaces.publicationHint')}</span></div>}
    {space && !canUpload && <Notice>{t('portal.spaces.readerHint')}</Notice>}
    {space && canUpload && <div className="grid gap-5">
      <section className="portal-panel" aria-labelledby="space-sources-title">
        <header className="portal-section-heading">
          <h2 id="space-sources-title">{t('portal.spaces.sourcesTitle')}</h2>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {syncs.length > 0 && <Button variant="outline" size="sm" disabled={syncing} onClick={() => void syncNow()}><RefreshCw size={15} />{t('portal.importSync.syncNow')}</Button>}
            <Link href={`/sources/new?collection=${encodeURIComponent(id)}`} className={buttonVariants({ size: 'sm' })}><Plus size={15} />{t('portal.chrome.addSource')}</Link>
          </div>
        </header>
        <div className="grid gap-4 px-5 pb-5">
          {syncs.length > 0 && <ul className="grid gap-3" aria-label={t('portal.spaces.syncsAria')}>{syncs.map(sync => <li key={sync.id}><ImportSyncSummary sync={sync} /></li>)}</ul>}
          {Boolean(importScopes?.items?.length) && <div>
            <h3 className="text-sm font-semibold text-[var(--ink-2)]">{t('portal.spaces.importScopeLabel')}</h3>
            <ul className="mt-2 grid gap-2 text-sm">{importScopes?.items?.map(scope => <li key={scope.value} className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <Link className="portal-inline-link" href={`/documents?${new URLSearchParams({ bereich: space.slug, q: scope.label })}`}>{`${scopeLabel(scope)} (${scope.count})`}</Link>
              <Link className="portal-inline-link text-xs" href={`/imports/${encodeURIComponent(scope.latest_run_id)}`}>{t('portal.spaces.importView')}</Link>
              {scope.can_edit && scope.edit_run_id && <Link className="portal-inline-link text-xs" href={`/imports/new?from=${encodeURIComponent(scope.edit_run_id)}`}>{t('portal.spaces.importEdit')}</Link>}
            </li>)}</ul>
          </div>}
          {!syncs.length && !importScopes?.items?.length && <p className="text-sm text-[var(--muted)]">{t('portal.spaces.sourcesEmpty')}</p>}
          <p className="text-xs text-[var(--muted)]">{t('portal.spaces.sourcesConnectionsHint')} <Link className="portal-inline-link" href="/connections">{t('portal.chrome.breadcrumb.connections')}</Link></p>
        </div>
      </section>
      <section className="portal-panel" aria-labelledby="space-access-title">
        <header className="portal-section-heading"><h2 id="space-access-title">{t('portal.spaces.accessTitle')}</h2></header>
        <div className="grid gap-2 px-5 pb-5">
          <AccessLine collection={space} name={space.name} canManage={space.can_manage} onChangeAccess={() => setAccessEditing(true)} />
          <p className="text-xs text-[var(--muted)]">{t('portal.spaces.ownersLine', { owners: ownerSummary(space.grants, locale) })}{space.responsible_team && ` · ${t('portal.spaces.responsibleLine', { team: space.responsible_team.name })}`}</p>
        </div>
      </section>
      <section className="portal-panel" aria-labelledby="space-maintenance-title">
        <header className="portal-section-heading"><h2 id="space-maintenance-title">{t('portal.spaces.maintenanceTitle')}</h2></header>
        <div className="grid gap-4 px-5 pb-5 text-sm">
          <div className="flex flex-wrap items-center gap-3"><Button variant="outline" size="sm" disabled={!documentTotal} onClick={() => setReindexing(true)}><RefreshCw size={15} />{t('portal.spaces.reindexSpace')}</Button><span className="text-[var(--muted)]">{t('portal.spaces.reindexExplanation')}</span></div>
          <div className="flex flex-wrap items-center gap-3"><Button variant="outline" size="sm" disabled={downloading || !documentTotal} onClick={() => void downloadAll()}><Archive size={15} />{downloading ? t('portal.spaces.zipCreating') : t('portal.spaces.zipAll')}</Button><span className="text-[var(--muted)]">{t('portal.spaces.zipExplanation')}</span></div>
        </div>
      </section>
    </div>}
    {accessEditing && space && <AccessDialog collection={space} onClose={() => setAccessEditing(false)} onSaved={updated => {
      setSpace(current => current ? { ...current, ...updated } : current);
      setAccessEditing(false);
      setNotice(t('portal.spaces.accessSavedNotice'));
    }} />}
    {reindexing && <ConfirmDialog title={t('portal.spaces.reindexSpace')} body={<p>{t('portal.spaces.reindexBodyPrefix')} <strong className="text-slate-950">{space?.name}</strong> {t('portal.spaces.reindexBodySuffix')}</p>} confirmLabel={t('portal.spaces.reindexConfirm')} onClose={() => setReindexing(false)} onConfirm={async () => {
      const result = await reindexKnowledgeSpace(id);
      setReindexing(false);
      setNotice(t('portal.spaces.reindexedNotice', { count: result.requeued }));
    }} />}
  </PortalPage>;
}
