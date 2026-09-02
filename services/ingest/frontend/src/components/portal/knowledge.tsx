'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Archive, BookOpen, Plus, Users } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { collectionDownloadName, downloadPortalFile, loadDocuments, markdownDownloadName, portalDownloadError, portalError, type DocumentPage, type KnowledgeSpace, type PortalDocument } from '@/lib/portal';
import { DocumentTable, EmptyState, Notice, Pagination, PortalPage } from './shared';

export function KnowledgeSpaces() {
  const [spaces, setSpaces] = useState<KnowledgeSpace[] | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const load = useCallback(() => apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections')
    .then(areas => { setSpaces(areas.items); setError(''); })
    .catch(err => setError(portalError(err))), []);
  useEffect(() => { void load(); }, [load]);
  const visible = spaces?.filter(space => `${space.name} ${space.description || ''}`.toLocaleLowerCase('de').includes(search.toLocaleLowerCase('de')));
  return <PortalPage title="Wissensbereiche" description="Ordne Wissen nach Themen und lege fest, welche Teams es über Bots und Chat nutzen dürfen." actions={Boolean(spaces?.length) && <Link href="/knowledge/new" className={buttonVariants()}><Plus size={17} />Wissensbereich anlegen</Link>}>
    {error && <Notice error action={load}>{error}</Notice>}
    {spaces === null && !error ? <Notice>Wissensbereiche werden geladen …</Notice> : spaces?.length ? <><label className="portal-search">Wissensbereiche finden<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Nach Name oder Beschreibung suchen" /></label><div className="portal-space-grid">{visible?.map(space => <article className="portal-panel portal-space-card" key={space.collection_id}><BookOpen size={25} aria-hidden="true" /><h2><Link href={`/knowledge/${space.collection_id}`}>{space.name}</Link></h2><p>{space.description || 'Dokumente und Quellen zu einem gemeinsamen Thema.'}</p><div className="portal-audience"><Users size={16} />{space.read_teams.length ? space.read_teams.join(', ') : 'Alle angemeldeten Teams'}</div><Link href={`/knowledge/${space.collection_id}`}>Wissensbereich öffnen <ArrowRight size={16} /></Link></article>)}</div>{visible?.length === 0 && <EmptyState title="Kein passender Wissensbereich">Versuche einen anderen Suchbegriff.</EmptyState>}</> : !error && <EmptyState title="Womit möchtest du beginnen?" href="/knowledge/new" action="Wissensbereich anlegen">Lege deinen ersten Wissensbereich an. Danach kannst du Dateien und Confluence-Seiten hinzufügen.</EmptyState>}
  </PortalPage>;
}

export function KnowledgeDetail({ id }: { id: string }) {
  const [space, setSpace] = useState<KnowledgeSpace | null>(null);
  const [documents, setDocuments] = useState<DocumentPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [downloadError, setDownloadError] = useState('');
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const load = useCallback(() => Promise.all([apiJson<KnowledgeSpace>(`/api/v1/collections/${encodeURIComponent(id)}`), loadDocuments(id, offset)])
    .then(([area, docs]) => { setSpace(area); setDocuments(docs); setError(''); })
    .catch(err => setError(portalError(err))), [id, offset]);
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
  return <PortalPage title={space?.name || 'Wissensbereich'} description={space?.description || 'Quellen hinzufügen, Inhalte prüfen und den nächsten Schritt im Blick behalten.'} eyebrow="DEINE WISSENSBASIS">
    <Link className="portal-back" href="/knowledge">← Alle Wissensbereiche</Link>
    {error && <Notice error action={load}>{error}</Notice>}
    {downloadError && <Notice error>{downloadError}</Notice>}
    {space && <div className="portal-context-bar"><span><Users size={17} />Berechtigte: {space.read_teams.length ? space.read_teams.join(', ') : 'Alle angemeldeten Teams'}</span><span>Veröffentlichung nach manueller Freigabe</span></div>}
    <section className="portal-panel"><div className="portal-section-heading"><div><p className="portal-eyebrow">INHALTE</p><h2>Dokumente{documents ? ` · ${documents.total}` : ''}</h2></div><div className="flex flex-wrap items-center gap-2">{Boolean(documents?.total) && <Button variant="outline" size="sm" disabled={downloadingId !== null} onClick={() => void downloadAll()}><Archive size={15} />{downloadingId === 'collection' ? 'ZIP wird erstellt …' : 'Alle als ZIP'}</Button>}{space?.can_manage && Boolean(documents?.total) && <Link href={`/sources/new?collection=${encodeURIComponent(id)}`} className={buttonVariants({ variant: 'outline', size: 'sm' })}><Plus size={15} />Quelle hinzufügen</Link>}<Button variant="ghost" size="sm" onClick={load}>Aktualisieren</Button></div></div>
      {!documents && !error ? <p className="portal-loading" role="status">Dokumente werden geladen …</p> : documents?.items.length ? <><DocumentTable documents={documents.items} onDownloadMarkdown={document => void downloadDocument(document)} downloadingId={downloadingId} /><Pagination offset={offset} total={documents.total} onChange={value => { setDocuments(null); setOffset(value); }} /></> : !error && <EmptyState title="Hier ist Platz für dein Wissen" href={space?.can_manage ? `/sources/new?collection=${encodeURIComponent(id)}` : undefined} action={space?.can_manage ? "Quelle hinzufügen" : undefined}>Die Eigentümer des Wissensbereichs können eine Quelle hinzufügen. Verbinde eine Confluence-Seite oder lade Dokumente hoch. Nach der Verarbeitung prüfst du den Inhalt.</EmptyState>}
    </section>
  </PortalPage>;
}
