import { expect, it } from 'vitest';
import { currentReleaseStatus, publicationState, type IndexingItem, type IndexingStatus } from './indexing-status';
const pending = { state: 'pending' as const, indexed_at: null, chunk_count: 0 };
const indexed = { state: 'indexed' as const, indexed_at: '2026-09-02T11:31:12Z', chunk_count: 2 };

it('only turns green when completed indexing is confirmed with timestamp and chunks', () => {
  expect(publicationState('sent').tone).toBe('working');
  expect(publicationState('sent', pending).label).toBe('Wird indiziert');
  expect(publicationState('sent', indexed).label).toBe('Für KI verfügbar');
  expect(publicationState('sent', indexed).hint).toContain('2 Textabschnitte');
  // A lost delivery acknowledgement must not hide real committed results.
  expect(publicationState('failed', indexed).tone).toBe('success');
  expect(publicationState('sent', { ...indexed, chunk_count: 0 }).tone).not.toBe('success');
  expect(publicationState('sent', { ...indexed, indexed_at: null }).tone).not.toBe('success');
});

it.each(['not_received', 'mismatch', 'empty', 'incomplete', 'failed', 'blocked', 'superseded', 'unavailable'] as const)('keeps %s out of the ready state', state => {
  const result = publicationState('sent', { ...indexed, state } as IndexingStatus);
  expect(result.tone).not.toBe('success');
  expect(result.label).not.toBe('Für KI verfügbar');
});

it('does not use a status response for another release', () => {
  const release = { id: 'current', status: 'sent' as const, created_at: '2026-09-02T11:30:00Z', error_message: null };
  const live: IndexingItem = { job_id: 'job', release: { ...release, id: 'old' }, indexing: indexed };
  const state = currentReleaseStatus(release, live);
  expect(publicationState(state.delivery, state.indexing).label).toBe('Indexstatus nicht verfügbar');
});
