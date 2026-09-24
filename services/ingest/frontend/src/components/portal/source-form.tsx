'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowRight, CheckCheck, Clock3, FileUp, Globe, Plus } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { jsonBody, portalError, type KnowledgeSpace } from '@/lib/portal';
import type { ImportSource, ImportRun } from '@/lib/imports';
import { portalProfiles, type PortalProfile, type ProcessingProfile } from '@/lib/portal-profiles';
import { useI18n } from '@/i18n/provider';
import { EmptyState, Notice, PortalPage } from './shared';

type UploadResult = { name: string; id?: string; ok: boolean; message: string };

const SOURCE_DRAFT_KEY = 'weave-source-form-draft';
type SourceDraft = { collectionId: string; kind: 'files' | 'confluence'; sourceId: string; pageUrl: string; automatic: boolean; confirmedAccess: boolean; profileId: string };

export function SourceForm({ initialCollection = '' }: { initialCollection?: string }) {
  const { t, locale } = useI18n();
  const [spaces, setSpaces] = useState<KnowledgeSpace[] | null>(null);
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [collectionId, setCollectionId] = useState(() => {
    if (typeof window === 'undefined') return initialCollection;
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.collectionId || initialCollection; }
    catch { return initialCollection; }
  });
  const [kind, setKind] = useState<'files' | 'confluence'>(() => {
    if (typeof window === 'undefined') return 'files';
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.kind || 'files'; }
    catch { return 'files'; }
  });
  const [files, setFiles] = useState<File[]>([]);
  const [sourceId, setSourceId] = useState(() => {
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
  const [pageUrl, setPageUrl] = useState(() => {
    if (typeof window === 'undefined') return '';
    try { return (JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.pageUrl || ''; }
    catch { return ''; }
  });
  const [automatic, setAutomatic] = useState(() => {
    if (typeof window === 'undefined') return false;
    try { return Boolean((JSON.parse(sessionStorage.getItem(SOURCE_DRAFT_KEY) || 'null') as SourceDraft | null)?.automatic); }
    catch { return false; }
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
  const [results, setResults] = useState<UploadResult[]>([]);
  const [run, setRun] = useState<ImportRun | null>(null);
  const completed = !saving && (results.length > 0 || run !== null);
  const completedHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    sessionStorage.setItem(SOURCE_DRAFT_KEY, JSON.stringify({ collectionId, kind, sourceId, pageUrl, automatic, confirmedAccess, profileId } satisfies SourceDraft));
  }, [collectionId, kind, sourceId, pageUrl, automatic, confirmedAccess, profileId]);
  useEffect(() => { if (completed) completedHeading.current?.focus(); }, [completed]);
  function addMore() {
    setResults([]); setRun(null); setFiles([]); setError(''); setSourceError(''); setProgress('');
  }
  const load = useCallback(async () => {
    setError('');
    try {
      const data = await apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections');
      setSpaces(data.items.filter(space => space.can_upload ?? space.can_manage));
    } catch (err) { setError(portalError(err, locale)); }
  }, [locale]);
  const loadSources = useCallback(async () => {
    setSourceError('');
    try { const data = await apiJson<{ items: ImportSource[] }>('/api/v1/import/sources'); setSources(data.items); }
    catch (err) { setSourceError(portalError(err, locale)); }
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
          setProgress(t('portal.sourceForm.progress', { current: index + 1, total: files.length, name: file.name }));
          let jobId: string | undefined;
          try {
            const body = new FormData(); body.append('file', file);
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
      } else {
        const created = await apiJson<ImportRun>('/api/v1/import/runs', jsonBody({ source_id: sourceId, scope: { type: 'page', value: pageUrl.trim() }, options: { collection_id: collectionId, include_attachments: true, ocr_attachments: true, ocr_profile_id: selectedProfile.value } }));
        setRun(created);
        if (automatic) {
          try { await apiJson(`/api/v1/import/sources/${encodeURIComponent(sourceId)}`, { ...jsonBody({ refresh_enabled: true, refresh_interval_seconds: 86400 }), method: 'PATCH' }); }
          catch { setError(t('portal.sourceForm.autoRefreshFailed')); }
        }
      }
      sessionStorage.removeItem(SOURCE_DRAFT_KEY);
    } catch (err) { setError(portalError(err, locale)); }
    finally { setSaving(false); setProgress(''); }
  }
  return <PortalPage title={t('portal.sourceForm.title')} description={t('portal.sourceForm.description')} eyebrow={t('portal.sourceForm.step')}>
    {error && <Notice error>{error}</Notice>}
    {spaces === null && !error && <Notice>{t('portal.spaces.loading')}</Notice>}
    {spaces?.length === 0 && <EmptyState title={t('portal.sourceForm.emptySpacesTitle')} href="/knowledge/new" action={t('portal.chrome.breadcrumb.knowledgeNew')}>{t('portal.sourceForm.emptySpacesBody')}</EmptyState>}
    {Boolean(spaces?.length) && !completed && <form className="portal-panel portal-source-form" onSubmit={submit}>
      <fieldset disabled={saving || Boolean(run)}><legend><span className="portal-step">01</span> {t('portal.sourceForm.step1Legend')}</legend><label>{t('portal.sourceForm.spaceSelectLabel')}<select required value={collectionId} onChange={event => setCollectionId(event.target.value)}><option value="">{t('portal.sourceForm.pleaseSelect')}</option>{spaces?.map(space => <option key={space.collection_id} value={space.collection_id}>{space.name}</option>)}</select></label><Link className="portal-inline-link" href="/knowledge/new"><Plus size={14} />{t('portal.sourceForm.newSpaceLink')}</Link>
        {selected && <p className="portal-field-hint">{t('portal.spaces.authorizedLabel')} {selected.read_teams.length ? selected.read_teams.join(', ') : t('portal.newSpace.allTeams')}.</p>}
      </fieldset>
      <fieldset disabled={saving || Boolean(run)}><legend><span className="portal-step">02</span> {t('portal.sourceForm.step2Legend')}</legend><div className="portal-source-types"><label className={kind === 'files' ? 'selected' : ''}><input type="radio" name="kind" value="files" checked={kind === 'files'} onChange={() => setKind('files')} /><FileUp size={23} /><span><strong>{t('portal.sourceForm.filesOption')}</strong><small>{t('portal.sourceForm.filesHint')}</small></span></label><label className={kind === 'confluence' ? 'selected' : ''}><input type="radio" name="kind" value="confluence" checked={kind === 'confluence'} onChange={() => setKind('confluence')} /><Globe size={23} /><span><strong>{t('portal.sourceForm.confluenceOption')}</strong><small>{t('portal.sourceForm.confluenceHint')}</small></span></label></div>
        {kind === 'files' ? <label className="portal-upload">{t('portal.sourceForm.chooseFiles')}<input type="file" multiple required accept=".pdf,.docx,.pptx,.xlsx,.xls,.png,.jpg,.jpeg,.eml" onChange={event => setFiles(Array.from(event.target.files || []))} /><span>{files.length ? t('portal.sourceForm.filesSelectedCount', { count: files.length }) : t('portal.sourceForm.filesPlaceholder')}</span><span>{t('portal.sourceForm.emailAttachmentsHint')}</span></label> : <div className="portal-form">
          {sourceError && <Notice error action={loadSources}>{sourceError}</Notice>}
          <label>{t('portal.sourceForm.connectionLabel')}<select required value={sourceId} onChange={event => { setSourceId(event.target.value); setAutomatic(false); }}><option value="">{t('portal.sourceForm.chooseConnection')}</option>{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label><Link className="portal-inline-link" href="/connections">{t('portal.sourceForm.setupConnectionLink')} <ArrowRight size={14} /></Link>
          <label>{t('portal.sourceForm.pageLabel')}<input required type="url" value={pageUrl} onChange={event => setPageUrl(event.target.value)} placeholder="https://confluence.example.com/..." /></label><p className="portal-field-hint">{t('portal.sourceForm.subpagesHint')}</p>
          <label className="portal-choice"><input type="checkbox" checked={automatic} onChange={event => setAutomatic(event.target.checked)} />{t('portal.sourceForm.autoRefreshLabel')}</label><p className="portal-field-hint">{t('portal.sourceForm.autoRefreshHint')}</p>
          <label className="portal-choice portal-access-confirm"><input required type="checkbox" checked={confirmedAccess} onChange={event => setConfirmedAccess(event.target.checked)} />{t('portal.sourceForm.confirmAccessLabel')}</label>
        </div>}
      </fieldset>
      <fieldset disabled={saving || Boolean(run)}>
        <legend><span className="portal-step">03</span> {t('portal.sourceForm.step3Legend')}</legend>
        {profileError && <Notice error action={loadProfiles}>{profileError}</Notice>}
        {!profiles && !profileError && <Notice>{t('portal.reprocess.loading')}</Notice>}
        {profiles?.length === 0 && <Notice error>{t('portal.sourceForm.noProfile')}</Notice>}
        {Boolean(profiles?.length) && <>
          <label>{t('portal.sourceForm.profileSelectLabel')}
            <select required value={profileId} onChange={event => setProfileId(event.target.value)} aria-describedby="profile-description profile-scope">
              {profiles?.some(profile => profile.kind === 'ocr') && <optgroup label={t('portal.reprocess.ocrGroup')}>{profiles.filter(profile => profile.kind === 'ocr').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
              {profiles?.some(profile => profile.kind === 'vl') && <optgroup label={t('portal.reprocess.vlGroup')}>{profiles.filter(profile => profile.kind === 'vl').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
            </select>
          </label>
          <p id="profile-description" className="portal-field-hint">{selectedProfile?.description}</p>
          <p id="profile-scope" className="portal-field-hint">{kind === 'confluence' ? t('portal.sourceForm.profileScopeConfluence') : t('portal.sourceForm.profileScopeFiles')}</p>
        </>}
      </fieldset>
      <div className="portal-source-summary"><ShieldNotice /><div className="portal-form-actions"><Button type="submit" disabled={saving || !selected || !selectedProfile || Boolean(run) || (kind === 'files' ? files.length === 0 : !sourceId || !pageUrl.trim() || !confirmedAccess)}>{saving ? t('common.starting') : kind === 'files' ? t('portal.sourceForm.uploadSubmit') : t('portal.sourceForm.importSubmit')}</Button><Link href="/knowledge" className={buttonVariants({ variant: 'ghost' })}>{t('common.cancel')}</Link></div></div>
    </form>}
    {progress && <Notice>{progress}</Notice>}
    {completed && <section className="portal-panel portal-form-panel max-w-[900px]" aria-labelledby="source-completed-title">
      <CheckCheck size={28} className="mb-4 text-emerald-700" aria-hidden="true" />
      <h2 id="source-completed-title" tabIndex={-1} ref={completedHeading}>
        {run ? t('portal.sourceForm.importStartedHeading') : results.every(result => result.ok) ? t('portal.sourceForm.uploadCompleteHeading') : results.some(result => result.ok) ? t('portal.sourceForm.uploadPartialHeading') : t('portal.sourceForm.uploadFailedHeading')}
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
        <Link href="/processing" className={buttonVariants()}>{t('portal.reviews.viewProcessing')}<ArrowRight size={16} aria-hidden="true" /></Link>
        <Button type="button" variant="outline" onClick={addMore}>{t('portal.sourceForm.addMore')}</Button>
      </div>
    </section>}
  </PortalPage>;
}

function ShieldNotice() {
  const { t } = useI18n();
  return <p>{t('portal.sourceForm.shieldNotice')}</p>;
}
