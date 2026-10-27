'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowRight, BookOpenText, CheckCheck, Clock3, FileUp, Globe, Link2, Plus } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { jsonBody, loadWritableSpaces, portalError, type KnowledgeSpace } from '@/lib/portal';
import { accessSummary } from '@/lib/access-summary';
import { hostOf, refreshIntervalOptions, sourceKind, type CrawlScope, type ImportSource, type ImportRun, type ImportSourceTestResponse } from '@/lib/imports';
import { loadSourceCatalog, type SourceCatalogEntry } from '@/lib/source-catalog';
import { useImportSourceTest } from '@/lib/use-import-source-test';
import { portalProfiles, type PortalProfile, type ProcessingProfile } from '@/lib/portal-profiles';
import { ConfluenceSetupDialog } from '@/components/connections/confluence-setup-dialog';
import { WebsiteSetupDialog } from '@/components/connections/website-setup-dialog';
import type { MessageKey } from '@/i18n/messages';
import { useI18n } from '@/i18n/provider';
import { EmptyState, Notice, PortalPage, RequiredMark } from './shared';

type UploadResult = { name: string; id?: string; ok: boolean; message: string };

/** files: upload · confluence: Confluence page/space · website: a website / intranet start page · catalog: a curated catalog entry. */
export type SourceFormKind = 'files' | 'confluence' | 'website' | 'catalog';
export const SOURCE_FORM_KINDS: SourceFormKind[] = ['files', 'confluence', 'website', 'catalog'];

const SOURCE_DRAFT_KEY = 'weave-source-form-draft';
type SourceDraft = {
  collectionId: string; kind: SourceFormKind; sourceId: string; scopeType?: 'page' | 'space'; pageUrl: string; automatic: boolean; confirmedAccess: boolean; profileId: string;
  startUrl?: string; crawlScope?: CrawlScope; linkedDocuments?: boolean; legalSource?: boolean; refreshInterval?: number; catalogEntryId?: string;
};
const CRAWL_SCOPES: CrawlScope[] = ['page', 'prefix', 'host'];

function readDraft(): SourceDraft | null {
  if (typeof window === 'undefined') return null;
  try { return JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null; } catch { return null; }
}

/** `initialKind`/`initialSourceId`/`initialCatalogEntryId` come from /sources/new?kind=…&source=…&catalog=… (e.g. right after the
 * Confluence setup in the new knowledge space flow, or from a catalog card) and win over a saved draft. */
export function SourceForm({ initialCollection = '', initialKind, initialSourceId, initialCatalogEntryId }: { initialCollection?: string; initialKind?: SourceFormKind; initialSourceId?: string; initialCatalogEntryId?: string }) {
  const { t, locale } = useI18n();
  const [spaces, setSpaces] = useState<KnowledgeSpace[] | null>(null);
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [collectionId, setCollectionId] = useState(() => {
    if (typeof window === 'undefined') return initialCollection;
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.collectionId || initialCollection; }
    catch { return initialCollection; }
  });
  const [kind, setKind] = useState<SourceFormKind>(() => {
    if (initialKind) return initialKind;
    if (typeof window === 'undefined') return 'files';
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.kind || 'files'; }
    catch { return 'files'; }
  });
  // --- Website / catalog state (draft-restored like the fields above) ---
  const [startUrl, setStartUrl] = useState(() => readDraft()?.startUrl || '');
  const [crawlScope, setCrawlScope] = useState<CrawlScope>(() => readDraft()?.crawlScope || 'prefix');
  const [linkedDocuments, setLinkedDocuments] = useState(() => readDraft()?.linkedDocuments ?? false);
  const [legalSource, setLegalSource] = useState(() => readDraft()?.legalSource ?? false);
  // 0 = manual only; otherwise the sync interval in seconds.
  const [refreshInterval, setRefreshInterval] = useState(() => readDraft()?.refreshInterval ?? 86400);
  const [catalogEntries, setCatalogEntries] = useState<SourceCatalogEntry[] | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [catalogEntryId, setCatalogEntryId] = useState(() => initialCatalogEntryId || readDraft()?.catalogEntryId || '');
  const [websiteSetupOpen, setWebsiteSetupOpen] = useState(false);
  const catalogEntry = catalogEntries?.find(entry => entry.id === catalogEntryId);
  const websiteSources = sources.filter(source => sourceKind(source) === 'website');
  const confluenceSources = sources.filter(source => sourceKind(source) === 'confluence');
  // The catalog entry's host decides the connection; one is created on the fly when none exists yet.
  const catalogSource = catalogEntry ? websiteSources.find(source => hostOf(source.base_url) === catalogEntry.host) : undefined;
  const intervalOptions = refreshIntervalOptions(locale);
  const [files, setFiles] = useState<File[]>([]);
  const [transcriptInstructions, setTranscriptInstructions] = useState('');
  const hasTranscripts = files.some(file => file.name.toLowerCase().endsWith('.vtt'));
  const [sourceId, setSourceId] = useState(() => {
    if (initialSourceId) return initialSourceId;
    if (typeof window === 'undefined') return '';
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.sourceId || ''; }
    catch { return ''; }
  });
  const [profiles, setProfiles] = useState<PortalProfile[] | null>(null);
  const [profileId, setProfileId] = useState(() => {
    if (typeof window === 'undefined') return '';
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.profileId || ''; }
    catch { return ''; }
  });
  const [profileError, setProfileError] = useState('');
  const selectedProfile = profiles?.find(profile => profile.value === profileId);
  const [scopeType, setScopeType] = useState<'page' | 'space'>(() => {
    if (typeof window === 'undefined') return 'page';
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.scopeType || 'page'; }
    catch { return 'page'; }
  });
  const [pageUrl, setPageUrl] = useState(() => {
    if (typeof window === 'undefined') return '';
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.pageUrl || ''; }
    catch { return ''; }
  });
  const [automatic, setAutomatic] = useState(() => {
    // Daily auto-sync is on by default; only an explicit opt-out in a saved draft turns it off.
    if (typeof window === 'undefined') return true;
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.automatic ?? true; }
    catch { return true; }
  });
  const [confirmedAccess, setConfirmedAccess] = useState(() => {
    if (typeof window === 'undefined') return false;
    try { return Boolean((JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.confirmedAccess); }
    catch { return false; }
  });
  const [saving, setSaving] = useState(false);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [sourcesLoaded, setSourcesLoaded] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const { test: testSource, testingId, cooldownSecondsFor } = useImportSourceTest();
  const [testMessage, setTestMessage] = useState('');
  const selectedSource = sources.find(source => source.id === sourceId);
  // The backend refuses import runs until the connection has been tested once.
  const untested = Boolean(selectedSource) && !selectedSource?.server_kind;
  const testCooldown = selectedSource ? cooldownSecondsFor(selectedSource.id) : 0;
  async function testSelected() {
    if (!selectedSource) return;
    setTestMessage('');
    const outcome = await testSource(selectedSource.id);
    if (outcome.kind === 'result' && outcome.result.ok) {
      setSources(current => current.map(entry => entry.id === selectedSource.id ? { ...entry, server_kind: outcome.result.server_kind ?? entry.server_kind, last_validated_at: new Date().toISOString() } : entry));
      return;
    }
    if (outcome.kind === 'result') setTestMessage(`${t('portal.connections.confluence.testFailed')}${outcome.result.detail ? `: ${outcome.result.detail}` : ''}`);
    else setTestMessage(outcome.rateLimited ? portalError(outcome.error, locale) : `${t('portal.connections.confluence.testFailed')}: ${portalError(outcome.error, locale)}`);
  }
  // Someone choosing Confluence without a connection gets the setup right away -- once, not after every reload.
  const setupOffered = useRef(false);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [run, setRun] = useState<ImportRun | null>(null);
  const completed = !saving && (results.length > 0 || run !== null);
  const completedHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    sessionStorage.setItem(SOURCE_DRAFT_KEY, JSON.stringify({ collectionId, kind, sourceId, scopeType, pageUrl, automatic, confirmedAccess, profileId, startUrl, crawlScope, linkedDocuments, legalSource, refreshInterval, catalogEntryId } satisfies SourceDraft));
  }, [collectionId, kind, sourceId, scopeType, pageUrl, automatic, confirmedAccess, profileId, startUrl, crawlScope, linkedDocuments, legalSource, refreshInterval, catalogEntryId]);
  // A chosen catalog entry prefills scope and options; the person may still change them.
  useEffect(() => {
    if (!catalogEntry) return;
    setStartUrl(catalogEntry.start_url);
    setCrawlScope(catalogEntry.crawl_scope);
    setLinkedDocuments(catalogEntry.include_linked_documents);
    setLegalSource(catalogEntry.legal_source);
    setRefreshInterval(catalogEntry.interval_seconds ?? 0);
  }, [catalogEntry]);
  useEffect(() => { if (completed) completedHeading.current?.focus(); }, [completed]);
  function addMore() {
    setResults([]); setRun(null); setFiles([]); setError(''); setSourceError(''); setProgress('');
  }
  const load = useCallback(async () => {
    setError('');
    try {
      setSpaces(await loadWritableSpaces());
    } catch (err) { setError(portalError(err, locale)); }
  }, [locale]);
  const loadSources = useCallback(async (offerSetupFor: 'confluence' | 'website' | null = 'confluence') => {
    setSourceError('');
    try {
      const data = await apiJson<{ items: ImportSource[] }>('/api/v1/import/sources');
      setSources(data.items); setSourcesLoaded(true);
      const ofKind = data.items.filter(source => sourceKind(source) === offerSetupFor);
      if (offerSetupFor && !ofKind.length && !setupOffered.current) {
        setupOffered.current = true;
        if (offerSetupFor === 'confluence') setSetupOpen(true); else setWebsiteSetupOpen(true);
      }
    }
    catch (err) { setSourceError(portalError(err, locale)); }
  }, [locale]);
  const loadCatalog = useCallback(async () => {
    setCatalogError('');
    try { setCatalogEntries((await loadSourceCatalog()).items); }
    catch (err) { setCatalogError(portalError(err, locale)); }
  }, [locale]);
  const loadProfiles = useCallback(async () => {
    setProfileError('');
    try {
      const [capabilities, settings] = await Promise.all([
        apiJson<{ profiles: ProcessingProfile[] }>('/api/v1/paddle/capabilities'),
        apiJson<{ default_profile: string }>('/api/v1/paddle/settings'),
      ]);
      const choices = portalProfiles(capabilities.profiles, locale);
      setProfiles(choices);
      setProfileId(current => choices.some(profile => profile.value === current) ? current
        : choices.find(profile => profile.value === settings.default_profile)?.value || choices[0]?.value || '');
    } catch (err) { setProfileError(portalError(err, locale)); }
  }, [locale]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadProfiles(); }, [loadProfiles]);
  useEffect(() => {
    if (kind === 'confluence') void loadSources('confluence');
    else if (kind === 'website') void loadSources('website');
    else if (kind === 'catalog') { void loadSources(null); void loadCatalog(); }
  }, [kind, loadSources, loadCatalog]);
  const selected = spaces?.find(space => space.collection_id === collectionId);
  const websiteKind = kind === 'website' || kind === 'catalog';

  /** The website connection a catalog import runs on: an existing one for the entry's host, or a
   * fresh credential-less one that is created and tested right here. */
  async function ensureCatalogSource(entry: SourceCatalogEntry): Promise<ImportSource> {
    let source = catalogSource;
    if (!source) {
      source = await apiJson<ImportSource>('/api/v1/import/sources', jsonBody({
        name: entry.host, kind: 'website', base_url: new URL(entry.start_url).origin, auth_type: 'none', credential: '',
      }));
      setSources(current => [...current, source!]);
    }
    if (!source.server_kind) {
      const result = await apiJson<ImportSourceTestResponse>(`/api/v1/import/sources/${encodeURIComponent(source.id)}/test`, { method: 'POST' });
      if (!result.ok) throw new Error(t('portal.sourceForm.connectionFailed', { detail: result.detail || '' }));
      source = { ...source, server_kind: result.server_kind || 'website' };
      setSources(current => current.map(entry => entry.id === source!.id ? source! : entry));
    }
    return source;
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected || !selectedProfile || saving || (kind !== 'files' && !confirmedAccess)) return;
    setSaving(true); setError(''); setResults([]);
    try {
      if (kind === 'files') {
        for (let index = 0; index < files.length; index++) {
          const file = files[index];
          setProgress(t('portal.sourceForm.progress', { current: index + 1, total: files.length, name: file.name }));
          let jobId: string | undefined;
          try {
            const body = new FormData(); body.append('file', file);
            if (file.name.toLowerCase().endsWith('.vtt') && transcriptInstructions.trim()) body.append('transcript_instructions', transcriptInstructions.trim());
            const upload = await apiJson<{ job_id: string }>(`/api/v1/collections/${encodeURIComponent(collectionId)}/upload`, { method: 'POST', body });
            jobId = upload.job_id;
            // Start only the newly uploaded document, never reprocess the
            // other (possibly already approved) files in this collection.
            await apiJson(`/api/v1/jobs/${upload.job_id}/restart`, jsonBody({ profile_id: selectedProfile.value }));
            setResults(current => [...current, { name: file.name, id: jobId, ok: true, message: t('portal.sourceForm.uploadSuccess') }]);
          } catch (err) {
            setResults(current => [...current, { name: file.name, id: jobId, ok: false, message: jobId ? t('portal.sourceForm.uploadPartialFail') : portalError(err, locale) }]);
          }
        }
        setFiles([]);
      } else if (kind === 'confluence') {
        // The import and its daily auto-sync are saved together as one configuration.
        const created = await apiJson<ImportRun>('/api/v1/import/runs', jsonBody({
          source_id: sourceId,
          scope: { type: scopeType, value: pageUrl.trim() },
          options: { collection_id: collectionId, include_attachments: true, ocr_attachments: true, ocr_profile_id: selectedProfile.value },
          sync: { enabled: automatic, interval_seconds: 86400 },
        }));
        setRun(created);
      } else {
        // Website / catalog: the page scope, linked documents, legal-status extraction and the
        // refresh cadence are saved with the import as one configuration (ImportSync).
        const source = kind === 'catalog' && catalogEntry ? await ensureCatalogSource(catalogEntry) : websiteSources.find(entry => entry.id === sourceId);
        if (!source) throw new Error(t('portal.sourceForm.chooseConnection'));
        const created = await apiJson<ImportRun>('/api/v1/import/runs', jsonBody({
          source_id: source.id,
          scope: { type: 'url', value: startUrl.trim() },
          options: {
            collection_id: collectionId, include_attachments: linkedDocuments, ocr_attachments: linkedDocuments, ocr_profile_id: selectedProfile.value,
            crawl_scope: crawlScope, legal_source: legalSource, catalog_entry_id: kind === 'catalog' ? catalogEntryId || null : null,
            ...(catalogEntry?.max_pages ? { max_pages: catalogEntry.max_pages } : {}),
          },
          sync: { enabled: refreshInterval > 0, interval_seconds: refreshInterval > 0 ? refreshInterval : null },
        }));
        setRun(created);
      }
      sessionStorage.removeItem(SOURCE_DRAFT_KEY);
    } catch (err) { setError(portalError(err, locale)); }
    finally { setSaving(false); setProgress(''); }
  }
  return <PortalPage title={t('portal.sourceForm.title')} description={t('portal.sourceForm.description')} eyebrow={null}>
    {error && <Notice error>{error}</Notice>}
    {spaces === null && !error && <Notice>{t('portal.spaces.loading')}</Notice>}
    {spaces?.length === 0 && <EmptyState title={t('portal.sourceForm.emptySpacesTitle')} href="/knowledge/new" action={t('portal.chrome.breadcrumb.knowledgeNew')}>{t('portal.sourceForm.emptySpacesBody')}</EmptyState>}
    {Boolean(spaces?.length) && !completed && <form className="portal-panel portal-source-form" onSubmit={submit}>
      <fieldset disabled={saving || Boolean(run)}><legend><span className="portal-step">01</span> {t('portal.sourceForm.step1Legend')}</legend><label>{t('portal.sourceForm.spaceSelectLabel')}<RequiredMark /><select required value={collectionId} onChange={event => setCollectionId(event.target.value)}><option value="">{t('portal.sourceForm.pleaseSelect')}</option>{spaces?.map(space => <option key={space.collection_id} value={space.collection_id}>{space.name}</option>)}</select></label><Link className="portal-inline-link" href="/knowledge/new"><Plus size={14} />{t('portal.sourceForm.newSpaceLink')}</Link>
        {selected && <p className="portal-field-hint">{t('portal.spaces.authorizedLabel')} {accessSummary(selected, locale)}.</p>}
      </fieldset>
      <fieldset disabled={saving || Boolean(run)}><legend><span className="portal-step">02</span> {t('portal.sourceForm.step2Legend')}</legend><div className="portal-source-types"><label className={kind === 'files' ? 'selected' : ''}><input type="radio" name="kind" value="files" checked={kind === 'files'} onChange={() => setKind('files')} /><FileUp size={23} /><span><strong>{t('portal.sourceForm.filesOption')}</strong><small>{t('portal.sourceForm.filesHint')}</small></span></label><label className={kind === 'confluence' ? 'selected' : ''}><input type="radio" name="kind" value="confluence" checked={kind === 'confluence'} onChange={() => setKind('confluence')} /><Globe size={23} /><span><strong>{t('portal.sourceForm.confluenceOption')}</strong><small>{t('portal.sourceForm.confluenceHint')}</small></span></label><label className={kind === 'website' ? 'selected' : ''}><input type="radio" name="kind" value="website" checked={kind === 'website'} onChange={() => setKind('website')} /><Link2 size={23} /><span><strong>{t('portal.sourceForm.websiteOption')}</strong><small>{t('portal.sourceForm.websiteHint')}</small></span></label><label className={kind === 'catalog' ? 'selected' : ''}><input type="radio" name="kind" value="catalog" checked={kind === 'catalog'} onChange={() => setKind('catalog')} /><BookOpenText size={23} /><span><strong>{t('portal.sourceForm.catalogOption')}</strong><small>{t('portal.sourceForm.catalogHint')}</small></span></label></div>
        {kind === 'files' ? <><label className="portal-upload">{t('portal.sourceForm.chooseFiles')}<RequiredMark /><input type="file" multiple required accept=".pdf,.docx,.pptx,.xlsx,.xls,.png,.jpg,.jpeg,.eml,.md,.xml,.vtt" onChange={event => setFiles(Array.from(event.target.files || []))} /><span>{files.length ? t('portal.sourceForm.filesSelectedCount', { count: files.length }) : t('portal.sourceForm.filesPlaceholder')}</span><span>{t('portal.sourceForm.emailAttachmentsHint')}</span></label>
          {hasTranscripts && <div className="portal-form"><label>{t('portal.sourceForm.transcriptInstructionsLabel')}<textarea rows={4} maxLength={4000} value={transcriptInstructions} placeholder={t('portal.sourceForm.transcriptInstructionsPlaceholder')} onChange={event => setTranscriptInstructions(event.target.value)} /></label><p className="portal-field-hint">{t('portal.sourceForm.transcriptInstructionsHint')}</p></div>}</> : websiteKind ? <div className="portal-form">
          {sourceError && <Notice error action={() => loadSources(kind === 'website' ? 'website' : null)}>{sourceError}</Notice>}
          {kind === 'catalog' ? <>
            {catalogError && <Notice error action={loadCatalog}>{catalogError}</Notice>}
            {catalogEntries?.length === 0 && <p className="portal-field-hint">{t('portal.sourceForm.catalogEmpty')}</p>}
            <label>{t('portal.sourceForm.catalogEntryLabel')}<RequiredMark /><select required value={catalogEntryId} onChange={event => setCatalogEntryId(event.target.value)}><option value="">{t('portal.sourceForm.chooseCatalogEntry')}</option>{catalogEntries?.map(entry => <option key={entry.id} value={entry.id}>{entry.title}{entry.publisher ? ` · ${entry.publisher}` : ''}</option>)}</select></label>
            {catalogEntry && <p className="portal-field-hint">{catalogEntry.description && <>{catalogEntry.description} </>}<a className="portal-inline-link" href={catalogEntry.start_url} target="_blank" rel="noopener noreferrer">{catalogEntry.start_url}</a></p>}
            {catalogEntry && <p className="portal-field-hint" role="status">{catalogSource ? t('portal.sourceForm.catalogConnectionExists', { name: catalogSource.name }) : t('portal.sourceForm.catalogAutoConnection', { host: catalogEntry.host })}</p>}
            <Link className="portal-inline-link" href="/knowledge/catalog">{t('portal.sourceForm.catalogLink')} <ArrowRight size={14} /></Link>
          </> : <>
            {sourcesLoaded && !websiteSources.length && <p className="portal-field-hint">{t('portal.sourceForm.noWebsiteConnectionHint')}</p>}
            <label>{t('portal.sourceForm.websiteConnectionLabel')}<RequiredMark /><select required value={sourceId} onChange={event => { setSourceId(event.target.value); setTestMessage(''); }}><option value="">{t('portal.sourceForm.chooseConnection')}</option>{websiteSources.map(source => <option key={source.id} value={source.id}>{source.name} · {hostOf(source.base_url)}</option>)}</select></label>
            {untested && <p className="portal-field-hint" role="status">{t('portal.sourceForm.untestedHint')} <button type="button" className="portal-inline-link" disabled={testingId !== null || testCooldown > 0} onClick={() => void testSelected()}>{testingId !== null ? t('portal.connections.confluence.testing') : testCooldown > 0 ? t('portal.connections.confluence.testWithCooldown', { seconds: testCooldown }) : t('portal.connections.confluence.test')}</button>{testMessage && <> {testMessage}</>}</p>}
            <div className="flex flex-wrap gap-x-6"><button type="button" className="portal-inline-link" onClick={() => setWebsiteSetupOpen(true)}><Plus size={14} />{t('portal.sourceForm.setupWebsiteConnectionButton')}</button><Link className="portal-inline-link" href="/connections?tab=websites">{t('portal.sourceForm.setupConnectionLink')} <ArrowRight size={14} /></Link></div>
            <label>{t('portal.sourceForm.startUrlLabel')}<RequiredMark /><input required type="url" value={startUrl} onChange={event => setStartUrl(event.target.value)} placeholder="https://intranet.example.com/handbuch/" /></label><p className="portal-field-hint">{t('portal.sourceForm.startUrlHint')}</p>
          </>}
          {(kind === 'website' || catalogEntry) && <>
            <div className="portal-filter-row" role="group" aria-label={t('portal.sourceForm.crawlScopeLabel')}>
              {CRAWL_SCOPES.map(scope => <button key={scope} type="button" aria-pressed={crawlScope === scope} onClick={() => setCrawlScope(scope)}>{t(`portal.sourceForm.crawlScope${scope.charAt(0).toUpperCase()}${scope.slice(1)}` as MessageKey)}</button>)}
            </div>
            <p className="portal-field-hint">{kind === 'catalog' ? t('portal.sourceForm.catalogScopeHint') : t('portal.sourceForm.crawlScopeHint')}</p>
            <label className="portal-choice"><input type="checkbox" checked={linkedDocuments} onChange={event => setLinkedDocuments(event.target.checked)} />{t('portal.sourceForm.linkedDocumentsLabel')}</label>
            <label className="portal-choice"><input type="checkbox" checked={legalSource} onChange={event => setLegalSource(event.target.checked)} />{t('portal.sourceForm.legalSourceLabel')}</label>
            <label>{t('portal.sourceForm.refreshLabel')}<select value={refreshInterval} onChange={event => setRefreshInterval(Number(event.target.value))}><option value={0}>{t('portal.sourceForm.refreshManual')}</option>{intervalOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><p className="portal-field-hint">{t('portal.sourceForm.refreshHint')}</p>
          </>}
          <label className="portal-choice portal-access-confirm"><input required type="checkbox" checked={confirmedAccess} onChange={event => setConfirmedAccess(event.target.checked)} />{t('portal.sourceForm.confirmAccessWebsiteLabel')}</label>
        </div> : <div className="portal-form">
          {sourceError && <Notice error action={() => loadSources('confluence')}>{sourceError}</Notice>}
          {sourcesLoaded && !confluenceSources.length && <p className="portal-field-hint">{t('portal.sourceForm.noConnectionHint')}</p>}
          <label>{t('portal.sourceForm.connectionLabel')}<RequiredMark /><select required value={sourceId} onChange={event => { setSourceId(event.target.value); setAutomatic(true); setTestMessage(''); }}><option value="">{t('portal.sourceForm.chooseConnection')}</option>{confluenceSources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
          {untested && <p className="portal-field-hint" role="status">{t('portal.sourceForm.untestedHint')} <button type="button" className="portal-inline-link" disabled={testingId !== null || testCooldown > 0} onClick={() => void testSelected()}>{testingId !== null ? t('portal.connections.confluence.testing') : testCooldown > 0 ? t('portal.connections.confluence.testWithCooldown', { seconds: testCooldown }) : t('portal.connections.confluence.test')}</button>{testMessage && <> {testMessage}</>}</p>}
          <div className="flex flex-wrap gap-x-6"><button type="button" className="portal-inline-link" onClick={() => setSetupOpen(true)}><Plus size={14} />{t('portal.sourceForm.setupConnectionButton')}</button><Link className="portal-inline-link" href="/connections">{t('portal.sourceForm.setupConnectionLink')} <ArrowRight size={14} /></Link></div>
          <div className="portal-filter-row" role="group" aria-label={t('portal.sourceForm.scopeLabel')}>
            <button type="button" aria-pressed={scopeType === 'page'} onClick={() => setScopeType('page')}>{t('portal.sourceForm.scopePage')}</button>
            <button type="button" aria-pressed={scopeType === 'space'} onClick={() => setScopeType('space')}>{t('portal.sourceForm.scopeSpace')}</button>
          </div>
          {scopeType === 'page'
            ? <><label>{t('portal.sourceForm.pageLabel')}<RequiredMark /><input required type="url" value={pageUrl} onChange={event => setPageUrl(event.target.value)} placeholder="https://confluence.example.com/..." /></label><p className="portal-field-hint">{t('portal.sourceForm.subpagesHint')}</p></>
            : <><label>{t('portal.sourceForm.spaceLabel')}<RequiredMark /><input required value={pageUrl} onChange={event => setPageUrl(event.target.value)} placeholder="DOCS · https://confluence.example.com/spaces/DOCS" /></label><p className="portal-field-hint">{t('portal.sourceForm.spaceHint')}</p></>}
          <label className="portal-choice"><input type="checkbox" checked={automatic} onChange={event => setAutomatic(event.target.checked)} />{t('portal.sourceForm.autoRefreshLabel')}</label><p className="portal-field-hint">{t('portal.sourceForm.autoRefreshHint')}</p>
          <label className="portal-choice portal-access-confirm"><input required type="checkbox" checked={confirmedAccess} onChange={event => setConfirmedAccess(event.target.checked)} />{t('portal.sourceForm.confirmAccessLabel')}</label>
        </div>}
      </fieldset>
      <fieldset disabled={saving || Boolean(run)}>
        <legend><span className="portal-step">03</span> {t('portal.sourceForm.step3Legend')}</legend>
        {profileError && <Notice error action={loadProfiles}>{profileError}</Notice>}
        {!profiles && !profileError && <Notice>{t('portal.reprocess.loading')}</Notice>}
        {profiles?.length === 0 && <Notice error>{t('portal.sourceForm.noProfile')}</Notice>}
        {/* The default profile is preselected; the choice is folded away so step 03 stays a one-liner for most people. */}
        {Boolean(profiles?.length) && <details className="portal-quality-legend"><summary>{t('portal.sourceForm.profileSummary', { label: selectedProfile?.label ?? '' })}</summary>
          <label>{t('portal.sourceForm.profileSelectLabel')}<RequiredMark />
            <select required value={profileId} onChange={event => setProfileId(event.target.value)} aria-describedby="profile-description profile-scope">
              {profiles?.some(profile => profile.kind === 'ocr') && <optgroup label={t('portal.reprocess.ocrGroup')}>{profiles.filter(profile => profile.kind === 'ocr').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
              {profiles?.some(profile => profile.kind === 'vl') && <optgroup label={t('portal.reprocess.vlGroup')}>{profiles.filter(profile => profile.kind === 'vl').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
            </select>
          </label>
          <p id="profile-description" className="portal-field-hint">{selectedProfile?.description}</p>
          <p id="profile-scope" className="portal-field-hint">{kind === 'confluence' ? t('portal.sourceForm.profileScopeConfluence') : websiteKind ? t('portal.sourceForm.profileScopeWebsite') : t('portal.sourceForm.profileScopeFiles')}</p>
        </details>}
      </fieldset>
      <div className="portal-source-summary"><ShieldNotice /><div className="portal-form-actions"><Button type="submit" disabled={saving || !selected || !selectedProfile || Boolean(run) || (kind === 'files' ? files.length === 0 : kind === 'catalog' ? !catalogEntry || !startUrl.trim() || !confirmedAccess : kind === 'website' ? !sourceId || untested || !startUrl.trim() || !confirmedAccess : !sourceId || untested || !pageUrl.trim() || !confirmedAccess)}>{saving ? t('common.starting') : kind === 'files' ? t('portal.sourceForm.uploadSubmit') : t('portal.sourceForm.importSubmit')}</Button><Link href={initialCollection ? `/knowledge/${encodeURIComponent(initialCollection)}` : '/knowledge'} className={buttonVariants({ variant: 'ghost' })}>{t('common.cancel')}</Link></div></div>
    </form>}
    {/* Outside the form above: the dialog has a form of its own. */}
    {setupOpen && <ConfluenceSetupDialog onClose={() => setSetupOpen(false)} onCreated={source => {
      setSources(current => [...current, source]); setSourcesLoaded(true);
      setSourceId(source.id); setAutomatic(true); setSetupOpen(false);
    }} />}
    {websiteSetupOpen && <WebsiteSetupDialog onClose={() => setWebsiteSetupOpen(false)} onCreated={source => {
      setSources(current => [...current, source]); setSourcesLoaded(true);
      setSourceId(source.id); setWebsiteSetupOpen(false);
    }} />}
    {progress && <Notice>{progress}</Notice>}
    {completed && <section className="portal-panel portal-form-panel max-w-[900px]" aria-labelledby="source-completed-title">
      <CheckCheck size={28} className="mb-4 text-emerald-700" aria-hidden="true" />
      <h2 id="source-completed-title" tabIndex={-1} ref={completedHeading}>
        {run ? (websiteKind ? t('portal.sourceForm.websiteImportStartedHeading') : t('portal.sourceForm.importStartedHeading')) : results.every(result => result.ok) ? t('portal.sourceForm.uploadCompleteHeading') : results.some(result => result.ok) ? t('portal.sourceForm.uploadPartialHeading') : t('portal.sourceForm.uploadFailedHeading')}
      </h2>
      {(run || results.some(result => result.ok)) && <>
        <p className="mt-3 text-sm leading-7 text-slate-600">{run ? t('portal.sourceForm.pagesInBackground') : t('portal.sourceForm.filesHandedOff', { count: results.filter(result => result.ok).length })}</p>
        <p className="mt-3 flex items-start gap-3 text-sm leading-7 text-slate-600"><Clock3 size={18} className="mt-1 shrink-0" aria-hidden="true" />{t('portal.sourceForm.processingTimeHint')}</p>
        <p className="mt-3 text-sm leading-7 text-slate-600">{t('portal.sourceForm.reviewAfter')}</p>
      </>}
      {results.some(result => !result.ok) && <div className="mt-5" role="alert"><h3 className="font-semibold">{t('portal.sourceForm.needsAttention')}</h3>
        <ul className="portal-upload-results">{results.filter(result => !result.ok).map((result, index) => <li key={index}><strong>{result.name}</strong><span>{result.message}</span>{result.id && <Link href={`/jobs/${result.id}`}>{t('portal.sourceForm.checkJob')} <ArrowRight size={14} aria-hidden="true" /></Link>}</li>)}</ul>
      </div>}
      <h3 className="mt-8 font-semibold">{t('portal.sourceForm.whatNext')}</h3>
      <div className="portal-form-actions">
        {collectionId && <Link href={`/knowledge/${collectionId}`} className={buttonVariants()}>{t('portal.sourceForm.openSpace')}<ArrowRight size={16} aria-hidden="true" /></Link>}
        <Link href="/documents?stand=processing" className={buttonVariants({ variant: 'outline' })}>{t('portal.reviews.viewProcessing')}<ArrowRight size={16} aria-hidden="true" /></Link>
        <Button type="button" variant="outline" onClick={addMore}>{t('portal.sourceForm.addMore')}</Button>
      </div>
    </section>}
  </PortalPage>;
}

function ShieldNotice() {
  const { t } = useI18n();
  return <p>{t('portal.sourceForm.shieldNotice')}</p>;
}
