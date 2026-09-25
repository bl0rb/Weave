// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import { IndexMaintenanceSection, RetrievalProviderTab } from './retrieval-provider-tab';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiJson: vi.fn(),
}));

vi.mock('@/lib/data-cache', () => ({ useVisiblePolling: vi.fn() }));

const json = vi.mocked(apiJson);

const config = {
  embedding_provider: 'fake', embedding_base_url: '', embedding_model: 'fake-embed', embedding_dimension: 1536,
  embedding_batch_size: 64, embedding_has_api_key: false, embedding_key_source: 'none', rerank_provider: 'none',
  rerank_base_url: '', rerank_model: '', rerank_max_documents: 50, rerank_batch_size: 16, rerank_threads: 4,
  rerank_has_api_key: false, rerank_key_source: 'none', semantic_weight: 0.5, lexical_weight: 0.5, updated_at: null,
};

beforeEach(() => {
  json.mockReset();
  json.mockImplementation(async (path: string) => {
    if (path === '/api/v1/auth/admin/retrieval-provider') return config;
    throw new Error(`unexpected request: ${path}`);
  });
});

afterEach(cleanup);

it('renders the search/model config without the index maintenance section (moved to /admin/betrieb)', async () => {
  render(<RetrievalProviderTab />);
  expect(await screen.findByRole('heading', { name: 'Suche und Modelle' })).toBeTruthy();
  expect(screen.queryByRole('heading', { name: 'Index-Wartung' })).toBeNull();
});

it('renders the Index-Wartung section with both maintenance buttons', async () => {
  render(<IndexMaintenanceSection />);
  expect(await screen.findByRole('heading', { name: 'Index-Wartung' })).toBeTruthy();
  expect(screen.getByRole('button', { name: /Vektoren neu berechnen/ })).toBeTruthy();
  expect(screen.getByRole('button', { name: /Index aus Freigaben neu aufbauen/ })).toBeTruthy();
});

it('starts a reindex only after the confirm dialog is accepted', async () => {
  render(<IndexMaintenanceSection />);
  const trigger = await screen.findByRole('button', { name: 'Vektoren neu berechnen' });
  fireEvent.click(trigger);

  const dialog = await screen.findByText(/indizierten Dokumente mit dem aktuell konfigurierten Embedding-Modell/);
  expect(dialog).toBeTruthy();
  expect(json).not.toHaveBeenCalledWith('/api/v1/admin/retrieval-provider/reindex', expect.anything());

  json.mockImplementation(async (path: string) => {
    if (path === '/api/v1/auth/admin/retrieval-provider') return config;
    if (path === '/api/v1/admin/retrieval-provider/reindex') return { started: true, task_id: 'task-42' };
    throw new Error(`unexpected request: ${path}`);
  });

  fireEvent.click(screen.getByRole('button', { name: 'Neu berechnen' }));

  await waitFor(() => expect(json).toHaveBeenCalledWith('/api/v1/admin/retrieval-provider/reindex', { method: 'POST' }));
  expect(await screen.findByText(/Neuberechnung gestartet \(Task task-42\)\./)).toBeTruthy();
  expect((screen.getByRole('button', { name: /Läuft…/ }) as HTMLButtonElement).disabled).toBe(true);
});

it('starts a rebuild only after the confirm dialog is accepted and shows the requeued count', async () => {
  render(<IndexMaintenanceSection />);
  const trigger = await screen.findByRole('button', { name: 'Index aus Freigaben neu aufbauen' });
  fireEvent.click(trigger);

  expect(await screen.findByText(/nicht zurückgezogenen Freigaben erneut zur Auslieferung/)).toBeTruthy();

  json.mockImplementation(async (path: string) => {
    if (path === '/api/v1/auth/admin/retrieval-provider') return config;
    if (path === '/api/v1/admin/knowledge/rebuild') return { requeued: 7, worker_required: true };
    throw new Error(`unexpected request: ${path}`);
  });

  fireEvent.click(screen.getByRole('button', { name: 'Neu aufbauen' }));

  await waitFor(() => expect(json).toHaveBeenCalledWith('/api/v1/admin/knowledge/rebuild', { method: 'POST' }));
  expect(await screen.findByText(/7 Veröffentlichungen zur Neuauslieferung eingereiht\./)).toBeTruthy();
});
