// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { AccessDialog, type AccessDialogCollection } from './access-dialog';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);

const owner = { user_id: 'u0', team_id: null, role: 'owner' as const, name: 'ada', team: null };
const collection: AccessDialogCollection = {
  collection_id: 'area-1', name: 'Servicewissen', visibility: 'restricted',
  grants: [
    owner,
    { user_id: 'u1', team_id: null, role: 'reader', name: 'jdoe', team: 'Service' },
    { user_id: null, team_id: 't-service', role: 'member', name: 'Service' },
  ],
};
const ownerOnly: AccessDialogCollection = { ...collection, grants: [owner] };
const teamsResponse = { items: [{ id: 't-service', name: 'Service', member_count: 4 }, { id: 't-recht', name: 'Recht', member_count: 2 }] };
const searchResponse = { items: [{ id: 'u2', username: 'msmith', display_name: null, team: 'Recht' }] };

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

it('keeps roles editable in public mode, since public only affects reading', async () => {
  render(<AccessDialog collection={collection} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  fireEvent.click(screen.getByRole('radio', { name: /^Öffentlich/ }));
  expect(screen.getByRole('checkbox', { name: /^Service/ })).toBeTruthy();
  expect(screen.getByRole('combobox', { name: 'Rolle von ada' })).toBeTruthy();
  expect(screen.getByText(/alle Mitarbeitenden können/)).toBeTruthy();
});

it('updates the summary count as teams and persons are added', async () => {
  render(<AccessDialog collection={ownerOnly} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  expect(screen.getByText(/^1 Person kann/)).toBeTruthy();

  fireEvent.click(screen.getByRole('checkbox', { name: /^Service/ }));
  expect(await screen.findByText(/^5 Personen können/)).toBeTruthy();

  fireEvent.change(screen.getByPlaceholderText('Name oder Team suchen …'), { target: { value: 'Max' } });
  fireEvent.click(await screen.findByRole('button', { name: /msmith/ }));
  expect(await screen.findByText(/^6 Personen können/)).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'msmith entfernen' }));
  expect(await screen.findByText(/^5 Personen können/)).toBeTruthy();
});

it('adds the first search hit on Enter as a reader', async () => {
  render(<AccessDialog collection={ownerOnly} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  const search = screen.getByPlaceholderText('Name oder Team suchen …');
  fireEvent.change(search, { target: { value: 'Max' } });
  await waitFor(() => expect(api.mock.calls.some(([path]) => typeof path === 'string' && path.startsWith('/api/v1/directory/users'))).toBe(true));

  fireEvent.keyDown(search, { key: 'Enter' });
  expect(await screen.findByRole('button', { name: 'msmith entfernen' })).toBeTruthy();
  expect((screen.getByRole('combobox', { name: 'Rolle von msmith' }) as HTMLSelectElement).value).toBe('reader');
  expect((search as HTMLInputElement).value).toBe('');
});

it('saves the visibility and every grant with its role', async () => {
  const onSaved = vi.fn();
  render(<AccessDialog collection={collection} onClose={vi.fn()} onSaved={onSaved} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  fireEvent.change(screen.getByRole('combobox', { name: 'Rolle von jdoe' }), { target: { value: 'owner' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Rolle von Service' }), { target: { value: 'reader' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /^Recht/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Freigabe speichern' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());

  const patchCall = api.mock.calls.find(([, init]) => init?.method === 'PATCH');
  expect(patchCall?.[0]).toBe('/api/v1/collections/area-1');
  expect(JSON.parse(patchCall?.[1]?.body as string)).toEqual({
    visibility: 'restricted',
    grants: [
      { user_id: 'u0', role: 'owner' },
      { user_id: 'u1', role: 'owner' },
      { team_id: 't-service', role: 'reader' },
      { team_id: 't-recht', role: 'member' },
    ],
  });
});

it('blocks saving without an owner', async () => {
  render(<AccessDialog collection={collection} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  fireEvent.change(screen.getByRole('combobox', { name: 'Rolle von ada' }), { target: { value: 'member' } });
  expect(screen.getByRole('alert').textContent).toContain('Besitzer');
  expect((screen.getByRole('button', { name: 'Freigabe speichern' }) as HTMLButtonElement).disabled).toBe(true);
});

it('shows the 422 detail and keeps the dialog open', async () => {
  api.mockImplementation(async (path, init) => {
    if (path === '/api/v1/directory/teams') return teamsResponse;
    if (typeof path === 'string' && path.startsWith('/api/v1/directory/users')) return searchResponse;
    if (init?.method === 'PATCH') throw new ApiError(422, 'Unknown user or team id(s): t-recht');
    throw new Error(`unexpected request: ${path}`);
  });
  const onSaved = vi.fn();
  const onClose = vi.fn();
  render(<AccessDialog collection={collection} onClose={onClose} onSaved={onSaved} />);
  await screen.findByRole('checkbox', { name: /^Service/ });
  fireEvent.click(screen.getByRole('button', { name: 'Freigabe speichern' }));

  expect(await screen.findByText('Unknown user or team id(s): t-recht')).toBeTruthy();
  expect(onSaved).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});
