import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import {
  contradictionDetails, feedbackDetails, gapDetails, headingPathLabel, isActiveTask, loadQualitySummary, loadReviewTask, loadReviewTaskCount,
  loadReviewTasks, reviewDueDetails, updateReviewTask,
} from './review-tasks';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn().mockResolvedValue({}) }));
const api = vi.mocked(apiJson);
const lastUrl = () => new URL(api.mock.calls.at(-1)?.[0] as string, 'http://localhost');

afterEach(() => api.mockClear());

describe('review task requests', () => {
  it('lists active tasks by default with page and page size', async () => {
    await loadReviewTasks();
    const url = lastUrl();
    expect(url.pathname).toBe('/api/v1/portal/review-tasks');
    expect(Object.fromEntries(url.searchParams)).toEqual({ status: 'active', page: '1', page_size: '25' });
  });

  it('adds kind, status, space and page only when set', async () => {
    await loadReviewTasks({ status: 'done', kind: 'contradiction', collectionId: 'c 1', page: 3, pageSize: 10 });
    expect(Object.fromEntries(lastUrl().searchParams)).toEqual({ status: 'done', page: '3', page_size: '10', kind: 'contradiction', collection_id: 'c 1' });
    await loadReviewTasks({ kind: '', collectionId: '' });
    expect(lastUrl().searchParams.has('kind')).toBe(false);
    expect(lastUrl().searchParams.has('collection_id')).toBe(false);
  });

  it('encodes task ids and sends PATCH bodies as JSON', async () => {
    await loadReviewTask('a/b');
    expect(api.mock.calls.at(-1)?.[0]).toBe('/api/v1/portal/review-tasks/a%2Fb');
    await updateReviewTask('t1', { status: 'done', resolution_note: 'ok' });
    const [path, init] = api.mock.calls.at(-1) as [string, RequestInit];
    expect(path).toBe('/api/v1/portal/review-tasks/t1');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ status: 'done', resolution_note: 'ok' });
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });

  it('loads the count and the summary, optionally for one space', async () => {
    await loadReviewTaskCount();
    expect(api.mock.calls.at(-1)?.[0]).toBe('/api/v1/portal/review-tasks/count');
    await loadQualitySummary();
    expect(api.mock.calls.at(-1)?.[0]).toBe('/api/v1/portal/quality/summary');
    await loadQualitySummary('space-1');
    expect(api.mock.calls.at(-1)?.[0]).toBe('/api/v1/portal/quality/summary?collection_id=space-1');
  });
});

describe('detail readers', () => {
  it('knows which statuses are active', () => {
    expect(isActiveTask({ status: 'open' })).toBe(true);
    expect(isActiveTask({ status: 'in_progress' })).toBe(true);
    expect(isActiveTask({ status: 'done' })).toBe(false);
    expect(isActiveTask({ status: 'dismissed' })).toBe(false);
  });

  it('joins heading paths and tolerates strings and junk', () => {
    expect(headingPathLabel(['Reisekosten', 'Tagegeld'])).toBe('Reisekosten › Tagegeld');
    expect(headingPathLabel(['A', 3, '', 'B'])).toBe('A › B');
    expect(headingPathLabel(' Kapitel 1 ')).toBe('Kapitel 1');
    expect(headingPathLabel(null)).toBe('');
  });

  it('reads gap questions with counts and skips malformed entries', () => {
    expect(gapDetails({
      questions: [{ text: 'Wie hoch ist das Tagegeld?', count: 3, created_at: '2026-10-01T10:00:00Z' }, { text: '' }, 'junk', { text: 'Ohne Zähler' }],
      rewritten_queries: ['tagegeld höhe', 7, ''],
    })).toEqual({
      questions: [
        { text: 'Wie hoch ist das Tagegeld?', count: 3, created_at: '2026-10-01T10:00:00Z' },
        { text: 'Ohne Zähler', count: 1, created_at: null },
      ],
      rewrittenQueries: ['tagegeld höhe'],
    });
    expect(gapDetails({})).toEqual({ questions: [], rewrittenQueries: [] });
  });

  it('reads feedback counts and samples', () => {
    const result = feedbackDetails({
      counts: { wrong: 2, outdated: 1 },
      samples: [{ rating: 'wrong', comment: ' Betrag stimmt nicht ', question: 'Q', answer_excerpt: 'A', heading_path: ['X', 'Y'], created_at: '2026-10-02T00:00:00Z' }],
    });
    expect(result.wrong).toBe(2);
    expect(result.outdated).toBe(1);
    expect(result.samples[0]).toEqual({ rating: 'wrong', comment: 'Betrag stimmt nicht', question: 'Q', answer_excerpt: 'A', heading_path: 'X › Y', created_at: '2026-10-02T00:00:00Z' });
    expect(feedbackDetails({ counts: 'x', samples: null })).toEqual({ wrong: 0, outdated: 0, samples: [] });
  });

  it('reads the review due date', () => {
    expect(reviewDueDetails({ review_due_at: '2026-11-01T00:00:00Z', overdue: true })).toEqual({ reviewDueAt: '2026-11-01T00:00:00Z', overdue: true });
    expect(reviewDueDetails({ overdue: 'yes' })).toEqual({ reviewDueAt: null, overdue: false });
  });

  it('reads contradiction pairs from pairs, excerpts or a single report', () => {
    const report = { excerpt: 'Tagegeld 28 €', other_excerpt: 'Tagegeld 24 €', heading_path: ['Reise'], other_heading_path: 'Reise › Inland', facts: ['28 €'], other_facts: ['24 €', 5], similarity: 0.91 };
    const expected = [{ excerpt: 'Tagegeld 28 €', other_excerpt: 'Tagegeld 24 €', heading_path: 'Reise', other_heading_path: 'Reise › Inland', facts: ['28 €'], other_facts: ['24 €', '5'], similarity: 0.91 }];
    expect(contradictionDetails({ pairs: [report, { excerpt: '', other_excerpt: '' }] })).toEqual(expected);
    expect(contradictionDetails({ excerpts: [report] })).toEqual(expected);
    expect(contradictionDetails(report)).toEqual(expected);
    expect(contradictionDetails({})).toEqual([]);
  });
});
