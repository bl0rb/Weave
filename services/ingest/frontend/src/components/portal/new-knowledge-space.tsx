'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { jsonBody, portalError, type KnowledgeSpace, type PortalConfig } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { Notice, PortalPage } from './shared';

export function NewKnowledgeSpace() {
  const router = useRouter();
  const { t, locale } = useI18n();
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
    .catch(err => setError(portalError(err, locale))), [locale]);
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
      setError(portalError(err, locale));
      setSaving(false);
    }
  }

  return <PortalPage title={t('portal.chrome.breadcrumb.knowledgeNew')} eyebrow={t('portal.newSpace.step')}
    description={t('portal.newSpace.description')}>
    <Link className="portal-back" href="/knowledge">{t('portal.newSpace.backLink')}</Link>
    {error && <Notice error action={!config ? load : undefined}>{error}</Notice>}
    {!config && !error && <Notice>{t('portal.newSpace.loadingTeams')}</Notice>}
    <section className="portal-panel portal-form-panel" aria-labelledby="new-space-title">
      <h2 id="new-space-title">{t('portal.newSpace.sectionTitle')}</h2>
      <form onSubmit={create} className="portal-form">
        <label>{t('common.name')}<input required maxLength={255} value={name} disabled={saving}
          onChange={event => setName(event.target.value)} placeholder={t('portal.newSpace.namePlaceholder')} /></label>
        <label>{t('common.description')} <span className="portal-optional">{t('common.optional')}</span>
          <textarea rows={3} value={description} disabled={saving} onChange={event => setDescription(event.target.value)}
            placeholder={t('portal.newSpace.descriptionPlaceholder')} />
        </label>
        <fieldset disabled={saving || !config}>
          <legend>{t('portal.newSpace.legend')}</legend>
          <label className="portal-choice"><input type="radio" name="readers" checked={!shareAll}
            onChange={() => { setShareAll(false); setConfirmed(false); }} disabled={!hasOwnTeams} />
            <span>{t('portal.newSpace.teamsOnly')}</span>
          </label>
          {!shareAll && hasOwnTeams && <div className="ml-6">
            {config?.team_names.map(team => <label className="portal-choice" key={team}>
              <input type="checkbox" checked={selectedTeams.includes(team)} onChange={() => toggleTeam(team)} disabled={saving} />
              {team}
            </label>)}
          </div>}
          <label className="portal-choice"><input type="radio" name="readers" checked={shareAll}
            onChange={() => setShareAll(true)} />{t('portal.newSpace.allTeams')}</label>
          {config && !hasOwnTeams && <p className="portal-field-hint">{t('portal.newSpace.noTeamHint')}</p>}
          {shareAll && <label className="portal-choice portal-access-confirm"><input type="checkbox" checked={confirmed}
            onChange={event => setConfirmed(event.target.checked)} />{t('portal.newSpace.confirmAllTeams')}</label>}
        </fieldset>
        <p className="portal-field-hint">{t('portal.newSpace.footerHint')}</p>
        <div className="portal-form-actions">
          <Button type="submit" disabled={!canSave || saving}>
            {saving ? t('portal.newSpace.creating') : t('portal.newSpace.submit')}<ArrowRight size={16} aria-hidden="true" />
          </Button>
          {!saving && <Link href="/knowledge" className={buttonVariants({ variant: 'ghost' })}>{t('common.cancel')}</Link>}
        </div>
      </form>
    </section>
  </PortalPage>;
}
