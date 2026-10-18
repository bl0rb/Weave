// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import NewImportPage from './page';

const navigation = vi.hoisted(() => ({ push: vi.fn(), query: 'from=old-run' }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation, useSearchParams: () => new URLSearchParams(navigation.query) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
vi.mock('@/lib/webhooks', () => ({ listWebhookConnections: async () => ({ items: [] }) }));
afterEach(() => { cleanup(); navigation.push.mockReset(); navigation.query = 'from=old-run'; vi.mocked(apiJson).mockReset(); });

const SOURCE = { id: 'source-1', name: 'Confluence', base_url: 'https://wiki.example', server_kind: 'cloud', auth_type: 'cloud_basic', auth_username: 'ada@example.com', has_credential: true };
const SYNC = {
  id: 'sync-1', source_id: 'source-1', source_name: 'Confluence', scope_type: 'space', scope_value: 'DOC', root_page_title: 'Handbuch',
  collection_id: 'space-a', collection_name: 'Servicewissen', enabled: false, interval_seconds: null, last_success_at: null,
  last_error: null, next_refresh_at: null, last_run: null, created_at: '2026-10-01T00:00:00Z',
  options: { collection_id: 'space-a', max_pages: 10, max_depth: 2, include_attachments: true, ocr_attachments: false, ocr_profile_id: null, tags: ['alt'], email: '', path_tags: false, webhook_connection_id: null },
};

it('preserves the knowledge collection when editing and rerunning an old import', async () => {
  const api = vi.mocked(apiJson);
  api.mockImplementation(async (path, options) => {
    if (path === '/api/v1/import/sources') return { items: [{ id: 'source-1', name: 'Confluence', base_url: 'https://wiki.example', server_kind: 'cloud', auth_type: 'cloud_basic', auth_username: 'ada@example.com', has_credential: true }] };
    if (path === '/api/v1/import/runs/old-run') return {
      id: 'old-run', source_id: 'source-1', scope_type: 'page', scope_value: '42',
      options: { collection_id: 'existing-collection', max_pages: 10, max_depth: 2, include_attachments: false, ocr_attachments: false, tags: [], webhook_connection_id: 'foreign-hook' },
    };
    if (path === '/api/v1/import/runs' && options?.method === 'POST') return { id: 'new-run' };
    return { items: [], profiles: [] };
  });
  render(<NewImportPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'Import starten' }));
  await waitFor(() => expect(navigation.push).toHaveBeenCalledWith('/imports/new-run'));
  const request = api.mock.calls.find(([path, options]) => path === '/api/v1/import/runs' && options?.method === 'POST');
  const payload = JSON.parse(String(request?.[1]?.body));
  expect(payload.options.collection_id).toBe('existing-collection');
  expect(payload.scope).toEqual({ type: 'page', value: '42' });
  expect(payload.options.webhook_connection_id).toBeNull();
  expect(payload.sync).toEqual({ enabled: false, interval_seconds: 86400 });
});

it('edits an import configuration in place and can sync it right away', async () => {
  navigation.query = 'sync=sync-1';
  const api = vi.mocked(apiJson);
  api.mockImplementation(async (path, options) => {
    if (path === '/api/v1/import/sources') return { items: [SOURCE] };
    if (path === '/api/v1/import/syncs/sync-1' && !options?.method) return SYNC;
    if (path === '/api/v1/import/syncs/sync-1' && options?.method === 'PATCH') return SYNC;
    if (path === '/api/v1/import/syncs/sync-1/run') return { id: 'sync-run' };
    return { items: [], profiles: [] };
  });
  render(<NewImportPage />);
  expect(await screen.findByRole('heading', { name: 'Import bearbeiten' })).toBeTruthy();
  expect(await screen.findByText('Servicewissen')).toBeTruthy();
  fireEvent.click(screen.getByRole('switch', { name: 'Automatisch synchronisieren' }));
  fireEvent.click(screen.getByRole('button', { name: 'Speichern & jetzt synchronisieren' }));
  await waitFor(() => expect(navigation.push).toHaveBeenCalledWith('/imports/sync-run'));
  const patch = api.mock.calls.find(([path, options]) => path === '/api/v1/import/syncs/sync-1' && options?.method === 'PATCH');
  const body = JSON.parse(String(patch?.[1]?.body));
  expect(body.enabled).toBe(true);
  expect(body.options.max_pages).toBe(10);
  expect(body.options.tags).toEqual(['alt']);
  expect(api.mock.calls.some(([path]) => path === '/api/v1/import/runs')).toBe(false);
});

it('requires a target knowledge space for a new import', async () => {
  navigation.query = 'source=source-1';
  const api = vi.mocked(apiJson);
  api.mockImplementation(async (path) => {
    if (path === '/api/v1/import/sources') return { items: [SOURCE] };
    if (path === '/api/v1/collections') return { items: [{ collection_id: 'space-a', name: 'Servicewissen', can_upload: true }] };
    return { items: [], profiles: [] };
  });
  render(<NewImportPage />);
  fireEvent.change(await screen.findByPlaceholderText(/DOCS or https/), { target: { value: 'DOC' } });
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Import starten' }));
  expect(await screen.findByText('Bitte einen Ziel-Wissensbereich wählen.')).toBeTruthy();
  expect(api.mock.calls.some(([path]) => path === '/api/v1/import/runs')).toBe(false);
});
