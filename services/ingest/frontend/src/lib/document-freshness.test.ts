import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiFetch, apiJson, type JobVersionEntry } from '@/lib/api';
import {
  changeHref, compareLink, confirmDocumentReview, createSubscription, dateInputValue, defaultBaseVersion, deleteSubscription, hasNewerRelease, loadChangeCount, loadChanges,
  loadDocumentDiff, loadDocumentVersions, loadSubscriptions, markChangesRead, searchChanges, setDocumentReviewDue,
} from './document-freshness';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn().mockResolvedValue({}), apiFetch: vi.fn() }));
const api = vi.mocked(apiJson);
const fetcher = vi.mocked(apiFetch);
const lastCall = () => api.mock.calls.at(-1)!;
const lastUrl = () => new URL(lastCall()[0] as string, 'http://localhost');
const lastBody = () => JSON.parse((lastCall()[1] as RequestInit).body as string);

afterEach(() => { api.mockClear(); fetcher.mockReset(); });

const version = (id: string, n: number): JobVersionEntry => ({ job_id: id, document_version: n, content_sha256: null, status: 'FINISHED', created_at: '2026-10-01T00:00:00Z', uploaded_by: null, is_current: false });

describe('review dates', () => {
  it('PUTs the due date (or null) of an encoded document id', async () => {
    await setDocumentReviewDue('doc/1', '2027-01-31');
    expect(lastUrl().pathname).toBe('/api/v1/portal/documents/doc%2F1/review');
    expect((lastCall()[1] as RequestInit).method).toBe('PUT');
    expect(lastBody()).toEqual({ review_due_at: '2027-01-31' });
    await setDocumentReviewDue('doc', null);
    expect(lastBody()).toEqual({ review_due_at: null });
  });

  it('confirms without a body, or with an explicit next date', async () => {
    await confirmDocumentReview('doc');
    expect(lastCall()).toEqual(['/api/v1/portal/documents/doc/review/confirm', { method: 'POST' }]);
    await confirmDocumentReview('doc', '2027-03-01');
    expect((lastCall()[1] as RequestInit).method).toBe('POST');
    expect(lastBody()).toEqual({ review_due_at: '2027-03-01' });
  });

  it('turns ISO timestamps into date input values and tolerates junk', () => {
    expect(dateInputValue('2027-01-31T00:00:00Z')).toBe('2027-01-31');
    expect(dateInputValue('2027-01-31T00:00:00+00:00')).toBe('2027-01-31');
    expect(dateInputValue(null)).toBe('');
    expect(dateInputValue('not a date')).toBe('');
  });
});

describe('version comparison', () => {
  it('requests the diff with an optional base', async () => {
    await loadDocumentDiff('doc');
    expect(lastUrl().pathname).toBe('/api/v1/portal/documents/doc/diff');
    expect(lastUrl().search).toBe('');
    await loadDocumentDiff('doc', 'old 1');
    expect(lastUrl().searchParams.get('base')).toBe('old 1');
  });

  it('loads versions without the auth redirect and tolerates a missing list', async () => {
    api.mockResolvedValueOnce({ items: [version('a', 1)] });
    expect(await loadDocumentVersions('a')).toHaveLength(1);
    expect(lastCall()[1]).toMatchObject({ skipAuthRedirect: true });
    api.mockResolvedValueOnce({});
    expect(await loadDocumentVersions('a')).toEqual([]);
  });

  it('picks the next older visible version as default base', () => {
    const versions = [version('v5', 5), version('v3', 3), version('v1', 1)];
    expect(defaultBaseVersion(versions, 'v5')?.job_id).toBe('v3');
    expect(defaultBaseVersion(versions, 'v3')?.job_id).toBe('v1');
    expect(defaultBaseVersion(versions, 'v1')).toBeNull();
    expect(defaultBaseVersion(versions, 'unknown')).toBeNull();
  });

  it('skips versions without markdown (pending, running, failed) as base', () => {
    const versions = [version('v3', 3), { ...version('v2', 2), status: 'FAILED' as const }, { ...version('v1b', 1), status: 'RUNNING' as const }, version('v0', 0)];
    expect(defaultBaseVersion(versions, 'v3')?.job_id).toBe('v0');
    expect(defaultBaseVersion([version('v3', 3), { ...version('v2', 2), status: 'PENDING' as const }], 'v3')).toBeNull();
  });

  it('links the comparison to the older version, or from the oldest one to the newest', () => {
    const versions = [version('v3', 3), { ...version('v2', 2), status: 'FAILED' as const }, version('v1', 1)];
    expect(compareLink(versions, 'v3')).toEqual({ target: 'v3', base: 'v1' });
    expect(compareLink(versions, 'v1')).toEqual({ target: 'v3', base: 'v1' });
    // A failed version has nothing to compare; one FINISHED version alone neither.
    expect(compareLink(versions, 'v2')).toBeNull();
    expect(compareLink([version('v3', 3), { ...version('v2', 2), status: 'FAILED' as const }], 'v3')).toBeNull();
    expect(compareLink(versions, 'unknown')).toBeNull();
  });

  it('detects a newer released version from the newer FINISHED versions only', async () => {
    const versions = [version('v4', 4), { ...version('v3', 3), status: 'PENDING' as const }, version('v2', 2), version('v1', 1)];
    api.mockImplementation(async path => ((path as string).endsWith('/v4') ? { release: null } : { release: { created_at: '2026-10-01T00:00:00Z' } }));
    expect(await hasNewerRelease(versions, 'v1')).toBe(true);
    const asked = api.mock.calls.map(([path]) => path);
    expect(asked).toEqual(['/api/v1/portal/documents/v4', '/api/v1/portal/documents/v2']);
    expect(api.mock.calls[0][1]).toMatchObject({ skipAuthRedirect: true, cache: 'no-store' });
    api.mockClear();
    expect(await hasNewerRelease(versions, 'v2')).toBe(false);
    expect(api.mock.calls.map(([path]) => path)).toEqual(['/api/v1/portal/documents/v4']);
    api.mockClear();
    expect(await hasNewerRelease(versions, 'v4')).toBe(false);
    expect(await hasNewerRelease(versions, 'unknown')).toBe(false);
    expect(api).not.toHaveBeenCalled();
    // A failing detail (e.g. password-protected) counts as not released.
    api.mockRejectedValue(new ApiError(401, 'password'));
    expect(await hasNewerRelease(versions, 'v1')).toBe(false);
    api.mockReset(); api.mockResolvedValue({});
  });

  it('links updates to their comparison and new documents to the document', () => {
    expect(changeHref({ job_id: 'v2', previous_job_id: 'v1', change_type: 'updated' })).toBe('/reviews/v2/changes?base=v1');
    expect(changeHref({ job_id: 'v1', previous_job_id: null, change_type: 'new' })).toBe('/reviews/v1');
    expect(changeHref({ job_id: 'v2', previous_job_id: null, change_type: 'updated' })).toBe('/reviews/v2');
  });
});

describe('subscriptions and change feed', () => {
  it('lists, creates and deletes subscriptions', async () => {
    await loadSubscriptions();
    expect(lastUrl().pathname).toBe('/api/v1/portal/subscriptions');
    await createSubscription({ query: 'Reisekosten', collection_id: 'c1' });
    expect((lastCall()[1] as RequestInit).method).toBe('POST');
    expect(lastBody()).toEqual({ query: 'Reisekosten', collection_id: 'c1' });
    fetcher.mockResolvedValueOnce({ ok: true, status: 204 } as Response);
    await deleteSubscription('s/1');
    expect(fetcher).toHaveBeenCalledWith('/api/v1/portal/subscriptions/s%2F1', { method: 'DELETE' });
  });

  it('turns a failed delete into an ApiError with the status', async () => {
    fetcher.mockResolvedValueOnce({ ok: false, status: 404 } as Response);
    const failure = await deleteSubscription('x').catch(err => err);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.status).toBe(404);
  });

  it('pages the feed and filters unread only when asked', async () => {
    await loadChanges();
    expect(Object.fromEntries(lastUrl().searchParams)).toEqual({ page: '1', page_size: '25' });
    await loadChanges({ unread: true, page: 2 });
    expect(Object.fromEntries(lastUrl().searchParams)).toEqual({ page: '2', page_size: '25', unread: 'true' });
  });

  it('marks ids or everything read and counts unread', async () => {
    await markChangesRead({ ids: ['n1'] });
    expect(lastUrl().pathname).toBe('/api/v1/portal/changes/read');
    expect(lastBody()).toEqual({ ids: ['n1'] });
    await markChangesRead({ all: true });
    expect(lastBody()).toEqual({ all: true });
    await loadChangeCount();
    expect(lastUrl().pathname).toBe('/api/v1/portal/changes/count');
  });

  it('searches changes with trimmed keywords, optional space and days', async () => {
    await searchChanges();
    expect(Object.fromEntries(lastUrl().searchParams)).toEqual({ days: '30' });
    await searchChanges({ q: '  Reiseregeln ', collectionId: 'c1', days: 90 });
    expect(lastUrl().pathname).toBe('/api/v1/portal/changes/search');
    expect(Object.fromEntries(lastUrl().searchParams)).toEqual({ days: '90', q: 'Reiseregeln', collection_id: 'c1' });
    await searchChanges({ q: '   ' });
    expect(lastUrl().searchParams.has('q')).toBe(false);
  });
});
