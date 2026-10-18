'use client';

import { Suspense } from 'react';
import { Cpu, ScanEye, Terminal } from 'lucide-react';

import {
  AdminPageShell,
  PageHead,
  SectionPanel,
  SectionTabs,
  useBereich,
} from '@/components/admin/admin-page-shared';
import { VlConnectionsTab } from '@/components/admin/vl-connections-tab';
import { PaddleTab } from '@/components/admin/paddle-tab';
import { LogsTab } from '@/components/admin/logs-tab';
import { useI18n } from '@/i18n/provider';

type Bereich = 'dokument-ki' | 'ocr' | 'worker-logs';

export default function AdminVerarbeitungPage() {
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <AdminVerarbeitungPageInner />
    </Suspense>
  );
}

function AdminVerarbeitungPageInner() {
  const { t } = useI18n();
  const TABS: { id: Bereich; label: string; icon: typeof ScanEye }[] = [
    { id: 'dokument-ki', label: t('admin.processing.tab.documentAi'), icon: ScanEye },
    { id: 'ocr', label: t('admin.processing.tab.ocr'), icon: Cpu },
    { id: 'worker-logs', label: t('admin.processing.tab.workerLogs'), icon: Terminal },
  ];
  const IDS = TABS.map((tab) => tab.id);
  const [bereich, setBereich] = useBereich<Bereich>(IDS, 'dokument-ki');

  return (
    <AdminPageShell>
      <PageHead title={t('admin.processing.pageTitle')} description={t('admin.processing.pageDescription')} />
      <SectionTabs idPrefix="verarbeitung" ariaLabel={t('admin.nav.section')} tabs={TABS} active={bereich} onChange={setBereich} />
      {bereich === 'dokument-ki' && (
        <SectionPanel idPrefix="verarbeitung" id="dokument-ki">
          <VlConnectionsTab />
        </SectionPanel>
      )}
      {bereich === 'ocr' && (
        <SectionPanel idPrefix="verarbeitung" id="ocr">
          <PaddleTab />
        </SectionPanel>
      )}
      {bereich === 'worker-logs' && (
        <SectionPanel idPrefix="verarbeitung" id="worker-logs">
          <LogsTab />
        </SectionPanel>
      )}
    </AdminPageShell>
  );
}
