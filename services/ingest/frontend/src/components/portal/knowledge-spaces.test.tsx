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

it('offers two "Wissensbereich anlegen" entry points, both pointing to /knowledge/new', async () => {
  render(<KnowledgeSpaces />);
  await screen.findByRole('heading', { name: 'Servicewissen' });
  expect(screen.getByText('Besitzer: Ada · Zuständig: Team Service')).toBeTruthy();
  const startLinks = screen.getAllByRole('link', { name: /Wissensbereich anlegen/ });
  expect(startLinks.length).toBe(2);
  startLinks.forEach(link => expect(link.getAttribute('href')).toBe('/knowledge/new'));
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

it('reindexes a knowledge space after confirmation and shows the requeued count', async () => {
  const document = { id: 'd1', original_filename: 'Doc.pdf', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  json.mockImplementation(async (path, init) => {
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/documents')) return { items: [document], total: 1 };
    if (path === '/api/v1/portal/collections/area-1/reindex' && init?.method === 'POST') return { requeued: 1 };
    return area;
  });
  render(<KnowledgeDetail id="area-1" />);
  await screen.findByText('Doc.pdf');
  fireEvent.click(screen.getByRole('button', { name: 'Wissensbereich neu indizieren' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Neu indizieren' }));
  await waitFor(() => expect(json.mock.calls.some(([path]) => path === '/api/v1/portal/collections/area-1/reindex')).toBe(true));
  expect(await screen.findByText('1 Dokument wird neu indiziert.')).toBeTruthy();
});

it('hides Wissensbereich neu indizieren for non-managers', async () => {
  const document = { id: 'd1', original_filename: 'Doc.pdf', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  json.mockImplementation(async path => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')
    ? { items: [document], total: 1 } : { ...area, role: 'reader', can_manage: false, can_upload: false });
  render(<KnowledgeDetail id="area-1" />);
  await screen.findByText('Doc.pdf');
  expect(screen.queryByRole('button', { name: 'Wissensbereich neu indizieren' })).toBeNull();
});

it('filters a knowledge space\'s documents by quality grade and resets pagination', async () => {
  const document = { id: 'd1', original_filename: 'Doc.pdf', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'B', quality_recommendation: 'warn', can_release: true, release: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  json.mockImplementation(async path => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')
    ? { items: [document], total: 1 } : area);
  render(<KnowledgeDetail id="area-1" />);
  await screen.findByText('Doc.pdf');

  fireEvent.click(screen.getByRole('button', { name: 'B' }));
  expect(screen.getByRole('button', { name: 'B' }).getAttribute('aria-pressed')).toBe('true');
  await waitFor(() => {
    const call = json.mock.calls.filter(([path]) => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')).at(-1);
    const params = new URL(call?.[0] as string, 'http://localhost').searchParams;
    expect(params.get('quality_grade')).toBe('B');
    expect(params.get('offset')).toBe('0');
    expect(params.get('collection_id')).toBe('area-1');
  });
});

it('shows the Confluence-Bereich filter when import scopes exist and filters by the selected scope', async () => {
  const document = { id: 'd1', original_filename: 'Handbuch.pdf', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, source: { kind: 'confluence', label: 'Confluence', path: null, url: null } };
  const scopes = { items: [{ value: 'space:DOCS', scope_type: 'space', scope_value: 'DOCS', label: 'Handbuch', count: 3 }], other_count: 2 };
  json.mockImplementation(async path => {
    if (typeof path !== 'string') return area;
    if (path.startsWith('/api/v1/portal/documents')) return { items: [document], total: 1 };
    if (path === '/api/v1/portal/collections/area-1/import-scopes') return scopes;
    return area;
  });
  render(<KnowledgeDetail id="area-1" />);
  await screen.findByText('Handbuch.pdf');
  const select = await screen.findByRole('combobox', { name: 'Confluence-Bereich' });
  expect(screen.getByRole('option', { name: 'Handbuch · DOCS (3)' })).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Ohne Confluence (Uploads, Mail)' })).toBeTruthy();

  fireEvent.change(select, { target: { value: 'space:DOCS' } });
  await waitFor(() => {
    const call = json.mock.calls.filter(([path]) => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')).at(-1);
    const params = new URL(call?.[0] as string, 'http://localhost').searchParams;
    expect(params.get('import_scope')).toBe('space:DOCS');
    expect(params.get('offset')).toBe('0');
  });
});

it('drops a Confluence-Bereich filter whose scope no longer exists', async () => {
  const document = { id: 'd1', original_filename: 'Handbuch.pdf', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, source: { kind: 'confluence', label: 'Confluence', path: null, url: null } };
  let scopes = { items: [{ value: 'space:DOCS', scope_type: 'space', scope_value: 'DOCS', label: 'Handbuch', count: 3 }], other_count: 0 };
  json.mockImplementation(async path => {
    if (typeof path !== 'string') return area;
    if (path.startsWith('/api/v1/portal/documents')) return { items: [document], total: 1 };
    if (path === '/api/v1/portal/collections/area-1/import-scopes') return scopes;
    return area;
  });
  render(<KnowledgeDetail id="area-1" />);
  const select = await screen.findByRole('combobox', { name: 'Confluence-Bereich' });
  // The scope's documents are gone by the time the filtered list loads.
  scopes = { items: [], other_count: 0 };
  fireEvent.change(select, { target: { value: 'space:DOCS' } });
  await waitFor(() => {
    const call = json.mock.calls.filter(([path]) => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')).at(-1);
    const params = new URL(call?.[0] as string, 'http://localhost').searchParams;
    expect(params.get('import_scope')).toBeNull();
  });
});

it('hides the Confluence-Bereich filter when the space has no import scopes', async () => {
  const document = { id: 'd1', original_filename: 'Doc.pdf', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  json.mockImplementation(async path => {
    if (typeof path !== 'string') return area;
    if (path.startsWith('/api/v1/portal/documents')) return { items: [document], total: 1 };
    if (path === '/api/v1/portal/collections/area-1/import-scopes') return { items: [], other_count: 0 };
    return area;
  });
  render(<KnowledgeDetail id="area-1" />);
  await screen.findByText('Doc.pdf');
  expect(screen.queryByRole('combobox', { name: 'Confluence-Bereich' })).toBeNull();
});
