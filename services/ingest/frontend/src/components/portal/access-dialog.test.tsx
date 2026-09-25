// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { AccessDialog, type AccessDialogCollection } from './access-dialog';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);

const collection: AccessDialogCollection = {
  collection_id: 'area-1', name: 'Servicewissen',
  visibility: 'restricted', read_teams: ['Service'], read_users: ['u1'],
  read_user_details: [{ id: 'u1', username: 'jdoe', display_name: 'Jane Doe', team: 'Service' }],
};
const openCollection: AccessDialogCollection = { ...collection, read_teams: [], read_users: [], read_user_details: [] };
const teamsResponse = { items: [{ name: 'Service', member_count: 4 }, { name: 'Recht', member_count: 2 }] };
const searchResponse = { items: [{ id: 'u2', username: 'msmith', display_name: 'Max Smith', team: 'Recht' }] };

beforeEach(() => {
  api.mockReset();
  api.mockImplementation(async (path, init) => {
    if (path === '/api/v1/directory/teams') return teamsResponse;
    if (typeof path === 'string' && path.startsWith('/api/v1/directory/users')) return searchResponse;
    if (init?.method === 'PATCH') return { ...collection, ...JSON.parse(init.body as string) };
    throw new Error(`unexpected request: ${path}`);
  });
});
afterEach(cleanup);

it('hides the team and person controls in public mode', async () => {
  render(<AccessDialog collection={collection} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  expect(screen.getByPlaceholderText('Name oder Team suchen …')).toBeTruthy();

  fireEvent.click(screen.getByRole('radio', { name: /^Öffentlich/ }));
  expect(screen.queryByRole('checkbox', { name: /^Service/ })).toBeNull();
  expect(screen.queryByPlaceholderText('Name oder Team suchen …')).toBeNull();

  fireEvent.click(screen.getByRole('radio', { name: /^Ausgewählte Teams/ }));
  expect(await screen.findByRole('checkbox', { name: /^Service/ })).toBeTruthy();
});

it('updates the summary count as teams and persons are toggled', async () => {
  render(<AccessDialog collection={openCollection} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  expect(screen.getByText(/Nur Editoren/)).toBeTruthy();

  fireEvent.click(screen.getByRole('checkbox', { name: /^Service/ }));
  expect(await screen.findByText(/^4 Personen können/)).toBeTruthy();

  fireEvent.change(screen.getByPlaceholderText('Name oder Team suchen …'), { target: { value: 'Max' } });
  fireEvent.click(await screen.findByRole('button', { name: /Max Smith/ }));
  expect(await screen.findByText(/^5 Personen können/)).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Max Smith entfernen' }));
  expect(await screen.findByText(/^4 Personen können/)).toBeTruthy();
});

it('adds the first search hit on Enter', async () => {
  render(<AccessDialog collection={openCollection} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  const search = screen.getByPlaceholderText('Name oder Team suchen …');
  fireEvent.change(search, { target: { value: 'Max' } });
  await waitFor(() => expect(api.mock.calls.some(([path]) => typeof path === 'string' && path.startsWith('/api/v1/directory/users'))).toBe(true));

  fireEvent.keyDown(search, { key: 'Enter' });
  expect(await screen.findByRole('button', { name: 'Max Smith entfernen' })).toBeTruthy();
  expect((search as HTMLInputElement).value).toBe('');
});

it('saves exactly {visibility, read_teams, read_users}', async () => {
  const onSaved = vi.fn();
  render(<AccessDialog collection={collection} onClose={vi.fn()} onSaved={onSaved} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  fireEvent.click(screen.getByRole('button', { name: 'Zugriff speichern' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());

  const patchCall = api.mock.calls.find(([, init]) => init?.method === 'PATCH');
  expect(patchCall?.[0]).toBe('/api/v1/collections/area-1');
  expect(JSON.parse(patchCall?.[1]?.body as string)).toEqual({ visibility: 'restricted', read_teams: ['Service'], read_users: ['u1'] });
});

it('sends empty lists when switching to public', async () => {
  render(<AccessDialog collection={collection} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  fireEvent.click(screen.getByRole('radio', { name: /^Öffentlich/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Zugriff speichern' }));
  await waitFor(() => expect(api.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));

  const patchCall = api.mock.calls.find(([, init]) => init?.method === 'PATCH');
  expect(JSON.parse(patchCall?.[1]?.body as string)).toEqual({ visibility: 'public', read_teams: [], read_users: [] });
});

it('shows the 422 detail and keeps the dialog open', async () => {
  api.mockImplementation(async (path, init) => {
    if (path === '/api/v1/directory/teams') return teamsResponse;
    if (typeof path === 'string' && path.startsWith('/api/v1/directory/users')) return searchResponse;
    if (init?.method === 'PATCH') throw new ApiError(422, 'Unknown team(s): Recht');
    throw new Error(`unexpected request: ${path}`);
  });
  const onSaved = vi.fn();
  const onClose = vi.fn();
  render(<AccessDialog collection={collection} onClose={onClose} onSaved={onSaved} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  fireEvent.click(screen.getByRole('button', { name: 'Zugriff speichern' }));

  expect(await screen.findByText('Unknown team(s): Recht')).toBeTruthy();
  expect(onSaved).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});
