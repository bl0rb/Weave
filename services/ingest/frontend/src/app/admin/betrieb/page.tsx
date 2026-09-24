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

type Bereich = 'sicherung' | 'identitaeten' | 'werkzeuge';

const TABS: { id: Bereich; label: string; icon: typeof DatabaseBackup }[] = [
  { id: 'sicherung', label: 'Sicherung & Wiederherstellung', icon: DatabaseBackup },
  { id: 'identitaeten', label: 'Technische Identitäten', icon: ShieldCheck },
  { id: 'werkzeuge', label: 'Werkzeuge', icon: Wrench },
];
const IDS = TABS.map((t) => t.id);

/** Link grid carried over unchanged from the old "Werkzeuge" tab. */
const WERKZEUGE_LINKS: { href: string; title: string; description: string }[] = [
  { href: '/connections', title: 'Verbindungen', description: 'Confluence und Webhooks einrichten und prüfen.' },
  { href: '/benchmark', title: 'Qualität vergleichen', description: 'OCR- und Vision-Profile mit denselben Dokumenten vergleichen.' },
  { href: '/jobs', title: 'Auftragsverwaltung', description: 'Alle sichtbaren Aufträge, technische Details und Wiederholungen.' },
  { href: '/imports', title: 'Confluence-Importe', description: 'Importfortschritt, Quellen und Fehler im Detail.' },
  { href: '/processing/new', title: 'Verarbeitung testen', description: 'Dateien mit erweiterten OCR-Einstellungen verarbeiten.' },
];

export default function AdminBetriebPage() {
  return (
    <Suspense fallback={<main className="min-h-screen" />}>
      <AdminBetriebPageInner />
    </Suspense>
  );
}

function AdminBetriebPageInner() {
  const [bereich, setBereich] = useBereich<Bereich>(IDS, 'sicherung');
  const [backupOpen, setBackupOpen] = useState(false);
  const [backupBusy, setBackupBusy] = useState(false);

  const downloadBackup = async () => {
    setBackupBusy(true);
    try {
      const response = await apiFetch('/api/v1/admin/backup.zip');
      if (!response.ok) throw new Error('Backup konnte nicht erstellt werden.');
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
        title="Betrieb & Sicherheit"
        description="Sichern, integrieren und warten."
        actions={
          <Button type="button" variant="outline" onClick={() => setBackupOpen(true)}>
            <Archive size={16} />
            Backup herunterladen
          </Button>
        }
      />
      <SectionTabs idPrefix="betrieb" ariaLabel="Bereich" tabs={TABS} active={bereich} onChange={setBereich} />
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
              title="Werkzeuge für die Administration"
              description="Verbindungen prüfen, Verarbeitungsprofile vergleichen und einzelne Aufträge untersuchen."
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
          title="Storage-Backup herunterladen"
          body={<p>Das ZIP enthält alle lokalen Upload- und Ergebnisdateien. Der Download kann vertrauliche Inhalte enthalten. Fortfahren?</p>}
          confirmLabel={backupBusy ? 'Wird erstellt…' : 'Backup herunterladen'}
          onClose={() => {
            if (!backupBusy) setBackupOpen(false);
          }}
          onConfirm={downloadBackup}
        />
      )}
    </AdminPageShell>
  );
}
