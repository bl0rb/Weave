// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiFetch } from '@/lib/api';
import JobDetailsPage from './page';

const router = vi.hoisted(() => ({ replace: vi.fn(), back: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useParams: () => ({ id: 'j1' }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => ({ user: { username: 'ada', role: 'user' } }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiFetch: vi.fn(), redirectIfSessionExpired: vi.fn(async () => false) }));

const fetchMock = vi.mocked(apiFetch);
const res = (status: number, body: unknown = {}) => ({ ok: status < 400, status, json: async () => body, text: async () => 'md' }) as Response;
const job = (status: string, collection?: string) => ({
  id: 'j1', original_filename: 'a.pdf', status, created_at: '2026-09-01T12:00:00Z',
  processing_info: { settings: collection ? { collection_id: collection } : {}, execution: {} },
});
function serve(jobBody: unknown, previewStatus = 200) {
  fetchMock.mockImplementation(async path => String(path).includes('/preview') ? res(previewStatus) : res(200, jobBody));
}

beforeEach(() => { fetchMock.mockReset(); router.replace.mockReset(); });
afterEach(cleanup);

it('redirects finished documents in a knowledge space to the review view', async () => {
  serve(job('FINISHED', 'c1'));
  render(<JobDetailsPage />);
  await waitFor(() => expect(router.replace).toHaveBeenCalledWith('/reviews/j1'));
  expect(screen.queryByText('Vorschau')).toBeNull();
});

it('keeps running jobs on the page with a text status and the technical button', async () => {
  serve(job('RUNNING', 'c1'));
  render(<JobDetailsPage />);
  expect(await screen.findByText('Wird verarbeitet', { selector: '.portal-badge' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Technische Details' })).toBeTruthy();
  expect(router.replace).not.toHaveBeenCalled();
});

it('shows the password prompt instead of redirecting when the preview answers 401', async () => {
  serve(job('FINISHED', 'c1'), 401);
  render(<JobDetailsPage />);
  expect(await screen.findByRole('button', { name: 'Entsperren' })).toBeTruthy();
  expect(router.replace).not.toHaveBeenCalled();
});
