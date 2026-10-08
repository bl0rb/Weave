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

it('offers no release-all and no header links; several documents are released through the selection', async () => {
  render(<PortalDocuments initialBereich="servicewissen" />);
  await screen.findByText('Handbuch.pdf');
  expect(screen.queryByRole('button', { name: /Dokumente freigeben/ })).toBeNull();
  expect(screen.queryByRole('link', { name: /Verarbeitung|Importe/ })).toBeNull();
});

it('selects every filtered document, not just the current page, and shows more per page on request', async () => {
  const many = Array.from({ length: 25 }, (_, index) => ({ ...document, id: `d${index}`, original_filename: `Dok-${index}.pdf` }));
  json.mockImplementation(async path => {
    if (path === '/api/v1/portal/documents/bulk') return { done: 25, errors: [] };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/documents')) return { items: many, total: 25 };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/indexing-status')) return { items: [] };
    return { items: [area] };
  });
  render(<PortalDocuments />);
  await screen.findByText('Dok-0.pdf');
  expect(screen.queryByText('Dok-24.pdf')).toBeNull();

  fireEvent.click(screen.getByRole('checkbox', { name: 'Alle 25 Dokumente auswählen' }));
  expect(screen.getByText('25 ausgewählt')).toBeTruthy();

  fireEvent.change(screen.getByRole('combobox', { name: 'Pro Seite' }), { target: { value: '50' } });
  expect(screen.getByText('Dok-24.pdf')).toBeTruthy();
  expect(screen.getByText('25 ausgewählt')).toBeTruthy();
});

it('deletes a single released document from its row, withdrawing it first', async () => {
  const released = { ...document, release: { id: 'r1', created_at: document.created_at, status: 'sent', error_message: null, released_by: 'anna' } };
  json.mockImplementation(async path => {
    if (path === '/api/v1/portal/documents/bulk') return { done: 1, errors: [] };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/documents')) return { items: [released], total: 1 };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/indexing-status')) return { items: [] };
    return { items: [area] };
  });
  render(<PortalDocuments />);
  fireEvent.click(await screen.findByRole('button', { name: 'Handbuch.pdf löschen' }));
  expect(screen.getByText(/aus dem Wissen zurückgezogen/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Löschen' }));
  await waitFor(() => {
    const call = json.mock.calls.find(([path]) => path === '/api/v1/portal/documents/bulk');
    expect(JSON.parse(call?.[1]?.body as string)).toMatchObject({ job_ids: ['d1'], action: 'delete', withdraw_released: true });
  });
});

it('does not count or list a parked document under the review stage, but keeps it under "Alle"', async () => {
  const parked = { ...document, id: 'd2', original_filename: 'Geparkt.pdf', review_decision: 'parked' };
  json.mockImplementation(async path => {
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/documents')) return { items: [document, parked], total: 2 };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/indexing-status')) return { items: [] };
    return { items: [area] };
  });
  render(<PortalDocuments />);
  await screen.findByText('Geparkt.pdf');
  const review = screen.getByRole('button', { name: /Prüfung/ });
  expect(review.querySelector('b')?.textContent).toBe('1');
  fireEvent.click(review);
  expect(screen.getByText('Handbuch.pdf')).toBeTruthy();
  expect(screen.queryByText('Geparkt.pdf')).toBeNull();
});

it('hints that only the newest documents are loaded when the space holds more', async () => {
  const two = [document, { ...document, id: 'd2', original_filename: 'Zweit.pdf' }];
  const mockPage = (total: number) => json.mockImplementation(async path => {
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/documents')) return { items: two, total };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/indexing-status')) return { items: [] };
    return { items: [area] };
  });
  mockPage(350);
  const { unmount } = render(<PortalDocuments />);
  expect(await screen.findByText(/Geladen sind die neuesten 2 von 350 Dokumenten/)).toBeTruthy();
  unmount();

  mockPage(2);
  render(<PortalDocuments />);
  await screen.findByText('Zweit.pdf');
  expect(screen.queryByText(/Geladen sind die neuesten/)).toBeNull();
});
