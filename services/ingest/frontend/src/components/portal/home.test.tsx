// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import { PortalHome } from './home';

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/' }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => ({ user: { username: 'ada', role: 'user' } }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));

const json = vi.mocked(apiJson);
const document = { id: 'd1', original_filename: 'Reisekosten.md', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: null, quality_recommendation: null, can_release: true, release: null, review_decision: null, source: { kind: 'confluence', label: 'Reisekosten', path: null, url: 'https://wiki.example/page' } };
const stats = {
  spaces: [{ collection_id: 'area-1', name: 'Servicewissen', slug: 'servicewissen', total: 12, in_chat: 8, review: 3, failed: 1 }],
  review_waiting: 3, review_wait_median_hours: 30, review_oldest_since: '2026-10-01T08:00:00Z',
  releases_per_week: Array.from({ length: 8 }, (_, index) => ({ week_start: `2026-09-${String(index + 1).padStart(2, '0')}T00:00:00Z`, count: index })),
};

beforeEach(() => {
  json.mockReset();
  router.push.mockReset();
  json.mockImplementation(async path => {
    if (path === '/api/v1/portal/stats') return stats;
    if (path === '/api/v1/import/syncs') return { items: [] };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/indexing-status')) return { items: [] };
    return { items: [document], total: 1 };
  });
});
afterEach(cleanup);

it('searches across all spaces: shows top hits inline and opens the Dokumente view on submit', async () => {
  render(<PortalHome />);
  fireEvent.change(screen.getByRole('searchbox', { name: 'Dokumente suchen' }), { target: { value: 'Reise' } });
  const hits = await screen.findByRole('list', { name: 'Treffer' });
  expect(hits.textContent).toContain('Reisekosten.md');
  await waitFor(() => expect(json.mock.calls.some(([path]) => String(path).includes('q=Reise') && String(path).includes('limit=5'))).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: 'Suchen' }));
  expect(router.push).toHaveBeenCalledWith('/documents?q=Reise');
});

it('shows per-space and throughput metrics that link into the filtered Dokumente views', async () => {
  render(<PortalHome />);
  const space = await screen.findByRole('rowheader', { name: 'Servicewissen' });
  const row = space.closest('tr')!;
  const hrefs = [...row.querySelectorAll('a')].map(link => link.getAttribute('href'));
  expect(hrefs).toEqual([
    '/documents?bereich=servicewissen',
    '/documents?bereich=servicewissen&ansicht=all',
    '/documents?bereich=servicewissen&stand=ready',
    '/documents?bereich=servicewissen&ansicht=review',
    '/documents?bereich=servicewissen&ansicht=failed',
  ]);
  expect(screen.getByText('30 Std.')).toBeTruthy();
  expect(screen.getByRole('list', { name: 'Freigaben pro Woche' }).children).toHaveLength(8);
  expect(screen.getByRole('link', { name: /Alle zu prüfenden Dokumente/ }).getAttribute('href')).toBe('/documents?ansicht=review');
});
