// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import type { PortalDocument } from '@/lib/portal';
import { DocumentTable } from './shared';

vi.mock('@/lib/api', async original => ({ ...await original<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
vi.mock('@/lib/data-cache', () => ({ useVisiblePolling: vi.fn() }));
afterEach(cleanup);

it('shows each document’s own confirmed status in the knowledge-area table using one batch', async () => {
  const release = { id: 'release', status: 'sent' as const, created_at: '2026-09-02T11:00:00Z', error_message: null };
  const document: PortalDocument = { id: 'first', original_filename: 'Handbuch.pdf', status: 'FINISHED', collection_id: 'area', collection_name: 'Service', created_at: release.created_at, quality_grade: 'A', quality_recommendation: 'allow', can_release: false, release };
  vi.mocked(apiJson).mockResolvedValueOnce({ items: [
    { job_id: 'first', release, indexing: { state: 'indexed', indexed_at: '2026-09-02T11:31:12Z', chunk_count: 4 } },
    { job_id: 'second', release, indexing: { state: 'pending', indexed_at: null, chunk_count: 0 } },
  ] });
  render(<DocumentTable documents={[document, { ...document, id: 'second', original_filename: 'Projekt.pdf' }]} />);
  await screen.findByText('Für KI verfügbar');
  const rows = screen.getAllByRole('row');
  expect(within(rows[1]).getByText('Für KI verfügbar')).toBeTruthy();
  expect(within(rows[1]).getByText(/4 Textabschnitte/)).toBeTruthy();
  expect(within(rows[2]).getByText('Wird indiziert')).toBeTruthy();
  expect(within(rows[2]).queryByText('Für KI verfügbar')).toBeNull();
  expect(apiJson).toHaveBeenCalledTimes(1);
});
