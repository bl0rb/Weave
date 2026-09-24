/**
 * Human-readable "who can use this in chat" line for a knowledge space —
 * the Wissensbereiche card's <AccessLine> (see
 * src/components/portal/access-line.tsx). Pure and framework-free so it is
 * trivial to unit test.
 *
 * Today the backend (CollectionResponse, see
 * services/ingest/backend/app/schemas/jobs.py) only carries `read_teams`:
 * empty means every team can read it. A planned follow-up adds explicit
 * `visibility` and per-user grants (`read_user_details`) — both optional
 * here so this keeps working unchanged once the API starts sending them.
 */

export type AccessUserDetail = {
  id: string;
  username: string;
  display_name?: string;
  team?: string;
};

export type AccessSummaryInput = {
  read_teams: string[];
  /** Not sent by the API yet — once present it takes precedence over the read_teams-only inference below. */
  visibility?: 'public' | 'restricted';
  /** Not sent by the API yet — individual grants layered on top of read_teams when visibility is 'restricted'. */
  read_user_details?: AccessUserDetail[];
};

function personLabel(user: AccessUserDetail): string {
  return user.display_name?.trim() || user.username;
}

export function accessSummary(collection: AccessSummaryInput): string {
  if (collection.visibility === 'public') return 'Öffentlich';
  if (collection.visibility === 'restricted') {
    const parts = [
      ...collection.read_teams.map(team => `Team ${team}`),
      ...(collection.read_user_details ?? []).map(personLabel),
    ];
    return parts.length ? parts.join(', ') : 'Nur Editoren';
  }
  // No visibility field yet (today's API): read_teams alone decides it —
  // empty means every team can read the collection.
  if (collection.read_teams.length === 0) return 'Öffentlich';
  return collection.read_teams.map(team => `Team ${team}`).join(', ');
}
