// @vitest-environment jsdom
import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { I18nProvider } from '@/i18n/provider';
import { AuthProvider, useAuth } from './auth-context';

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, refresh: vi.fn() }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiFetch: vi.fn() }));
const fetcher = vi.mocked(apiFetch);

function Consumer() {
  useAuth();
  return null;
}

beforeEach(() => {
  replace.mockReset();
  fetcher.mockReset();
});
afterEach(() => {
  cleanup();
  document.documentElement.lang = 'de';
});

it('redirects an unauthenticated mount of a protected page to /login with the original path as returnTo', async () => {
  window.history.pushState({}, '', '/knowledge/area?tab=docs');
  fetcher.mockImplementation(async path => {
    if (path === '/api/v1/auth/me') return { ok: false, status: 401 } as Response;
    return { ok: false, status: 404 } as Response;
  });
  render(<AuthProvider><Consumer /></AuthProvider>);
  await waitFor(() => expect(replace).toHaveBeenCalledWith('/login?returnTo=%2Fknowledge%2Farea%3Ftab%3Ddocs'));
});

it('does not redirect once a session is present', async () => {
  fetcher.mockImplementation(async path => path === '/api/v1/auth/me'
    ? ({ ok: true, json: async () => ({ id: 'u', username: 'Ada', role: 'member' }) } as unknown as Response)
    : ({ ok: false, status: 404 } as Response));
  render(<AuthProvider><Consumer /></AuthProvider>);
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/v1/auth/me', { skipAuthRedirect: true }));
  expect(replace).not.toHaveBeenCalled();
});

it("applies the signed-in user's account locale over the cookie/browser locale on load", async () => {
  document.documentElement.lang = 'de';
  fetcher.mockImplementation(async path => path === '/api/v1/auth/me'
    ? ({ ok: true, json: async () => ({ id: 'u', username: 'Ada', role: 'member', locale: 'en' }) } as unknown as Response)
    : ({ ok: false, status: 404 } as Response));

  render(
    <I18nProvider initialLocale="de">
      <AuthProvider><Consumer /></AuthProvider>
    </I18nProvider>,
  );

  await waitFor(() => expect(document.documentElement.lang).toBe('en'));
});

it('leaves the locale untouched and makes no write call when the account locale is null', async () => {
  document.documentElement.lang = 'de';
  fetcher.mockImplementation(async path => path === '/api/v1/auth/me'
    ? ({ ok: true, json: async () => ({ id: 'u', username: 'Ada', role: 'member', locale: null }) } as unknown as Response)
    : ({ ok: false, status: 404 } as Response));

  render(
    <I18nProvider initialLocale="de">
      <AuthProvider><Consumer /></AuthProvider>
    </I18nProvider>,
  );

  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/v1/auth/me', { skipAuthRedirect: true }));
  expect(document.documentElement.lang).toBe('de');
  expect(fetcher.mock.calls.every(([, init]) => !(init as RequestInit | undefined)?.method || (init as RequestInit).method === 'GET')).toBe(true);
});
