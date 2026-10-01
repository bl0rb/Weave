// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import { PortalDocuments } from './documents';

vi.mock('next/navigation', () => ({ usePathname: () => '/documents' }));
vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
}));

const json = vi.mocked(apiJson);
const area = { collection_id: 'area-1', slug: 'servicewissen', name: 'Servicewissen', can_manage: true, can_upload: true };
const document = { id: 'd1', original_filename: 'Handbuch.pdf', status: 'FINISHED', collection_id: 'area-1', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };

function mockApi(space = area) {
  json.mockImplementation(async path => {
    if (path === '/api/v1/portal/documents/bulk') return { done: 1, errors: [] };
    if (path === '/api/v1/portal/collections/area-1/release-all') return { released: 1, skipped: 0 };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/documents')) return { items: [document], total: 1 };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/indexing-status')) return { items: [] };
    return { items: [space] };
  });
}

beforeEach(() => { json.mockReset(); mockApi(); });
afterEach(cleanup);

it('shows no page description and lets documents be selected for a bulk action', async () => {
  render(<PortalDocuments />);
  await screen.findByText('Handbuch.pdf');
  expect(screen.queryByText(/Der Weg jedes Dokuments/)).toBeNull();
  expect(screen.queryByRole('toolbar')).toBeNull();

  fireEvent.click(screen.getByRole('checkbox', { name: 'Handbuch.pdf auswählen' }));
  expect(screen.getByRole('toolbar')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich habe die Inhalte geprüft/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Freigeben' }));

  await waitFor(() => {
    const call = json.mock.calls.find(([path]) => path === '/api/v1/portal/documents/bulk');
    expect(JSON.parse(call?.[1]?.body as string)).toMatchObject({ job_ids: ['d1'], action: 'release' });
  });
  await waitFor(() => expect(screen.queryByRole('toolbar')).toBeNull());
});

it('offers "Sammlung freigeben" only when a space is selected and releases it through release-all', async () => {
  const { unmount } = render(<PortalDocuments />);
  await screen.findByText('Handbuch.pdf');
  expect(screen.queryByRole('button', { name: /Sammlung freigeben/ })).toBeNull();
  unmount();

  render(<PortalDocuments initialBereich="servicewissen" />);
  await screen.findByText('Handbuch.pdf');
  fireEvent.click(await screen.findByRole('button', { name: /Sammlung freigeben/ }));
  fireEvent.click(screen.getByRole('button', { name: /Ja, Sammlung freigeben/ }));
  await waitFor(() => expect(json.mock.calls.some(([path, init]) => path === '/api/v1/portal/collections/area-1/release-all' && init?.method === 'POST')).toBe(true));
  expect(await screen.findByText('1 Dokumente wurden freigegeben.')).toBeTruthy();
});

it('hides "Sammlung freigeben" when the user may not release in the selected space', async () => {
  mockApi({ ...area, can_manage: false, can_upload: false });
  render(<PortalDocuments initialBereich="servicewissen" />);
  await screen.findByText('Handbuch.pdf');
  expect(screen.queryByRole('button', { name: /Sammlung freigeben/ })).toBeNull();
});
