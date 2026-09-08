'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { CheckCheck, RefreshCw, ShieldCheck } from 'lucide-react';
import { ApiError, apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { MarkdownView } from '@/components/markdown/markdown-view';
import { dateLabel, documentState, jsonBody, loadDocuments, portalError, type DocumentPage, type DocumentPreview, type PortalConfig, type Publication } from '@/lib/portal';
import { DocumentTable, EmptyState, Notice, Pagination, PortalPage } from './shared';
import { ReprocessForm } from './reprocess-form';
import { IndexingProgress } from './indexing-progress';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { currentReleaseStatus } from '@/lib/indexing-status';

export function ReviewInbox() {
  const [documents, setDocuments] = useState<DocumentPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [reviewOnly, setReviewOnly] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(() => loadDocuments(undefined, offset, reviewOnly)
    .then(docs => { setDocuments(docs); setError(''); })
    .catch(err => setError(portalError(err))), [offset, reviewOnly]);
  useEffect(() => { void load(); }, [load]);
  return <PortalPage title="Prüfen und freigeben" description="Lies den verarbeiteten Stand und entscheide, welche Inhalte euren Assistenten zur Verfügung stehen sollen." actions={<Button variant="outline" onClick={load}>Aktualisieren</Button>}>
    <div className="portal-filter-row" role="group" aria-label="Dokumentauswahl">{[true, false].map(value => <button key={String(value)} aria-pressed={value === reviewOnly} onClick={() => { setReviewOnly(value); setOffset(0); setDocuments(null); }}>{value ? 'Zur Prüfung' : 'Alle Dokumente'}</button>)}</div>
    {error && <Notice error action={load}>{error}</Notice>}
    <section className="portal-panel">{!documents && !error ? <p role="status" className="portal-loading">Dokumente werden geladen …</p> : documents?.items.length ? <><DocumentTable documents={documents.items} /><Pagination offset={offset} total={documents.total} onChange={value => { setDocuments(null); setOffset(value); }} /></> : !error && <EmptyState title={reviewOnly ? 'Aktuell gibt es nichts zu prüfen' : 'Noch keine zugeordneten Dokumente'} href="/sources/new" action="Quelle hinzufügen">{reviewOnly ? 'Verarbeitete Dokumente erscheinen hier, sobald sie für eine Prüfung bereitstehen.' : 'Ordne eine Quelle einem Wissensbereich zu, damit sie hier erscheint.'}</EmptyState>}</section>
  </PortalPage>;
}

export function ReviewDocument({ id }: { id: string }) {
  return <ReviewDocumentContent key={id} id={id} />;
}

function ReviewDocumentContent({ id }: { id: string }) {
  const [preview, setPreview] = useState<DocumentPreview | null>(null);
  const [config, setConfig] = useState<PortalConfig | null>(null);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [reprocessStarted, setReprocessStarted] = useState(false);
  const startedHeading = useRef<HTMLHeadingElement>(null);
  const load = useCallback(() => Promise.all([apiJson<DocumentPreview>(`/api/v1/portal/documents/${encodeURIComponent(id)}`), apiJson<PortalConfig>('/api/v1/portal/config')])
    .then(([content, configuration]) => { setPreview(content); setConfig(configuration); setError(''); setConfirmed(false); setReprocessOpen(false); })
    .catch(err => { setPreview(null); setError(portalError(err)); }), [id]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (reprocessStarted) startedHeading.current?.focus(); }, [reprocessStarted]);
  async function release() {
    if (saving || reprocessOpen || reprocessStarted || !preview || !confirmed || !preview.can_release || !config?.publication_configured) return;
    setSaving(true); setError('');
    try {
      const publication = await apiJson<Publication>(`/api/v1/portal/documents/${encodeURIComponent(id)}/release`, jsonBody({ markdown_sha256: preview.markdown_sha256, ...(preview.quality_grade?.toUpperCase() === 'C' ? { accept_quality_warning: true } : {}) }));
      setPreview({ ...preview, release: publication }); setConfirmed(false);
    } catch (err) { setConfirmed(false); setError(portalError(err)); } finally { setSaving(false); }
  }
  async function retry() {
    if (saving || !preview?.release) return;
    setSaving(true); setError('');
    try { const publication = await apiJson<Publication>(`/api/v1/portal/releases/${preview.release.id}/retry`, { method: 'POST' }); setPreview({ ...preview, release: publication }); }
    catch (err) { setError(portalError(err)); } finally { setSaving(false); }
  }
  async function reprocess(profileId: string) {
    if (saving || reprocessStarted || !preview?.can_reprocess || preview.release) return;
    setSaving(true); setConfirmed(false); setError('');
    try {
      await apiJson(`/api/v1/portal/documents/${encodeURIComponent(id)}/reprocess`, jsonBody({
        profile_id: profileId, markdown_sha256: preview.markdown_sha256,
      }));
      // The previous preview must disappear as soon as its replacement is queued.
      setReprocessStarted(true); setReprocessOpen(false);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409
        ? 'Dieser Stand kann nicht mehr neu verarbeitet werden. Bitte lade ihn erneut; möglicherweise wurde er geändert, freigegeben oder bereits gestartet.'
        : portalError(err));
    } finally { setSaving(false); }
  }
  const { items: indexingItems } = useIndexingStatus(preview?.release ? [id] : []);
  const live = indexingItems[id];
  const state = preview && documentState(preview, live);
  const delivery = preview?.release ? currentReleaseStatus(preview.release, live).delivery : null;
  return <PortalPage title={preview?.original_filename || 'Dokument prüfen'} description="Prüfe Inhalt, Verständlichkeit und Berechtigte vor der Freigabe." eyebrow="VERÖFFENTLICHUNG" actions={!reprocessStarted && <Button variant="outline" disabled={saving} onClick={load}>Stand neu laden</Button>}>
    <Link className="portal-back" href="/reviews">← Zurück zur Prüfung</Link>
    {error && <Notice error action={load}>{error}</Notice>}
    {!preview && !error && <Notice>Der verarbeitete Stand wird geladen …</Notice>}
    {reprocessStarted && <section className="portal-panel portal-form-panel" aria-labelledby="reprocess-started-title">
      <RefreshCw size={26} aria-hidden="true" className="mb-4" />
      <h2 id="reprocess-started-title" tabIndex={-1} ref={startedHeading}>Erneute Verarbeitung gestartet</h2>
      <p className="mt-4">Das Dokument wird mit dem gewählten Profil neu aufbereitet. Je nach Umfang und Auslastung kann das einige Minuten dauern.</p>
      <p className="mt-3">Du kannst diese Seite verlassen. Prüfe das neue Ergebnis anschließend unter „Prüfen & freigeben“.</p>
      <div className="portal-form-actions"><Link href="/processing" className={buttonVariants()}>Verarbeitung ansehen</Link><Link href="/reviews" className={buttonVariants({ variant: 'ghost' })}>Zurück zur Prüfung</Link></div>
    </section>}
    {preview && !reprocessStarted && <><div className="portal-context-bar"><Link href={`/knowledge/${preview.collection_id}`}>{preview.collection_name}</Link><span aria-live="polite" className={`portal-badge portal-badge-${state?.tone}`}>{state?.label}</span></div>
      <div className="portal-review-grid"><article className="portal-panel portal-preview"><h2>{preview.release ? 'Freigegebener Stand' : 'Verarbeiteter Inhalt'}</h2><p className="portal-field-hint">Dies ist der Stand, den du freigibst. Nachträgliche Änderungen verändern eine bestehende Freigabe nicht.</p><MarkdownView markdown={preview.markdown} jobId={preview.id} /></article>
      <aside className="portal-panel portal-release-panel"><ShieldCheck size={27} /><h2>{reprocessOpen ? 'Erneut verarbeiten' : 'Freigabe'}</h2>
        <dl><dt>Qualitätsbewertung</dt><dd>{preview.quality_grade ? `Stufe ${preview.quality_grade}` : 'Keine automatische Bewertung'}</dd><dt>Hinzugefügt</dt><dd>{dateLabel(preview.created_at)}</dd></dl>
        {preview.quality_grade?.toUpperCase() === 'C' ? <Notice>Stufe C: Die automatische Prüfung meldet Qualitätsmängel. Eine bewusste Freigabe nach inhaltlicher Prüfung ist möglich.</Notice> : preview.quality_recommendation === 'block' && <Notice error>Die Qualitätsprüfung blockiert diesen Stand. Bitte korrigiere oder verarbeite das Dokument erneut.</Notice>}
        {(!preview.quality_recommendation || preview.quality_recommendation === 'warn') && <Notice>Bitte prüfe diesen Inhalt besonders sorgfältig. Die automatische Bewertung liefert keine uneingeschränkte Empfehlung.</Notice>}
        {preview.release ? <><div className="portal-release-receipt"><CheckCheck size={23} /><strong>Freigabe gespeichert</strong><span>{dateLabel(preview.release.created_at)}</span></div><p>Dieser Stand ist freigegeben und bleibt unverändert.</p>{delivery === 'failed' && <><Notice error>Die Übergabe ist fehlgeschlagen. Eine berechtigte Person kann sie erneut anstoßen.</Notice>{preview.can_release && <Button disabled={saving} onClick={retry}>Übergabe erneut versuchen</Button>}</>}<IndexingProgress release={preview.release} live={live} /></> : reprocessOpen ? <ReprocessForm currentProfileId={preview.profile_id} busy={saving} onSubmit={reprocess} onCancel={() => { setReprocessOpen(false); setError(''); }} /> : <>
          {preview.can_reprocess && <Button className="w-full whitespace-normal h-auto py-3" variant="outline" disabled={saving} onClick={() => { setReprocessOpen(true); setConfirmed(false); setError(''); }}>Mit anderem Profil neu verarbeiten</Button>}
          {!config?.publication_configured && <Notice>Die Administration muss die Verbindung zur Wissensindexierung noch einrichten.</Notice>}
          {!preview.can_release && preview.quality_recommendation !== 'block' && <p className="portal-field-hint">Freigeben können der Dokument- oder Wissensbereichseigentümer sowie Administratoren. Geschützte Inhalte und Seiten aus noch nicht vollständig abgeschlossenen Importen lassen sich hier nicht freigeben.</p>}
          <label className="portal-choice portal-approval"><input type="checkbox" checked={confirmed} disabled={saving || !preview.can_release || !config?.publication_configured} onChange={event => setConfirmed(event.target.checked)} />{preview.quality_grade?.toUpperCase() === 'C' ? 'Ich habe den Inhalt geprüft und gebe ihn trotz Qualitätsstufe C für die Berechtigten des Wissensbereichs frei.' : 'Ich habe den Inhalt geprüft und möchte diesen Stand für die Berechtigten des Wissensbereichs freigeben.'}</label>
          <Button className="w-full" disabled={saving || !confirmed || !preview.can_release || !config?.publication_configured} onClick={release}>{saving ? 'Freigabe wird gespeichert …' : 'Geprüften Stand freigeben'}</Button>
          <Link href="/reviews" className="portal-defer">Später prüfen</Link>
        </>}
      </aside></div></>}
  </PortalPage>;
}
