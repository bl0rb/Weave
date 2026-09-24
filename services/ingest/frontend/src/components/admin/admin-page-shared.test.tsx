import { expect, it } from 'vitest';

import { OLD_TAB_REDIRECTS, oldTabRedirectUrl } from './admin-page-shared';

// The old single-page admin had exactly these 13 tabs; every one of them
// must still resolve to a working URL on the new 5-page structure so
// `/admin?tab=<old-key>` links (bookmarks, other docs, etc.) keep working.
const OLD_TAB_IDS = [
  'collections',
  'users',
  'teams',
  'bots',
  'technical-identities',
  'providers',
  'chat-provider',
  'retrieval-provider',
  'vl-connections',
  'paddle',
  'logs',
  'backup',
  'tools',
];

it('maps every old tab id to a new admin page', () => {
  for (const id of OLD_TAB_IDS) {
    expect(OLD_TAB_REDIRECTS[id], `missing redirect for old tab "${id}"`).toBeDefined();
  }
  expect(Object.keys(OLD_TAB_REDIRECTS)).toHaveLength(OLD_TAB_IDS.length);
});

it('resolves each old tab id to its new page and section', () => {
  expect(oldTabRedirectUrl('collections')).toBe('/admin/menschen?bereich=zugriff');
  expect(oldTabRedirectUrl('users')).toBe('/admin/menschen?bereich=personen');
  expect(oldTabRedirectUrl('teams')).toBe('/admin/menschen?bereich=teams');
  expect(oldTabRedirectUrl('providers')).toBe('/admin/menschen?bereich=anmeldung');
  expect(oldTabRedirectUrl('bots')).toBe('/admin/wissen?bereich=bots');
  expect(oldTabRedirectUrl('chat-provider')).toBe('/admin/wissen?bereich=chat-llm');
  expect(oldTabRedirectUrl('retrieval-provider')).toBe('/admin/wissen?bereich=suche-modelle');
  expect(oldTabRedirectUrl('vl-connections')).toBe('/admin/verarbeitung?bereich=dokument-ki');
  expect(oldTabRedirectUrl('paddle')).toBe('/admin/verarbeitung?bereich=ocr');
  expect(oldTabRedirectUrl('logs')).toBe('/admin/verarbeitung?bereich=worker-logs');
  expect(oldTabRedirectUrl('backup')).toBe('/admin/betrieb?bereich=sicherung');
  expect(oldTabRedirectUrl('technical-identities')).toBe('/admin/betrieb?bereich=identitaeten');
  expect(oldTabRedirectUrl('tools')).toBe('/admin/betrieb?bereich=werkzeuge');
});

it('returns null for an unknown or missing tab id', () => {
  expect(oldTabRedirectUrl('does-not-exist')).toBeNull();
  expect(oldTabRedirectUrl('')).toBeNull();
});
