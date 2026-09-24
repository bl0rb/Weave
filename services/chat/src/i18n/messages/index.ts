// Message catalogs, one flat file per namespace and locale. English files are
// typed against the German ones, so a missing translation fails `tsc`.
//
// Values may contain `{name}` placeholders and a `singular|plural` pair that is
// picked by the numeric `count` variable, e.g. '{count} Quelle|{count} Quellen'.

import { DEFAULT_LOCALE, type Locale } from '../config';
import { chat as deChat } from './de/chat';
import { common as deCommon } from './de/common';
import { chat as enChat } from './en/chat';
import { common as enCommon } from './en/common';

const catalogs = {
  de: { common: deCommon, chat: deChat },
  en: { common: enCommon, chat: enChat },
} satisfies Record<Locale, unknown>;

type Catalog = (typeof catalogs)['de'];
export type Namespace = keyof Catalog;
export type MessageKey = { [N in Namespace]: `${N}.${keyof Catalog[N] & string}` }[Namespace];
export type MessageVars = Record<string, string | number>;

export function translate(locale: Locale, key: MessageKey, vars?: MessageVars): string {
  const dot = key.indexOf('.');
  const namespace = key.slice(0, dot) as Namespace;
  const id = key.slice(dot + 1);
  const table = catalogs[locale][namespace] as Record<string, string>;
  let message = table[id] ?? (catalogs.de[namespace] as Record<string, string>)[id] ?? key;
  if (message.includes('|') && typeof vars?.count === 'number') {
    const [one, other] = message.split('|');
    message = vars.count === 1 ? one : other;
  }
  return vars ? message.replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name]) : match)) : message;
}

/** Same as `translate`, pinned to `DEFAULT_LOCALE` — for the handful of
 * plain (non-component) helper functions that render user-facing text but
 * have no React context to read the current locale from (e.g.
 * lib/chat-types.ts's `scopeLabel`, components/chat/source-cards.tsx's
 * `formatPages`/`formatCollection`). Each of those takes an optional `t`
 * parameter that a component passes its own `useI18n().t` into; this is
 * only the default when none is given, which keeps their existing direct
 * unit tests (which call them with no `t` at all) asserting German text. */
export const translateDefault = (key: MessageKey, vars?: MessageVars) => translate(DEFAULT_LOCALE, key, vars);
