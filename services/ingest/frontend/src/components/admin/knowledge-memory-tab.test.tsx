// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import { KnowledgeMemoryTab } from './knowledge-memory-tab';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiJson: vi.fn(),
}));

const json = vi.mocked(apiJson);

const config = {
  mode: 'off', llm_endpoint: 'default', debounce_seconds: 120, max_documents_per_run: 25,
  max_input_chars: 12000, managed: false, updated_at: null,
};
const status = {
  mode: 'llm', llm_endpoint: 'default', documents: 4, summarized: 3, summarized_llm: 2, llm_calls: 5, llm_failures: 1,
  collections_total: 1, collections_built: 1, collections_pending: 0, collections_building: 0, last_run_at: null,
  collections: [{
    collection: 'personal', name: 'Personal', documents: 4, summarized: 3, summarized_llm: 2, overview_generator: 'llm',
    built_at: '2026-10-18T10:00:00Z', pending: false, building: false, last_run_at: '2026-10-18T10:00:00Z',
    last_duration_ms: 1500, last_error: 'Kein zentrales LLM verfügbar, extraktiv aufgebaut.', llm_calls: 5, llm_failures: 1,
  }],
};

beforeEach(() => {
  json.mockReset();
  json.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/api/v1/auth/admin/knowledge-memory' && init?.method === 'PUT') return { ...JSON.parse(String(init.body)), managed: true, updated_at: null };
    if (path === '/api/v1/auth/admin/knowledge-memory') return config;
    if (path === '/api/v1/auth/admin/chat-provider/endpoints') return { items: [{ id: 'klein', name: 'Kleines Modell', model: 'm' }] };
    if (path === '/api/v1/admin/knowledge-memory/status') return status;
    if (path === '/api/v1/admin/knowledge-memory/rebuild') return { queued: 1 };
    throw new Error(`unexpected request: ${path}`);
  });
});

afterEach(cleanup);

it('saves mode, endpoint and limits', async () => {
  render(<KnowledgeMemoryTab />);
  expect(await screen.findByText(/Noch nicht hier gespeichert/)).toBeTruthy();
  fireEvent.click(screen.getByLabelText(/^Mit LLM/));
  fireEvent.change(screen.getByLabelText(/LLM-Endpunkt/), { target: { value: 'klein' } });
  fireEvent.change(screen.getByLabelText(/Dokumente pro Lauf/), { target: { value: '10' } });
  fireEvent.click(screen.getByRole('button', { name: /Speichern/ }));
  await waitFor(() => expect(json).toHaveBeenCalledWith('/api/v1/auth/admin/knowledge-memory', expect.objectContaining({ method: 'PUT' })));
  const put = json.mock.calls.find(([, init]) => init?.method === 'PUT');
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({
    mode: 'llm', llm_endpoint: 'klein', debounce_seconds: 120, max_documents_per_run: 10, max_input_chars: 12000,
  });
  expect(await screen.findByText(/Gespeichert/)).toBeTruthy();
});

it('shows metrics per knowledge space and starts a confirmed rebuild', async () => {
  render(<KnowledgeMemoryTab />);
  expect(await screen.findByText('75 %')).toBeTruthy();
  expect(screen.getByText('Personal')).toBeTruthy();
  expect(screen.getByText('Kein zentrales LLM verfügbar, extraktiv aufgebaut.')).toBeTruthy();
  expect(screen.getByText('5 (1 Fehler)')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /Alles neu aufbauen/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Neu aufbauen' }));
  expect(await screen.findByText(/Neuaufbau für 1 Wissensbereich/)).toBeTruthy();
  expect(json).toHaveBeenCalledWith('/api/v1/admin/knowledge-memory/rebuild', expect.objectContaining({ method: 'POST' }));
});
