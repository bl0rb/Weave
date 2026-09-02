'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { apiJson } from '@/lib/api';
import { portalError } from '@/lib/portal';
import { portalProfiles, type PortalProfile, type ProcessingProfile } from '@/lib/portal-profiles';
import { Button } from '@/components/ui/button';
import { Notice } from './shared';

export function ReprocessForm({ currentProfileId, busy, onSubmit, onCancel }: {
  currentProfileId: string | null;
  busy: boolean;
  onSubmit: (profileId: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [profiles, setProfiles] = useState<PortalProfile[] | null>(null);
  const [profileId, setProfileId] = useState('');
  const [error, setError] = useState('');
  const selectRef = useRef<HTMLSelectElement>(null);
  const load = useCallback(() => apiJson<{ profiles: ProcessingProfile[] }>('/api/v1/paddle/capabilities')
    .then(capabilities => { setProfiles(portalProfiles(capabilities.profiles)); setError(''); })
    .catch(err => setError(portalError(err))), []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (profiles?.length) selectRef.current?.focus(); }, [profiles]);
  const selected = profiles?.find(profile => profile.value === profileId);
  const previous = profiles?.find(profile => profile.value === currentProfileId);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!busy && selected) void onSubmit(selected.value);
  }

  return <form className="portal-form" aria-label="Erneute Verarbeitung" onSubmit={submit}>
    <p>Wähle ein Profil, das besser zu diesem Dokument passt.</p>
    {previous && <p className="portal-field-hint">Bisher: {previous.label}</p>}
    {error && <Notice error action={load}>{error}</Notice>}
    {!profiles && !error && <Notice>Verarbeitungsprofile werden geladen …</Notice>}
    {profiles?.length === 0 && <Notice>Es ist kein passendes Profil verfügbar. Bitte wende dich an die Administration.</Notice>}
    {Boolean(profiles?.length) && <>
      <label>Neues Verarbeitungsprofil
        <select ref={selectRef} required disabled={busy} value={profileId} onChange={event => setProfileId(event.target.value)} aria-describedby="reprocess-description reprocess-effect">
          <option value="">Profil auswählen</option>
          {profiles?.some(profile => profile.kind === 'ocr') && <optgroup label="Dokumenterkennung">{profiles.filter(profile => profile.kind === 'ocr').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
          {profiles?.some(profile => profile.kind === 'vl') && <optgroup label="Eingerichtete KI-Modelle">{profiles.filter(profile => profile.kind === 'vl').map(profile => <option key={profile.value} value={profile.value}>{profile.label}</option>)}</optgroup>}
        </select>
      </label>
      <p id="reprocess-description" className="portal-field-hint">{selected?.description}</p>
    </>}
    <p id="reprocess-effect" className="portal-field-hint">Der aktuelle Entwurf wird neu erstellt. Das kann einige Minuten dauern. Anschließend prüfst du das Ergebnis und gibst es erneut frei.</p>
    <div className="portal-form-actions">
      <Button type="submit" disabled={busy || !selected}>{busy ? 'Wird gestartet …' : 'Neu verarbeiten'}</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>Abbrechen</Button>
    </div>
  </form>;
}
