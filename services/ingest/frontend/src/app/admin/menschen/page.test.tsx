// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import AdminMenschenPage from './page';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiJson: vi.fn(),
}));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => ({ user: { username: 'Ada', role: 'admin' } }) }));
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(''),
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => '/admin/menschen',
}));

const api = vi.mocked(apiJson);
const user = { id: 'alice', username: 'alice', email: 'alice@example.com', role: 'user', team_id: 'a', team_ids: ['a'], is_active: true, oidc_provider_id: null, created_at: '2026-09-08T00:00:00Z' };

beforeEach(() => {
  api.mockReset();
  api.mockImplementation(async (path: string) => {
    if (path === '/api/v1/auth/admin/users') return { items: [user] };
    if (path === '/api/v1/auth/admin/teams') return { items: [{ id: 'a', name: 'Legal' }, { id: 'b', name: 'Finance' }] };
    return { items: [] };
  });
});
afterEach(cleanup);

it('renders the Menschen & Zugriffe page head and defaults to the Personen section', async () => {
  render(<AdminMenschenPage />);
  expect(await screen.findByRole('heading', { name: 'Menschen & Zugriffe' })).toBeTruthy();
  expect(await screen.findByText('alice@example.com')).toBeTruthy();
  const personenTab = screen.getByRole('tab', { name: 'Personen' });
  expect(personenTab.getAttribute('aria-selected')).toBe('true');
});

it('switches to the Teams section via the segmented control', async () => {
  render(<AdminMenschenPage />);
  await screen.findByText('alice@example.com');
  fireEvent.click(screen.getByRole('tab', { name: 'Teams' }));
  expect(await screen.findByText('Legal')).toBeTruthy();
  expect(screen.getByRole('tab', { name: 'Teams' }).getAttribute('aria-selected')).toBe('true');
});
