// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiFetch, apiJson } from '@/lib/api';
import { TechnicalIdentitiesTab } from './technical-identities-tab';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
}));

const json = vi.mocked(apiJson);
const fetcher = vi.mocked(apiFetch);

const savedIdentity = {
  id: 'ident-1',
  name: 'n8n-Integration',
  description: 'Standalone n8n-Anbindung.',
  allowed_collections: ['handbuch'],
  enabled: true,
  token_prefix: 'wti_abcd12',
  created_at: '2026-09-04T08:00:00Z',
  updated_at: '2026-09-04T08:00:00Z',
  last_used_at: null,
  expires_at: null,
  revoked_at: null,
  created_by: 'admin-1',
};

const createdResponse = { ...savedIdentity, token: 'wti_freshraw-token-value' };

beforeEach(() => {
  json.mockReset();
  fetcher.mockReset();
  json.mockImplementation(async (path, init) => {
    if (path === '/api/v1/auth/admin/technical-identities' && init?.method === 'POST') return createdResponse;
    if (path === '/api/v1/auth/admin/technical-identities') return { items: [] };
    if (path === '/api/v1/collections') return { items: [{ collection_id: 'area-1', slug: 'handbuch', name: 'Handbuch', description: null, read_teams: [], can_manage: true }] };
    if (String(path).endsWith('/rotate')) return { ...savedIdentity, token: 'wti_rotated-raw-token' };
    if (String(path).endsWith('/audit')) return { items: [] };
    throw new Error(`unexpected request: ${path}`);
  });
  fetcher.mockResolvedValue({ ok: true } as Response);
});

afterEach(cleanup);

it('shows that a new identity has no knowledge access by default', async () => {
  render(<TechnicalIdentitiesTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Identität anlegen' }));
  expect(screen.getByText(/erhält KEINEN Wissenszugriff/)).toBeTruthy();
});

it('creates an identity with selected collections and shows the token once', async () => {
  render(<TechnicalIdentitiesTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Identität anlegen' }));

  fireEvent.change(screen.getByRole('textbox', { name: /^Name/ }), { target: { value: 'n8n-Integration' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'handbuch' }));
  fireEvent.click(screen.getByRole('button', { name: 'Identität speichern' }));

  await waitFor(() => expect(json.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  const mutation = json.mock.calls.find(([, init]) => init?.method === 'POST');
  const body = JSON.parse(mutation?.[1]?.body as string);
  expect(body.name).toBe('n8n-Integration');
  expect(body.allowed_collections).toEqual(['handbuch']);

  expect(await screen.findByText('wti_freshraw-token-value')).toBeTruthy();
  expect(screen.getByText(/wird nur jetzt angezeigt/)).toBeTruthy();
});

it('revokes an identity only after confirmation', async () => {
  json.mockImplementation(async path => {
    if (path === '/api/v1/auth/admin/technical-identities') return { items: [savedIdentity] };
    if (path === '/api/v1/collections') return { items: [] };
    throw new Error(`unexpected request: ${path}`);
  });
  render(<TechnicalIdentitiesTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'n8n-Integration widerrufen' }));
  fireEvent.click(screen.getByRole('button', { name: 'Identität widerrufen' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    '/api/v1/auth/admin/technical-identities/ident-1/revoke',
    { method: 'POST' },
  ));
});

it('rotates the token and shows the new raw value once', async () => {
  json.mockImplementation(async (path, init) => {
    if (path === '/api/v1/auth/admin/technical-identities') return { items: [savedIdentity] };
    if (path === '/api/v1/collections') return { items: [] };
    if (String(path).endsWith('/rotate') && init?.method === 'POST') return { ...savedIdentity, token: 'wti_rotated-raw-token' };
    throw new Error(`unexpected request: ${path}`);
  });
  render(<TechnicalIdentitiesTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'n8n-Integration rotieren' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Token erneuern' }));
  expect(await screen.findByText('wti_rotated-raw-token')).toBeTruthy();
});

it('does not rotate the token before confirmation', async () => {
  json.mockImplementation(async path => {
    if (path === '/api/v1/auth/admin/technical-identities') return { items: [savedIdentity] };
    if (path === '/api/v1/collections') return { items: [] };
    throw new Error(`unexpected request: ${path}`);
  });
  render(<TechnicalIdentitiesTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'n8n-Integration rotieren' }));
  expect(json.mock.calls.some(([path]) => String(path).endsWith('/rotate'))).toBe(false);
});
