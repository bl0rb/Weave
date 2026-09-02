// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from './api';
import { useVisiblePolling } from './data-cache';
import { useIndexingStatus } from './use-indexing-status';

vi.mock('./api', () => ({ apiJson: vi.fn() }));
vi.mock('./data-cache', () => ({ useVisiblePolling: vi.fn() }));
const api = vi.mocked(apiJson);
const polling = vi.mocked(useVisiblePolling);
const publication = { id: 'release', status: 'sent', created_at: '2026-09-02T11:00:00Z', error_message: null };
const status = (id: string, state = 'pending') => ({ job_id: id, release: publication, indexing: { state, chunk_count: state === 'indexed' ? 2 : 0, indexed_at: state === 'indexed' ? '2026-09-02T11:31:12Z' : null } });
beforeEach(() => { api.mockReset(); polling.mockReset(); });
afterEach(cleanup);

it('batches a visible page and polls pending status faster than completed status', async () => {
  api.mockResolvedValueOnce({ items: [status('one'), status('two')] });
  const { result } = renderHook(() => useIndexingStatus(['two', 'one', 'one']));
  await waitFor(() => expect(result.current.items.one?.indexing?.state).toBe('pending'));
  expect(api).toHaveBeenCalledTimes(1);
  expect(new URL(api.mock.calls[0][0], 'http://localhost').searchParams.getAll('job_id')).toEqual(['one', 'two']);
  expect(api.mock.calls[0][1]?.cache).toBe('no-store');
  expect(polling.mock.calls.at(-1)?.[1]).toBe(5_000);
  api.mockResolvedValueOnce({ items: [status('one', 'indexed'), status('two', 'indexed')] });
  act(() => polling.mock.calls.at(-1)?.[0]());
  await waitFor(() => expect(result.current.items.one?.indexing?.state).toBe('indexed'));
  expect(polling.mock.calls.at(-1)?.[1]).toBe(30_000);
});

it('does not fetch or poll before a document has a release', () => {
  renderHook(() => useIndexingStatus([]));
  expect(api).not.toHaveBeenCalled();
  expect(polling.mock.calls.at(-1)?.[1]).toBeNull();
});

it.each(['failure', 'permission removed'])('clears a previous success on %s and retries', async mode => {
  api.mockResolvedValueOnce({ items: [status('one', 'indexed')] });
  const { result } = renderHook(() => useIndexingStatus(['one']));
  await waitFor(() => expect(result.current.items.one?.indexing?.state).toBe('indexed'));
  if (mode === 'failure') api.mockRejectedValueOnce(new Error('network'));
  else api.mockResolvedValueOnce({ items: [] });
  await act(() => result.current.refresh());
  expect(result.current.items.one.indexing?.state).toBe('unavailable');
  expect(polling.mock.calls.at(-1)?.[1]).toBe(5_000);
});

it('aborts an old page lookup and never paints its status on the new page', async () => {
  let resolveOld: (value: unknown) => void = () => {};
  api.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  const { result, rerender } = renderHook(({ ids }) => useIndexingStatus(ids), { initialProps: { ids: ['old'] } });
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  const signal = api.mock.calls[0][1]?.signal;
  api.mockResolvedValueOnce({ items: [status('new')] });
  rerender({ ids: ['new'] });
  await waitFor(() => expect(result.current.items.new?.indexing?.state).toBe('pending'));
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolveOld({ items: [status('old', 'indexed')] }); });
  expect(result.current.items.old).toBeUndefined();
});
