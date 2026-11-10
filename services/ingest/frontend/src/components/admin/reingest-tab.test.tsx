// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { ApiError, apiJson } from '@/lib/api';
import { useVisiblePolling } from '@/lib/data-cache';
import { ReingestTab } from './reingest-tab';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiJson: vi.fn(),
}));

vi.mock('@/lib/data-cache', () => ({ useVisiblePolling: vi.fn() }));

const json = vi.mocked(apiJson);
const polling = vi.mocked(useVisiblePolling);

const preview = {
  documents: { rerelease: 12, reprocess: 5, skipped: { edited: 3, mystery_code: 2 }, imported: 4 },
  connections: { ready: 2, blocked: { owner_inactive: 1 } },
  publication_configured: true,
  knowledge_available: true,
  active_run_id: null,
};

const runningRun = {
  id: 'run-1',
  status: 'running',
  options: { include_documents: true, include_connections: true, rerelease: true, accept_quality_warnings: true, include_edited: false },
  counts: {
    document: { queued: 5, done: 10, failed: 1, skipped: 2, results: { released: 6, unchanged: 4, failed: 1 } },
    connection: { pending: 1, done: 1, results: { reimported: 1 } },
  },
  summary: null,
  error_message: null,
  created_by: 'admin',
  created_at: '2026-11-10T08:00:00Z',
  updated_at: '2026-11-10T08:05:00Z',
  finished_at: null,
};

type Handler = (path: string, init?: RequestInit) => unknown;

function mockApi({ runs = [] as unknown[], previewValue = preview as unknown, onPost }: { runs?: unknown[]; previewValue?: unknown; onPost?: Handler } = {}) {
  json.mockImplementation(async (path, init) => {
    if (init?.method === 'POST' && onPost) return onPost(path, init);
    if (path.startsWith('/api/v1/admin/reingest/preview?')) return previewValue;
    if (path === '/api/v1/admin/reingest/runs?limit=10') return { items: runs };
    throw new Error(`unexpected request: ${path}`);
  });
}

function previewCalls() {
  return json.mock.calls.filter(([path]) => path.startsWith('/api/v1/admin/reingest/preview?')).map(([path]) => path);
}

function rowText(label: string) {
  return screen.getByText(label).closest('div')?.textContent ?? '';
}

beforeEach(() => {
  json.mockReset();
  polling.mockReset();
  mockApi();
});

afterEach(cleanup);

it('renders the preview counts with labelled (and raw unknown) reasons', async () => {
  render(<ReingestTab />);
  expect(await screen.findByText('Werden erneut freigegeben')).toBeTruthy();
  expect(previewCalls()[0]).toBe('/api/v1/admin/reingest/preview?include_edited=false&rerelease=true');
  expect(rowText('Werden erneut freigegeben')).toContain('12');
  expect(rowText('Werden an Ort und Stelle neu verarbeitet')).toContain('5');
  expect(rowText('Werden übersprungen')).toContain('5');
  expect(rowText('Manuell bearbeitet')).toContain('3');
  expect(rowText('mystery_code')).toContain('2');
  expect(rowText('Importierte Dokumente (laufen über ihre Connection)')).toContain('4');
  expect(rowText('Werden neu importiert')).toContain('2');
  expect(rowText('Eigentümer der Quelle inaktiv')).toContain('1');

  // Including edited documents changes the preview, so it is fetched again.
  fireEvent.click(screen.getByRole('switch', { name: /Manuell bearbeitete Dokumente einbeziehen/ }));
  await waitFor(() => expect(previewCalls()).toContain('/api/v1/admin/reingest/preview?include_edited=true&rerelease=true'));
});

it('posts the confirmed counts and options after the explicit confirmation', async () => {
  const posted: unknown[] = [];
  mockApi({
    onPost: (path, init) => {
      posted.push({ path, body: JSON.parse(String(init?.body)) });
      return runningRun;
    },
  });
  render(<ReingestTab />);
  await screen.findByText('Werden erneut freigegeben');
  await waitFor(() => expect((screen.getByRole('button', { name: 'Neu einlesen starten' }) as HTMLButtonElement).disabled).toBe(false));

  fireEvent.click(screen.getByRole('button', { name: 'Neu einlesen starten' }));
  const confirm = screen.getByRole('button', { name: 'Neu einlesen' }) as HTMLButtonElement;
  expect(screen.getByText('12 freigegebene Dokumente werden neu verarbeitet und erneut freigegeben.')).toBeTruthy();
  expect(confirm.disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich habe geprüft, dass/ }));
  expect(confirm.disabled).toBe(false);
  fireEvent.click(confirm);

  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toEqual({
    path: '/api/v1/admin/reingest/runs',
    body: {
      include_documents: true,
      include_connections: true,
      rerelease: true,
      accept_quality_warnings: true,
      include_edited: false,
      expected_documents: 17,
      expected_connections: 2,
      expected_rereleases: 12,
    },
  });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Neu einlesen' })).toBeNull());
});

it('shows a refused start in the dialog and reloads the preview', async () => {
  mockApi({
    onPost: () => {
      throw new ApiError(409, 'Der Bestand hat sich seit der Vorschau geändert.');
    },
  });
  render(<ReingestTab />);
  await screen.findByText('Werden erneut freigegeben');
  await waitFor(() => expect((screen.getByRole('button', { name: 'Neu einlesen starten' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Neu einlesen starten' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich habe geprüft, dass/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Neu einlesen' }));

  expect(await screen.findByText('Der Bestand hat sich seit der Vorschau geändert.')).toBeTruthy();
  await waitFor(() => expect(previewCalls()).toHaveLength(2));
  // The admin has to re-check the reloaded numbers.
  expect((screen.getByRole('checkbox', { name: /Ich habe geprüft, dass/ }) as HTMLInputElement).checked).toBe(false);
});

it('blocks the start while Knowledge is unreachable for a re-release', async () => {
  mockApi({ previewValue: { ...preview, knowledge_available: false } });
  render(<ReingestTab />);
  expect(await screen.findByText(/Knowledge ist nicht erreichbar/)).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Neu einlesen starten' }) as HTMLButtonElement).disabled).toBe(true);
});

it('shows the progress of an active run and cancels it after confirmation', async () => {
  const posted: string[] = [];
  mockApi({
    runs: [runningRun],
    previewValue: { ...preview, active_run_id: 'run-1' },
    onPost: (path) => {
      posted.push(path);
      return { ...runningRun, status: 'cancelling' };
    },
  });
  render(<ReingestTab />);
  expect(await screen.findByText('Aktueller Lauf')).toBeTruthy();
  expect(screen.getByText('13 von 18 erledigt')).toBeTruthy();
  expect(screen.getByText('1 von 2 erledigt')).toBeTruthy();
  expect(screen.getByText(/Erneut freigegeben: 6 · Unverändert \(neue Version verworfen\): 4/)).toBeTruthy();
  expect(screen.getByText('Neu importiert: 1')).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Ein Lauf ist aktiv' }) as HTMLButtonElement).disabled).toBe(true);
  expect(polling).toHaveBeenLastCalledWith(expect.any(Function), 3000);

  fireEvent.click(screen.getByRole('button', { name: 'Lauf abbrechen' }));
  fireEvent.click(screen.getByRole('button', { name: 'Jetzt abbrechen' }));
  await waitFor(() => expect(posted).toEqual(['/api/v1/admin/reingest/runs/run-1/cancel']));
});
