// Message catalogs, one flat file per namespace and locale. English files are
// typed against the German ones, so a missing translation fails `tsc`.
//
// Values may contain `{name}` placeholders and a `singular|plural` pair that is
// picked by the numeric `count` variable, e.g. '{count} Dokument|{count} Dokumente'.

import type { Locale } from '../config';
import { admin as deAdmin } from './de/admin';
import { common as deCommon } from './de/common';
import { portal as dePortal } from './de/portal';
import { admin as enAdmin } from './en/admin';
import { common as enCommon } from './en/common';
import { portal as enPortal } from './en/portal';

const catalogs = {
  de: { common: deCommon, portal: dePortal, admin: deAdmin },
  en: { common: enCommon, portal: enPortal, admin: enAdmin },
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
