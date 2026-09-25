import { DEFAULT_LOCALE, INTL_LOCALE, type Locale } from '@/i18n/config';
import { translate } from '@/i18n/messages';
import type { Publication } from './portal';

export type IndexingStatus = {
  state: 'not_received' | 'mismatch' | 'pending' | 'indexed' | 'empty' | 'incomplete' | 'failed' | 'blocked' | 'superseded' | 'unavailable';
  indexed_at: string | null;
  chunk_count: number;
};
export type IndexingItem = { job_id: string; release: Publication | null; indexing: IndexingStatus | null };
export type PublicationState = { label: string; tone: 'neutral' | 'working' | 'warning' | 'success' | 'error'; hint: string };

export const unavailableIndexing: IndexingStatus = { state: 'unavailable', indexed_at: null, chunk_count: 0 };
export function isIndexReady(indexing?: IndexingStatus | null): boolean {
  return indexing?.state === 'indexed' && indexing.chunk_count > 0 && Boolean(indexing.indexed_at && Number.isFinite(Date.parse(indexing.indexed_at)));
}

export function indexingDate(value: string, locale: Locale = DEFAULT_LOCALE): string {
  return new Intl.DateTimeFormat(INTL_LOCALE[locale], { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}

export function publicationState(delivery: Publication['status'], indexing?: IndexingStatus | null, locale: Locale = DEFAULT_LOCALE): PublicationState {
  if (isIndexReady(indexing)) return {
    label: translate(locale, 'portal.indexing.ready.label'), tone: 'success',
    hint: `${translate(locale, 'portal.indexing.chunks.count', { count: indexing!.chunk_count })} · ${indexingDate(indexing!.indexed_at!, locale)}`,
  };
  switch (indexing?.state) {
    case 'pending': return { label: translate(locale, 'portal.indexing.pending.label'), tone: 'working', hint: translate(locale, 'portal.indexing.pending.hint') };
    // Self-service since the 2026-09-22 incident (permanent embedding_request_failed
    // documents that used to need admin help): point at the new 'Neu indizieren' action.
    case 'failed': return { label: translate(locale, 'portal.indexing.failed.label'), tone: 'error', hint: translate(locale, 'portal.indexing.failed.hint') };
    case 'blocked': return { label: translate(locale, 'portal.indexing.blocked.label'), tone: 'error', hint: translate(locale, 'portal.indexing.blocked.hint') };
    case 'empty': return { label: translate(locale, 'portal.indexing.empty.label'), tone: 'warning', hint: translate(locale, 'portal.indexing.empty.hint') };
    case 'indexed':
    case 'incomplete': return { label: translate(locale, 'portal.indexing.incomplete.label'), tone: 'error', hint: translate(locale, 'portal.indexing.incomplete.hint') };
    case 'mismatch': return { label: translate(locale, 'portal.indexing.mismatch.label'), tone: 'warning', hint: translate(locale, 'portal.indexing.mismatch.hint') };
    case 'superseded': return { label: translate(locale, 'portal.indexing.superseded.label'), tone: 'neutral', hint: translate(locale, 'portal.indexing.superseded.hint') };
  }
  if (delivery === 'failed') return { label: translate(locale, 'portal.indexing.deliveryFailed.label'), tone: 'error', hint: translate(locale, 'portal.indexing.deliveryFailed.hint') };
  if (indexing?.state === 'unavailable') return { label: translate(locale, 'portal.indexing.statusUnavailable.label'), tone: 'warning', hint: translate(locale, 'portal.indexing.statusUnavailable.hint') };
  if (delivery === 'pending') return { label: translate(locale, 'portal.indexing.released.label'), tone: 'working', hint: translate(locale, 'portal.indexing.released.hint') };
  if (indexing?.state === 'not_received') return { label: translate(locale, 'portal.indexing.waitingForIndex.label'), tone: 'working', hint: translate(locale, 'portal.indexing.waitingForIndex.hint') };
  return { label: translate(locale, 'portal.indexing.submitted.label'), tone: 'working', hint: translate(locale, 'portal.indexing.submitted.hint') };
}

// Do not let a response for another release decorate the current preview.
export function currentReleaseStatus(release: Publication, live?: IndexingItem): { delivery: Publication['status']; indexing?: IndexingStatus | null } {
  if (!live) return { delivery: release.status };
  if (live.release?.id !== release.id) return { delivery: release.status, indexing: unavailableIndexing };
  return { delivery: live.release.status, indexing: live.indexing ?? unavailableIndexing };
}
