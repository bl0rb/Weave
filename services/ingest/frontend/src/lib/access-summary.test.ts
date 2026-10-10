import { describe, expect, it } from 'vitest';
import type { CollectionGrant } from './portal';
import { accessSummary, ownerSummary } from './access-summary';

const person = (name: string, role: CollectionGrant['role']): CollectionGrant => ({ user_id: name, team_id: null, role, name });
const team = (name: string, role: CollectionGrant['role']): CollectionGrant => ({ user_id: null, team_id: name, role, name });

describe('accessSummary', () => {
  it('reports a public space as public regardless of its grants', () => {
    expect(accessSummary({ visibility: 'public', grants: [person('jdoe', 'owner'), team('A', 'reader')] })).toBe('Öffentlich');
  });

  it('names teams and counts individual people instead of listing them', () => {
    expect(accessSummary({ visibility: 'restricted', grants: [person('jdoe', 'owner'), team('A', 'member'), person('max', 'reader')] }))
      .toBe('Gruppe A und 2 Personen');
    expect(accessSummary({ visibility: 'restricted', grants: [team('A', 'reader'), team('B', 'member'), person('max', 'reader')] }))
      .toBe('Gruppe A, Gruppe B und 1 Person');
    expect(accessSummary({ visibility: 'restricted', grants: [person('jdoe', 'owner')] })).toBe('1 Person');
  });

  it('names at most three teams and counts the rest', () => {
    const grants = ['A', 'B', 'C', 'D', 'E'].map(name => team(name, 'reader'));
    expect(accessSummary({ visibility: 'restricted', grants })).toBe('Gruppe A, Gruppe B, Gruppe C und 2 weitere Gruppen');
    expect(accessSummary({ visibility: 'restricted', grants: [...grants.slice(0, 4), person('max', 'reader')] }))
      .toBe('Gruppe A, Gruppe B, Gruppe C, 1 weitere Gruppe und 1 Person');
  });

  it('reports "Nur Administration" for a restricted space without grants', () => {
    expect(accessSummary({ visibility: 'restricted', grants: [] })).toBe('Nur Administration');
  });

  it('translates the summary when an English locale is passed', () => {
    expect(accessSummary({ visibility: 'public', grants: [] }, 'en')).toBe('Public');
    expect(accessSummary({ visibility: 'restricted', grants: [team('A', 'reader'), team('B', 'member'), person('max', 'reader'), person('eva', 'reader')] }, 'en')).toBe('Group A, Group B and 2 people');
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
