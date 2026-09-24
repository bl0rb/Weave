'use client';

import { LOCALES } from './config';
import { useI18n } from './provider';

/** Two-option segmented control; the choice is stored in the shared `weave_locale` cookie. */
export function LanguageSwitch({ className }: { className?: string }) {
  const { locale, setLocale, t } = useI18n();
  return (
    <div role="group" aria-label={t('common.language')} className={`chat-lang-switch ${className ?? ''}`}>
      {LOCALES.map((option) => (
        <button
          key={option}
          type="button"
          lang={option}
          aria-pressed={locale === option}
          onClick={() => option !== locale && setLocale(option)}
        >
          {t(`common.language.${option}`)}
        </button>
      ))}
    </div>
  );
}
