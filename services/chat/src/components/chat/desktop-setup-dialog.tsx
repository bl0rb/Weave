'use client';

import { useEffect, useState } from 'react';
import { Download, Monitor, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';

type DesktopSetup = { server_url: string; setup_url: string; download_url: string };

/**
 * "Desktop-App einrichten": opens the `weave://setup?server=...` link the
 * desktop app (bl0rb/WeaveDesktop) registers, so it picks up this
 * deployment's Weave-API address and asks the user to confirm it there.
 * The browser shows its own "open Weave?" prompt; with no app installed
 * nothing happens, hence the download link and the address to type in.
 */
export function DesktopSetupDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const [setup, setSetup] = useState<DesktopSetup | null>(null);
  const [failed, setFailed] = useState(false);
  const [opened, setOpened] = useState(false);

  useEffect(() => {
    let active = true;
    fetch('/api/desktop')
      .then(response => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
      .then((body: DesktopSetup) => { if (active) setSetup(body); })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) { if (event.key === 'Escape') onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  function openApp() {
    if (!setup) return;
    window.location.assign(setup.setup_url);
    setOpened(true);
  }

  return <div className="fixed inset-0 z-50 flex items-center justify-center p-0 sm:p-4">
    <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
    <div role="dialog" aria-modal="true" aria-labelledby="desktop-setup-title"
      className="relative flex max-h-[90dvh] w-full max-w-lg flex-col overflow-y-auto rounded-xl border border-[var(--line)] bg-[var(--surface)] shadow-2xl">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
        <h2 id="desktop-setup-title" className="flex items-center gap-2 text-[17px] font-semibold"><Monitor className="h-4 w-4" aria-hidden="true" />{t('chat.desktop.title')}</h2>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label={t('chat.desktop.close')}><X className="h-4 w-4" aria-hidden="true" /></Button>
      </div>
      <div className="space-y-4 p-5 text-sm">
        <p className="text-[var(--muted)]">{t('chat.desktop.intro')}</p>
        {failed && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-red-700">{t('chat.desktop.loadFailed')}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={openApp} disabled={!setup}><Monitor className="h-3.5 w-3.5" aria-hidden="true" />{t('chat.desktop.open')}</Button>
          {setup && <a className="inline-flex items-center gap-1 text-[var(--accent)] underline-offset-2 hover:underline" href={setup.download_url} target="_blank" rel="noreferrer">
            <Download className="h-3.5 w-3.5" aria-hidden="true" />{t('chat.desktop.download')}
          </a>}
        </div>
        {opened && <p role="status" className="rounded-lg bg-[var(--accent-soft)] px-3 py-2">{t('chat.desktop.opened')}</p>}
        {setup && <div className="space-y-1">
          <p className="text-xs text-[var(--muted)]">{t('chat.desktop.manual')}</p>
          <code className="block break-all rounded bg-[var(--surface-2,var(--line))] px-2 py-1 text-xs">{setup.server_url}</code>
        </div>}
        <p className="text-xs text-[var(--muted)]">{t('chat.desktop.safety')}</p>
      </div>
    </div>
  </div>;
}
