import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** "mathias.werk@example.com" / "mathias_werk" -> "Mathias": long logins
 * don't fit the sidebar or the greeting, the first name does. A lone initial
 * ("m.werk@firma.de") is no name: the local part is kept as is ("m.werk"). */
export function firstName(username: string): string {
  const local = username.split('@')[0];
  const first = local.split(/[._\-\s]+/).find(Boolean) ?? username;
  if (first.length === 1 && local.length > 1) return local;
  return first.charAt(0).toLocaleUpperCase() + first.slice(1);
}
