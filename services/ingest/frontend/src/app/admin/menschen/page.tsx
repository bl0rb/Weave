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

type Bereich = 'personen' | 'teams' | 'anmeldung' | 'zugriff';

const TABS: { id: Bereich; label: string; icon: typeof Users }[] = [
  { id: 'personen', label: 'Personen', icon: Users },
  { id: 'teams', label: 'Teams', icon: UsersRound },
  { id: 'anmeldung', label: 'Anmeldung', icon: KeyRound },
  { id: 'zugriff', label: 'Wissensbereiche & Zugriff', icon: FolderOpen },
];
const IDS = TABS.map((t) => t.id);

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
  const [bereich, setBereich] = useBereich<Bereich>(IDS, 'personen');

  return (
    <AdminPageShell>
      <PageHead
        title="Menschen & Zugriffe"
        description="Wer Weave nutzt und für wen welches Wissen im Chat freigegeben ist."
      />
      <SectionTabs idPrefix="menschen" ariaLabel="Bereich" tabs={TABS} active={bereich} onChange={setBereich} />
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
