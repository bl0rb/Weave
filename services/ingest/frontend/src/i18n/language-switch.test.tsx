// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { apiJson } from '@/lib/api';
import { I18nProvider } from './provider';
import { LanguageSwitch } from './language-switch';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: () => {} }) }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiJson: vi.fn(),
}));
const apiJsonMock = vi.mocked(apiJson);

beforeEach(() => {
  // Deterministic browser-language resolution for the Auto option,
  // regardless of the test runner's own jsdom locale.
  Object.defineProperty(window.navigator, 'languages', { value: ['en-US'], configurable: true });
});

afterEach(() => {
  cleanup();
  apiJsonMock.mockReset();
  document.documentElement.lang = 'de';
  document.cookie = 'weave_lang=; path=/; max-age=0';
});

describe('LanguageSwitch', () => {
  it('without persist (the login/setup pages), switching only changes the cookie/html locale — no account call', async () => {
    render(
      <I18nProvider initialLocale="de">
        <LanguageSwitch />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'English' }));

    await waitFor(() => expect(document.documentElement.lang).toBe('en'));
    expect(apiJsonMock).not.toHaveBeenCalled();
  });

  it('with persist, applies immediately and PATCHes the choice to the account with the right body', async () => {
    apiJsonMock.mockResolvedValue({});

    render(
      <I18nProvider initialLocale="de">
        <LanguageSwitch persist />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'English' }));

    await waitFor(() => expect(document.documentElement.lang).toBe('en'));
    await waitFor(() =>
      expect(apiJsonMock).toHaveBeenCalledWith(
        '/api/v1/auth/me',
        expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ locale: 'en' }) }),
      ),
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('on a failed save, keeps the newly chosen locale for this browser and shows a notice', async () => {
    apiJsonMock.mockRejectedValue(new Error('network unreachable'));

    render(
      <I18nProvider initialLocale="de">
        <LanguageSwitch persist />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'English' }));

    await waitFor(() => expect(document.documentElement.lang).toBe('en'));
    await screen.findByRole('alert');
  });

  it('Auto is pressed by default; German/English are not', () => {
    render(
      <I18nProvider initialLocale="de">
        <LanguageSwitch />
      </I18nProvider>,
    );
    expect(screen.getByRole('button', { name: 'Auto' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'Deutsch' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: 'English' }).getAttribute('aria-pressed')).toBe('false');
  });

  it('choosing Auto after an explicit pick deletes the cookie and re-resolves from the browser languages', async () => {
    render(
      <I18nProvider initialLocale="de">
        <LanguageSwitch />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Deutsch' }));
    await waitFor(() => expect(document.cookie).toContain('weave_lang=de'));

    fireEvent.click(screen.getByRole('button', { name: 'Auto' }));

    // navigator.languages is stubbed to ['en-US'] in beforeEach.
    await waitFor(() => expect(document.documentElement.lang).toBe('en'));
    expect(document.cookie).not.toContain('weave_lang=de');
    expect(screen.getByRole('button', { name: 'Auto' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('with persist, choosing Auto PATCHes locale: null', async () => {
    apiJsonMock.mockResolvedValue({});

    render(
      <I18nProvider initialLocale="en" initialPreference="en">
        <LanguageSwitch persist />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Auto' }));

    await waitFor(() =>
      expect(apiJsonMock).toHaveBeenCalledWith(
        '/api/v1/auth/me',
        expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ locale: null }) }),
      ),
    );
  });
});
