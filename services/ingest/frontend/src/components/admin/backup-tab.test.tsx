// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiFetch, apiJson } from '@/lib/api';
import { BackupTab } from './backup-tab';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
}));

vi.mock('@/lib/data-cache', () => ({ useVisiblePolling: vi.fn() }));

const json = vi.mocked(apiJson);
const fetcher = vi.mocked(apiFetch);

const freshState = { fresh: true, reasons: [] as string[] };
const dirtyState = { fresh: false, reasons: ['Tabelle "jobs" enthält bereits 3 Zeile(n).'] };

const finishedExportRun = {
  id: 'run-1',
  kind: 'export',
  status: 'finished',
  file_name: '20260101T000000Z-run-1.weave-backup.tar.gz',
  size_bytes: 1024,
  progress: {},
  report: { tables: { users: 1, jobs: 3 } },
  error_message: null,
  created_by: 'admin-1',
  created_at: '2026-09-04T08:00:00Z',
  updated_at: '2026-09-04T08:00:05Z',
  started_at: '2026-09-04T08:00:00Z',
  finished_at: '2026-09-04T08:00:05Z',
};

function mockRuns(runs: unknown[] = []) {
  json.mockImplementation(async (path) => {
    if (path === '/api/v1/admin/backup/runs') return { runs };
    if (path === '/api/v1/admin/backup/target-state') return freshState;
    throw new Error(`unexpected request: ${path}`);
  });
}

beforeEach(() => {
  json.mockReset();
  fetcher.mockReset();
  mockRuns([]);
  fetcher.mockResolvedValue({ ok: true } as Response);
});

afterEach(cleanup);

it('rejects a passphrase shorter than 12 characters', async () => {
  render(<BackupTab />);
  const passphraseInput = await screen.findByLabelText(/^Passphrase für die Sicherung/);
  fireEvent.change(passphraseInput, { target: { value: 'short' } });
  fireEvent.change(screen.getByLabelText('Passphrase wiederholen'), { target: { value: 'short' } });
  expect((screen.getByRole('button', { name: 'Sicherung erstellen' }) as HTMLButtonElement).disabled).toBe(true);
});

it('rejects mismatched passphrases', async () => {
  render(<BackupTab />);
  const passphraseInput = await screen.findByLabelText(/^Passphrase für die Sicherung/);
  fireEvent.change(passphraseInput, { target: { value: 'a-long-enough-passphrase' } });
  fireEvent.change(screen.getByLabelText('Passphrase wiederholen'), { target: { value: 'a-different-passphrase-value' } });
  expect((screen.getByRole('button', { name: 'Sicherung erstellen' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText('Stimmt nicht überein.')).toBeTruthy();
});

it('starts an export with a matching, long-enough passphrase', async () => {
  json.mockImplementation(async (path, init) => {
    if (path === '/api/v1/admin/backup/exports' && init?.method === 'POST') return { ...finishedExportRun, status: 'queued' };
    if (path === '/api/v1/admin/backup/runs') return { runs: [] };
    if (path === '/api/v1/admin/backup/target-state') return freshState;
    throw new Error(`unexpected request: ${path}`);
  });
  render(<BackupTab />);
  const passphraseInput = await screen.findByLabelText(/^Passphrase für die Sicherung/);
  fireEvent.change(passphraseInput, { target: { value: 'a-long-enough-passphrase' } });
  fireEvent.change(screen.getByLabelText('Passphrase wiederholen'), { target: { value: 'a-long-enough-passphrase' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sicherung erstellen' }));

  await waitFor(() => expect(json.mock.calls.some(([path, init]) => path === '/api/v1/admin/backup/exports' && init?.method === 'POST')).toBe(true));
  const call = json.mock.calls.find(([path, init]) => path === '/api/v1/admin/backup/exports' && (init as { method?: string })?.method === 'POST');
  const body = JSON.parse((call?.[1] as { body?: string })?.body ?? '{}');
  expect(body.passphrase).toBe('a-long-enough-passphrase');
});

it('lists a finished export and allows deleting it after confirmation', async () => {
  mockRuns([finishedExportRun]);
  render(<BackupTab />);
  expect(await screen.findByText(finishedExportRun.file_name)).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: `${finishedExportRun.file_name} löschen` }));
  fireEvent.click(screen.getByRole('button', { name: 'Sicherung löschen' }));

  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/admin/backup/exports/${finishedExportRun.id}`,
    { method: 'DELETE' },
  ));
});

it('shows the force-overwrite checkbox only when the target is not fresh, and requires it', async () => {
  mockRuns([]);
  json.mockImplementation(async (path) => {
    if (path === '/api/v1/admin/backup/runs') return { runs: [] };
    if (path === '/api/v1/admin/backup/target-state') return dirtyState;
    throw new Error(`unexpected request: ${path}`);
  });
  render(<BackupTab />);
  expect(await screen.findByText(/nicht frisch/)).toBeTruthy();
  expect(screen.getByText(dirtyState.reasons[0])).toBeTruthy();

  const checkbox = screen.getByRole('checkbox', { name: 'Vorhandene Daten überschreiben' });
  const file = new File(['data'], 'archive.tar.gz', { type: 'application/gzip' });
  fireEvent.change(screen.getByLabelText(/^Sicherungsarchiv/), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText(/^Passphrase des Archivs/), { target: { value: 'the-export-passphrase' } });

  expect((screen.getByRole('button', { name: 'Wiederherstellung starten' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(checkbox);
  expect((screen.getByRole('button', { name: 'Wiederherstellung starten' }) as HTMLButtonElement).disabled).toBe(false);
});

it('shows the import report after a finished restore, including the reindex hint', async () => {
  const finishedImportRun = {
    id: 'run-2',
    kind: 'import',
    status: 'finished',
    file_name: 'archive.tar.gz',
    size_bytes: 2048,
    progress: {},
    report: {
      tables: { users: 2 },
      files_restored: 5,
      warnings: [],
      skipped: [],
      requeued_releases: 3,
      requires_relogin: true,
      indexing_note: 'Der Wissensindex wird über die bestehende Publikations-Outbox neu aufgebaut, sobald ein Ingest-Worker läuft.',
    },
    error_message: null,
    created_by: 'admin-1',
    created_at: '2026-09-04T08:00:00Z',
    updated_at: '2026-09-04T08:00:05Z',
    started_at: '2026-09-04T08:00:00Z',
    finished_at: '2026-09-04T08:00:05Z',
  };
  mockRuns([finishedImportRun]);
  render(<BackupTab />);
  expect(await screen.findByText('Der Wissensindex wird jetzt automatisch neu aufgebaut.')).toBeTruthy();
  expect(screen.getByText(/Bitte neu anmelden/)).toBeTruthy();
  expect(screen.getByText(/3 Veröffentlichung\(en\) zur Neuindizierung eingereiht/)).toBeTruthy();
});
