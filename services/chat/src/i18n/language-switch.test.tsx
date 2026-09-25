// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from './provider';
import { LanguageSwitch } from './language-switch';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: () => {} }) }));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.documentElement.lang = 'de';
});

describe('LanguageSwitch', () => {
  it('without persist (the login page), switching only changes the cookie/html locale — no persistence call', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(
      <I18nProvider initialLocale="de">
        <LanguageSwitch />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'English' }));

    await waitFor(() => expect(document.documentElement.lang).toBe('en'));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('with persist, applies immediately and PUTs the choice to the account with the right body', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe('/api/session/locale');
      expect(init?.method).toBe('PUT');
      expect(JSON.parse(init?.body as string)).toEqual({ locale: 'en' });
      return jsonResponse({ locale: 'en' });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <I18nProvider initialLocale="de">
        <LanguageSwitch persist />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'English' }));

    await waitFor(() => expect(document.documentElement.lang).toBe('en'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('on a failed save, keeps the newly chosen locale for this browser and shows a notice', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ kind: 'gateway_unreachable', message: 'x', detail: null }, 503)),
    );

    render(
      <I18nProvider initialLocale="de">
        <LanguageSwitch persist />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'English' }));

    await waitFor(() => expect(document.documentElement.lang).toBe('en'));
    await screen.findByRole('alert');
  });
});
