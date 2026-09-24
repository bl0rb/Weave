'use client';

import { useCallback, useEffect, useMemo, useState, type CSSProperties, type FormEvent } from 'react';
import Link from 'next/link';
import { AlertTriangle, Archive, CheckCheck, Pencil, Plus, RefreshCw, Trash2, Users } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { apiSend, ConfirmDialog, Modal, inputClass } from '@/components/admin/admin-shared';
import { AccessDialog } from './access-dialog';
import { AccessLine } from './access-line';
import { spaceColorVar, spaceMark } from '@/lib/space-color';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { bulkPortalAction, collectionDownloadName, downloadPortalFile, jsonBody, loadDocuments, markdownDownloadName, pipelineStage, portalDownloadError, portalError, reindexKnowledgeSpace, type DocumentPage, type KnowledgeSpace, type PortalDocument, type QualityGradeFilter } from '@/lib/portal';
import { BulkActionBar, DocumentTable, EmptyState, Notice, Pagination, PortalPage, QualityGradeFilterRow, QualityGradeLegend } from './shared';

/** Bounds the "Im Chat verfügbar" counts and state chips below to the most recent N documents visible to the user. */
const SPACE_STATS_SCAN_LIMIT = 200;

function cssVar(color: string): CSSProperties {
  return { '--c': color } as CSSProperties;
}

export function KnowledgeSpaces() {
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
    .catch(err => setError(portalError(err))), []);
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
  return <PortalPage eyebrow={null} title="Wissensbereiche" description="Du legst fest, wer welches Wissen im Chat nutzen darf, und behältst Inhalt und Zugriff an einem Ort." actions={Boolean(spaces?.length) && <Link href="/knowledge/new" className={buttonVariants()}><Plus size={17} />Wissensbereich anlegen</Link>}>
    {notice && <Notice>{notice}</Notice>}
    {error && <Notice error action={load}>{error}</Notice>}
    {spaces === null && !error ? <Notice>Wissensbereiche werden geladen …</Notice> : spaces?.length ? <><label className="portal-search">Wissensbereiche finden<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Nach Name oder Beschreibung suchen" /></label><div className="portal-space-grid">
      {visible?.map(space => {
        const stats = statsFor(space.collection_id);
        const chips = [
          stats.review > 0 && <span className="portal-chip portal-chip-warn" key="review">{stats.review} zu prüfen</span>,
          stats.error > 0 && <span className="portal-chip portal-chip-err" key="error"><AlertTriangle aria-hidden="true" />{stats.error} Fehler</span>,
          stats.working > 0 && <span className="portal-chip portal-chip-proc" key="working">{stats.working} in Arbeit</span>,
        ].filter(Boolean);
        return <article className="portal-panel portal-space-card" key={space.collection_id} style={cssVar(spaceColorVar(space.collection_id))}>
          <div className="portal-space-top">
            <span className="portal-space-mark" aria-hidden="true">{spaceMark(space.name)}</span>
            <div><h2><Link href={`/knowledge/${space.collection_id}`}>{space.name}</Link></h2><p>{space.description || 'Dokumente und Quellen zu einem gemeinsamen Thema.'}</p></div>
          </div>
          <p className="text-sm font-semibold text-[var(--ink-2)]">Im Chat verfügbar: {stats.ready} {stats.ready === 1 ? 'Dokument' : 'Dokumente'}</p>
          <AccessLine collection={space} name={space.name} canManage={space.can_manage} onChangeAccess={() => setAccessEditing(space)} />
          <p className="portal-space-state">{chips.length ? chips : <span className="portal-chip portal-chip-ok"><CheckCheck aria-hidden="true" />Alles aktuell</span>}</p>
          <div className="portal-space-actions">
            <div className="flex flex-wrap gap-2">
              <Link className={buttonVariants({ variant: 'outline', size: 'sm' })} href={`/documents?bereich=${encodeURIComponent(space.slug)}`}>Dokumente ansehen</Link>
              <Link className={buttonVariants({ variant: 'outline', size: 'sm' })} href={`/sources/new?collection=${encodeURIComponent(space.collection_id)}`}><Plus size={15} aria-hidden="true" />Quelle</Link>
            </div>
            {space.can_manage && <div className="portal-space-management"><Button variant="ghost" size="sm" onClick={() => { setEditing(space); setNotice(''); }} aria-label={`${space.name} umbenennen`}><Pencil size={14} aria-hidden="true" />Bearbeiten</Button><Button variant="ghost" size="sm" onClick={() => { setDeleting(space); setNotice(''); }} aria-label={`${space.name} löschen`}><Trash2 size={14} aria-hidden="true" />Löschen</Button></div>}
          </div>
        </article>;
      })}
      <Link href="/knowledge/new" className="portal-panel portal-space-card portal-space-new"><Plus aria-hidden="true" /><strong>Wissensbereich anlegen</strong><small>Für ein Team oder ein Thema – mit eigenem Zugriff.</small></Link>
    </div>{visible?.length === 0 && <EmptyState title="Kein passender Wissensbereich">Versuche einen anderen Suchbegriff.</EmptyState>}</> : !error && <EmptyState title="Womit möchtest du beginnen?" href="/knowledge/new" action="Wissensbereich anlegen">Lege deinen ersten Wissensbereich an. Danach kannst du Dateien und Confluence-Seiten hinzufügen.</EmptyState>}
    {editing && <KnowledgeSpaceEditor space={editing} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); setNotice('Wissensbereich gespeichert.'); await load(); }} />}
    {deleting && <ConfirmDialog title="Wissensbereich löschen" body={<p>Den leeren Wissensbereich <strong className="text-slate-950">{deleting.name}</strong> löschen? Enthält er Dokumente, einen laufenden Import oder eine Bot-Zuordnung, wird die Aktion zum Schutz der Inhalte und Rechte abgelehnt.</p>} confirmLabel="Wissensbereich löschen" onClose={() => setDeleting(null)} onConfirm={async () => { await apiSend(`/api/v1/collections/${encodeURIComponent(deleting.collection_id)}`, { method: 'DELETE' }); setDeleting(null); setNotice('Wissensbereich gelöscht.'); await load(); }} />}
    {accessEditing && <AccessDialog collection={accessEditing} onClose={() => setAccessEditing(null)} onSaved={updated => {
      setSpaces(current => current?.map(space => space.collection_id === updated.collection_id ? { ...space, ...updated } : space) ?? current);
      setAccessEditing(null);
      setNotice('Zugriff gespeichert.');
    }} />}
  </PortalPage>;
}

function KnowledgeSpaceEditor({ space, onClose, onSaved }: { space: KnowledgeSpace; onClose: () => void; onSaved: () => Promise<void> }) {
  const [name, setName] = useState(space.name);
  const [description, setDescription] = useState(space.description || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true); setError('');
    try {
      await apiJson(`/api/v1/collections/${encodeURIComponent(space.collection_id)}`, {
        ...jsonBody({ name: name.trim(), description: description.trim() }), method: 'PATCH',
      });
      await onSaved();
    } catch (err) { setError(portalError(err)); setSaving(false); }
  }
  return <Modal title="Wissensbereich bearbeiten" onClose={onClose}>
    {error && <Notice error>{error}</Notice>}
    <form className="space-y-4" onSubmit={save}>
      <label className="block text-sm font-medium text-slate-700">Name<input autoFocus required maxLength={255} className={inputClass} value={name} onChange={event => setName(event.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">Beschreibung<textarea rows={4} maxLength={4000} className={inputClass} value={description} onChange={event => setDescription(event.target.value)} /></label>
      <div className="flex flex-wrap justify-end gap-2"><Button type="button" variant="outline" onClick={onClose} disabled={saving}>Abbrechen</Button><Button type="submit" disabled={saving || !name.trim()}>{saving ? 'Wird gespeichert …' : 'Speichern'}</Button></div>
    </form>
  </Modal>;
}

export function KnowledgeDetail({ id }: { id: string }) {
  const [space, setSpace] = useState<KnowledgeSpace | null>(null);
  const [documents, setDocuments] = useState<DocumentPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [qualityGrade, setQualityGrade] = useState<QualityGradeFilter>('');
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
  const [reindexing, setReindexing] = useState(false);
  const load = useCallback(() => Promise.all([apiJson<KnowledgeSpace>(`/api/v1/collections/${encodeURIComponent(id)}`), loadDocuments(id, offset, 'all', qualityGrade || undefined)])
    .then(([area, docs]) => { setSpace(area); setDocuments(docs); setError(''); })
    .catch(err => setError(portalError(err))), [id, offset, qualityGrade]);
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
      await downloadPortalFile(`/api/v1/portal/collections/${encodeURIComponent(id)}/markdown.zip`, collectionDownloadName(space));
    } catch (err) {
      setDownloadError(portalDownloadError(err));
    } finally {
      setDownloadingId(null);
    }
  };
  const downloadDocument = async (document: PortalDocument) => {
    setDownloadingId(document.id);
    setDownloadError('');
    try {
      await downloadPortalFile(`/api/v1/portal/documents/${encodeURIComponent(document.id)}/markdown`, markdownDownloadName(document.original_filename));
    } catch (err) {
      setDownloadError(portalDownloadError(err));
    } finally {
      setDownloadingId(null);
    }
  };
  const releaseAll = async () => {
    if (!confirmReleaseAll || releasingAll) return;
    setReleasingAll(true); setError(''); setNotice('');
    try {
      const result = await apiJson<{ released: number; skipped: number }>(`/api/v1/portal/collections/${encodeURIComponent(id)}/release-all`, jsonBody({ accept_quality_warnings: true }));
      setNotice(`${result.released} Dokumente wurden freigegeben${result.skipped ? `, ${result.skipped} übersprungen` : ''}.`);
      setConfirmReleaseAll(false);
      await load();
    } catch (err) { setError(portalError(err)); }
    finally { setReleasingAll(false); }
  };
  const selectedDocuments = documents?.items.filter(document => selectedIds.has(document.id)) ?? [];
  const gradeCCount = selectedDocuments.filter(document => document.quality_grade?.toUpperCase() === 'C').length;
  function clearSelection() { setSelectedIds(new Set()); setAcceptQualityWarnings(false); setReleaseConfirmed(false); }
  async function runBulk(action: 'release' | 'skip' | 'delete') {
    if (bulkBusy || selectedIds.size === 0) return;
    setBulkBusy(true); setNotice('');
    try {
      const result = await bulkPortalAction([...selectedIds], action, acceptQualityWarnings);
      setNotice(`${result.done} Dokument${result.done === 1 ? '' : 'e'} bearbeitet${result.errors.length ? `, ${result.errors.length} Fehler` : ''}.`);
      clearSelection();
      setDocuments(null);
      await load();
    } catch (err) { setError(portalError(err)); } finally { setBulkBusy(false); setBulkDeleting(false); }
  }
  return <PortalPage title={space?.name || 'Wissensbereich'} description={space?.description || 'Quellen hinzufügen, Inhalte prüfen und den nächsten Schritt im Blick behalten.'} eyebrow="DEINE WISSENSBASIS">
    <Link className="portal-back" href="/knowledge">← Alle Wissensbereiche</Link>
    {error && <Notice error action={load}>{error}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    {downloadError && <Notice error>{downloadError}</Notice>}
    {space && <div className="portal-context-bar"><span><Users size={17} />Berechtigte: {space.read_teams.length ? space.read_teams.join(', ') : 'Alle angemeldeten Teams'}</span><span>Veröffentlichung nach manueller Freigabe</span></div>}
    <section className="portal-panel"><div className="portal-section-heading"><div><p className="portal-eyebrow">INHALTE</p><h2>Dokumente{documents ? ` · ${documents.total}` : ''}</h2><QualityGradeFilterRow value={qualityGrade} onChange={value => { setQualityGrade(value); setOffset(0); setDocuments(null); }} /><QualityGradeLegend /></div><div className="flex flex-wrap items-center gap-2">{(space?.can_upload ?? space?.can_manage) && Boolean(documents?.total) && (confirmReleaseAll ? <><Button variant="outline" size="sm" disabled={releasingAll} onClick={() => setConfirmReleaseAll(false)}>Abbrechen</Button><Button variant="danger" size="sm" disabled={releasingAll} onClick={() => void releaseAll()}><CheckCheck size={15} />{releasingAll ? 'Wird freigegeben …' : 'Ja, Sammlung freigeben'}</Button></> : <Button variant="outline" size="sm" onClick={() => setConfirmReleaseAll(true)}><CheckCheck size={15} />Sammlung freigeben</Button>)}{Boolean(documents?.total) && <Button variant="outline" size="sm" disabled={downloadingId !== null} onClick={() => void downloadAll()}><Archive size={15} />{downloadingId === 'collection' ? 'ZIP wird erstellt …' : 'Alle als ZIP'}</Button>}{(space?.can_upload ?? space?.can_manage) && Boolean(documents?.total) && <Link href={`/sources/new?collection=${encodeURIComponent(id)}`} className={buttonVariants({ variant: 'outline', size: 'sm' })}><Plus size={15} />Quelle hinzufügen</Link>}{(space?.can_upload ?? space?.can_manage) && Boolean(documents?.total) && <Button variant="outline" size="sm" onClick={() => setReindexing(true)}><RefreshCw size={15} />Wissensbereich neu indizieren</Button>}<Button variant="ghost" size="sm" onClick={load}>Aktualisieren</Button></div></div>
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
      {!documents && !error ? <p className="portal-loading" role="status">Dokumente werden geladen …</p> : documents?.items.length ? <><DocumentTable documents={documents.items} onDownloadMarkdown={document => void downloadDocument(document)} downloadingId={downloadingId} selectedIds={selectedIds} onToggle={docId => setSelectedIds(previous => { const next = new Set(previous); if (next.has(docId)) next.delete(docId); else next.add(docId); return next; })} onToggleAll={checked => setSelectedIds(checked ? new Set(documents.items.map(document => document.id)) : new Set())} /><Pagination offset={offset} total={documents.total} onChange={value => { setDocuments(null); setOffset(value); }} /></> : !error && <EmptyState title="Hier ist Platz für dein Wissen" href={(space?.can_upload ?? space?.can_manage) ? `/sources/new?collection=${encodeURIComponent(id)}` : undefined} action={(space?.can_upload ?? space?.can_manage) ? "Quelle hinzufügen" : undefined}>Die Eigentümer des Wissensbereichs können eine Quelle hinzufügen. Verbinde eine Confluence-Seite oder lade Dokumente hoch. Nach der Verarbeitung prüfst du den Inhalt.</EmptyState>}
    </section>
    {bulkDeleting && <ConfirmDialog title="Dokumente löschen" body={<p>{selectedIds.size} Dokument{selectedIds.size === 1 ? '' : 'e'} unwiderruflich löschen?</p>} confirmLabel="Dokumente löschen" onClose={() => setBulkDeleting(false)} onConfirm={() => runBulk('delete')} />}
    {reindexing && <ConfirmDialog title="Wissensbereich neu indizieren" body={<p>Alle freigegebenen Dokumente in <strong className="text-slate-950">{space?.name}</strong> erneut indizieren?</p>} confirmLabel="Neu indizieren" onClose={() => setReindexing(false)} onConfirm={async () => {
      const result = await reindexKnowledgeSpace(id);
      setReindexing(false);
      setNotice(`${result.requeued} Dokument${result.requeued === 1 ? '' : 'e'} ${result.requeued === 1 ? 'wird' : 'werden'} neu indiziert.`);
    }} />}
  </PortalPage>;
}
