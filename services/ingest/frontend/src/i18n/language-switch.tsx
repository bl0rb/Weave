'use client';

import { useState } from 'react';
import { LOCALES, type Locale } from './config';
import { useI18n } from './provider';
import { apiJson } from '@/lib/api';

/**
 * Two-option segmented control; the choice is stored in the shared
 * `weave_locale` cookie. With `persist` (the signed-in sidebar profile
 * menu, not the login/setup pages) it also saves the choice to the
 * account via PATCH /api/v1/auth/me, optimistically: the new language is
 * applied to this browser immediately regardless of whether that save
 * succeeds — a failure only shows a small inline notice next to the
 * control, it never reverts the choice (see auth-context.tsx's own
 * mount-time sync for the read side of this).
 */
export function LanguageSwitch({ className, persist = false }: { className?: string; persist?: boolean }) {
  const { locale, setLocale, t } = useI18n();
  const [saveFailed, setSaveFailed] = useState(false);

  async function select(option: Locale) {
    if (option === locale) return;
    setLocale(option);
    setSaveFailed(false);
    if (!persist) return;
    try {
      await apiJson('/api/v1/auth/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locale: option }),
        skipAuthRedirect: true,
      });
    } catch {
      setSaveFailed(true);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <div role="group" aria-label={t('common.language')} className={`portal-lang-switch ${className ?? ''}`}>
        {LOCALES.map((option) => (
          <button key={option} type="button" lang={option} aria-pressed={locale === option} onClick={() => select(option)}>
            {t(`common.language.${option}`)}
          </button>
        ))}
      </div>
      {saveFailed ? (
        <p role="alert" className="text-[11px] text-red-600">
          {t('common.language.saveFailed')}
        </p>
      ) : null}
    </div>
  );
}
