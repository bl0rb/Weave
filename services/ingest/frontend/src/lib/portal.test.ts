import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import type { IndexingItem } from './indexing-status';
import { collectionDownloadName, documentState, loadDocuments, markdownDownloadName, pipelineStage, portalDownloadError, portalError, summarizePipeline, type PortalDocument } from './portal';

const document: PortalDocument = { id: 'a', original_filename: 'Wissen.pdf', status: 'FINISHED', collection_id: 'c', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, source: { kind: 'upload', label: 'Hochgeladen', path: 'inbox', url: null }, review_decision: null };
describe('publication state', () => {
  it('never labels processed content as published', () => { expect(documentState(document).label).toBe('Bereit zur Prüfung'); });
  it('distinguishes delivery acknowledgement from completed indexing', () => {
    const sent = { ...document, release: { id: 'r', status: 'sent' as const, created_at: document.created_at, error_message: null, released_by: null } };
    expect(documentState(sent).label).toBe('Zur Indexierung übergeben');
    expect(documentState(sent).label).not.toMatch(/aktiv|verfügbar/i);
  });
  it('keeps quality blocks visible', () => { expect(documentState({ ...document, quality_recommendation: 'block' }).tone).toBe('error'); });
  it('shows skipped documents as neutral', () => { expect(documentState({ ...document, review_decision: 'skipped' })).toEqual({ label: 'Übersprungen', tone: 'neutral' }); });
  it('does not expose internal errors or tokens', () => { expect(portalError(new ApiError(500, 'secret-key=example SQL error'))).not.toContain('secret'); });
});

describe('portal download names', () => {
  it('creates flat Markdown filenames from client supplied paths', () => {
    expect(markdownDownloadName('../Vertrag: Süd?.PDF')).toBe('Vertrag_ Süd_.md');
    expect(markdownDownloadName('ohne-endung')).toBe('ohne-endung.md');
  });

  it('keeps dots in knowledge area names when naming the ZIP', () => {
    expect(collectionDownloadName({ name: 'HR.Wissen 2026', slug: 'hr-wissen' })).toBe('HR.Wissen 2026-markdown.zip');
  });

  it('explains an empty export without exposing a server detail', () => {
    expect(portalDownloadError(new ApiError(404, 'internal marker'))).toMatch(/noch keine fertige Markdown-Datei/);
    expect(portalDownloadError(new ApiError(404, 'internal marker'))).not.toContain('internal');
  });
});

describe('loadDocuments', () => {
  afterEach(() => { vi.unstubAllGlobals(); });
  it('appends the quality_grade filter to the query string', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], total: 0 }) });
    vi.stubGlobal('fetch', fetchMock);
    await loadDocuments('area', 0, 'all', 'A');
    const url = new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost');
    expect(url.searchParams.get('quality_grade')).toBe('A');
  });
  it('omits the quality_grade param when no filter is chosen', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], total: 0 }) });
    vi.stubGlobal('fetch', fetchMock);
    await loadDocuments('area', 0, 'all');
    const url = new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost');
    expect(url.searchParams.has('quality_grade')).toBe(false);
  });

  it('defaults to a limit of 20 but accepts an override for bulk fetches', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], total: 0 }) });
    vi.stubGlobal('fetch', fetchMock);
    await loadDocuments();
    expect(new URL(fetchMock.mock.calls[0][0] as string, 'http://localhost').searchParams.get('limit')).toBe('20');
    await loadDocuments(undefined, 0, 'all', undefined, 200);
    expect(new URL(fetchMock.mock.calls[1][0] as string, 'http://localhost').searchParams.get('limit')).toBe('200');
  });
});

describe('pipelineStage', () => {
  const released = { id: 'r', status: 'sent' as const, created_at: document.created_at, error_message: null, released_by: null };

  it('buckets a queued or running job as processing', () => {
    expect(pipelineStage({ ...document, status: 'PENDING' })).toBe('processing');
    expect(pipelineStage({ ...document, status: 'RUNNING' })).toBe('processing');
  });

  it('buckets a failed job as error', () => {
    expect(pipelineStage({ ...document, status: 'FAILED' })).toBe('error');
  });

  it('buckets a finished, unreleased document (including quality grade C) as review', () => {
    expect(pipelineStage(document)).toBe('review');
    expect(pipelineStage({ ...document, quality_grade: 'C' })).toBe('review');
  });

  it('buckets a released document without a live indexing snapshot as indexing', () => {
    expect(pipelineStage({ ...document, release: released })).toBe('indexing');
  });

  it('buckets a released and confirmed-indexed document as ready', () => {
    const live: IndexingItem = { job_id: document.id, release: released, indexing: { state: 'indexed', indexed_at: document.created_at, chunk_count: 3 } };
    expect(pipelineStage({ ...document, release: released }, live)).toBe('ready');
  });

  it('buckets a blocked or failed indexing run as error, even though delivery succeeded', () => {
    const live: IndexingItem = { job_id: document.id, release: released, indexing: { state: 'blocked', indexed_at: null, chunk_count: 0 } };
    expect(pipelineStage({ ...document, release: released }, live)).toBe('error');
  });

  it('buckets a failed release delivery as error', () => {
    expect(pipelineStage({ ...document, release: { ...released, status: 'failed' } })).toBe('error');
  });
});

describe('summarizePipeline', () => {
  it('tallies documents per pipeline stage', () => {
    const counts = summarizePipeline([
      { ...document, id: '1', status: 'RUNNING' },
      { ...document, id: '2', status: 'FAILED' },
      { ...document, id: '3' },
    ]);
    expect(counts).toEqual({ processing: 1, review: 1, indexing: 0, ready: 0, error: 1 });
  });
});
