'use client';

import { Suspense } from 'react';
import { Bot, MessageSquareText, ScanEye } from 'lucide-react';

import {
  AdminPageShell,
  PageHead,
  SectionPanel,
  SectionTabs,
  useBereich,
} from '@/components/admin/admin-page-shared';
import { BotsTab } from '@/components/admin/bots-tab';
import { ChatProviderTab } from '@/components/admin/chat-provider-tab';
import { RetrievalProviderTab } from '@/components/admin/retrieval-provider-tab';

type Bereich = 'bots' | 'chat-llm' | 'suche-modelle';

const TABS: { id: Bereich; label: string; icon: typeof Bot }[] = [
  { id: 'bots', label: 'Bots', icon: Bot },
  { id: 'chat-llm', label: 'Chat & LLM', icon: MessageSquareText },
  { id: 'suche-modelle', label: 'Suche & Modelle', icon: ScanEye },
];
const IDS = TABS.map((t) => t.id);

export default function AdminWissenPage() {
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <AdminWissenPageInner />
    </Suspense>
  );
}

function AdminWissenPageInner() {
  const [bereich, setBereich] = useBereich<Bereich>(IDS, 'bots');

  return (
    <AdminPageShell>
      <PageHead
        title="Wissen & Assistenten"
        description="Wie Weave passende Textstellen findet und daraus Antworten formuliert."
      />
      <SectionTabs idPrefix="wissen" ariaLabel="Bereich" tabs={TABS} active={bereich} onChange={setBereich} />
      {bereich === 'bots' && (
        <SectionPanel idPrefix="wissen" id="bots">
          <BotsTab />
        </SectionPanel>
      )}
      {bereich === 'chat-llm' && (
        <SectionPanel idPrefix="wissen" id="chat-llm">
          <ChatProviderTab />
        </SectionPanel>
      )}
      {bereich === 'suche-modelle' && (
        <SectionPanel idPrefix="wissen" id="suche-modelle">
          <RetrievalProviderTab />
        </SectionPanel>
      )}
    </AdminPageShell>
  );
}
