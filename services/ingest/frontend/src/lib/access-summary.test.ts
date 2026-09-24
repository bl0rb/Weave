import { describe, expect, it } from 'vitest';
import { accessSummary } from './access-summary';

describe('accessSummary', () => {
  it('treats an empty read_teams list as public (today\'s API contract)', () => {
    expect(accessSummary({ read_teams: [] })).toBe('Öffentlich');
  });

  it('lists teams by name when read_teams is set', () => {
    expect(accessSummary({ read_teams: ['A', 'B'] })).toBe('Team A, Team B');
  });

  it('prefers an explicit public visibility over read_teams', () => {
    expect(accessSummary({ read_teams: ['A'], visibility: 'public' })).toBe('Öffentlich');
  });

  it('combines teams and individual grants when restricted', () => {
    expect(accessSummary({
      read_teams: ['A'],
      visibility: 'restricted',
      read_user_details: [{ id: '1', username: 'jdoe', display_name: 'Jane Doe' }],
    })).toBe('Team A, Jane Doe');
  });

  it('falls back to the username when a granted user has no display name', () => {
    expect(accessSummary({ read_teams: [], visibility: 'restricted', read_user_details: [{ id: '1', username: 'jdoe' }] })).toBe('jdoe');
  });

  it('reports "Nur Editoren" for a restricted space with no grants at all', () => {
    expect(accessSummary({ read_teams: [], visibility: 'restricted' })).toBe('Nur Editoren');
  });

  it('translates the summary when an English locale is passed', () => {
    expect(accessSummary({ read_teams: [] }, 'en')).toBe('Public');
    expect(accessSummary({ read_teams: ['A', 'B'] }, 'en')).toBe('Team A, Team B');
    expect(accessSummary({ read_teams: [], visibility: 'restricted' }, 'en')).toBe('Editors only');
  });
});
