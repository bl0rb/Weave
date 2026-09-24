// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import ImportsPage from './page';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiJson: vi.fn(),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

const api = vi.mocked(apiJson);
const run = {
  id: 'run-1', kind: 'confluence', status: 'finished' as const, scope_type: 'page', scope_value: '42',
  root_page_title: 'Servicehandbuch', can_sync: true, can_edit: true,
  pages_discovered: 1, pages_imported: 1, pages_failed: 0, attachments_saved: 0,
  artifact_bytes: 0, content_bytes: 20, created_at: '2026-09-14T08:00:00Z', updated_at: '2026-09-14T08:01:00Z',
  started_at: '2026-09-14T08:00:00Z', finished_at: '2026-09-14T08:01:00Z', owner: { id: 'u1', username: 'Ada' },
};

beforeEach(() => api.mockReset());
afterEach(cleanup);

it('lists completed imports and links their editable configuration', async () => {
  api.mockResolvedValue({ items: [run] });
  render(<ImportsPage />);
  const link = await screen.findByRole('link', { name: 'Bearbeiten & erneut ausführen' });
  expect(link.getAttribute('href')).toBe('/imports/new?from=run-1');
  expect(screen.getByRole('link', { name: 'Servicehandbuch' })).toBeTruthy();
});

it('honors an explicit server denial for editing a completed import', async () => {
  api.mockResolvedValue({ items: [{ ...run, can_edit: false }] });
  render(<ImportsPage />);
  await screen.findByRole('link', { name: 'Servicehandbuch' });
  expect(screen.queryByRole('link', { name: 'Bearbeiten & erneut ausführen' })).toBeNull();
});
