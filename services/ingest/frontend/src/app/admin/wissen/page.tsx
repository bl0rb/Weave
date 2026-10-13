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
import { ChatEndpointsSection, ChatProviderTab } from '@/components/admin/chat-provider-tab';
import { RetrievalProviderTab } from '@/components/admin/retrieval-provider-tab';
import { useI18n } from '@/i18n/provider';
import { useAuth } from '@/lib/auth-context';

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
  const { user } = useAuth();
  const ALL_TABS: { id: Bereich; label: string; icon: typeof Bot }[] = [
    { id: 'bots', label: t('admin.knowledge.tab.bots'), icon: Bot },
    { id: 'chat-llm', label: t('admin.knowledge.tab.chatLlm'), icon: MessageSquareText },
    { id: 'suche-modelle', label: t('admin.knowledge.tab.searchModels'), icon: ScanEye },
  ];
  // Bot admins manage bots only; the LLM and search settings stay with admins.
  const TABS = user?.role === 'admin' ? ALL_TABS : ALL_TABS.filter((tab) => tab.id === 'bots');
  const IDS = ALL_TABS.map((tab) => tab.id);
  const [selected, setBereich] = useBereich<Bereich>(IDS, 'bots');
  const bereich = TABS.some((tab) => tab.id === selected) ? selected : 'bots';

  return (
    <AdminPageShell allowBotAdmin>
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
          <ChatEndpointsSection />
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
