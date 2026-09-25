'use client';

import { useRef, useState } from 'react';
import { LOCALES, type Locale } from './config';
import { useI18n } from './provider';
import { putJson } from '@/lib/api-client';

/**
 * Two-option segmented control; the choice is stored in the shared
 * `weave_locale` cookie. With `persist` (the signed-in rail footer, not
 * the login page) it also saves the choice to the account via PUT
 * /api/session/locale, optimistically: the new language is applied to
 * this browser immediately regardless of whether that save succeeds — a
 * failure only shows a small inline notice next to the control, it never
 * reverts the choice (see chat-app.tsx's own mount-time sync for the read
 * side of this).
 */
export function LanguageSwitch({ className, persist = false }: { className?: string; persist?: boolean }) {
  const { locale, setLocale, t } = useI18n();
  const [saveFailed, setSaveFailed] = useState(false);
  // Saves run strictly one after another and a superseded one is skipped,
  // so the account always ends on the last choice (never an older request
  // landing late), and only the newest save may report a failure.
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const latestSave = useRef(0);

  function select(option: Locale) {
    if (option === locale) return;
    setLocale(option);
    setSaveFailed(false);
    if (!persist) return;
    const seq = ++latestSave.current;
    saveQueue.current = saveQueue.current.then(async () => {
      if (seq !== latestSave.current) return;
      // Never let the queue reject, or every later save would be skipped.
      const result = await putJson<{ locale: Locale }>('/api/session/locale', { locale: option }).catch(() => null);
      if (!result?.ok && seq === latestSave.current) setSaveFailed(true);
    });
  }

  return (
    <div className="flex flex-col gap-1">
      <div role="group" aria-label={t('common.language')} className={`chat-lang-switch ${className ?? ''}`}>
        {LOCALES.map((option) => (
          <button key={option} type="button" lang={option} aria-pressed={locale === option} onClick={() => select(option)}>
            {t(`common.language.${option}`)}
          </button>
        ))}
      </div>
      {saveFailed ? (
        <p role="alert" className="text-[11px] text-[var(--err)]">
          {t('common.language.saveFailed')}
        </p>
      ) : null}
    </div>
  );
}
