// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import AdminStatusPage from './page';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiJson: vi.fn(),
}));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => ({ user: { username: 'Ada', role: 'admin' } }) }));

const api = vi.mocked(apiJson);
const status = {
  status: 'down', checked_at: '2026-09-28T10:00:00Z',
  areas: [{ key: 'portal', status: 'ok' }, { key: 'processing', status: 'ok' }, { key: 'chat', status: 'down' }],
  components: [
    { key: 'database', area: 'portal', status: 'ok', latency_ms: 2, detail: null, target: null },
    { key: 'ingest-worker', area: 'processing', status: 'ok', latency_ms: 40, detail: '1 worker', target: null },
    { key: 'retrieval', area: 'chat', status: 'down', latency_ms: 1001, detail: 'timeout', target: 'http://weave-retrieval:8000' },
    { key: 'future-service', area: 'chat', status: 'ok', latency_ms: 5, detail: null, target: null },
  ],
};

beforeEach(() => {
  api.mockReset();
  api.mockResolvedValue(status);
});
afterEach(cleanup);

it('lists every component per area with status, latency and address', async () => {
  render(<AdminStatusPage />);
  expect(await screen.findByText('Nicht alle Dienste laufen einwandfrei – 1 betroffen.')).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Chat & Suche' })).toBeTruthy();
  expect(screen.getByRole('rowheader', { name: /Retrieval \(Suche\)/ }).textContent).toContain('http://weave-retrieval:8000');
  expect(screen.getByText('Ausgefallen')).toBeTruthy();
  expect(screen.getByText('1001 ms')).toBeTruthy();
  expect(screen.getByText('timeout')).toBeTruthy();
  // A component this UI does not know yet still shows up under its key.
  expect(screen.getByRole('rowheader', { name: 'future-service' })).toBeTruthy();
  expect(api).toHaveBeenCalledWith('/api/v1/auth/admin/system-status', expect.anything());
});

it('"Jetzt prüfen" bypasses the probe cache', async () => {
  render(<AdminStatusPage />);
  await screen.findByText('Nicht alle Dienste laufen einwandfrei – 1 betroffen.');
  fireEvent.click(screen.getByRole('button', { name: /Jetzt prüfen/ }));
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/v1/auth/admin/system-status?refresh=true', expect.anything()));
});
