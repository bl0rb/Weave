// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import { Aufgaben } from './aufgaben';

const auth = vi.hoisted(() => ({ user: { username: 'lea', role: 'user' as const } }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => auth }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));

const api = vi.mocked(apiJson);

const reviewDoc = { id: 'r1', original_filename: 'Handbuch.pdf', status: 'FINISHED', collection_id: 'c1', collection_name: 'Service', created_at: '2026-09-01T09:00:00Z', quality_grade: 'B', quality_recommendation: 'warn', can_release: true, release: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null }, review_decision: null };
const failedJob = { id: 'j1', original_filename: 'Vertrag.pdf', status: 'FAILED', error_message: 'Seite nicht erreichbar (404)', created_at: '2026-09-01T09:00:00Z', updated_at: '2026-09-01T09:00:00Z' };
const releasedByMe = { id: 'd1', original_filename: 'FAQ.pdf', status: 'FINISHED', collection_id: 'c1', collection_name: 'Service', created_at: '2026-09-01T08:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, review_decision: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null }, release: { id: 'rel1', status: 'sent', created_at: '2026-09-02T10:00:00Z', error_message: null, released_by: 'lea' } };
const releasedByOther = { id: 'd2', original_filename: 'Roadmap.xlsx', status: 'FINISHED', collection_id: 'c1', collection_name: 'Produkt', created_at: '2026-09-01T08:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, review_decision: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null }, release: { id: 'rel2', status: 'sent', created_at: '2026-09-01T10:00:00Z', error_message: null, released_by: 'tom' } };

function mockData({ review = [reviewDoc], failed = [failedJob], all = [releasedByMe, releasedByOther] } = {}) {
  api.mockImplementation(async (path) => {
    if (typeof path !== 'string') throw new Error('unexpected non-string path');
    if (path.startsWith('/api/v1/portal/documents')) {
      const reviewState = new URL(path, 'http://localhost').searchParams.get('review_state');
      return reviewState === 'review' ? { items: review, total: review.length } : { items: all, total: all.length };
    }
    if (path.startsWith('/api/v1/search')) return { items: failed, total: failed.length };
    throw new Error(`unexpected path ${path}`);
  });
}

beforeEach(() => { api.mockReset(); mockData(); });
afterEach(cleanup);

it('lists documents waiting for review with a Prüfen action', async () => {
  render(<Aufgaben />);
  expect(await screen.findByText('Handbuch.pdf')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Prüfen' }).getAttribute('href')).toBe('/reviews/r1');
});

it('lists failed jobs under Braucht Hilfe with a link to the job', async () => {
  render(<Aufgaben />);
  expect(await screen.findByText('Vertrag.pdf')).toBeTruthy();
  expect(screen.getByText('Seite nicht erreichbar (404)')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Auftrag ansehen' }).getAttribute('href')).toBe('/jobs/j1');
});

it('shows only documents released by the signed-in user under "Von dir freigegeben"', async () => {
  render(<Aufgaben />);
  expect(await screen.findByText('FAQ.pdf')).toBeTruthy();
  expect(screen.queryByText('Roadmap.xlsx')).toBeNull();
});

it('shows empty states when nothing needs attention', async () => {
  mockData({ review: [], failed: [], all: [] });
  render(<Aufgaben />);
  expect(await screen.findByText(/Nichts zu prüfen/)).toBeTruthy();
  expect(screen.getByText(/Keine Fehler/)).toBeTruthy();
  expect(screen.getByText(/noch nichts freigegeben/)).toBeTruthy();
});
