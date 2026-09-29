'use client';

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { DEFAULT_LOCALE, INTL_LOCALE, LOCALE_COOKIE, resolveLocale, type Locale, type LocalePreference } from './config';
import { translate, type MessageKey, type MessageVars } from './messages';

type I18nContextValue = {
  locale: Locale;
  preference: LocalePreference;
  setLocale: (preference: LocalePreference) => void;
  applyLocale: (preference: LocalePreference) => void;
};

// Without a provider (unit tests, isolated renders) everything falls back to
// German/Auto, which is also what the existing tests assert.
const I18nContext = createContext<I18nContextValue>({
  locale: DEFAULT_LOCALE,
  preference: 'auto',
  setLocale: () => {},
  applyLocale: () => {},
});

export function I18nProvider({
  initialLocale,
  initialPreference = 'auto',
  children,
}: {
  initialLocale: Locale;
  initialPreference?: LocalePreference;
  children: ReactNode;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const [preference, setPreferenceState] = useState<LocalePreference>(initialPreference);
  const router = useRouter();

  // The pure mechanism — cookie + <html lang> + refresh, never anything
  // account-related. `setLocale` (the user-facing action <LanguageSwitch/>
  // calls) is currently the same function; `applyLocale` is the name
  // callers that must NEVER persist (auth-context.tsx's account-locale
  // sync effect) use, so that intent is explicit at the call site even
  // though the mechanism is shared. 'auto' deletes the cookie and re-derives
  // the locale from the browser's own languages — the same fallback the
  // server would apply on the next request if it found no cookie.
  const applyLocale = useCallback(
    (next: LocalePreference) => {
      if (next === 'auto') {
        document.cookie = `${LOCALE_COOKIE}=; path=/; max-age=0; samesite=lax`;
      } else {
        document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax`;
      }
      const resolved = next === 'auto' ? resolveLocale(null, navigator.languages.join(',')) : next;
      document.documentElement.lang = resolved;
      setLocaleState(resolved);
      setPreferenceState(next);
      // Server components (layout metadata, <html lang>) re-render with the new cookie.
      router.refresh();
    },
    [router],
  );

  const value = useMemo(
    () => ({ locale, preference, setLocale: applyLocale, applyLocale }),
    [locale, preference, applyLocale],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const { locale, preference, setLocale, applyLocale } = useContext(I18nContext);
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
  return { locale, preference, setLocale, applyLocale, t, formatDate, formatNumber };
}
