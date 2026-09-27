/**
 * Human-readable summaries of a knowledge space's grants (ADR 0008) — the
 * Wissensbereiche card's <AccessLine> (see
 * src/components/portal/access-line.tsx) and the admin table. Pure and
 * framework-free so it is trivial to unit test.
 */

import { DEFAULT_LOCALE, type Locale } from '@/i18n/config';
import { translate } from '@/i18n/messages';
import type { CollectionGrant } from '@/lib/portal';

export type AccessSummaryInput = {
  visibility: 'public' | 'restricted';
  grants: CollectionGrant[];
};

function grantLabel(grant: CollectionGrant, locale: Locale): string {
  return grant.team_id ? translate(locale, 'portal.access.team', { team: grant.name }) : grant.name;
}

/** Who can use the space in chat: everybody, or every grant (each role includes reading). */
export function accessSummary(collection: AccessSummaryInput, locale: Locale = DEFAULT_LOCALE): string {
  if (collection.visibility === 'public') return translate(locale, 'portal.access.public');
  const parts = collection.grants.map(grant => grantLabel(grant, locale));
  return parts.length ? parts.join(', ') : translate(locale, 'portal.access.adminsOnly');
}

/** The owners' names, deactivated ones marked, or a dash when a space has
 * none (legacy, admin-managed). */
export function ownerSummary(grants: CollectionGrant[], locale: Locale = DEFAULT_LOCALE): string {
  const owners = grants.filter(grant => grant.role === 'owner')
    .map(grant => grant.is_active === false ? `${grant.name} (${translate(locale, 'portal.access.inactive')})` : grant.name);
  return owners.length ? owners.join(', ') : '—';
}
