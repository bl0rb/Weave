'use client';

import { Suspense, useState } from 'react';
import { Archive, DatabaseBackup, ShieldCheck, Wrench } from 'lucide-react';

import {
  AdminPageShell,
  PageHead,
  SectionPanel,
  SectionTabs,
  ToolLinkCard,
  useBereich,
} from '@/components/admin/admin-page-shared';
import { BackupTab } from '@/components/admin/backup-tab';
import { TechnicalIdentitiesTab } from '@/components/admin/technical-identities-tab';
import { IndexMaintenanceSection } from '@/components/admin/retrieval-provider-tab';
import { ConfirmDialog, SectionCard } from '@/components/admin/admin-shared';
import { Button } from '@/components/ui/button';
import { apiFetch } from '@/lib/api';
import { useI18n } from '@/i18n/provider';

type Bereich = 'sicherung' | 'identitaeten' | 'werkzeuge';

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
    { id: 'identitaeten', label: t('admin.operations.tab.identities'), icon: ShieldCheck },
    { id: 'werkzeuge', label: t('admin.operations.tab.tools'), icon: Wrench },
  ];
  const IDS = TABS.map((tab) => tab.id);
  // Link grid carried over unchanged from the old "Werkzeuge" tab.
  const WERKZEUGE_LINKS: { href: string; title: string; description: string }[] = [
    { href: '/connections', title: t('admin.operations.tools.connections.title'), description: t('admin.operations.tools.connections.description') },
    { href: '/benchmark', title: t('admin.operations.tools.benchmark.title'), description: t('admin.operations.tools.benchmark.description') },
    { href: '/jobs', title: t('admin.operations.tools.jobs.title'), description: t('admin.operations.tools.jobs.description') },
    { href: '/imports', title: t('admin.operations.tools.imports.title'), description: t('admin.operations.tools.imports.description') },
    { href: '/processing/new', title: t('admin.operations.tools.processNew.title'), description: t('admin.operations.tools.processNew.description') },
  ];
  const [bereich, setBereich] = useBereich<Bereich>(IDS, 'sicherung');
  const [backupOpen, setBackupOpen] = useState(false);
  const [backupBusy, setBackupBusy] = useState(false);

  const downloadBackup = async () => {
    setBackupBusy(true);
    try {
      const response = await apiFetch('/api/v1/admin/backup.zip');
      if (!response.ok) throw new Error(t('admin.operations.backupDialog.error'));
      const blob = await response.blob();
      const href = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = 'weave-storage-backup.zip';
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(href);
      setBackupOpen(false);
    } finally {
      setBackupBusy(false);
    }
  };

  return (
    <AdminPageShell>
      <PageHead
        title={t('admin.operations.pageTitle')}
        description={t('admin.operations.pageDescription')}
        actions={
          <Button type="button" variant="outline" onClick={() => setBackupOpen(true)}>
            <Archive size={16} />
            {t('admin.operations.downloadBackup')}
          </Button>
        }
      />
      <SectionTabs idPrefix="betrieb" ariaLabel={t('admin.nav.section')} tabs={TABS} active={bereich} onChange={setBereich} />
      {bereich === 'sicherung' && (
        <SectionPanel idPrefix="betrieb" id="sicherung">
          <BackupTab />
        </SectionPanel>
      )}
      {bereich === 'identitaeten' && (
        <SectionPanel idPrefix="betrieb" id="identitaeten">
          <TechnicalIdentitiesTab />
        </SectionPanel>
      )}
      {bereich === 'werkzeuge' && (
        <SectionPanel idPrefix="betrieb" id="werkzeuge">
          <div className="space-y-6">
            <SectionCard
              title={t('admin.operations.tools.sectionTitle')}
              description={t('admin.operations.tools.sectionDescription')}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                {WERKZEUGE_LINKS.map(({ href, title, description }) => (
                  <ToolLinkCard key={href} href={href} title={title} description={description} />
                ))}
              </div>
            </SectionCard>
            <IndexMaintenanceSection />
          </div>
        </SectionPanel>
      )}

      {backupOpen && (
        <ConfirmDialog
          title={t('admin.operations.backupDialog.title')}
          body={<p>{t('admin.operations.backupDialog.body')}</p>}
          confirmLabel={backupBusy ? t('admin.operations.backupDialog.busy') : t('admin.operations.backupDialog.confirm')}
          onClose={() => {
            if (!backupBusy) setBackupOpen(false);
          }}
          onConfirm={downloadBackup}
        />
      )}
    </AdminPageShell>
  );
}
