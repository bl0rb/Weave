'use client';

import { useRouter } from 'next/navigation';
import { Lock, LogOut } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';

/** Shown instead of the chat when Weave-API answers 403 "No knowledge space
 * access": a non-admin without any knowledge-space grant may not use the chat
 * (backend/app/core/auth.py's has_knowledge_access). Logging out is the only
 * thing left to do here. */
export function NoAccess() {
  const { t } = useI18n();
  const router = useRouter();

  async function logout() {
    await fetch('/api/session/logout', { method: 'POST' }).catch(() => {});
    router.replace('/login');
    router.refresh();
  }

  return (
    <main className="flex min-h-[100dvh] items-center justify-center bg-[var(--bg)] px-4">
      <div
        role="alert"
        className="w-full max-w-md rounded-[var(--radius-card)] border border-[var(--line)] bg-[var(--surface)] p-8 text-center shadow-sm"
      >
        <Lock className="mx-auto h-8 w-8 text-[var(--muted)]" aria-hidden="true" />
        <h1 className="mt-4 text-lg font-semibold text-[var(--ink)]">{t('chat.noAccess.title')}</h1>
        <p className="mt-2 text-sm leading-relaxed text-[var(--muted)]">{t('chat.noAccess.text')}</p>
        <Button type="button" variant="outline" className="mt-6 gap-2" onClick={() => void logout()}>
          <LogOut className="h-4 w-4" aria-hidden="true" />
          {t('chat.noAccess.logout')}
        </Button>
      </div>
    </main>
  );
}
