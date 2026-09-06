// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiFetch, apiJson } from '@/lib/api';
import { KnowledgeSpaces } from './knowledge';

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
  read_teams: ['Service'],
  can_manage: true,
};

beforeEach(() => {
  json.mockReset();
  fetcher.mockReset();
  json.mockImplementation(async (_path, init) => init?.method === 'PATCH'
    ? { ...area, name: 'Neuer Name' }
    : { items: [area] });
  fetcher.mockResolvedValue({ ok: true } as Response);
});

afterEach(cleanup);

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
  fireEvent.change(screen.getByRole('textbox', { name: 'Beschreibung' }), { target: { value: 'Neue Beschreibung' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));

  await waitFor(() => expect(json.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
  const mutation = json.mock.calls.find(([, init]) => init?.method === 'PATCH');
  expect(mutation?.[0]).toBe('/api/v1/collections/area-1');
  expect(JSON.parse(mutation?.[1]?.body as string)).toEqual({ name: 'Neuer Name', description: 'Neue Beschreibung' });
  expect(await screen.findByText('Wissensbereich gespeichert.')).toBeTruthy();
});

it('requires a confirmation before deleting an empty knowledge space', async () => {
  render(<KnowledgeSpaces />);
  fireEvent.click(await screen.findByRole('button', { name: 'Servicewissen löschen' }));
  expect(screen.getByText(/Dokumente, einen laufenden Import oder eine Bot-Zuordnung/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Wissensbereich löschen' }));

  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    '/api/v1/collections/area-1',
    { method: 'DELETE' },
  ));
  expect(await screen.findByText('Wissensbereich gelöscht.')).toBeTruthy();
});
