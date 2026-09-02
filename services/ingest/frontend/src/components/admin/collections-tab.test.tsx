// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { CollectionsTab } from './collections-tab';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const area = { collection_id: 'area', slug: 'wissen', name: 'Wissen', description: 'Thema', read_teams: ['Service'], owner: { id: 'user', username: 'Ada' }, document_count: 6, pending_count: 2, running_count: 1, review_count: 1, failed_count: 1, released_count: 1 };

beforeEach(() => {
  api.mockReset();
  api.mockImplementation(async path => {
    if (path.startsWith('/api/v1/portal/admin/collections')) return { items: [area], total: 1 };
    if (path === '/api/v1/auth/admin/teams') return { items: [{ id: 'team', name: 'Service' }, { id: 'legal', name: 'Recht' }] };
    return area;
  });
});
afterEach(cleanup);

async function edit() {
  render(<CollectionsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Wissen bearbeiten' }));
  await screen.findByRole('checkbox', { name: /^Service/ });
}

it('shows ownership and separate processing and approval counts', async () => {
  render(<CollectionsTab />);
  await screen.findByText('Ada');
  expect(screen.getByText('2 warten')).toBeTruthy();
  expect(screen.getByText('1 zur Prüfung')).toBeTruthy();
  expect(screen.getByText('1 freigegeben')).toBeTruthy();
  expect(screen.queryByText(/erfolgreich indexiert/i)).toBeNull();
});

it('cannot turn an empty team selection into public read access', async () => {
  await edit();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Service' }));
  const save = screen.getByRole('button', { name: 'Änderungen speichern' }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  fireEvent.click(save);
  expect(api.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(false);
  fireEvent.click(screen.getByRole('radio', { name: 'Alle angemeldeten Teams' }));
  expect(save.disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich bestätige/ }));
  expect(save.disabled).toBe(false);
  fireEvent.click(save);
  await screen.findByText('Wissensbereich gespeichert.');
  const mutation = api.mock.calls.find(([, options]) => options?.method === 'PATCH');
  expect(JSON.parse(mutation?.[1]?.body as string).read_teams).toEqual([]);
});

it('preserves existing readers not returned in the current team list when renaming', async () => {
  api.mockImplementation(async path => path === '/api/v1/auth/admin/teams' ? { items: [] }
    : path.startsWith('/api/v1/portal/admin/collections') ? { items: [area], total: 1 } : area);
  await edit();
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Neues Thema' } });
  fireEvent.click(screen.getByRole('button', { name: 'Änderungen speichern' }));
  await waitFor(() => expect(api.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(true));
  const mutation = api.mock.calls.find(([, options]) => options?.method === 'PATCH');
  expect(JSON.parse(mutation?.[1]?.body as string)).toEqual({ name: 'Neues Thema', description: 'Thema', read_teams: ['Service'] });
});

it('shows a denied admin response without a collection editor', async () => {
  api.mockRejectedValue(new ApiError(403, 'admin required'));
  render(<CollectionsTab />);
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: /bearbeiten/ })).toBeNull();
});
