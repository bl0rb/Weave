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
import { useI18n } from '@/i18n/provider';

type Bereich = 'bots' | 'chat-llm' | 'suche-modelle';

export default function AdminWissenPage() {
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <AdminWissenPageInner />
    </Suspense>
  );
}

function AdminWissenPageInner() {
  const { t } = useI18n();
  const TABS: { id: Bereich; label: string; icon: typeof Bot }[] = [
    { id: 'bots', label: t('admin.knowledge.tab.bots'), icon: Bot },
    { id: 'chat-llm', label: t('admin.knowledge.tab.chatLlm'), icon: MessageSquareText },
    { id: 'suche-modelle', label: t('admin.knowledge.tab.searchModels'), icon: ScanEye },
  ];
  const IDS = TABS.map((tab) => tab.id);
  const [bereich, setBereich] = useBereich<Bereich>(IDS, 'bots');

  return (
    <AdminPageShell>
      <PageHead title={t('admin.knowledge.pageTitle')} description={t('admin.knowledge.pageDescription')} />
      <SectionTabs idPrefix="wissen" ariaLabel={t('admin.nav.section')} tabs={TABS} active={bereich} onChange={setBereich} />
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
