'use client';

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { DEFAULT_LOCALE, INTL_LOCALE, LOCALE_COOKIE, type Locale } from './config';
import { translate, type MessageKey, type MessageVars } from './messages';

type I18nContextValue = { locale: Locale; setLocale: (locale: Locale) => void };

// Without a provider (unit tests, isolated renders) everything falls back to
// German, which is also what the existing tests assert.
const I18nContext = createContext<I18nContextValue>({ locale: DEFAULT_LOCALE, setLocale: () => {} });

export function I18nProvider({ initialLocale, children }: { initialLocale: Locale; children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const router = useRouter();

  const setLocale = useCallback(
    (next: Locale) => {
      document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax`;
      document.documentElement.lang = next;
      setLocaleState(next);
      // Server components (layout metadata, <html lang>) re-render with the new cookie.
      router.refresh();
    },
    [router],
  );

  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const { locale, setLocale } = useContext(I18nContext);
  const t = useCallback((key: MessageKey, vars?: MessageVars) => translate(locale, key, vars), [locale]);
  const formatDate = useCallback(
    (value: string | number | Date | null | undefined, options: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }) => {
      // Missing or unparsable timestamps (e.g. a token never used yet) render as a dash
      // instead of throwing: Intl.DateTimeFormat.format() raises on an invalid Date.
      const date = value === null || value === undefined || value === '' ? null : new Date(value);
      return date && !Number.isNaN(date.getTime()) ? new Intl.DateTimeFormat(INTL_LOCALE[locale], options).format(date) : '–';
    },
    [locale],
  );
  const formatNumber = useCallback(
    (value: number, options?: Intl.NumberFormatOptions) => new Intl.NumberFormat(INTL_LOCALE[locale], options).format(value),
    [locale],
  );
  return { locale, setLocale, t, formatDate, formatNumber };
}
