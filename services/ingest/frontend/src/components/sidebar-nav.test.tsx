// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '@/i18n/provider';
import { apiJson } from '@/lib/api';
import { REVIEW_TASKS_CHANGED_EVENT } from '@/lib/review-tasks';
import { CHANGES_CHANGED_EVENT } from '@/lib/document-freshness';
import { SidebarNav } from './sidebar-nav';

const auth = vi.hoisted(() => ({ user: { username: 'ada', role: 'user' as 'user' | 'admin' }, logout: vi.fn() }));
const navigation = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation, usePathname: () => '/' }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => auth }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn().mockResolvedValue({ items: [], total: 0 }) }));

afterEach(() => {
  cleanup();
  auth.user.role = 'user';
  vi.mocked(apiJson).mockReset();
  vi.mocked(apiJson).mockResolvedValue({ items: [], total: 0 });
});

it('renders English labels when the locale is English', async () => {
  // ThemeToggle reads this on mount; jsdom has no real implementation.
  window.matchMedia = window.matchMedia || (() => ({ matches: false }) as MediaQueryList);
  render(
    <I18nProvider initialLocale="en">
      <SidebarNav open={false} onOpenChange={() => {}} />
    </I18nProvider>,
  );

  expect(await screen.findByText('Overview')).toBeTruthy();
  expect(screen.queryByText('Tasks')).toBeNull();
  expect(screen.getByText('Knowledge spaces')).toBeTruthy();
  expect(screen.getByText('Documents')).toBeTruthy();
  expect(screen.queryByText('Wissensbereiche')).toBeNull();
  // Verbindungen sits right below Wissensbereiche, no longer in the profile menu.
  const links = screen.getAllByRole('link').filter(link => link.closest('nav'));
  expect(links.map(link => link.getAttribute('href'))).toEqual(['/', '/documents', '/aenderungen', '/knowledge', '/connections']);
  expect(screen.getByText('Changes')).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: /ada/i }));
  expect(screen.getByRole('button', { name: 'English' })).toBeTruthy();
  expect(screen.getByText('API tokens')).toBeTruthy();
  expect(screen.getByText('Sign out')).toBeTruthy();
  expect(screen.getAllByText('Connections')).toHaveLength(1);
});

function mockApi(writableSpaces: number, openTasks: () => number) {
  vi.mocked(apiJson).mockImplementation(async (path) => {
    if (path === '/api/v1/collections') return { items: Array.from({ length: writableSpaces }, (_, i) => ({ collection_id: `c${i}`, can_upload: true, can_manage: false })) };
    if (path === '/api/v1/portal/review-tasks/count') return { open: openTasks(), by_kind: {} };
    return { items: [], total: 0 };
  });
}
const navHrefs = () => screen.getAllByRole('link').filter(link => link.closest('nav')).map(link => link.getAttribute('href'));

it('shows Prüfaufgaben with the active-task badge to owners and members of a space', async () => {
  window.matchMedia = window.matchMedia || (() => ({ matches: false }) as MediaQueryList);
  let open = 4;
  mockApi(1, () => open);
  render(<SidebarNav open={false} onOpenChange={() => {}} />);
  const link = await screen.findByRole('link', { name: /Prüfaufgaben/ });
  expect(navHrefs()).toEqual(['/', '/documents', '/pruefaufgaben', '/aenderungen', '/knowledge', '/connections']);
  await waitFor(() => expect(link.textContent).toContain('4'));
  // The inbox announces a change: the badge refreshes without a navigation.
  open = 0;
  act(() => { window.dispatchEvent(new Event(REVIEW_TASKS_CHANGED_EVENT)); });
  await waitFor(() => expect(link.textContent).toBe('Prüfaufgaben'));
});

it('shows Prüfaufgaben to admins without checking their spaces', async () => {
  window.matchMedia = window.matchMedia || (() => ({ matches: false }) as MediaQueryList);
  auth.user.role = 'admin';
  mockApi(0, () => 2);
  render(<SidebarNav open={false} onOpenChange={() => {}} />);
  const link = await screen.findByRole('link', { name: /Prüfaufgaben/ });
  await waitFor(() => expect(link.textContent).toContain('2'));
  expect(vi.mocked(apiJson).mock.calls.some(call => call[0] === '/api/v1/collections')).toBe(false);
});

it('hides Prüfaufgaben from readers and never loads their count', async () => {
  window.matchMedia = window.matchMedia || (() => ({ matches: false }) as MediaQueryList);
  mockApi(0, () => 5);
  render(<SidebarNav open={false} onOpenChange={() => {}} />);
  await waitFor(() => expect(vi.mocked(apiJson).mock.calls.some(call => call[0] === '/api/v1/collections')).toBe(true));
  expect(screen.queryByRole('link', { name: /Prüfaufgaben/ })).toBeNull();
  expect(vi.mocked(apiJson).mock.calls.some(call => call[0] === '/api/v1/portal/review-tasks/count')).toBe(false);
});

it('shows Änderungen to everybody with the unread badge, refreshed when the feed marks notifications read', async () => {
  window.matchMedia = window.matchMedia || (() => ({ matches: false }) as MediaQueryList);
  let unread = 3;
  vi.mocked(apiJson).mockImplementation(async (path) => {
    if (path === '/api/v1/portal/changes/count') return { unread };
    return { items: [], total: 0 };
  });
  render(<SidebarNav open={false} onOpenChange={() => {}} />);
  const link = await screen.findByRole('link', { name: /Änderungen/ });
  expect(link.getAttribute('href')).toBe('/aenderungen');
  await waitFor(() => expect(link.textContent).toContain('3'));
  unread = 0;
  act(() => { window.dispatchEvent(new Event(CHANGES_CHANGED_EVENT)); });
  await waitFor(() => expect(link.textContent).toBe('Änderungen'));
});

it('hides the Änderungen badge when the count cannot be loaded', async () => {
  window.matchMedia = window.matchMedia || (() => ({ matches: false }) as MediaQueryList);
  vi.mocked(apiJson).mockImplementation(async (path) => {
    if (path === '/api/v1/portal/changes/count') throw new Error('offline');
    return { items: [], total: 0 };
  });
  render(<SidebarNav open={false} onOpenChange={() => {}} />);
  const link = await screen.findByRole('link', { name: /Änderungen/ });
  await waitFor(() => expect(vi.mocked(apiJson).mock.calls.some(call => call[0] === '/api/v1/portal/changes/count')).toBe(true));
  expect(link.textContent).toBe('Änderungen');
});
