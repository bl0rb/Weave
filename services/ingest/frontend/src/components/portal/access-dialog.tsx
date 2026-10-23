'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/button';
import { ErrorNotice, Modal } from '@/components/admin/admin-shared';
import { portalError, updateCollectionAccess, type CollectionGrant, type KnowledgeSpace } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { AccessModeStep, AccessPersonsStep, AccessReviewRows, AccessTeamsStep, ReviewProblems, StepNav, useAccessDraft } from './access-steps';

/** Minimal shape the dialog needs from a collection -- both the portal cards'
 * KnowledgeSpace and the admin tab's ManagedCollection satisfy this. */
export type AccessDialogCollection = {
  collection_id: string;
  name: string;
  visibility: 'public' | 'restricted';
  grants: CollectionGrant[];
};

// Same step layout as the bot editor (bots-tab.tsx): free navigation, save from any step.
const STEPS = ['mode', 'persons', 'teams', 'review'] as const;
type AccessStep = typeof STEPS[number];

/**
 * Knowledge-space access dialog (ADR 0008): who may read the space, and
 * which persons and teams are owners, members or readers. Reuses the app's
 * existing Modal (admin-shared.tsx), already used for the other portal
 * editors (KnowledgeSpaceEditor, ConfirmDialog) and already dark-mode-safe
 * via globals.css's html.dark overrides.
 */
export function AccessDialog({ collection, onClose, onSaved }: {
  collection: AccessDialogCollection;
  onClose: () => void;
  /** Called once PATCH succeeds, with the backend's fresh collection -- the caller merges it into its own state instead of reloading. */
  onSaved: (updated: KnowledgeSpace) => void;
}) {
  const { t, locale } = useI18n();
  const draft = useAccessDraft({
    initialMode: collection.visibility,
    initialPersons: collection.grants.filter(grant => grant.user_id)
      .map(grant => ({ id: grant.user_id as string, username: grant.name, team: grant.team, role: grant.role, isActive: grant.is_active })),
    initialTeamRoles: Object.fromEntries(collection.grants.filter(grant => grant.team_id)
      .map(grant => [grant.team_id as string, grant.role === 'reader' ? 'reader' : 'member'])),
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [step, setStep] = useState<AccessStep>('mode');

  // Modal itself has no focus trap or return -- restore focus to whatever
  // opened the dialog (the "Ändern"/"Zugriff ändern" button) once it closes.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    return () => opener?.focus?.();
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft.hasOwner) return;
    setSaving(true);
    setError('');
    try {
      onSaved(await updateCollectionAccess(collection.collection_id, { visibility: draft.mode, grants: draft.grants() }));
    } catch (err) {
      setError(portalError(err, locale));
      setSaving(false);
    }
  }

  const stepIndex = STEPS.indexOf(step);
  const stepLabel = (item: AccessStep) => t(`portal.access.dialog.step.${item}`);
  const problems: Partial<Record<AccessStep, string[]>> = draft.hasOwner ? {} : { persons: [t('portal.access.dialog.ownerRequired')] };

  return <Modal size="lg" title={collection.name} onClose={onClose} footer={<>
    <Button type="button" variant="outline" disabled={saving} onClick={onClose}>{t('common.cancel')}</Button>
    {stepIndex > 0 && <Button type="button" variant="outline" disabled={saving} onClick={() => setStep(STEPS[stepIndex - 1])}>{t('portal.access.dialog.back')}</Button>}
    {stepIndex < STEPS.length - 1 && <Button type="button" variant="outline" disabled={saving} onClick={() => setStep(STEPS[stepIndex + 1])}>{t('portal.access.dialog.next')}</Button>}
    <Button type="submit" form="access-dialog-form" disabled={saving || !draft.hasOwner}>{saving ? t('portal.access.dialog.saving') : t('portal.access.dialog.save')}</Button>
  </>}>
    <p className="portal-eyebrow">{t('portal.access.dialog.eyebrow')}</p>
    <ErrorNotice message={error || null} />
    <StepNav steps={STEPS} current={step} onSelect={setStep} label={t('portal.access.dialog.stepsLabel')} stepLabel={stepLabel}
      counts={{ persons: draft.persons.length, ...(draft.selectedTeams.length ? { teams: draft.selectedTeams.length } : {}) }} problems={problems} />
    <form id="access-dialog-form" className="portal-access-form" onSubmit={save} noValidate>
      {step === 'mode' && <AccessModeStep draft={draft} disabled={saving} intro={<p className="text-[15px] text-[var(--ink-2)]">{t('portal.access.dialog.intro')}</p>} />}
      {step === 'persons' && <AccessPersonsStep draft={draft} disabled={saving} />}
      {step === 'teams' && <AccessTeamsStep draft={draft} disabled={saving} />}
      {step === 'review' && <section className="space-y-4" aria-label={stepLabel('review')}>
        <dl className="grid gap-x-6 gap-y-3 text-[15px] sm:grid-cols-[max-content_1fr]"><AccessReviewRows draft={draft} /></dl>
        <ReviewProblems problems={problems} stepLabel={stepLabel} onSelect={setStep} />
      </section>}
      <p className="portal-access-summary" aria-live="polite"><strong>{draft.summary}</strong></p>
    </form>
  </Modal>;
}
