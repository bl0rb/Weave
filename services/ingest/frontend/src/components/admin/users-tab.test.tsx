// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import { UsersTab } from './users-tab';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const user = { id: 'alice', username: 'alice', email: 'alice@example.com', role: 'user', team_id: 'a', team_ids: ['a'], is_active: true, oidc_provider_id: null, created_at: '2026-09-08T00:00:00Z' };

beforeEach(() => {
  api.mockReset();
  api.mockImplementation(async path => {
    if (path === '/api/v1/auth/admin/users') return { items: [user] };
    if (path === '/api/v1/auth/admin/teams') return { items: [{ id: 'a', name: 'Legal' }, { id: 'b', name: 'Finance' }] };
    return user;
  });
});
afterEach(cleanup);

it('saves multiple memberships without changing the primary team', async () => {
  render(<UsersTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit alice' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Finance' }));
  expect((screen.getByRole('checkbox', { name: /^Legal/ }) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(api.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(true));
  const mutation = api.mock.calls.find(([, options]) => options?.method === 'PATCH');
  expect(JSON.parse(mutation?.[1]?.body as string)).toMatchObject({ team_id: 'a', team_ids: ['a', 'b'], team_roles: { a: 'member', b: 'member' } });
});

it('clears all memberships when selecting no team', async () => {
  render(<UsersTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit alice' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Primary team' }), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(api.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(true));
  const mutation = api.mock.calls.find(([, options]) => options?.method === 'PATCH');
  expect(JSON.parse(mutation?.[1]?.body as string)).toMatchObject({ clear_team: true, team_ids: [] });
});