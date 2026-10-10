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

it('marks a stale confirmation with its time and shows a retried outage as neutral', () => {
  const stale = publicationState('sent', { ...indexed, stale: true, checked_at: '2026-09-02T11:35:00Z' });
  expect(stale.tone).toBe('success');
  expect(stale.hint).toMatch(/2 Textabschnitte · .* · Stand \d{2}:\d{2}$/);
  const retrying = publicationState('sent', { state: 'unavailable', indexed_at: null, chunk_count: 0, retrying: true });
  expect(retrying).toMatchObject({ label: 'Status wird aktualisiert…', tone: 'neutral' });
  expect(publicationState('sent', { state: 'unavailable', indexed_at: null, chunk_count: 0 })).toMatchObject({ label: 'Indexstatus nicht verfügbar', tone: 'warning' });
});

it('does not use a status response for another release', () => {
  const release = { id: 'current', status: 'sent' as const, created_at: '2026-09-02T11:30:00Z', error_message: null, released_by: null };
  const live: IndexingItem = { job_id: 'job', release: { ...release, id: 'old' }, indexing: indexed };
  const state = currentReleaseStatus(release, live);
  expect(publicationState(state.delivery, state.indexing).label).toBe('Indexstatus nicht verfügbar');
});
