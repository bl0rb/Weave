import { describe, expect, it } from 'vitest';
import type { CollectionGrant } from './portal';
import { accessSummary, ownerSummary } from './access-summary';

const person = (name: string, role: CollectionGrant['role']): CollectionGrant => ({ user_id: name, team_id: null, role, name });
const team = (name: string, role: CollectionGrant['role']): CollectionGrant => ({ user_id: null, team_id: name, role, name });

describe('accessSummary', () => {
  it('reports a public space as public regardless of its grants', () => {
    expect(accessSummary({ visibility: 'public', grants: [person('jdoe', 'owner'), team('A', 'reader')] })).toBe('Öffentlich');
  });

  it('lists every grant of a restricted space, since every role reads', () => {
    expect(accessSummary({ visibility: 'restricted', grants: [person('jdoe', 'owner'), team('A', 'member'), person('max', 'reader')] }))
      .toBe('jdoe, Team A, max');
  });

  it('reports "Nur Administration" for a restricted space without grants', () => {
    expect(accessSummary({ visibility: 'restricted', grants: [] })).toBe('Nur Administration');
  });

  it('translates the summary when an English locale is passed', () => {
    expect(accessSummary({ visibility: 'public', grants: [] }, 'en')).toBe('Public');
    expect(accessSummary({ visibility: 'restricted', grants: [team('A', 'reader'), team('B', 'member')] }, 'en')).toBe('Team A, Team B');
    expect(accessSummary({ visibility: 'restricted', grants: [] }, 'en')).toBe('Admins only');
  });
});

describe('ownerSummary', () => {
  it('names only the owners', () => {
    expect(ownerSummary([person('jdoe', 'owner'), person('max', 'member'), person('eva', 'owner')])).toBe('jdoe, eva');
  });

  it('shows a dash for a space without owners', () => {
    expect(ownerSummary([team('A', 'member')])).toBe('—');
  });
});
