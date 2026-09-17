'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Ban, CheckCheck, Download, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { ApiError, apiJson } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { Button, buttonVariants } from '@/components/ui/button';
import { ConfirmDialog, apiSend } from '@/components/admin/admin-shared';
import { MarkdownView } from '@/components/markdown/markdown-view';
import { bulkPortalAction, dateLabel, documentState, downloadPortalFile, jsonBody, loadDocuments, markdownDownloadName, portalError, skipPortalDocument, unskipPortalDocument, type DocumentPage, type DocumentPreview, type PortalConfig, type QualityGradeFilter, type Publication, type ReviewStateFilter } from '@/lib/portal';
import { BulkActionBar, DocumentTable, EmptyState, Notice, Pagination, PortalPage, QualityGradeFilterRow, QualityGradeLegend } from './shared';
import { ReprocessForm } from './reprocess-form';
import { IndexingProgress } from './indexing-progress';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { currentReleaseStatus } from '@/lib/indexing-status';

const qualityMissingReasonLabels: Record<string, string> = {
  not_finished: 'Die Verarbeitung ist noch nicht abgeschlossen.',
  failed: 'Die Verarbeitung ist fehlgeschlagen.',
  legacy: 'Das Dokument wurde verarbeitet, bevor es die automatische Qualitätsprüfung gab.',
  import_without_gate: 'Confluence-Importe durchlaufen keine automatische Qualitätsprüfung.',
  unknown: 'Der Grund ist nicht bekannt.',
};
function qualityMissingReasonText(reason: string | null): string {
  return (reason && qualityMissingReasonLabels[reason]) || qualityMissingReasonLabels.unknown;
}

const reviewStateChips: Array<{ value: ReviewStateFilter; label: string }> = [
  { value: 'review', label: 'Zur Prüfung' },
  { value: 'all', label: 'Alle Dokumente' },
  { value: 'skipped', label: 'Übersprungen' },
];

export function ReviewInbox() {
  const [documents, setDocuments] = useState<DocumentPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [reviewState, setReviewState] = useState<ReviewStateFilter>('review');
  const [qualityGrade, setQualityGrade] = useState<QualityGradeFilter>('');
  const [error, setError] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [acceptQualityWarnings, setAcceptQualityWarnings] = useState(false);
  const [releaseConfirmed, setReleaseConfirmed] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [notice, setNotice] = useState('');
  const load = useCallback(() => loadDocuments(undefined, offset, reviewState, qualityGrade || undefined)
    .then(docs => { setDocuments(docs); setError(''); })
    .catch(err => setError(portalError(err))), [offset, reviewState, qualityGrade]);
  useEffect(() => { void load(); }, [load]);
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
  return <PortalPage title="Prüfen und freigeben" description="Lies den verarbeiteten Stand und entscheide, welche Inhalte euren Assistenten zur Verfügung stehen sollen." actions={<Button variant="outline" onClick={load}>Aktualisieren</Button>}>
    <div className="portal-filter-row" role="group" aria-label="Dokumentauswahl">{reviewStateChips.map(chip => <button key={chip.value} aria-pressed={chip.value === reviewState} onClick={() => { setReviewState(chip.value); setOffset(0); setDocuments(null); clearSelection(); }}>{chip.label}</button>)}</div>
    <QualityGradeFilterRow value={qualityGrade} onChange={value => { setQualityGrade(value); setOffset(0); setDocuments(null); }} />
    <QualityGradeLegend />
    {error && <Notice error action={load}>{error}</Notice>}
    {notice && <Notice>{notice}</Notice>}
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
    <section className="portal-panel">{!documents && !error ? <p role="status" className="portal-loading">Dokumente werden geladen …</p> : documents?.items.length ? <><DocumentTable documents={documents.items} selectedIds={selectedIds} onToggle={id => setSelectedIds(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; })} onToggleAll={checked => setSelectedIds(checked ? new Set(documents.items.map(document => document.id)) : new Set())} /><Pagination offset={offset} total={documents.total} onChange={value => { setDocuments(null); setOffset(value); }} /></> : !error && <EmptyState title={reviewState === 'review' ? 'Aktuell gibt es nichts zu prüfen' : reviewState === 'skipped' ? 'Keine übersprungenen Dokumente' : 'Noch keine zugeordneten Dokumente'} href="/sources/new" action="Quelle hinzufügen">{reviewState === 'review' ? 'Verarbeitete Dokumente erscheinen hier, sobald sie für eine Prüfung bereitstehen.' : 'Ordne eine Quelle einem Wissensbereich zu, damit sie hier erscheint.'}</EmptyState>}</section>
    {bulkDeleting && <ConfirmDialog title="Dokumente löschen" body={<p>{selectedIds.size} Dokument{selectedIds.size === 1 ? '' : 'e'} unwiderruflich löschen?</p>} confirmLabel="Dokumente löschen" onClose={() => setBulkDeleting(false)} onConfirm={() => runBulk('delete')} />}
  </PortalPage>;
}

export function ReviewDocument({ id }: { id: string }) {
  return <ReviewDocumentContent key={id} id={id} />;
}

function ReviewDocumentContent({ id }: { id: string }) {
  const router = useRouter();
  const { user } = useAuth();
  const [preview, setPreview] = useState<DocumentPreview | null>(null);
  const [config, setConfig] = useState<PortalConfig | null>(null);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [reprocessStarted, setReprocessStarted] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [downloadingDiagnostics, setDownloadingDiagnostics] = useState(false);
  const [skipping, setSkipping] = useState(false);
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
  async function skip() {
    if (skipping || !preview) return;
    setSkipping(true); setError('');
    try { const updated = await skipPortalDocument(id); setPreview({ ...preview, review_decision: updated.review_decision }); }
    catch (err) { setError(portalError(err)); } finally { setSkipping(false); }
  }
  async function unskip() {
    if (skipping || !preview) return;
    setSkipping(true); setError('');
    try { const updated = await unskipPortalDocument(id); setPreview({ ...preview, review_decision: updated.review_decision }); }
    catch (err) { setError(portalError(err)); } finally { setSkipping(false); }
  }
  async function downloadDiagnostics() {
    if (downloadingDiagnostics || !preview?.release || user?.role !== 'admin') return;
    setDownloadingDiagnostics(true); setError('');
    try {
      const filename = `${markdownDownloadName(preview.original_filename).slice(0, -3)}-indexing-diagnostics.json`;
      await downloadPortalFile(`/api/v1/portal/documents/${encodeURIComponent(id)}/indexing-diagnostics`, filename);
    } catch (err) { setError(portalError(err)); } finally { setDownloadingDiagnostics(false); }
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
        <dl><dt>Qualitätsbewertung</dt><dd>{preview.quality_grade ? `Stufe ${preview.quality_grade}` : 'Keine automatische Bewertung'}</dd><dt>Herkunft</dt><dd>{preview.source?.url ? <a href={preview.source.url} target="_blank" rel="noopener noreferrer">{preview.source.label}</a> : preview.source?.path ? `${preview.source.label}: ${preview.source.path}` : preview.source?.label}</dd><dt>Hinzugefügt</dt><dd>{dateLabel(preview.created_at)}</dd></dl>
        <details className="portal-quality-reason"><summary>{preview.quality_grade ? `Warum Stufe ${preview.quality_grade}?` : 'Warum keine Bewertung?'}</summary>
          {preview.quality ? <>
            <ul>
              <li>OCR-Konfidenz: {preview.quality.signals.ocr_confidence != null ? `${Math.round(preview.quality.signals.ocr_confidence * 100)} % (Stichprobe: ${preview.quality.signals.confidence_sample_size.toLocaleString('de-DE')} Werte)` : 'nicht gemessen'}</li>
              <li>Strukturqualität: {Math.round(preview.quality.signals.structure_quality * 100)} %</li>
              <li>Textqualität: {Math.round(preview.quality.signals.text_quality * 100)} % (Rauschen {Math.round(preview.quality.signals.noise_penalty * 100)} %)</li>
              <li>Feldprüfung: {preview.quality.issues.length} Auffälligkeiten{preview.quality.issues.length > 0 ? `: ${preview.quality.issues.join(', ')}` : ''}</li>
            </ul>
            <p>Schwellenwerte: A ab {Math.round(preview.quality.thresholds.A * 100)} %, B ab {Math.round(preview.quality.thresholds.B * 100)} %, sonst C.</p>
            <p>Gesamtwert: {preview.quality.score != null ? `${Math.round(preview.quality.score * 100)} %` : 'unbekannt'}</p>
          </> : <p>Keine automatische Bewertung – {qualityMissingReasonText(preview.quality_missing_reason)}</p>}
          <QualityGradeLegend />
        </details>
        {preview.quality_grade?.trim().toUpperCase() === 'C' ? <Notice>Stufe C: Die automatische Prüfung meldet Qualitätsmängel. Eine bewusste Freigabe nach inhaltlicher Prüfung ist möglich.</Notice> : preview.quality_recommendation?.trim().toLowerCase() === 'block' && <Notice error>Die Qualitätsprüfung blockiert diesen Stand. Bitte korrigiere oder verarbeite das Dokument erneut.</Notice>}
        {(!preview.quality_recommendation || preview.quality_recommendation.trim().toLowerCase() === 'warn') && <Notice>Bitte prüfe diesen Inhalt besonders sorgfältig. Die automatische Bewertung liefert keine uneingeschränkte Empfehlung.</Notice>}
        {preview.release ? <><div className="portal-release-receipt"><CheckCheck size={23} /><strong>Freigabe gespeichert</strong><span>{dateLabel(preview.release.created_at)}</span>{preview.release.released_by && <span>Freigegeben von {preview.release.released_by}</span>}</div><p>Dieser Stand ist freigegeben und bleibt unverändert.</p>{delivery === 'failed' && <><Notice error>Die Übergabe ist fehlgeschlagen. Eine berechtigte Person kann sie erneut anstoßen.</Notice>{preview.can_release && <Button disabled={saving} onClick={retry}>Übergabe erneut versuchen</Button>}</>}<IndexingProgress release={preview.release} live={live} />{user?.role === 'admin' && <Button className="w-full whitespace-normal h-auto py-3" variant="outline" disabled={downloadingDiagnostics} onClick={() => void downloadDiagnostics()}><Download size={16} />{downloadingDiagnostics ? 'Diagnose wird heruntergeladen …' : 'Indizierungsdiagnose herunterladen'}</Button>}</> : preview.review_decision === 'skipped' ? <>
          <p className="portal-field-hint">Dieses Dokument wurde nicht freigegeben und übersprungen. Es erscheint nicht in der Liste „Zur Prüfung“.</p>
          <Button className="w-full" variant="outline" disabled={skipping} onClick={() => void unskip()}>{skipping ? 'Wird aktualisiert …' : 'Wieder zur Prüfung'}</Button>
        </> : reprocessOpen ? <ReprocessForm currentProfileId={preview.profile_id} busy={saving} onSubmit={reprocess} onCancel={() => { setReprocessOpen(false); setError(''); }} /> : <>
          {preview.can_reprocess && <Button className="w-full whitespace-normal h-auto py-3" variant="outline" disabled={saving} onClick={() => { setReprocessOpen(true); setConfirmed(false); setError(''); }}>Erneut prüfen</Button>}
          {!preview.release && preview.can_release && <Button className="w-full" variant="outline" disabled={skipping} onClick={() => void skip()}><Ban size={16} />{skipping ? 'Wird gespeichert …' : 'Nicht freigeben / überspringen'}</Button>}
          {!deleting && <Button className="w-full" variant="outline" disabled={saving} onClick={() => setDeleting(true)}><Trash2 size={16} />Dokument löschen</Button>}
          {!config?.publication_configured && <Notice>Die Administration muss die Verbindung zur Wissensindexierung noch einrichten.</Notice>}
          {!preview.can_release && preview.quality_recommendation !== 'block' && <p className="portal-field-hint">Freigeben können der Dokument- oder Wissensbereichseigentümer sowie Administratoren. Geschützte Inhalte und Seiten aus noch nicht vollständig abgeschlossenen Importen lassen sich hier nicht freigeben.</p>}
          <label className="portal-choice portal-approval"><input type="checkbox" checked={confirmed} disabled={saving || !preview.can_release || !config?.publication_configured} onChange={event => setConfirmed(event.target.checked)} />{preview.quality_grade?.toUpperCase() === 'C' ? 'Ich habe den Inhalt geprüft und gebe ihn trotz Qualitätsstufe C für die Berechtigten des Wissensbereichs frei.' : 'Ich habe den Inhalt geprüft und möchte diesen Stand für die Berechtigten des Wissensbereichs freigeben.'}</label>
          <Button className="w-full" disabled={saving || !confirmed || !preview.can_release || !config?.publication_configured} onClick={release}>{saving ? 'Freigabe wird gespeichert …' : 'Geprüften Stand freigeben'}</Button>
          <Link href="/reviews" className="portal-defer">Später prüfen</Link>
        </>}
      </aside></div></>}
    {deleting && preview && <ConfirmDialog title="Dokument löschen" body={<p>Das Dokument <strong className="text-slate-950">{preview.original_filename}</strong> unwiderruflich löschen?</p>} confirmLabel="Dokument löschen" onClose={() => setDeleting(false)} onConfirm={async () => { await apiSend(`/api/v1/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }); router.push('/reviews'); router.refresh(); }} />}
  </PortalPage>;
}
