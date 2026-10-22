// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiFetch } from '@/lib/api';
import { TechnicalDetailsButton } from './technical-details';

const auth = vi.hoisted(() => ({ role: 'user' }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => ({ user: { username: 'ada', role: auth.role } }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiFetch: vi.fn() }));

const job = {
  id: 'j1', original_filename: 'a.pdf', status: 'FINISHED', created_at: '2026-09-01T12:00:00Z', document_version: 2,
  processing_info: { settings: { profile_id: 'ppocrv6_small' }, execution: { converter: 'paddle', quality_gate: { grade: 'B', score: 0.8 } } },
};
const fetchMock = vi.mocked(apiFetch);
const reply = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

beforeEach(() => {
  auth.role = 'user';
  fetchMock.mockReset();
  fetchMock.mockImplementation(async path => reply(String(path).endsWith('/versions') ? { items: [] } : job));
});
afterEach(cleanup);

it('fetches nothing until opened, then shows only the non-admin sections', async () => {
  render(<TechnicalDetailsButton jobId="j1" extraActions={<button>Extra</button>} />);
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Technische Details' }));
  expect(await screen.findByText(/Qualitätsstufe B/)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Markdown herunterladen' }).getAttribute('href')).toContain('/api/v1/jobs/j1/download');
  expect(screen.getByRole('button', { name: 'Extra' })).toBeTruthy();
  expect(screen.queryByText(/Converter/)).toBeNull();
  expect(screen.queryByRole('link', { name: /JSON/ })).toBeNull();
  expect(screen.queryByText('Admin')).toBeNull();
});

it('shows admin sections with an Admin badge for admins', async () => {
  auth.role = 'admin';
  render(<TechnicalDetailsButton jobId="j1" />);
  fireEvent.click(screen.getByRole('button', { name: 'Technische Details' }));
  expect(await screen.findByText(/Converter: paddle/)).toBeTruthy();
  expect(screen.getByRole('link', { name: /JSON/ })).toBeTruthy();
  expect(screen.getAllByText('Admin').length).toBeGreaterThan(0);
});
