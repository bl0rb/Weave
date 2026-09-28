// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import { SystemStatusIndicator } from './system-status-indicator';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiJson: vi.fn(),
}));
vi.mock('next/navigation', () => ({ usePathname: () => '/' }));

const api = vi.mocked(apiJson);
const areas = (chat: string, processing = 'ok') => ({
  status: chat === 'ok' && processing === 'ok' ? 'ok' : 'degraded', checked_at: '2026-09-28T10:00:00Z',
  areas: [{ key: 'portal', status: 'ok' }, { key: 'processing', status: processing }, { key: 'chat', status: chat }],
});

// Braces: a function returned from beforeEach runs as teardown -- the mock itself.
beforeEach(() => { api.mockReset(); });
afterEach(cleanup);

it('says all systems are operational', async () => {
  api.mockResolvedValue(areas('ok'));
  render(<SystemStatusIndicator isAdmin={false} />);
  expect(await screen.findByText('Alle Systeme betriebsbereit')).toBeTruthy();
  expect(screen.queryByRole('link')).toBeNull();
});

it('names the limited areas and links admins to the status page', async () => {
  api.mockResolvedValue(areas('down', 'degraded'));
  render(<SystemStatusIndicator isAdmin />);
  expect(await screen.findByText('Eingeschränkt: Dokumentverarbeitung, Chat & Suche')).toBeTruthy();
  expect(screen.getByRole('status').getAttribute('href')).toBe('/admin/status');
});

it('admits when the status cannot be read', async () => {
  api.mockRejectedValue(new Error('offline'));
  render(<SystemStatusIndicator isAdmin={false} />);
  expect(await screen.findByText('Systemstatus nicht abrufbar')).toBeTruthy();
});
