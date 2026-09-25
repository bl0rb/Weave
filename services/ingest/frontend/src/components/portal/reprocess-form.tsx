'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { apiJson } from '@/lib/api';
import { portalError } from '@/lib/portal';
import { portalProfiles, type PortalProfile, type ProcessingProfile } from '@/lib/portal-profiles';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';
import { Notice } from './shared';

export function ReprocessForm({ currentProfileId, busy, onSubmit, onCancel }: {
  currentProfileId: string | null;
  busy: boolean;
  onSubmit: (profileId: string) => Promise<void>;
  onCancel: () => void;
}) {
  const { t, locale } = useI18n();
  const [profiles, setProfiles] = useState<PortalProfile[] | null>(null);
  const [profileId, setProfileId] = useState('');
  const [error, setError] = useState('');
  const selectRef = useRef<HTMLSelectElement>(null);
  const load = useCallback(() => apiJson<{ profiles: ProcessingProfile[] }>('/api/v1/paddle/capabilities')
    .then(capabilities => { setProfiles(portalProfiles(capabilities.profiles, locale)); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (profiles?.length) selectRef.current?.focus(); }, [profiles]);
  const selected = profiles?.find(profile => profile.value === profileId);
  const previous = profiles?.find(profile => profile.value === currentProfileId);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!busy && selected) void onSubmit(selected.value);
  }

  return <form className="portal-form" aria-label={t('portal.reprocess.formAria')} onSubmit={submit}>
    <p>{t('portal.reprocess.intro')}</p>
    {previous && <p className="portal-field-hint">{t('portal.reprocess.previous', { label: previous.label })}</p>}
    {error && <Notice error action={load}>{error}</Notice>}
    {!profiles && !error && <Notice>{t('portal.reprocess.loading')}</Notice>}
    {profiles?.length === 0 && <Notice>{t('portal.reprocess.empty')}</Notice>}
    {Boolean(profiles?.length) && <>
      <label>{t('portal.reprocess.selectLabel')}
        <select ref={selectRef} required disabled={busy} value={profileId} onChange={event => setProfileId(event.target.value)} aria-describedby="reprocess-description reprocess-effect">
          <option value="">{t('portal.reprocess.selectPlaceholder')}</option>
          {profiles?.some(profile => profile.kind === 'ocr') && <optgroup label={t('portal.reprocess.ocrGroup')}>{profiles.filter(profile => profile.kind === 'ocr').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
          {profiles?.some(profile => profile.kind === 'vl') && <optgroup label={t('portal.reprocess.vlGroup')}>{profiles.filter(profile => profile.kind === 'vl').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
        </select>
      </label>
      <p id="reprocess-description" className="portal-field-hint">{selected?.description}</p>
    </>}
    <p id="reprocess-effect" className="portal-field-hint">{t('portal.reprocess.effectHint')}</p>
    <div className="portal-form-actions">
      <Button type="submit" disabled={busy || !selected}>{busy ? t('common.starting') : t('portal.reprocess.submit')}</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>{t('common.cancel')}</Button>
    </div>
  </form>;
}
