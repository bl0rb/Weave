'use client';

import { Suspense } from 'react';
import { FolderOpen, KeyRound, Users, UsersRound } from 'lucide-react';

import {
  AdminPageShell,
  PageHead,
  SectionPanel,
  SectionTabs,
  useBereich,
} from '@/components/admin/admin-page-shared';
import { UsersTab } from '@/components/admin/users-tab';
import { TeamsTab } from '@/components/admin/teams-tab';
import { ProvidersTab } from '@/components/admin/providers-tab';
import { CollectionsTab } from '@/components/admin/collections-tab';
import { useI18n } from '@/i18n/provider';

type Bereich = 'personen' | 'teams' | 'anmeldung' | 'zugriff';

export default function AdminMenschenPage() {
  // useSearchParams (via useBereich) requires a Suspense boundary, same as
  // app/connections/page.tsx.
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <AdminMenschenPageInner />
    </Suspense>
  );
}

function AdminMenschenPageInner() {
  const { t } = useI18n();
  const TABS: { id: Bereich; label: string; icon: typeof Users }[] = [
    { id: 'personen', label: t('admin.people.tab.people'), icon: Users },
    { id: 'teams', label: t('admin.people.tab.teams'), icon: UsersRound },
    { id: 'anmeldung', label: t('admin.people.tab.signIn'), icon: KeyRound },
    { id: 'zugriff', label: t('admin.people.tab.access'), icon: FolderOpen },
  ];
  const IDS = TABS.map((tab) => tab.id);
  const [bereich, setBereich] = useBereich<Bereich>(IDS, 'personen');

  return (
    <AdminPageShell>
      <PageHead title={t('admin.people.pageTitle')} description={t('admin.people.pageDescription')} />
      <SectionTabs idPrefix="menschen" ariaLabel={t('admin.nav.section')} tabs={TABS} active={bereich} onChange={setBereich} />
      {bereich === 'personen' && (
        <SectionPanel idPrefix="menschen" id="personen">
          <UsersTab />
        </SectionPanel>
      )}
      {bereich === 'teams' && (
        <SectionPanel idPrefix="menschen" id="teams">
          <TeamsTab />
        </SectionPanel>
      )}
      {bereich === 'anmeldung' && (
        <SectionPanel idPrefix="menschen" id="anmeldung">
          <ProvidersTab />
        </SectionPanel>
      )}
      {bereich === 'zugriff' && (
        <SectionPanel idPrefix="menschen" id="zugriff">
          <CollectionsTab />
        </SectionPanel>
      )}
    </AdminPageShell>
  );
}
