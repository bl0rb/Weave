// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import type { DocumentReview } from '@/lib/document-freshness';
import { DocumentReviewPanel } from './document-review-panel';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);

const review: DocumentReview = { review_due_at: '2026-11-01T00:00:00Z', last_reviewed_at: '2026-05-01T09:00:00Z', last_reviewed_by: 'anna', overdue: true };
const versions = { items: [
  { job_id: 'doc', document_version: 3, content_sha256: null, status: 'FINISHED', created_at: '2026-10-01T00:00:00Z', uploaded_by: null, is_current: true },
  { job_id: 'doc-v1', document_version: 1, content_sha256: null, status: 'FINISHED', created_at: '2026-01-01T00:00:00Z', uploaded_by: null, is_current: false },
] };
let response: (path: string, init?: RequestInit) => unknown;

beforeEach(() => {
  response = path => (path.endsWith('/versions') ? versions : review);
  api.mockReset();
  api.mockImplementation(async (path, init) => response(path as string, init));
});
afterEach(cleanup);

const mutations = () => api.mock.calls.filter(([, init]) => init?.method === 'PUT' || init?.method === 'POST');

it('shows due date, overdue badge, last review and the comparison link with the older visible version', async () => {
  render(<DocumentReviewPanel jobId="doc" review={review} released canManage />);
  expect(screen.getByRole('heading', { name: 'Prüfung & Aktualität' })).toBeTruthy();
  expect(screen.getByText('Überfällig')).toBeTruthy();
  expect(screen.getByText(/von anna$/)).toBeTruthy();
  expect(screen.getByText(/01\.11\.2026|1\. Nov\. 2026/)).toBeTruthy();
  const link = await screen.findByRole('link', { name: 'Versionen vergleichen' });
  expect(link.getAttribute('href')).toBe('/reviews/doc/changes?base=doc-v1');
});

it('saves a new due date and clears it', async () => {
  render(<DocumentReviewPanel jobId="doc" review={review} released canManage />);
  const save = screen.getByRole('button', { name: 'Datum speichern' }) as HTMLButtonElement;
  expect(save.disabled).toBe(true); // unchanged date
  const input = screen.getByLabelText('Nächste Prüfung am') as HTMLInputElement;
  expect(input.value).toBe('2026-11-01');
  response = (path, init) => (path.endsWith('/versions') ? versions : init?.method === 'PUT' ? { ...review, review_due_at: '2027-02-01T00:00:00Z', overdue: false } : review);
  fireEvent.change(input, { target: { value: '2027-02-01' } });
  fireEvent.click(save);
  await screen.findByText('Prüfdatum gespeichert.');
  const [path, init] = mutations().at(-1)!;
  expect(path).toBe('/api/v1/portal/documents/doc/review');
  expect(JSON.parse(init!.body as string)).toEqual({ review_due_at: '2027-02-01' });
  expect(screen.queryByText('Überfällig')).toBeNull();

  response = (path, init) => (path.endsWith('/versions') ? versions : init?.method === 'PUT' ? { ...review, review_due_at: null, overdue: false } : review);
  fireEvent.click(screen.getByRole('button', { name: 'Datum entfernen' }));
  await screen.findByText('Prüfdatum entfernt.');
  expect(JSON.parse(mutations().at(-1)![1]!.body as string)).toEqual({ review_due_at: null });
  expect(screen.getByText('Keine Prüfung geplant')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Datum entfernen' })).toBeNull();
});

it('confirms the review without a body and shows the new state', async () => {
  response = (path, init) => (path.endsWith('/versions') ? versions
    : init?.method === 'POST' ? { review_due_at: '2027-11-01T00:00:00Z', last_reviewed_at: '2026-11-17T08:00:00Z', last_reviewed_by: 'ada', overdue: false } : review);
  render(<DocumentReviewPanel jobId="doc" review={review} released canManage />);
  fireEvent.click(screen.getByRole('button', { name: 'Als geprüft bestätigen' }));
  await screen.findByText('Prüfung bestätigt.');
  expect(mutations().at(-1)).toEqual(['/api/v1/portal/documents/doc/review/confirm', { method: 'POST' }]);
  expect(screen.getByText(/von ada$/)).toBeTruthy();
  expect((screen.getByLabelText('Nächste Prüfung am') as HTMLInputElement).value).toBe('2027-11-01');
});

it('shows a forbidden error from the backend', async () => {
  response = (path, init) => {
    if (path.endsWith('/versions')) return versions;
    if (init?.method === 'POST') throw new ApiError(403, 'User cannot review this document');
    return review;
  };
  render(<DocumentReviewPanel jobId="doc" review={review} released canManage />);
  fireEvent.click(screen.getByRole('button', { name: 'Als geprüft bestätigen' }));
  expect((await screen.findByRole('alert')).textContent).toBe('Für diese Aktion fehlen dir die Berechtigungen.');
});

it('is read-only for people who do not control the document', async () => {
  render(<DocumentReviewPanel jobId="doc" review={review} released canManage={false} />);
  expect(screen.getByText('Überfällig')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Als geprüft bestätigen' })).toBeNull();
  expect(screen.queryByLabelText('Nächste Prüfung am')).toBeNull();
  await screen.findByRole('link', { name: 'Versionen vergleichen' });
});

it('shows placeholders for a released version without review data', async () => {
  response = () => ({ items: [] });
  render(<DocumentReviewPanel jobId="doc" review={null} released canManage />);
  expect(screen.getByText('Keine Prüfung geplant')).toBeTruthy();
  expect(screen.getByText('Noch nicht geprüft')).toBeTruthy();
  await waitFor(() => expect(api).toHaveBeenCalled());
  expect(screen.queryByRole('link', { name: 'Versionen vergleichen' })).toBeNull();
});

it('renders nothing for an unreleased single version, and only the link for an unreleased later version', async () => {
  response = () => ({ items: [versions.items[0]] });
  const { container, unmount } = render(<DocumentReviewPanel jobId="doc" review={null} released={false} canManage />);
  await waitFor(() => expect(api).toHaveBeenCalled());
  expect(container.textContent).toBe('');
  unmount();
  response = () => versions;
  render(<DocumentReviewPanel jobId="doc" review={null} released={false} canManage />);
  await screen.findByRole('link', { name: 'Versionen vergleichen' });
  expect(screen.queryByText('Nächste Prüfung')).toBeNull();
});

it('hides the comparison link when the versions cannot be loaded', async () => {
  response = path => { if (path.endsWith('/versions')) throw new ApiError(401, 'password'); return review; };
  render(<DocumentReviewPanel jobId="doc" review={review} released canManage />);
  await waitFor(() => expect(api).toHaveBeenCalled());
  expect(screen.queryByRole('link', { name: 'Versionen vergleichen' })).toBeNull();
});

it('links the oldest version to its comparison with the newest one', async () => {
  response = path => (path.endsWith('/versions') ? versions : { release: null });
  render(<DocumentReviewPanel jobId="doc-v1" review={null} released={false} canManage />);
  const link = await screen.findByRole('link', { name: 'Versionen vergleichen' });
  expect(link.getAttribute('href')).toBe('/reviews/doc/changes?base=doc-v1');
});

it('ignores versions without markdown for the comparison link', async () => {
  response = path => (path.endsWith('/versions')
    ? { items: [versions.items[0], { ...versions.items[1], status: 'FAILED' }] }
    : review);
  render(<DocumentReviewPanel jobId="doc" review={review} released canManage />);
  await waitFor(() => expect(api).toHaveBeenCalled());
  expect(screen.queryByRole('link', { name: 'Versionen vergleichen' })).toBeNull();
});

const supersededText = /Eine neuere Version dieses Dokuments wurde freigegeben/;

it('hides the actions and the overdue badge on a version superseded by a visible newer release', async () => {
  response = path => (path.endsWith('/versions') ? versions : path === '/api/v1/portal/documents/doc' ? { release: { created_at: '2026-10-02T00:00:00Z' } } : review);
  render(<DocumentReviewPanel jobId="doc-v1" review={review} released canManage />);
  expect(await screen.findByText(supersededText)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Neueste Version öffnen' }).getAttribute('href')).toBe('/reviews/doc');
  expect(screen.queryByText('Überfällig')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Als geprüft bestätigen' })).toBeNull();
  expect(screen.queryByLabelText('Nächste Prüfung am')).toBeNull();
  // The dates themselves stay readable.
  expect(screen.getByText(/von anna$/)).toBeTruthy();
  expect(mutations()).toEqual([]);
});

it('keeps the actions while the newer version is not released', async () => {
  response = path => (path.endsWith('/versions') ? versions : path === '/api/v1/portal/documents/doc' ? { release: null } : review);
  render(<DocumentReviewPanel jobId="doc-v1" review={review} released canManage />);
  await waitFor(() => expect(api.mock.calls.map(([path]) => path)).toContain('/api/v1/portal/documents/doc'));
  expect(screen.getByRole('button', { name: 'Als geprüft bestätigen' })).toBeTruthy();
  expect(screen.getByText('Überfällig')).toBeTruthy();
  expect(screen.queryByText(supersededText)).toBeNull();
});

it('does not ask for newer releases on an unreleased version', async () => {
  response = path => (path.endsWith('/versions') ? versions : { release: { created_at: '2026-10-02T00:00:00Z' } });
  render(<DocumentReviewPanel jobId="doc-v1" review={null} released={false} canManage />);
  await screen.findByRole('link', { name: 'Versionen vergleichen' });
  expect(api.mock.calls.map(([path]) => path)).toEqual(['/api/v1/jobs/doc-v1/versions']);
});

it('turns a 409 (newer release the caller may not see) into the superseded notice', async () => {
  response = (path, init) => {
    if (path.endsWith('/versions')) return { items: [] };
    if (init?.method === 'POST') throw new ApiError(409, 'A newer version of this document has been released');
    return review;
  };
  render(<DocumentReviewPanel jobId="doc" review={review} released canManage />);
  fireEvent.click(screen.getByRole('button', { name: 'Als geprüft bestätigen' }));
  expect(await screen.findByText(supersededText)).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Als geprüft bestätigen' })).toBeNull();
  expect(screen.queryByText('Überfällig')).toBeNull();
  expect(screen.queryByRole('link', { name: 'Neueste Version öffnen' })).toBeNull();
});
