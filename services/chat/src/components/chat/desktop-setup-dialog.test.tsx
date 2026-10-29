// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DesktopSetupDialog } from '@/components/chat/desktop-setup-dialog';
import { I18nProvider } from '@/i18n/provider';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: () => {}, refresh: () => {} }),
}));

const SETUP = {
  server_url: 'https://weave.example.com/api',
  setup_url: 'weave://setup?server=https%3A%2F%2Fweave.example.com%2Fapi',
  download_url: 'https://github.com/bl0rb/WeaveDesktop/releases/latest',
};

const fetchMock = vi.fn();
beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const renderDialog = () => render(<I18nProvider initialLocale="de"><DesktopSetupDialog onClose={() => {}} /></I18nProvider>);

it('opens the weave:// link and shows the address for manual entry', async () => {
  fetchMock.mockResolvedValueOnce(json(200, SETUP));
  const assign = vi.fn();
  vi.stubGlobal('location', { ...window.location, assign });
  renderDialog();

  const open = await screen.findByRole('button', { name: 'Desktop-App öffnen' });
  expect(screen.getByText('https://weave.example.com/api')).toBeTruthy();
  expect((screen.getByRole('link', { name: 'Desktop-App herunterladen' }) as HTMLAnchorElement).href).toBe(SETUP.download_url);

  fireEvent.click(open);
  expect(assign).toHaveBeenCalledWith(SETUP.setup_url);
  expect(await screen.findByRole('status')).toBeTruthy();
});

it('reports when the address cannot be loaded', async () => {
  fetchMock.mockResolvedValueOnce(json(502, { detail: 'down' }));
  renderDialog();
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Desktop-App öffnen' }) as HTMLButtonElement).disabled).toBe(true);
});
