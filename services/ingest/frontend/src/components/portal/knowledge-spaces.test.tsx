// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiFetch, apiJson } from '@/lib/api';
import { KnowledgeDetail, KnowledgeSpaces } from './knowledge';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
}));

const json = vi.mocked(apiJson);
const fetcher = vi.mocked(apiFetch);
const area = {
  collection_id: 'area-1',
  slug: 'servicewissen',
  name: 'Servicewissen',
  description: 'Anleitungen für den Service.',
  visibility: 'restricted',
  grants: [{ user_id: 'u1', team_id: null, role: 'owner', name: 'Ada' }, { user_id: null, team_id: 't-service', role: 'member', name: 'Service' }],
  created_by: { id: 'u1', username: 'Ada' },
  responsible_team: { id: 't-service', name: 'Service' },
  role: 'owner',
  can_manage: true,
  can_upload: true,
};

beforeEach(() => {
  json.mockReset();
  fetcher.mockReset();
  json.mockImplementation(async (path, init) => init?.method === 'PATCH'
    ? { ...area, name: 'Neuer Name' }
    : path === '/api/v1/directory/teams' ? { items: [{ id: 't-service', name: 'Service', member_count: 3 }] } : { items: [area] });
  fetcher.mockResolvedValue({ ok: true } as Response);
});

afterEach(cleanup);

it('offers "Wissensbereich anlegen" once, on the card after the spaces', async () => {
  render(<KnowledgeSpaces />);
  await screen.findByRole('heading', { name: 'Servicewissen' });
  expect(screen.getByText('Besitzer: Ada · Zuständig: Gruppe Service')).toBeTruthy();
  const startLinks = screen.getAllByRole('link', { name: /Wissensbereich anlegen/ });
  expect(startLinks.length).toBe(1);
  expect(startLinks[0].getAttribute('href')).toBe('/knowledge/new');
});

it('links a space\'s "zu prüfen" chip to its filtered document list', async () => {
  json.mockImplementation(async path => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')
    ? { items: [{ id: 'd1', original_filename: 'a.pdf', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', release: null }], total: 1 }
    : { items: [area] });
  render(<KnowledgeSpaces />);
  const chip = await screen.findByRole('link', { name: '1 zu prüfen' });
  expect(chip.getAttribute('href')).toBe('/documents?bereich=servicewissen&stand=review');
});

it('tells a reader that the space is for chat only instead of showing empty counts and document links', async () => {
  json.mockImplementation(async () => ({ items: [{ ...area, role: 'reader', can_manage: false, can_upload: false }] }));
  render(<KnowledgeSpaces />);
  await screen.findByRole('heading', { name: 'Servicewissen' });
  expect(screen.getByText(/Nur Lesezugriff/)).toBeTruthy();
  expect(screen.queryByText(/Im Chat verfügbar/)).toBeNull();
  expect(screen.queryByRole('link', { name: 'Dokumente ansehen' })).toBeNull();
});

it('removes the eyebrow and offers rename and delete only to managers', async () => {
  const { rerender } = render(<KnowledgeSpaces />);
  await screen.findByRole('heading', { name: 'Servicewissen' });
  expect(screen.queryByText('WISSEN VERBINDET')).toBeNull();
  expect(screen.getByRole('button', { name: 'Servicewissen umbenennen' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Servicewissen löschen' })).toBeTruthy();

  json.mockImplementation(async () => ({ items: [{ ...area, can_manage: false }] }));
  rerender(<KnowledgeSpaces />);
  // Remount because the list loader intentionally runs once per page visit.
  cleanup();
  render(<KnowledgeSpaces />);
  await screen.findByRole('heading', { name: 'Servicewissen' });
  expect(screen.queryByRole('button', { name: /umbenennen/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /löschen/ })).toBeNull();
});

it('renames a knowledge space through the existing patch contract', async () => {
  render(<KnowledgeSpaces />);
  fireEvent.click(await screen.findByRole('button', { name: 'Servicewissen umbenennen' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Neuer Name' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Details angeben' }), { target: { value: 'Neue Beschreibung' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));

  await waitFor(() => expect(json.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
  const mutation = json.mock.calls.find(([, init]) => init?.method === 'PATCH');
  expect(mutation?.[0]).toBe('/api/v1/collections/area-1');
  expect(JSON.parse(mutation?.[1]?.body as string)).toEqual({ name: 'Neuer Name', description: 'Neue Beschreibung', responsible_team_id: 't-service' });
  expect(await screen.findByText('Wissensbereich gespeichert.')).toBeTruthy();
});

it('requires a confirmation before deleting an empty knowledge space', async () => {
  render(<KnowledgeSpaces />);
  fireEvent.click(await screen.findByRole('button', { name: 'Servicewissen löschen' }));
  expect(screen.getByText(/Ohne die Option unten muss er leer sein/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Wissensbereich löschen' }));

  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    '/api/v1/collections/area-1',
    { method: 'DELETE' },
  ));
  expect(await screen.findByText('Wissensbereich gelöscht.')).toBeTruthy();
});

it('deletes a knowledge space with all its content only after its name is typed', async () => {
  render(<KnowledgeSpaces />);
  fireEvent.click(await screen.findByRole('button', { name: 'Servicewissen löschen' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /Mit allen Dokumenten löschen/ }));
  const confirm = screen.getByRole('button', { name: 'Wissensbereich löschen' }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(screen.getByRole('textbox', { name: /Namen „Servicewissen“ eingeben/ }), { target: { value: 'Servicewissen' } });
  expect(confirm.disabled).toBe(false);
  fireEvent.click(confirm);

  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    '/api/v1/collections/area-1?with_content=true&confirm_name=Servicewissen',
    { method: 'DELETE' },
  ));
});

const sync = { id: 's1', source_id: 'src', source_name: 'Firmen-Wiki', scope_type: 'space', scope_value: 'DOCS', root_page_title: 'Handbuch', collection_id: 'area-1', collection_name: 'Servicewissen', options: {}, enabled: true, interval_seconds: 86400, last_success_at: '2026-10-20T06:00:00Z', last_error: 'Seite 12 nicht erreichbar', next_refresh_at: '2099-01-01T06:00:00Z', last_run: { id: 'run-9', status: 'finished', is_refresh: true, created_at: '2026-10-20T06:00:00Z', finished_at: '2026-10-20T06:05:00Z', pages_imported: 4, pages_failed: 0 }, created_at: '2026-09-01T12:00:00Z' };

function mockDetail({ total = 3, scopes = { items: [], other_count: 0 } as unknown, syncs = [] as unknown[], space = area as unknown } = {}) {
  json.mockImplementation(async (path, init) => {
    if (typeof path !== 'string') return space;
    if (path.startsWith('/api/v1/portal/documents')) return { items: [], total };
    if (path === '/api/v1/portal/collections/area-1/import-scopes') return scopes;
    if (path === '/api/v1/import/syncs') return { items: syncs };
    if (path === '/api/v1/portal/collections/area-1/reindex' && init?.method === 'POST') return { requeued: 1 };
    if (path === '/api/v1/import/syncs/s1/run' && init?.method === 'POST') return { id: 'run', status: 'pending' };
    return space;
  });
}

it('links a space card and the detail header to the space\'s filtered documents instead of a document table', async () => {
  render(<KnowledgeSpaces />);
  expect((await screen.findByRole('link', { name: 'Dokumente ansehen' })).getAttribute('href')).toBe('/documents?bereich=servicewissen');
  cleanup();

  mockDetail();
  render(<KnowledgeDetail id="area-1" />);
  expect((await screen.findByRole('link', { name: /3 Dokumente ansehen/ })).getAttribute('href')).toBe('/documents?bereich=servicewissen');
  expect(screen.getByText(/Deine Rolle:/).textContent).toContain('Besitzer');
  expect(screen.getByRole('heading', { name: 'Quellen' })).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Zugriff' })).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Wartung' })).toBeTruthy();
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getByRole('link', { name: 'Verbindungen' }).getAttribute('href')).toBe('/connections');
});

it('reindexes a knowledge space after confirmation, explaining that nothing is re-imported', async () => {
  mockDetail({ total: 1 });
  render(<KnowledgeDetail id="area-1" />);
  await screen.findByRole('link', { name: /1 Dokument ansehen/ });
  expect(screen.getByText(/Es wird nichts neu importiert/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Wissensbereich neu indizieren' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Neu indizieren' }));
  await waitFor(() => expect(json.mock.calls.some(([path]) => path === '/api/v1/portal/collections/area-1/reindex')).toBe(true));
  expect(await screen.findByText('1 Dokument wird neu indiziert.')).toBeTruthy();
});

it('lists the space\'s Confluence imports with schedule, last run and error, and syncs only its own', async () => {
  const website = { ...sync, id: 's3', source_id: 'web', source_name: 'Gesetze im Internet', source_kind: 'website', scope_type: 'url', scope_value: 'https://www.gesetze-im-internet.de/vwvfg/', root_page_title: 'VwVfG', options: { crawl_scope: 'prefix', include_attachments: true, legal_source: true }, last_error: 'https://www.gesetze-im-internet.de/vwvfg/ returned HTTP 403 (access denied by the server)' };
  mockDetail({ syncs: [sync, { ...sync, id: 's2', collection_id: 'other' }, website] });
  render(<KnowledgeDetail id="area-1" />);
  const list = await screen.findByRole('list', { name: 'Zugeordnete Quellen' });
  expect(list.textContent).toContain('Handbuch');
  expect(list.textContent).toContain('Herkunft: Confluence · Firmen-Wiki');
  expect(list.textContent).toContain('Umfang: Ganzer Bereich · DOCS');
  expect(list.textContent).toContain('Aktualisierung: Täglich');
  expect(list.textContent).toContain('Letzter erfolgreicher Abruf: ');
  expect(list.textContent).toContain('4 Seiten geändert');
  expect(list.textContent).toContain('Fehler: Seite 12 nicht erreichbar');
  // Website source: origin, start URL, scope with options and a readable error next to the raw one.
  expect(list.textContent).toContain('Herkunft: Website · Gesetze im Internet · https://www.gesetze-im-internet.de/vwvfg/');
  expect(list.textContent).toContain('Umfang: Bereich unter der Startadresse · inkl. verlinkter Dokumente · Rechtsquelle');
  expect(list.textContent).toContain('Fehler: Zugriff verweigert – Zugangsdaten der Verbindung prüfen. (https://www.gesetze-im-internet.de/vwvfg/ returned HTTP 403');
  fireEvent.click(screen.getByRole('button', { name: 'Jetzt synchronisieren' }));
  expect(await screen.findByText('Synchronisierung gestartet.')).toBeTruthy();
  const runs = json.mock.calls.filter(([path, init]) => init?.method === 'POST' && String(path).startsWith('/api/v1/import/syncs/'));
  expect(runs.map(([path]) => path)).toEqual(['/api/v1/import/syncs/s1/run', '/api/v1/import/syncs/s3/run']);
});

it('offers no sync button for a space without own Confluence imports', async () => {
  mockDetail();
  render(<KnowledgeDetail id="area-1" />);
  await screen.findByText(/Noch keine Confluence- oder Website-Importe/);
  expect(screen.queryByRole('button', { name: 'Jetzt synchronisieren' })).toBeNull();
});

it('shows only the reader hint, no management cards, to a reader', async () => {
  mockDetail({ space: { ...area, role: 'reader', can_manage: false, can_upload: false } });
  render(<KnowledgeDetail id="area-1" />);
  await screen.findByText(/Nur Lesezugriff/);
  expect(screen.queryByRole('button', { name: 'Wissensbereich neu indizieren' })).toBeNull();
  expect(screen.queryByRole('link', { name: /Dokumente ansehen/ })).toBeNull();
});

it('lists the space\'s Confluence-Bereiche with view, edit and filtered-documents links', async () => {
  mockDetail({ scopes: { items: [
    { value: 'space:DOCS', scope_type: 'space', scope_value: 'DOCS', label: 'Handbuch', count: 3, latest_run_id: 'run-other', edit_run_id: 'run-mine', can_edit: true },
    { value: 'page:1', scope_type: 'page', scope_value: '1', label: 'Reisekosten', count: 1, latest_run_id: 'run-2', edit_run_id: null, can_edit: false },
  ], other_count: 2 } });
  render(<KnowledgeDetail id="area-1" />);
  expect((await screen.findByRole('link', { name: 'Handbuch · DOCS (3)' })).getAttribute('href')).toBe('/documents?bereich=servicewissen&q=Handbuch');
  const views = screen.getAllByRole('link', { name: 'Import ansehen' }).map(link => link.getAttribute('href'));
  expect(views).toEqual(['/imports/run-other', '/imports/run-2']);
  // Only the caller's own import may be edited, even when someone else imported the scope later.
  expect(screen.getAllByRole('link', { name: 'Import bearbeiten' }).map(link => link.getAttribute('href'))).toEqual(['/imports/new?from=run-mine']);
});
