// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import type { ImportSync } from '@/lib/imports';
import { ConfluenceSyncs } from './confluence-syncs';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
afterEach(() => { cleanup(); vi.mocked(apiJson).mockReset(); });

const sync = (overrides: Partial<ImportSync> = {}): ImportSync => ({
  id: 'sync-1', source_id: 'source-1', source_name: 'Confluence', scope_type: 'space', scope_value: 'DOC', root_page_title: 'Handbuch',
  collection_id: 'space-a', collection_name: 'Servicewissen', enabled: true, interval_seconds: 21600,
  last_success_at: '2026-10-17T08:00:00Z', last_error: null, next_refresh_at: '2999-01-01T00:00:00Z', created_at: '2026-10-01T00:00:00Z',
  last_run: { id: 'run-9', status: 'finished', is_refresh: true, created_at: '2026-10-17T07:50:00Z', finished_at: '2026-10-17T08:00:00Z', pages_imported: 4, pages_failed: 0 },
  options: { collection_id: 'space-a', max_pages: 10, max_depth: 2, include_attachments: true, ocr_attachments: false, ocr_profile_id: null, tags: [], email: '', path_tags: false, webhook_connection_id: null },
  ...overrides,
});

it('lists each configured import with target, schedule, last run and actions', () => {
  render(<ConfluenceSyncs sourceId="source-1" syncs={[sync(), sync({ id: 'sync-2', scope_value: 'HR', root_page_title: '', enabled: false, next_refresh_at: null, last_run: null, last_success_at: null, last_error: 'Seite nicht gefunden' })]} onChanged={async () => {}} />);
  expect(screen.getByText('Importe (2)')).toBeTruthy();
  expect(screen.getAllByText('Ziel: Servicewissen')).toHaveLength(2);
  expect(screen.getByText('Bereich HR')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Abgeschlossen' }).getAttribute('href')).toBe('/imports/run-9');
  expect(screen.getByRole('link', { name: 'Handbuch bearbeiten' }).getAttribute('href')).toBe('/imports/new?sync=sync-1');
  expect(screen.getByRole('link', { name: /Import hinzufügen/ }).getAttribute('href')).toBe('/imports/new?source=source-1');
  expect(screen.getByText('Seite nicht gefunden')).toBeTruthy();
});

it('toggles auto-sync and starts a run now', async () => {
  const api = vi.mocked(apiJson);
  api.mockImplementation(async (path) => (String(path).endsWith('/run') ? { id: 'run-new' } : sync({ enabled: false })));
  const onChanged = vi.fn(async () => {});
  render(<ConfluenceSyncs sourceId="source-1" syncs={[sync()]} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole('switch', { name: 'Automatisch aktualisieren' }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  expect(api).toHaveBeenCalledWith('/api/v1/import/syncs/sync-1', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ enabled: false }) }));
  fireEvent.click(screen.getByRole('button', { name: 'Jetzt synchronisieren' }));
  expect((await screen.findByRole('link', { name: 'Lauf ansehen' })).getAttribute('href')).toBe('/imports/run-new');
  expect(api).toHaveBeenCalledWith('/api/v1/import/syncs/sync-1/run', { method: 'POST' });
});

it('explains an empty connection', () => {
  render(<ConfluenceSyncs sourceId="source-1" syncs={[]} onChanged={async () => {}} />);
  expect(screen.getByText('Über diese Verbindung ist noch kein Import eingerichtet.')).toBeTruthy();
});
