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
  const release = { id: 'release', status: 'sent' as const, created_at: '2026-09-02T11:00:00Z', error_message: null, released_by: 'anna' };
  const source = { kind: 'upload' as const, label: 'Hochgeladen', path: 'Kunden', url: null };
  const document: PortalDocument = { id: 'first', original_filename: 'Handbuch.pdf', status: 'FINISHED', collection_id: 'area', collection_name: 'Service', created_at: release.created_at, quality_grade: 'A', quality_recommendation: 'allow', can_release: false, release, source, review_decision: null };
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

it('keeps documents that cannot be selected out of the row and the select-all choice', () => {
  const source = { kind: 'upload' as const, label: 'Hochgeladen', path: 'Kunden', url: null };
  const finished: PortalDocument = { id: 'done', original_filename: 'Fertig.pdf', status: 'FINISHED', collection_id: 'area', collection_name: 'Service', created_at: '2026-09-02T11:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, source, review_decision: null };
  const pending: PortalDocument = { ...finished, id: 'waiting', original_filename: 'Wartet.pdf', status: 'PENDING', can_release: false };
  const onToggleAll = vi.fn();
  render(<DocumentTable documents={[finished, pending]} selectedIds={new Set(['done'])} onToggle={vi.fn()} onToggleAll={onToggleAll} canSelect={document => document.status !== 'PENDING'} />);
  const [all, first, second] = screen.getAllByRole('checkbox') as HTMLInputElement[];
  expect(first.disabled).toBe(false);
  expect(second.disabled).toBe(true);
  // Every selectable row is selected, so select-all reads as checked.
  expect(all.checked).toBe(true);
});
