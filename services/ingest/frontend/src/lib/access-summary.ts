/**
 * Human-readable summaries of a knowledge space's grants (ADR 0008) — the
 * Wissensbereiche card's <AccessLine> (see
 * src/components/portal/access-line.tsx) and the admin table. Pure and
 * framework-free so it is trivial to unit test.
 */

import { DEFAULT_LOCALE, INTL_LOCALE, type Locale } from '@/i18n/config';
import { translate } from '@/i18n/messages';
import type { CollectionGrant } from '@/lib/portal';

export type AccessSummaryInput = {
  visibility: 'public' | 'restricted';
  grants: CollectionGrant[];
};

const MAX_NAMED_TEAMS = 3;

/** Who can use the space in chat: everybody, or the granted teams by name
 * (at most three, then a count) plus individual people as a count only --
 * a long list of names is unreadable (every role includes reading). */
export function accessSummary(collection: AccessSummaryInput, locale: Locale = DEFAULT_LOCALE): string {
  if (collection.visibility === 'public') return translate(locale, 'portal.access.public');
  const teams = collection.grants.filter(grant => grant.team_id);
  const persons = collection.grants.length - teams.length;
  const parts = teams.slice(0, MAX_NAMED_TEAMS).map(grant => translate(locale, 'portal.access.team', { team: grant.name }));
  if (teams.length > MAX_NAMED_TEAMS) parts.push(translate(locale, 'portal.access.moreTeams', { count: teams.length - MAX_NAMED_TEAMS }));
  if (persons) parts.push(translate(locale, 'portal.access.morePersons', { count: persons }));
  return parts.length ? new Intl.ListFormat(INTL_LOCALE[locale], { type: 'conjunction' }).format(parts) : translate(locale, 'portal.access.adminsOnly');
}

/** The owners' names, deactivated ones marked, or a dash when a space has
 * none (legacy, admin-managed). */
export function ownerSummary(grants: CollectionGrant[], locale: Locale = DEFAULT_LOCALE): string {
  const owners = grants.filter(grant => grant.role === 'owner')
    .map(grant => grant.is_active === false ? `${grant.name} (${translate(locale, 'portal.access.inactive')})` : grant.name);
  return owners.length ? owners.join(', ') : '—';
}
