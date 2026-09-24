// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '@/i18n/provider';
import { SidebarNav } from './sidebar-nav';

const auth = vi.hoisted(() => ({ user: { username: 'ada', role: 'user' as const }, logout: vi.fn() }));
const navigation = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation, usePathname: () => '/' }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => auth }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn().mockResolvedValue({ items: [], total: 0 }) }));

afterEach(cleanup);

it('renders English labels when the locale is English', async () => {
  // ThemeToggle reads this on mount; jsdom has no real implementation.
  window.matchMedia = window.matchMedia || (() => ({ matches: false }) as MediaQueryList);
  render(
    <I18nProvider initialLocale="en">
      <SidebarNav open={false} onOpenChange={() => {}} />
    </I18nProvider>,
  );

  expect(await screen.findByText('Overview')).toBeTruthy();
  expect(screen.getByText('Tasks')).toBeTruthy();
  expect(screen.getByText('Knowledge spaces')).toBeTruthy();
  expect(screen.getByText('Documents')).toBeTruthy();
  expect(screen.queryByText('Wissensbereiche')).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: /ada/i }));
  expect(screen.getByRole('button', { name: 'English' })).toBeTruthy();
  expect(screen.getByText('API tokens')).toBeTruthy();
  expect(screen.getByText('Sign out')).toBeTruthy();
});
