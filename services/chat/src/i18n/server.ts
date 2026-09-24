import { cookies, headers } from 'next/headers';
import { LOCALE_COOKIE, resolveLocale, type Locale } from './config';
import { translate, type MessageKey, type MessageVars } from './messages';

export async function getLocale(): Promise<Locale> {
  const [cookieStore, headerStore] = await Promise.all([cookies(), headers()]);
  return resolveLocale(cookieStore.get(LOCALE_COOKIE)?.value, headerStore.get('accept-language'));
}

export async function getTranslator() {
  const locale = await getLocale();
  return { locale, t: (key: MessageKey, vars?: MessageVars) => translate(locale, key, vars) };
}
