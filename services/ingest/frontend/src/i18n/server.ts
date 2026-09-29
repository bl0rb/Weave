import { cookies, headers } from 'next/headers';
import { isLocale, LOCALE_COOKIE, resolveLocale, type Locale, type LocalePreference } from './config';
import { translate, type MessageKey, type MessageVars } from './messages';

export async function getLocale(): Promise<Locale> {
  const [cookieStore, headerStore] = await Promise.all([cookies(), headers()]);
  return resolveLocale(cookieStore.get(LOCALE_COOKIE)?.value, headerStore.get('accept-language'));
}

/** The raw stored choice ('de'/'en'), or 'auto' when no cookie is set —
 * i.e. before the Accept-Language/German fallback {@link getLocale} applies. */
export async function getLocalePreference(): Promise<LocalePreference> {
  const cookieStore = await cookies();
  const value = cookieStore.get(LOCALE_COOKIE)?.value;
  return isLocale(value) ? value : 'auto';
}

export async function getTranslator() {
  const locale = await getLocale();
  return { locale, t: (key: MessageKey, vars?: MessageVars) => translate(locale, key, vars) };
}
