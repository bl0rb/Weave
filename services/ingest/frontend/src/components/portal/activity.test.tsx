// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import { ProcessingActivity } from './activity';

vi.mock('@/lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
vi.mock('@/lib/data-cache', () => ({ useVisiblePolling: vi.fn() }));

const api = vi.mocked(apiJson);
const item = (overrides: Record<string, unknown> = {}) => ({
  id: 'job-1',
  original_filename: 'Handbuch.pdf',
  status: 'FINISHED',
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T11:00:00Z',
  collection_id: 'collection-1',
  collection_name: 'Service',
  import_run_id: null,
  import_status: null,
  release_status: null,
  quality_grade: 'A',
  quality_recommendation: 'allow',
  ...overrides,
});

const response = (items = [item()], counts = { pending: 1, running: 1, finished: 1, failed: 1 }) => ({ items, total: items.length, counts });

beforeEach(() => {
  api.mockReset();
  api.mockResolvedValue(response());
});
afterEach(cleanup);

describe('ProcessingActivity', () => {
  it('loads search and status filters and resets pagination', async () => {
    render(<ProcessingActivity />);
    await screen.findByText('Handbuch.pdf');

    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Confluence' } });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'FAILED' } });

    await waitFor(() => {
      const path = api.mock.calls.at(-1)?.[0] as string;
      const params = new URL(path, 'http://localhost').searchParams;
      expect(params.get('q')).toBe('Confluence');
      expect(params.get('status')).toBe('FAILED');
      expect(params.get('offset')).toBe('0');
      expect(params.get('limit')).toBe('20');
    });
  });

  it('shows status counts, failed jobs and legacy jobs without a collection', async () => {
    api.mockResolvedValueOnce(response([item({ id: 'legacy-1', status: 'FAILED', collection_id: null, collection_name: null })]));
    render(<ProcessingActivity />);

    expect(await screen.findByText('Verarbeitung fehlgeschlagen')).toBeTruthy();
    expect(screen.getByText('Ohne Wissensbereich')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Wartet: 1/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /In Verarbeitung: 1/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Verarbeitet: 1/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Fehlgeschlagen: 1/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Handbuch.pdf öffnen' }).getAttribute('href')).toBe('/jobs/legacy-1');
  });

  it('shows indexing progress from the live status lookup', async () => {
    api.mockImplementation(async path => path.startsWith('/api/v1/portal/indexing-status')
      ? { items: [{ job_id: 'job-1', release: { id: 'release', status: 'sent' }, indexing: { state: 'pending', indexed_at: null, chunk_count: 0 } }] }
      : response([item({ release_status: 'sent' })]));
    render(<ProcessingActivity />);
    expect(await screen.findByText('Wird indiziert')).toBeTruthy();
    expect(screen.getByText(/für die KI-Suche aufbereitet/)).toBeTruthy();
    expect(screen.queryByText('Für KI verfügbar')).toBeNull();
    expect(screen.getByRole('link', { name: 'Handbuch.pdf öffnen' }).getAttribute('href')).toBe('/reviews/job-1');
  });

  it('shows confirmed completion and chunk count in processing', async () => {
    api.mockImplementation(async path => path.startsWith('/api/v1/portal/indexing-status')
      ? { items: [{ job_id: 'job-1', release: { id: 'release', status: 'sent' }, indexing: { state: 'indexed', indexed_at: '2026-09-02T11:31:12Z', chunk_count: 3 } }] }
      : response([item({ release_status: 'sent' })]));
    render(<ProcessingActivity />);
    expect(await screen.findByText('Für KI verfügbar')).toBeTruthy();
    expect(screen.getByText(/3 Textabschnitte/)).toBeTruthy();
  });

  it('blocks release for quality grade C regardless of case', async () => {
    api.mockResolvedValueOnce(response([item({ quality_grade: ' c ', quality_recommendation: 'ALLOW' })]));
    render(<ProcessingActivity />);

    expect(await screen.findByText('Qualitätsprüfung blockiert')).toBeTruthy();
    expect(screen.getByText(/Freigabe ist wegen der Qualitätsprüfung blockiert/)).toBeTruthy();
    expect(screen.queryByText('Bereit zur Prüfung')).toBeNull();
  });

  it.each([
    ['pending', 'Import läuft'],
    ['RUNNING', 'Import läuft'],
    ['failed', 'Import prüfen'],
  ])('does not offer review while the Confluence import is %s', async (importStatus, label) => {
    api.mockResolvedValueOnce(response([item({ import_status: importStatus, import_run_id: 'run-1' })]));
    render(<ProcessingActivity />);

    expect(await screen.findByText(label)).toBeTruthy();
    expect(screen.queryByText('Bereit zur Prüfung')).toBeNull();
    expect(screen.getByRole('link', { name: /Confluence-Import ansehen/ }).getAttribute('href')).toBe('/imports/run-1');
  });

  it('keeps a finished legacy job as processed until a source is assigned to a knowledge area', async () => {
    api.mockResolvedValueOnce(response([item({ collection_id: null, collection_name: null })]));
    render(<ProcessingActivity />);

    expect((await screen.findAllByText('Verarbeitet')).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Für eine Freigabe füge die Quelle einem Wissensbereich hinzu/)).toBeTruthy();
    expect(screen.queryByText('Bereit zur Prüfung')).toBeNull();
  });

  it('shows an empty state without adding another upload CTA', async () => {
    api.mockResolvedValue(response([], { pending: 0, running: 0, finished: 0, failed: 0 }));
    render(<ProcessingActivity />);
    expect(await screen.findByText('Noch keine Verarbeitung')).toBeTruthy();
    expect(screen.getAllByRole('link', { name: /Quelle hinzufügen/ })).toHaveLength(1);
  });
});
