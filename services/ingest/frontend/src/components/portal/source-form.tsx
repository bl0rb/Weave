'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowRight, CheckCheck, Clock3, FileUp, Globe, Plus } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { jsonBody, portalError, type KnowledgeSpace } from '@/lib/portal';
import type { ImportSource, ImportRun } from '@/lib/imports';
import { portalProfiles, type PortalProfile, type ProcessingProfile } from '@/lib/portal-profiles';
import { EmptyState, Notice, PortalPage } from './shared';

type UploadResult = { name: string; id?: string; ok: boolean; message: string };

export function SourceForm({ initialCollection = '' }: { initialCollection?: string }) {
  const [spaces, setSpaces] = useState<KnowledgeSpace[] | null>(null);
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [collectionId, setCollectionId] = useState(initialCollection);
  const [kind, setKind] = useState<'files' | 'confluence'>('files');
  const [files, setFiles] = useState<File[]>([]);
  const [sourceId, setSourceId] = useState('');
  const [profiles, setProfiles] = useState<PortalProfile[] | null>(null);
  const [profileId, setProfileId] = useState('');
  const [profileError, setProfileError] = useState('');
  const selectedProfile = profiles?.find(profile => profile.value === profileId);
  const [pageUrl, setPageUrl] = useState('');
  const [automatic, setAutomatic] = useState(false);
  const [confirmedAccess, setConfirmedAccess] = useState(false);
  const [saving, setSaving] = useState(false);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [results, setResults] = useState<UploadResult[]>([]);
  const [run, setRun] = useState<ImportRun | null>(null);
  const completed = !saving && (results.length > 0 || run !== null);
  const completedHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (completed) completedHeading.current?.focus(); }, [completed]);
  function addMore() {
    setResults([]); setRun(null); setFiles([]); setError(''); setSourceError(''); setProgress('');
  }
  const load = useCallback(async () => {
    setError('');
    try {
      const data = await apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections');
      setSpaces(data.items.filter(space => space.can_upload ?? space.can_manage));
    } catch (err) { setError(portalError(err)); }
  }, []);
  const loadSources = useCallback(async () => {
    setSourceError('');
    try { const data = await apiJson<{ items: ImportSource[] }>('/api/v1/import/sources'); setSources(data.items); }
    catch (err) { setSourceError(portalError(err)); }
  }, []);
  const loadProfiles = useCallback(async () => {
    setProfileError('');
    try {
      const [capabilities, settings] = await Promise.all([
        apiJson<{ profiles: ProcessingProfile[] }>('/api/v1/paddle/capabilities'),
        apiJson<{ default_profile: string }>('/api/v1/paddle/settings'),
      ]);
      const choices = portalProfiles(capabilities.profiles);
      setProfiles(choices);
      setProfileId(current => choices.some(profile => profile.value === current) ? current
        : choices.find(profile => profile.value === settings.default_profile)?.value || choices[0]?.value || '');
    } catch (err) { setProfileError(portalError(err)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadProfiles(); }, [loadProfiles]);
  useEffect(() => { if (kind === 'confluence') void loadSources(); }, [kind, loadSources]);
  const selected = spaces?.find(space => space.collection_id === collectionId);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected || !selectedProfile || saving || (kind === 'confluence' && !confirmedAccess)) return;
    setSaving(true); setError(''); setResults([]);
    try {
      if (kind === 'files') {
        for (let index = 0; index < files.length; index++) {
          const file = files[index];
          setProgress(`${index + 1} von ${files.length}: ${file.name}`);
          let jobId: string | undefined;
          try {
            const body = new FormData(); body.append('file', file);
            const upload = await apiJson<{ job_id: string }>(`/api/v1/collections/${encodeURIComponent(collectionId)}/upload`, { method: 'POST', body });
            jobId = upload.job_id;
            // Start only the newly uploaded document, never reprocess the
            // other (possibly already approved) files in this collection.
            await apiJson(`/api/v1/jobs/${upload.job_id}/restart`, jsonBody({ profile_id: selectedProfile.value }));
            setResults(current => [...current, { name: file.name, id: jobId, ok: true, message: 'Hochgeladen und zur Verarbeitung übergeben' }]);
          } catch (err) {
            setResults(current => [...current, { name: file.name, id: jobId, ok: false, message: jobId ? 'Hochgeladen. Die Verarbeitung konnte nicht gestartet werden; bitte öffne das Dokument.' : portalError(err) }]);
          }
        }
        setFiles([]);
      } else {
        const created = await apiJson<ImportRun>('/api/v1/import/runs', jsonBody({ source_id: sourceId, scope: { type: 'page', value: pageUrl.trim() }, options: { collection_id: collectionId, include_attachments: true, ocr_attachments: true, ocr_profile_id: selectedProfile.value } }));
        setRun(created);
        if (automatic) {
          try { await apiJson(`/api/v1/import/sources/${encodeURIComponent(sourceId)}`, { ...jsonBody({ refresh_enabled: true, refresh_interval_seconds: 86400 }), method: 'PATCH' }); }
          catch { setError('Der Import wurde gestartet. Die tägliche Aktualisierung konnte nicht aktiviert werden. Bitte prüfe die Verbindungseinstellungen.'); }
        }
      }
    } catch (err) { setError(portalError(err)); }
    finally { setSaving(false); setProgress(''); }
  }
  return <PortalPage title="Eine Quelle hinzufügen" description="Bringe Dokumente oder Confluence-Seiten in einen Wissensbereich. Die Veröffentlichung entscheidest du nach der Verarbeitung." eyebrow="SCHRITT 2 VON 3">
    {error && <Notice error>{error}</Notice>}
    {spaces === null && !error && <Notice>Wissensbereiche werden geladen …</Notice>}
    {spaces?.length === 0 && <EmptyState title="Lege deinen eigenen Wissensbereich an" href="/knowledge/new" action="Wissensbereich anlegen">Quellen hinzufügen können die Eigentümer eines Wissensbereichs und Administratoren. Leserechte allein reichen dafür nicht aus.</EmptyState>}
    {Boolean(spaces?.length) && !completed && <form className="portal-panel portal-source-form" onSubmit={submit}>
      <fieldset disabled={saving || Boolean(run)}><legend><span className="portal-step">01</span> Wissensbereich wählen</legend><label>Wohin gehört die Quelle?<select required value={collectionId} onChange={event => setCollectionId(event.target.value)}><option value="">Bitte auswählen</option>{spaces?.map(space => <option key={space.collection_id} value={space.collection_id}>{space.name}</option>)}</select></label><Link className="portal-inline-link" href="/knowledge/new"><Plus size={14} />Neuen Wissensbereich anlegen</Link>
        {selected && <p className="portal-field-hint">Berechtigte: {selected.read_teams.length ? selected.read_teams.join(', ') : 'Alle angemeldeten Teams'}.</p>}
      </fieldset>
      <fieldset disabled={saving || Boolean(run)}><legend><span className="portal-step">02</span> Quelle auswählen</legend><div className="portal-source-types"><label className={kind === 'files' ? 'selected' : ''}><input type="radio" name="kind" value="files" checked={kind === 'files'} onChange={() => setKind('files')} /><FileUp size={23} /><span><strong>Dateien hochladen</strong><small>PDF, Office, Bilder und E-Mails (.eml)</small></span></label><label className={kind === 'confluence' ? 'selected' : ''}><input type="radio" name="kind" value="confluence" checked={kind === 'confluence'} onChange={() => setKind('confluence')} /><Globe size={23} /><span><strong>Confluence verbinden</strong><small>Eine Seite mit ihren Unterseiten</small></span></label></div>
        {kind === 'files' ? <label className="portal-upload">Dateien auswählen<input type="file" multiple required accept=".pdf,.docx,.pptx,.xlsx,.xls,.png,.jpg,.jpeg,.eml" onChange={event => setFiles(Array.from(event.target.files || []))} /><span>{files.length ? `${files.length} Datei(en) ausgewählt` : 'PDF, Office, Bilder und E-Mails (.eml). Mehrere Dateien sind möglich.'}</span><span>Bei E-Mails werden unterstützte Anhänge mitverarbeitet. Prüfe auch deren Inhalt vor der Freigabe.</span></label> : <div className="portal-form">
          {sourceError && <Notice error action={loadSources}>{sourceError}</Notice>}
          <label>Deine Confluence-Verbindung<select required value={sourceId} onChange={event => { setSourceId(event.target.value); setAutomatic(false); }}><option value="">Verbindung auswählen</option>{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label><Link className="portal-inline-link" href="/connections">Verbindung einrichten oder prüfen <ArrowRight size={14} /></Link>
          <label>Confluence-Seite<input required type="url" value={pageUrl} onChange={event => setPageUrl(event.target.value)} placeholder="https://confluence.example.com/..." /></label><p className="portal-field-hint">Unterseiten und Anhänge werden innerhalb der konfigurierten Importgrenzen übernommen.</p>
          <label className="portal-choice"><input type="checkbox" checked={automatic} onChange={event => setAutomatic(event.target.checked)} />Diese Verbindung täglich auf Änderungen prüfen</label><p className="portal-field-hint">Die Aktualisierung gilt für die ausgewählte Verbindung und verwendet deren zuletzt gestarteten Import. Neue Dokumentstände benötigen weiterhin deine Freigabe.</p>
          <label className="portal-choice portal-access-confirm"><input required type="checkbox" checked={confirmedAccess} onChange={event => setConfirmedAccess(event.target.checked)} />Ich prüfe die Berechtigten vor der Freigabe. Individuelle Confluence-Seitenrechte werden nicht automatisch übernommen.</label>
        </div>}
      </fieldset>
      <fieldset disabled={saving || Boolean(run)}>
        <legend><span className="portal-step">03</span> Verarbeitung wählen</legend>
        {profileError && <Notice error action={loadProfiles}>{profileError}</Notice>}
        {!profiles && !profileError && <Notice>Verarbeitungsprofile werden geladen …</Notice>}
        {profiles?.length === 0 && <Notice error>Es ist kein passendes Verarbeitungsprofil verfügbar. Bitte wende dich an die Administration.</Notice>}
        {Boolean(profiles?.length) && <>
          <label>Wie sollen die Dokumente aufbereitet werden?
            <select required value={profileId} onChange={event => setProfileId(event.target.value)} aria-describedby="profile-description profile-scope">
              {profiles?.some(profile => profile.kind === 'ocr') && <optgroup label="Dokumenterkennung">{profiles.filter(profile => profile.kind === 'ocr').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
              {profiles?.some(profile => profile.kind === 'vl') && <optgroup label="Eingerichtete KI-Modelle">{profiles.filter(profile => profile.kind === 'vl').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
            </select>
          </label>
          <p id="profile-description" className="portal-field-hint">{selectedProfile?.description}</p>
          <p id="profile-scope" className="portal-field-hint">{kind === 'confluence' ? 'Das Profil wird für unterstützte Confluence-Anhänge verwendet. Der Text der Seiten wird direkt übernommen.' : 'Das Profil gilt für die ausgewählten Dateien und unterstützte E-Mail-Anhänge. Nachrichtentext wird direkt übernommen.'}</p>
        </>}
      </fieldset>
      <div className="portal-source-summary"><ShieldNotice /><div className="portal-form-actions"><Button type="submit" disabled={saving || !selected || !selectedProfile || Boolean(run) || (kind === 'files' ? files.length === 0 : !sourceId || !pageUrl.trim() || !confirmedAccess)}>{saving ? 'Wird gestartet …' : kind === 'files' ? 'Hochladen und verarbeiten' : 'Import starten'}</Button><Link href="/knowledge" className={buttonVariants({ variant: 'ghost' })}>Abbrechen</Link></div></div>
    </form>}
    {progress && <Notice>{progress}</Notice>}
    {completed && <section className="portal-panel portal-form-panel max-w-[900px]" aria-labelledby="source-completed-title">
      <CheckCheck size={28} className="mb-4 text-emerald-700" aria-hidden="true" />
      <h2 id="source-completed-title" tabIndex={-1} ref={completedHeading}>
        {run ? 'Confluence-Import gestartet' : results.every(result => result.ok) ? 'Upload abgeschlossen' : results.some(result => result.ok) ? 'Ein Teil deiner Dateien wurde übergeben' : 'Die Verarbeitung konnte nicht gestartet werden'}
      </h2>
      {(run || results.some(result => result.ok)) && <>
        <p className="mt-3 text-sm leading-7 text-slate-600">{run ? 'Die Seiten werden jetzt im Hintergrund übernommen.' : `${results.filter(result => result.ok).length} ${results.filter(result => result.ok).length === 1 ? 'Datei wurde' : 'Dateien wurden'} zur Verarbeitung übergeben.`}</p>
        <p className="mt-3 flex items-start gap-3 text-sm leading-7 text-slate-600"><Clock3 size={18} className="mt-1 shrink-0" aria-hidden="true" />Je nach Umfang und Auslastung kann die Verarbeitung einige Minuten dauern. Du kannst diese Seite verlassen und den Fortschritt unter „Verarbeitung“ verfolgen.</p>
        <p className="mt-3 text-sm leading-7 text-slate-600">Anschließend prüfst du die Inhalte und gibst sie für die KI-Nutzung frei.</p>
      </>}
      {results.some(result => !result.ok) && <div className="mt-5" role="alert"><h3 className="font-semibold">Diese Dateien benötigen deine Aufmerksamkeit</h3>
        <ul className="portal-upload-results">{results.filter(result => !result.ok).map((result, index) => <li key={index}><strong>{result.name}</strong><span>{result.message}</span>{result.id && <Link href={`/jobs/${result.id}`}>Auftrag prüfen <ArrowRight size={14} aria-hidden="true" /></Link>}</li>)}</ul>
      </div>}
      <h3 className="mt-8 font-semibold">Wie möchtest du weitermachen?</h3>
      <div className="portal-form-actions">
        <Link href="/processing" className={buttonVariants()}>Verarbeitung ansehen<ArrowRight size={16} aria-hidden="true" /></Link>
        <Button type="button" variant="outline" onClick={addMore}>Weitere Quellen hinzufügen</Button>
      </div>
    </section>}
  </PortalPage>;
}

function ShieldNotice() { return <p>Der Inhalt wird zuerst verarbeitet. Für Bots und die KI-Suche wird er erst nach einer ausdrücklichen Freigabe bereitgestellt.</p>; }
