// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import NewImportPage from './page';

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation, useSearchParams: () => new URLSearchParams('from=old-run') }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
vi.mock('@/lib/webhooks', () => ({ listWebhookConnections: async () => ({ items: [] }) }));
afterEach(cleanup);

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
  expect(payload.options.webhook_connection_id).toBeUndefined();
});
