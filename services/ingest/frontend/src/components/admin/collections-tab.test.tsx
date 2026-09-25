// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { CollectionsTab } from './collections-tab';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const area = {
  collection_id: 'area', slug: 'wissen', name: 'Wissen', description: 'Thema',
  read_teams: ['Service'], visibility: 'restricted', read_users: [], read_user_details: [],
  owner: { id: 'user', username: 'Ada' }, document_count: 6, pending_count: 2, running_count: 1, review_count: 1, failed_count: 1, released_count: 1,
};
const teams = { items: [{ name: 'Service', member_count: 4 }, { name: 'Recht', member_count: 2 }] };

beforeEach(() => {
  api.mockReset();
  api.mockImplementation(async path => {
    if (path.startsWith('/api/v1/portal/admin/collections')) return { items: [area], total: 1 };
    if (path === '/api/v1/directory/teams') return teams;
    if (path.startsWith('/api/v1/directory/users')) return { items: [] };
    return area;
  });
});
afterEach(cleanup);

async function edit() {
  render(<CollectionsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Wissen bearbeiten' }));
  await screen.findByRole('textbox', { name: 'Name' });
}

it('shows ownership and separate processing and approval counts', async () => {
  render(<CollectionsTab />);
  await screen.findByText('Ada');
  expect(screen.getByText('2 warten')).toBeTruthy();
  expect(screen.getByText('1 zur Prüfung')).toBeTruthy();
  expect(screen.getByText('1 freigegeben')).toBeTruthy();
  expect(screen.queryByText(/erfolgreich indexiert/i)).toBeNull();
});

it('renames a collection without touching its access', async () => {
  await edit();
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Neues Thema' } });
  fireEvent.click(screen.getByRole('button', { name: 'Änderungen speichern' }));
  await waitFor(() => expect(api.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(true));
  const mutation = api.mock.calls.find(([, options]) => options?.method === 'PATCH');
  expect(JSON.parse(mutation?.[1]?.body as string)).toEqual({ name: 'Neues Thema', description: 'Thema' });
  await screen.findByText('Wissensbereich gespeichert.');
});

it('opens the access dialog and saves the selected teams', async () => {
  await edit();
  fireEvent.click(screen.getByRole('button', { name: 'Zugriff ändern' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /^Recht/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Zugriff speichern' }));
  await waitFor(() => expect(api.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(true));
  const mutation = api.mock.calls.find(([, options]) => options?.method === 'PATCH');
  expect(JSON.parse(mutation?.[1]?.body as string)).toEqual({ visibility: 'restricted', read_teams: ['Service', 'Recht'], read_users: [] });
});

it('shows a denied admin response without a collection editor', async () => {
  api.mockRejectedValue(new ApiError(403, 'admin required'));
  render(<CollectionsTab />);
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: /bearbeiten/ })).toBeNull();
});
