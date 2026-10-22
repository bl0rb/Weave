'use client';

import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import { ApiError, apiJson } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import type { AuthUser } from '@/lib/auth-types';
import { Button, buttonVariants } from '@/components/ui/button';
import { BackLink } from '@/components/back-link';
import { jsonBody, portalError, type KnowledgeSpace, type PortalConfig } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { Notice, PortalPage, RequiredMark } from './shared';
import { AccessModeStep, AccessPersonsStep, AccessReviewRows, AccessTeamsStep, ReviewProblems, StepNav, useAccessDraft } from './access-steps';

// Guided like the bot editor and the access dialog: purpose first, then the
// same access steps as "Ändern" on an existing space, then a review.
const STEPS = ['basics', 'mode', 'persons', 'teams', 'review'] as const;
type NewSpaceStep = typeof STEPS[number];

export function NewKnowledgeSpace() {
  const { t, locale } = useI18n();
  const { user } = useAuth();
  const [config, setConfig] = useState<PortalConfig | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => apiJson<PortalConfig>('/api/v1/portal/config')
    .then(configuration => { setConfig(configuration); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);

  return <PortalPage title={t('portal.chrome.breadcrumb.knowledgeNew')} eyebrow={null}
    description={t('portal.newSpace.description')}>
    <BackLink className="portal-back" fallbackHref="/knowledge" fallbackLabel={t('portal.newSpace.backLink')} />
    {error && !config && <Notice error action={load}>{error}</Notice>}
    {(!config || !user) && !error && <Notice>{t('portal.newSpace.loadingTeams')}</Notice>}
    {/* Mounted once the config is there: the access draft starts from its own teams. */}
    {config && user && <NewSpaceWizard config={config} user={user} />}
  </PortalPage>;
}

function NewSpaceWizard({ config, user }: { config: PortalConfig; user: AuthUser }) {
  const router = useRouter();
  const { t, locale } = useI18n();
  // Suggest the first own team as responsible and as member team (ADR 0008);
  // both can be changed or removed before saving.
  const firstTeam = config.teams[0]?.id ?? '';
  const fallbackTeams = useMemo(() => config.teams.map(team => ({ id: team.id, name: team.name, member_count: -1 })), [config.teams]);
  const draft = useAccessDraft({
    initialMode: 'restricted',
    // The creator always becomes owner (backend); shown fixed so the reach is right.
    initialPersons: [{ id: user.id ?? 'me', username: user.username, role: 'owner', locked: true }],
    initialTeamRoles: firstTeam ? { [firstTeam]: 'member' } : {},
    fallbackTeams,
  });
  const [step, setStep] = useState<NewSpaceStep>('basics');
  const [name, setName] = useState('');
  const [purpose, setPurpose] = useState('');
  const [responsibleTeam, setResponsibleTeam] = useState(firstTeam);
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const problems: Partial<Record<NewSpaceStep, string[]>> = {};
  const basicsProblems = [...(name.trim() ? [] : [t('portal.newSpace.problem.name')]), ...(purpose.trim() ? [] : [t('portal.newSpace.problem.purpose')])];
  if (basicsProblems.length) problems.basics = basicsProblems;
  if (draft.mode === 'public' && !confirmed) problems.mode = [t('portal.newSpace.problem.confirm')];
  const canSave = !Object.keys(problems).length;
  const stepIndex = STEPS.indexOf(step);
  const stepLabel = (item: NewSpaceStep) => item === 'basics' ? t('portal.newSpace.step.basics') : t(`portal.access.dialog.step.${item}`);

  function chooseResponsibleTeam(team: string) {
    setResponsibleTeam(team);
    if (team) draft.toggleTeam(team, true);
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
        visibility: draft.mode,
        grants: draft.grants(),
      }));
      // Continue with the new area already selected; the form stays disabled while navigating.
      // Choosing Confluence (and setting up a connection) happens in the source form's step 02.
      router.replace(`/sources/new?collection=${encodeURIComponent(space.collection_id)}`);
    } catch (err) {
      // The only conflict here: a space the user can see already has this name.
      setError(err instanceof ApiError && err.status === 409 ? t('portal.spaces.nameTaken') : portalError(err, locale));
      setSaving(false);
    }
  }

  const responsibleName = config.teams.find(team => team.id === responsibleTeam)?.name;
  return <section className="portal-panel portal-form-panel" aria-label={t('portal.chrome.breadcrumb.knowledgeNew')}>
    {error && <Notice error>{error}</Notice>}
    <StepNav steps={STEPS} current={step} onSelect={setStep} label={t('portal.newSpace.stepsLabel')} stepLabel={stepLabel}
      counts={{ persons: draft.persons.length, ...(draft.selectedTeams.length ? { teams: draft.selectedTeams.length } : {}) }} problems={problems} />
    <form onSubmit={create} className="portal-form portal-access-form" noValidate>
      {step === 'basics' && <section className="grid gap-4" aria-label={stepLabel('basics')}>
        <label>{t('common.name')}<RequiredMark /><input required maxLength={255} value={name} disabled={saving} autoFocus
          onChange={event => setName(event.target.value)} placeholder={t('portal.newSpace.namePlaceholder')} /></label>
        <label>{t('portal.newSpace.purposeLabel')}<RequiredMark />
          <textarea required rows={3} value={purpose} disabled={saving} onChange={event => setPurpose(event.target.value)}
            placeholder={t('portal.newSpace.purposePlaceholder')} />
        </label>
        <label>{t('portal.newSpace.responsibleTeam')} <span className="portal-optional">{t('common.optional')}</span>
          <select value={responsibleTeam} disabled={saving} onChange={event => chooseResponsibleTeam(event.target.value)}>
            <option value="">{t('portal.newSpace.noResponsibleTeam')}</option>
            {config.teams.map(team => <option key={team.id} value={team.id}>{team.name}</option>)}
          </select>
          <span className="portal-field-hint">{t('portal.newSpace.responsibleHint')}</span>
        </label>
      </section>}
      {step === 'mode' && <AccessModeStep draft={draft} disabled={saving}>
        {draft.mode === 'public' && <label className="portal-choice portal-access-confirm"><input type="checkbox" checked={confirmed} disabled={saving}
          onChange={event => setConfirmed(event.target.checked)} />{t('portal.newSpace.confirmAllTeams')}</label>}
      </AccessModeStep>}
      {step === 'persons' && <AccessPersonsStep draft={draft} disabled={saving} />}
      {step === 'teams' && <AccessTeamsStep draft={draft} disabled={saving} />}
      {step === 'review' && <section className="space-y-4" aria-label={stepLabel('review')}>
        <dl className="grid gap-x-6 gap-y-3 text-[15px] sm:grid-cols-[max-content_1fr]">
          <dt className="font-medium text-[var(--ink-2)]">{t('portal.newSpace.review.name')}</dt><dd>{name.trim() || '—'}</dd>
          <dt className="font-medium text-[var(--ink-2)]">{t('portal.newSpace.review.purpose')}</dt><dd className="whitespace-pre-wrap">{purpose.trim() || '—'}</dd>
          <dt className="font-medium text-[var(--ink-2)]">{t('portal.newSpace.review.responsible')}</dt><dd>{responsibleName ?? '—'}</dd>
          <AccessReviewRows draft={draft} />
        </dl>
        <ReviewProblems problems={problems} stepLabel={stepLabel} onSelect={setStep} />
      </section>}
      <p className="portal-access-summary" aria-live="polite"><strong>{draft.summary}</strong></p>
      <div className="portal-form-actions">
        {step === 'review'
          ? <Button type="submit" disabled={!canSave || saving}>
            {saving ? t('portal.newSpace.creating') : t('portal.newSpace.submit')}<ArrowRight size={16} aria-hidden="true" />
          </Button>
          : <Button type="button" disabled={saving} onClick={() => setStep(STEPS[stepIndex + 1])}>{t('portal.access.dialog.next')}</Button>}
        {stepIndex > 0 && <Button type="button" variant="outline" disabled={saving} onClick={() => setStep(STEPS[stepIndex - 1])}>{t('portal.access.dialog.back')}</Button>}
        {!saving && <Link href="/knowledge" className={buttonVariants({ variant: 'ghost' })}>{t('common.cancel')}</Link>}
      </div>
    </form>
  </section>;
}
