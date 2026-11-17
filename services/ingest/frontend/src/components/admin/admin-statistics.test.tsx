// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n/provider';
import { apiJson } from '@/lib/api';
import { AdminStatisticsSection, formatBytes, type AdminStatistics } from './admin-statistics';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiJson: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

const json = vi.mocked(apiJson);

const knowledge = {
  documents: { total: 40, by_status: { pending: 1, indexed: 37, blocked: 0, superseded: 1, failed: 1 } },
  chunks: { total: 12345, embedded: 12000, avg_chars: 812.6 },
  embedding_models: [{ model: 'bge-m3', chunks: 12000 }],
  collections: { total: 6, with_documents: 4 },
  memory: { documents: 30, collections: 3 },
  vector_backend: 'pgvector',
  storage: { documents_bytes: 1024, chunks_bytes: 2048, memory_bytes: 0, total_bytes: 5 * 1024 * 1024 },
  generated_at: '2026-11-17T10:00:00Z',
};

const stats: AdminStatistics = {
  documents: { total: 42, versions: 50, by_status: { PENDING: 2, RUNNING: 1, FINISHED: 45, FAILED: 2 }, released: 37, uploaded_last_30_days: 9 },
  storage: { objects: 1500, bytes: 1536, by_backend: { db: { objects: 1500, bytes: 1536 } } },
  collections: { total: 7, public: 2, restricted: 5 },
  users: { total: 20, active: 18, admins: 2 },
  teams: 4,
  review_tasks: { open: 3, in_progress: 1 },
  subscriptions: 11,
  knowledge,
  knowledge_error: null,
  generated_at: '2026-11-17T10:00:00Z',
};

function respondWith(body: unknown) {
  json.mockImplementation(async (path: string) => {
    if (path === '/api/v1/admin/statistics') {
      if (body instanceof Error) throw body;
      return body;
    }
    throw new Error(`unexpected request: ${path}`);
  });
}

function value(card: string, label: string): string | null {
  const heading = screen.getByRole('heading', { name: card });
  const box = heading.parentElement as HTMLElement;
  const term = within(box).getByText(label, { selector: 'dt' });
  return term.nextElementSibling?.textContent ?? null;
}

beforeEach(() => {
  json.mockReset();
});
afterEach(cleanup);

it('formats bytes with binary units and the locale decimal separator', () => {
  const de = (v: number, o?: Intl.NumberFormatOptions) => new Intl.NumberFormat('de-DE', o).format(v);
  const en = (v: number, o?: Intl.NumberFormatOptions) => new Intl.NumberFormat('en-US', o).format(v);
  expect(formatBytes(512, de)).toBe('512 B');
  expect(formatBytes(1536, de)).toBe('1,5 KB');
  expect(formatBytes(1536, en)).toBe('1.5 KB');
  expect(formatBytes(3 * 1024 ** 3, en)).toBe('3 GB');
  // Just below a unit boundary rounds into the next unit, not "1.024 KB".
  expect(formatBytes(1048575, de)).toBe('1 MB');
  expect(formatBytes(1073741000, de)).toBe('1 GB');
  expect(formatBytes(1023, de)).toBe('1.023 B');
});

it('shows ingest and knowledge statistics in labelled cards', async () => {
  respondWith(stats);
  render(<AdminStatisticsSection />);
  expect(screen.getByRole('region', { name: 'Statistik' })).toBeTruthy();
  expect(await screen.findByText('42')).toBeTruthy();

  expect(value('Dokumente', 'Freigegeben')).toBe('37');
  expect(value('Dokumente', 'Versionen in Verarbeitung')).toBe('3');
  expect(value('Dokumente', 'Fehlgeschlagene Versionen')).toBe('2');
  expect(value('Dokumente', 'Versionen')).toBe('50');
  expect(value('Dokumente', 'Neue Versionen (30 Tage)')).toBe('9');
  expect(value('Speicher', 'Objekte')).toBe('1.500');
  expect(value('Speicher', 'Größe')).toBe('1,5 KB');
  expect(value('Speicher', 'Datenbank')).toBe('1.500 Objekte · 1,5 KB');
  expect(value('Suchindex', 'Chunks gesamt')).toBe('12.345');
  expect(value('Suchindex', 'Indexierte Dokumente')).toBe('37');
  expect(value('Suchindex', 'Ø Chunk-Größe (Zeichen)')).toBe('813');
  expect(value('Suchindex', 'Vektor-Backend')).toBe('pgvector');
  expect(value('Suchindex', 'Datenbankgröße')).toBe('5 MB');
  expect(screen.getByText('bge-m3').nextElementSibling?.textContent).toBe('12.000 Chunks');
  expect(value('Wissensbereiche', 'Eingeschränkt')).toBe('5');
  expect(value('Wissensbereiche', 'Mit indexierten Dokumenten')).toBe('4');
  expect(value('Wissensgedächtnis', 'Bereichszusammenfassungen')).toBe('3');
  expect(value('Nutzer & Teams', 'Teams')).toBe('4');
  expect(value('Prüfaufgaben', 'Themen-Abos')).toBe('11');
});

it('shows dashes and the backend note when knowledge is unavailable', async () => {
  respondWith({ ...stats, knowledge: null, knowledge_error: 'Der Wissensdienst ist nicht konfiguriert.' });
  render(<AdminStatisticsSection />);
  expect(await screen.findByText('42')).toBeTruthy();

  expect(value('Suchindex', 'Chunks gesamt')).toBe('–');
  expect(value('Suchindex', 'Vektor-Backend')).toBe('–');
  expect(screen.queryByText('Datenbankgröße')).toBeNull();
  expect(value('Wissensbereiche', 'Gesamt')).toBe('7');
  expect(value('Wissensbereiche', 'Mit indexierten Dokumenten')).toBe('–');
  expect(value('Wissensgedächtnis', 'Dokumentzusammenfassungen')).toBe('–');
  expect(screen.getAllByText('Der Wissensdienst ist nicht konfiguriert.').length).toBeGreaterThan(0);
});

it('localizes the knowledge note instead of showing the German backend reason', async () => {
  respondWith({ ...stats, knowledge: null, knowledge_error: 'Der Wissensdienst ist nicht konfiguriert.' });
  render(<I18nProvider initialLocale="en"><AdminStatisticsSection /></I18nProvider>);
  expect(await screen.findByText('42')).toBeTruthy();

  expect(value('Search index', 'Total chunks')).toBe('–');
  expect(screen.getAllByText('The knowledge service returned no statistics.').length).toBeGreaterThan(0);
  expect(screen.queryByText('Der Wissensdienst ist nicht konfiguriert.')).toBeNull();
});

it('shows a loading placeholder and then an error note when the request fails', async () => {
  respondWith(new Error('boom'));
  render(<AdminStatisticsSection />);
  expect(value('Dokumente', 'Gesamt')).toBe('…');
  expect(await screen.findByText('Die Statistik konnte nicht geladen werden.')).toBeTruthy();
  expect(value('Dokumente', 'Gesamt')).toBe('–');
});
