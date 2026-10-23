'use client';

import { Suspense } from 'react';
import { DatabaseBackup, ShieldCheck, SlidersHorizontal, Wrench } from 'lucide-react';

import {
  AdminPageShell,
  PageHead,
  SectionPanel,
  SectionTabs,
  useBereich,
} from '@/components/admin/admin-page-shared';
import { BackupTab } from '@/components/admin/backup-tab';
import { ConfigTab } from '@/components/admin/config-tab';
import { TechnicalIdentitiesTab } from '@/components/admin/technical-identities-tab';
import { IndexMaintenanceSection } from '@/components/admin/retrieval-provider-tab';
import { useI18n } from '@/i18n/provider';

type Bereich = 'sicherung' | 'konfiguration' | 'identitaeten' | 'werkzeuge';

export default function AdminBetriebPage() {
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <AdminBetriebPageInner />
    </Suspense>
  );
}

function AdminBetriebPageInner() {
  const { t } = useI18n();
  const TABS: { id: Bereich; label: string; icon: typeof DatabaseBackup }[] = [
    { id: 'sicherung', label: t('admin.operations.tab.backup'), icon: DatabaseBackup },
    { id: 'konfiguration', label: t('admin.operations.tab.config'), icon: SlidersHorizontal },
    { id: 'identitaeten', label: t('admin.operations.tab.identities'), icon: ShieldCheck },
    { id: 'werkzeuge', label: t('admin.operations.tab.tools'), icon: Wrench },
  ];
  const IDS = TABS.map((tab) => tab.id);
  const [bereich, setBereich] = useBereich<Bereich>(IDS, 'sicherung');
  return (
    <AdminPageShell>
      <PageHead
        title={t('admin.operations.pageTitle')}
        description={t('admin.operations.pageDescription')}
      />
      <SectionTabs idPrefix="betrieb" ariaLabel={t('admin.nav.section')} tabs={TABS} active={bereich} onChange={setBereich} />
      {bereich === 'sicherung' && (
        <SectionPanel idPrefix="betrieb" id="sicherung">
          <BackupTab />
        </SectionPanel>
      )}
      {bereich === 'konfiguration' && (
        <SectionPanel idPrefix="betrieb" id="konfiguration">
          <ConfigTab />
        </SectionPanel>
      )}
      {bereich === 'identitaeten' && (
        <SectionPanel idPrefix="betrieb" id="identitaeten">
          <TechnicalIdentitiesTab />
        </SectionPanel>
      )}
      {bereich === 'werkzeuge' && (
        <SectionPanel idPrefix="betrieb" id="werkzeuge">
          <IndexMaintenanceSection />
        </SectionPanel>
      )}
    </AdminPageShell>
  );
}
