// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import { ImportSyncButton, MissingConfluencePages } from './import-sync';

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/api', () => ({ apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const missing = { page_id: '42', title: 'Alte Anleitung', job_id: 'job', url: 'https://confluence.example/42' };
beforeEach(() => { api.mockReset(); push.mockReset(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('starts an incremental sync for the selected historical run', async () => {
  api.mockResolvedValue({ id: 'new-sync' });
  render(<ImportSyncButton runId="old-run" />);
  fireEvent.click(screen.getByRole('button', { name: 'Jetzt synchronisieren' }));
  await waitFor(() => expect(push).toHaveBeenCalledWith('/imports/new-sync'));
  expect(api).toHaveBeenCalledWith('/api/v1/import/runs/old-run/sync', { method: 'POST' });
});

it('never removes a page without confirmation', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  const changed = vi.fn();
  render(<MissingConfluencePages runId="sync" pages={[missing]} canRemove onChanged={changed} />);
  fireEvent.click(screen.getByRole('button', { name: 'Aus Wissen entfernen' }));
  expect(api).not.toHaveBeenCalled();
  vi.mocked(window.confirm).mockReturnValue(true);
  api.mockResolvedValue({ status: 'pending' });
  fireEvent.click(screen.getByRole('button', { name: 'Aus Wissen entfernen' }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(api.mock.calls[0][0]).toBe('/api/v1/import/runs/sync/missing/42/withdraw');
  expect(JSON.parse(api.mock.calls[0][1]?.body as string)).toEqual({ confirm: true });
});

it('does not claim pending removal is completed or allow readers to delete', () => {
  render(<MissingConfluencePages runId="sync" pages={[{ ...missing, withdrawal_status: 'pending' }]} canRemove={false} onChanged={vi.fn()} />);
  expect(screen.getByRole('status').textContent).toBe('Löschung ausstehend');
  expect(screen.queryByRole('button', { name: 'Aus Wissen entfernen' })).toBeNull();
  expect(screen.queryByText('Aus Wissen entfernt')).toBeNull();
});