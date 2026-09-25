// Locale settings shared by the server layout and the client provider.
// The cookie name is deliberately identical in services/chat so a switch in
// one app carries over to the other when both are served from the same host.

export const LOCALES = ['de', 'en'] as const;
export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = 'de';
export const LOCALE_COOKIE = 'weave_locale';

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
