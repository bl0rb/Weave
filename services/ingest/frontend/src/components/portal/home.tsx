'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, BookOpen, CheckCheck, MessageSquare, ShieldCheck } from 'lucide-react';
import { useAuth } from '@/lib/auth-context';
import { apiJson } from '@/lib/api';
import { loadDocuments, portalError, type DocumentPage, type KnowledgeSpace } from '@/lib/portal';
import { DocumentTable, EmptyState, Notice, PortalPage } from './shared';

export function PortalHome() {
  const { user } = useAuth();
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [documents, setDocuments] = useState<DocumentPage | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => Promise.all([apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections'), loadDocuments()])
    .then(([areas, docs]) => { setSpaces(areas.items); setDocuments(docs); setError(''); })
    .catch(err => setError(portalError(err))), []);
  useEffect(() => { void load(); }, [load]);
  return <PortalPage title="Dein Wissen. Bereit für KI." description={`Willkommen${user ? `, ${user.username}` : ''}. Hier wird aus Dokumenten und Confluence verlässliches Wissen für eure Assistenten.`}>
    <section className="portal-journey" aria-label="So kommt dein Wissen in die KI">{[['01', 'Wissensbereich anlegen', 'Thema und Berechtigte festlegen.', '/knowledge/new'], ['02', 'Quellen verbinden', 'Dateien oder Confluence hinzufügen.', '/sources/new'], ['03', 'Prüfen und freigeben', 'Du entscheidest, was verwendet wird.', '/reviews']].map(([step, title, text, href]) => <Link key={step} href={href}><span className="portal-step">{step}</span><div><h2>{title}</h2><p>{text}</p></div><ArrowRight size={17} aria-hidden="true" /></Link>)}</section>
    {error && <Notice error action={load}>{error}</Notice>}
    <div className="portal-home-grid"><section className="portal-panel"><div className="portal-section-heading"><div><p className="portal-eyebrow">DEINE WISSENSBASIS</p><h2>Wissensbereiche</h2></div><Link href="/knowledge">Alle ansehen <ArrowRight size={15} /></Link></div>
      {!documents && !error ? <p role="status" className="portal-loading">Deine Wissensbereiche werden geladen …</p> : spaces.length ? <div className="portal-space-list">{spaces.slice(0, 4).map(space => <Link href={`/knowledge/${space.collection_id}`} key={space.collection_id}><span className="portal-space-icon"><BookOpen size={21} /></span><span><strong>{space.name}</strong><small>{space.description || 'Dokumente und Quellen zu einem gemeinsamen Thema'}</small></span><ArrowRight size={16} /></Link>)}</div> : <EmptyState title="Ein gemeinsamer Ort für euer Wissen" href="/knowledge/new" action="Wissensbereich anlegen">Beginne mit einem Thema, etwa Personalwissen, Service oder Projektunterlagen.</EmptyState>}
    </section><aside className="portal-assurance"><ShieldCheck size={29} aria-hidden="true" /><h2>Du behältst die Kontrolle.</h2><p>Neue Inhalte werden zuerst verarbeitet. Erst nach deiner Freigabe wird der geprüfte Stand zur Indexierung übergeben.</p><div className="portal-assurance-rule" /><CheckCheck size={18} aria-hidden="true" /><p>Eine Freigabe ist noch keine Bestätigung, dass die Indexierung abgeschlossen ist.</p><Link href="/chat"><MessageSquare size={17} />Zum Chat <ArrowRight size={16} /></Link></aside></div>
    <section className="portal-panel"><div className="portal-section-heading"><div><p className="portal-eyebrow">IM BLICK BEHALTEN</p><h2>Zuletzt hinzugefügt</h2></div><Link href="/processing">Verarbeitung ansehen <ArrowRight size={15} /></Link></div>{!documents && !error ? <p role="status" className="portal-loading">Dokumente werden geladen …</p> : documents?.items.length ? <DocumentTable documents={documents.items.slice(0, 6)} /> : <EmptyState title="Deine Quellen machen den Anfang" href="/sources/new" action="Erste Quelle hinzufügen">Sobald du Dateien oder Confluence-Seiten einem Wissensbereich zuordnest, erscheinen sie hier.</EmptyState>}</section>
  </PortalPage>;
}
