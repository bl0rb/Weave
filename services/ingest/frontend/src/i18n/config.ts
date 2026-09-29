// Locale settings shared by the server layout and the client provider.
// The cookie name is deliberately identical in services/chat so a switch in
// one app carries over to the other when both are served from the same host.

export const LOCALES = ['de', 'en'] as const;
export type Locale = (typeof LOCALES)[number];

/** An explicit locale, or 'auto' to follow the browser (no cookie/account choice stored). */
export type LocalePreference = Locale | 'auto';

export const DEFAULT_LOCALE: Locale = 'de';
// Renamed from 'weave_locale': language now follows the browser by default
// (Auto), with an explicit Auto/DE/EN choice on top (see provider.tsx) —
// the old cookie's mere presence used to mean an explicit pick, so this
// rename deliberately resets every browser's stored choice back to Auto
// once.
export const LOCALE_COOKIE = 'weave_lang';

/** BCP 47 tags for Intl date/number formatting. */
export const INTL_LOCALE: Record<Locale, string> = { de: 'de-DE', en: 'en-GB' };

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALES as readonly string[]).includes(value);
}

/** Explicit cookie choice first, then the browser's Accept-Language, else German. */
export function resolveLocale(cookieValue?: string | null, acceptLanguage?: string | null): Locale {
  if (isLocale(cookieValue)) return cookieValue;
  const preferred = (acceptLanguage ?? '')
    .split(',')
    .map((part) => part.split(';')[0].trim().slice(0, 2).toLowerCase());
  return preferred.find(isLocale) ?? DEFAULT_LOCALE;
}
