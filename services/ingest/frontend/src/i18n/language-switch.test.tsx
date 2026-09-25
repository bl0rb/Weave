// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
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

afterEach(() => {
  cleanup();
  apiJsonMock.mockReset();
  document.documentElement.lang = 'de';
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
});
