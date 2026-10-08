'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import { ApiError, apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { jsonBody, portalError, type KnowledgeSpace, type PortalConfig } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { Notice, PortalPage, RequiredMark } from './shared';

export function NewKnowledgeSpace() {
  const router = useRouter();
  const { t, locale } = useI18n();
  const [config, setConfig] = useState<PortalConfig | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [purpose, setPurpose] = useState('');
  const [responsibleTeam, setResponsibleTeam] = useState('');
  const [memberTeams, setMemberTeams] = useState<string[]>([]);
  const [shareAll, setShareAll] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const load = useCallback(() => apiJson<PortalConfig>('/api/v1/portal/config')
    .then(configuration => {
      setConfig(configuration);
      // Suggest the first own team as responsible and as member team
      // (ADR 0008); both can be changed or removed before saving.
      const first = configuration.teams[0]?.id ?? '';
      setResponsibleTeam(first);
      setMemberTeams(first ? [first] : []);
      setError('');
    })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);

  const canSave = Boolean(name.trim() && purpose.trim() && config && (!shareAll || confirmed));

  function toggleTeam(team: string) {
    setMemberTeams(current => current.includes(team) ? current.filter(value => value !== team) : [...current, team]);
  }
  function chooseResponsibleTeam(team: string) {
    setResponsibleTeam(team);
    if (team) setMemberTeams(current => current.includes(team) ? current : [...current, team]);
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (saving || !canSave) return;
    setSaving(true);
    setError('');
    try {
      const space = await apiJson<KnowledgeSpace>('/api/v1/collections', jsonBody({
        name: name.trim(),
        description: purpose.trim(),
        responsible_team_id: responsibleTeam || null,
        visibility: shareAll ? 'public' : 'restricted',
        grants: memberTeams.map(team_id => ({ team_id, role: 'member' })),
      }));
      // Continue the journey with the new area already selected.
      router.replace(`/sources/new?collection=${encodeURIComponent(space.collection_id)}`);
    } catch (err) {
      // The only conflict here: a space the user can see already has this name.
      setError(err instanceof ApiError && err.status === 409 ? t('portal.spaces.nameTaken') : portalError(err, locale));
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
        <label>{t('common.name')}<RequiredMark /><input required maxLength={255} value={name} disabled={saving}
          onChange={event => setName(event.target.value)} placeholder={t('portal.newSpace.namePlaceholder')} /></label>
        <label>{t('portal.newSpace.purposeLabel')}<RequiredMark />
          <textarea required rows={3} value={purpose} disabled={saving} onChange={event => setPurpose(event.target.value)}
            placeholder={t('portal.newSpace.purposePlaceholder')} />
        </label>
        <label>{t('portal.newSpace.responsibleTeam')} <span className="portal-optional">{t('common.optional')}</span>
          <select value={responsibleTeam} disabled={saving || !config} onChange={event => chooseResponsibleTeam(event.target.value)}>
            <option value="">{t('portal.newSpace.noResponsibleTeam')}</option>
            {config?.teams.map(team => <option key={team.id} value={team.id}>{team.name}</option>)}
          </select>
        </label>
        {Boolean(config?.teams.length) && <fieldset disabled={saving}>
          <legend>{t('portal.newSpace.memberTeamsLegend')}</legend>
          {config?.teams.map(team => <label className="portal-choice" key={team.id}>
            <input type="checkbox" checked={memberTeams.includes(team.id)} onChange={() => toggleTeam(team.id)} />
            {team.name}
          </label>)}
          <p className="portal-field-hint">{t('portal.newSpace.memberTeamsHint')}</p>
        </fieldset>}
        <fieldset disabled={saving || !config}>
          <legend>{t('portal.newSpace.legend')}</legend>
          <label className="portal-choice"><input type="radio" name="readers" checked={!shareAll}
            onChange={() => { setShareAll(false); setConfirmed(false); }} />
            <span>{t('portal.newSpace.teamsOnly')}</span>
          </label>
          <label className="portal-choice"><input type="radio" name="readers" checked={shareAll}
            onChange={() => setShareAll(true)} />{t('portal.newSpace.allTeams')}</label>
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
