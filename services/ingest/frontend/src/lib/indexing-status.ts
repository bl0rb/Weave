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

export function indexingDate(value: string): string {
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}

export function publicationState(delivery: Publication['status'], indexing?: IndexingStatus | null): PublicationState {
  if (isIndexReady(indexing)) return {
    label: 'Für KI verfügbar', tone: 'success',
    hint: `${indexing!.chunk_count} ${indexing!.chunk_count === 1 ? 'Textabschnitt' : 'Textabschnitte'} · ${indexingDate(indexing!.indexed_at!)}`,
  };
  switch (indexing?.state) {
    case 'pending': return { label: 'Wird indiziert', tone: 'working', hint: 'Die freigegebenen Inhalte werden für die KI-Suche aufbereitet. Das kann etwas dauern.' };
    case 'failed': return { label: 'Indexierung fehlgeschlagen', tone: 'error', hint: 'Die Inhalte konnten nicht für die KI-Suche aufbereitet werden. Bitte wende dich an die Administration.' };
    case 'blocked': return { label: 'Indexierung blockiert', tone: 'error', hint: 'Die Qualitätsprüfung hat die Indexierung gestoppt. Bitte kläre den Inhalt mit der Administration.' };
    case 'empty': return { label: 'Kein durchsuchbarer Inhalt', tone: 'warning', hint: 'Es wurden keine Textabschnitte für die KI-Suche gefunden. Bitte prüfe die Quelle mit der Administration.' };
    case 'indexed':
    case 'incomplete': return { label: 'Index unvollständig', tone: 'error', hint: 'Der freigegebene Stand ist nicht vollständig durchsuchbar. Bitte wende dich an die Administration.' };
    case 'mismatch': return { label: 'Freigabe nicht im Index', tone: 'warning', hint: 'Im Wissensindex liegt ein anderer Stand vor. Bitte wende dich an die Administration.' };
    case 'superseded': return { label: 'Durch neueren Stand ersetzt', tone: 'neutral', hint: 'Diese Version wird nicht mehr durchsucht. Verwende den neueren Stand im Wissensbereich.' };
  }
  if (delivery === 'failed') return { label: 'Übergabe fehlgeschlagen', tone: 'error', hint: 'Die Freigabe konnte nicht zugestellt werden. Öffne das Dokument, um die Übergabe erneut anzustoßen.' };
  if (indexing?.state === 'unavailable') return { label: 'Indexstatus nicht verfügbar', tone: 'warning', hint: 'Der Status konnte gerade nicht abgefragt werden. Wir versuchen es automatisch erneut.' };
  if (delivery === 'pending') return { label: 'Freigegeben', tone: 'working', hint: 'Die Freigabe ist gespeichert und wird automatisch zur Indexierung übergeben.' };
  if (indexing?.state === 'not_received') return { label: 'Wartet auf Indexierung', tone: 'working', hint: 'Die freigegebenen Inhalte sind noch nicht im Wissensindex angekommen.' };
  return { label: 'Zur Indexierung übergeben', tone: 'working', hint: 'Der aktuelle Indexstatus wird abgefragt.' };
}

// Do not let a response for another release decorate the current preview.
export function currentReleaseStatus(release: Publication, live?: IndexingItem): { delivery: Publication['status']; indexing?: IndexingStatus | null } {
  if (!live) return { delivery: release.status };
  if (live.release?.id !== release.id) return { delivery: release.status, indexing: unavailableIndexing };
  return { delivery: live.release.status, indexing: live.indexing ?? unavailableIndexing };
}
