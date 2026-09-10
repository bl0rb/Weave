'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { jsonBody, portalError, type KnowledgeSpace, type PortalConfig } from '@/lib/portal';
import { Notice, PortalPage } from './shared';

export function NewKnowledgeSpace() {
  const router = useRouter();
  const [config, setConfig] = useState<PortalConfig | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [selectedTeams, setSelectedTeams] = useState<string[]>([]);
  const [shareAll, setShareAll] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const load = useCallback(() => apiJson<PortalConfig>('/api/v1/portal/config')
    .then(configuration => {
      setConfig(configuration);
      setSelectedTeams(configuration.team_names);
      setError('');
    })
    .catch(err => setError(portalError(err))), []);
  useEffect(() => { void load(); }, [load]);

  const hasOwnTeams = Boolean(config?.team_names.length);
  const canSave = Boolean(name.trim() && config && (shareAll ? confirmed : selectedTeams.length > 0));

  function toggleTeam(team: string) {
    setSelectedTeams(current => current.includes(team) ? current.filter(value => value !== team) : [...current, team]);
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (saving || !canSave) return;
    setSaving(true);
    setError('');
    try {
      const space = await apiJson<KnowledgeSpace>('/api/v1/collections', jsonBody({
        name: name.trim(),
        description: description.trim(),
        read_teams: shareAll ? [] : selectedTeams,
      }));
      // Continue the journey with the new area already selected.
      router.replace(`/sources/new?collection=${encodeURIComponent(space.collection_id)}`);
    } catch (err) {
      setError(portalError(err));
      setSaving(false);
    }
  }

  return <PortalPage title="Wissensbereich anlegen" eyebrow="SCHRITT 1 VON 3"
    description="Gib eurem Wissen einen gemeinsamen Ort. Danach fügst du Dateien oder eine Confluence-Seite hinzu.">
    <Link className="portal-back" href="/knowledge">← Alle Wissensbereiche</Link>
    {error && <Notice error action={!config ? load : undefined}>{error}</Notice>}
    {!config && !error && <Notice>Deine Teaminformationen werden geladen …</Notice>}
    <section className="portal-panel portal-form-panel" aria-labelledby="new-space-title">
      <h2 id="new-space-title">Thema und Berechtigte</h2>
      <form onSubmit={create} className="portal-form">
        <label>Name<input required maxLength={255} value={name} disabled={saving}
          onChange={event => setName(event.target.value)} placeholder="Zum Beispiel: Wissen im Kundenservice" /></label>
        <label>Beschreibung <span className="portal-optional">optional</span>
          <textarea rows={3} value={description} disabled={saving} onChange={event => setDescription(event.target.value)}
            placeholder="Welche Fragen soll dieser Wissensbereich beantworten?" />
        </label>
        <fieldset disabled={saving || !config}>
          <legend>Wer darf dieses Wissen abfragen?</legend>
          <label className="portal-choice"><input type="radio" name="readers" checked={!shareAll}
            onChange={() => { setShareAll(false); setConfirmed(false); }} disabled={!hasOwnTeams} />
            <span>Ausgewählte Teams</span>
          </label>
          {!shareAll && hasOwnTeams && <div className="ml-6">
            {config?.team_names.map(team => <label className="portal-choice" key={team}>
              <input type="checkbox" checked={selectedTeams.includes(team)} onChange={() => toggleTeam(team)} disabled={saving} />
              {team}
            </label>)}
          </div>}
          <label className="portal-choice"><input type="radio" name="readers" checked={shareAll}
            onChange={() => setShareAll(true)} />Alle angemeldeten Teams</label>
          {config && !hasOwnTeams && <p className="portal-field-hint">Für einen eingeschränkten Wissensbereich muss dir die Administration zuerst ein Team zuordnen.</p>}
          {shareAll && <label className="portal-choice portal-access-confirm"><input type="checkbox" checked={confirmed}
            onChange={event => setConfirmed(event.target.checked)} />Ich bestätige, dass freigegebene Inhalte allen angemeldeten Teams zur Verfügung stehen dürfen.</label>}
        </fieldset>
        <p className="portal-field-hint">Du und die Administration verwalten den Wissensbereich. Du legst fest, welche Teams als Berechtigte freigegebene Inhalte über Chat, Bots und Suche verwenden dürfen.</p>
        <div className="portal-form-actions">
          <Button type="submit" disabled={!canSave || saving}>
            {saving ? 'Wird angelegt …' : 'Anlegen und Quelle hinzufügen'}<ArrowRight size={16} aria-hidden="true" />
          </Button>
          {!saving && <Link href="/knowledge" className={buttonVariants({ variant: 'ghost' })}>Abbrechen</Link>}
        </div>
      </form>
    </section>
  </PortalPage>;
}
