// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiAccessDialog } from '@/components/chat/api-access-dialog';
import { I18nProvider } from '@/i18n/provider';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: () => {}, refresh: () => {} }),
}));

const fetchMock = vi.fn();
beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const renderDialog = () => render(<I18nProvider initialLocale="de"><ApiAccessDialog onClose={() => {}} /></I18nProvider>);

it('creates a token, shows it once and lists it', async () => {
  const listed = { id: 't1', label: 'n8n Vertrieb', created_at: '2026-09-27T08:00:00Z', expires_at: null, last_used_at: null };
  fetchMock
    .mockResolvedValueOnce(json(200, { items: [] }))
    .mockResolvedValueOnce(json(201, { ...listed, token: 'raw-secret-token' }))
    .mockResolvedValueOnce(json(200, { items: [listed] }));
  renderDialog();
  await screen.findByText('Noch keine Tokens.');

  fireEvent.change(screen.getByRole('textbox', { name: 'Bezeichnung' }), { target: { value: 'n8n Vertrieb' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Gültig' }), { target: { value: 'never' } });
  fireEvent.click(screen.getByRole('button', { name: 'Token erstellen' }));

  expect(await screen.findByText('raw-secret-token')).toBeTruthy();
  const [, init] = fetchMock.mock.calls[1];
  expect(JSON.parse(init.body)).toEqual({ label: 'n8n Vertrieb', expires_in_days: null });
  await waitFor(() => expect(screen.getByRole('list', { name: 'Deine Tokens' }).textContent).toContain('n8n Vertrieb'));
});

it('explains that a token login cannot manage tokens', async () => {
  fetchMock.mockResolvedValueOnce(json(403, { detail: 'API tokens cannot manage tokens' }));
  renderDialog();
  expect(await screen.findByText(/nur verwalten, wenn du über die Weave-Anmeldung angemeldet bist/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Token erstellen' })).toBeNull();
});
