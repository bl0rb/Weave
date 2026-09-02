'use client';

import { Check, Circle } from 'lucide-react';
import { currentReleaseStatus, indexingDate, isIndexReady, publicationState, type IndexingItem } from '@/lib/indexing-status';
import type { Publication } from '@/lib/portal';

export function IndexingProgress({ release, live }: { release: Publication; live?: IndexingItem }) {
  const { delivery, indexing } = currentReleaseStatus(release, live);
  const state = publicationState(delivery, indexing);
  const ready = isIndexReady(indexing);
  const active = indexing?.state === 'pending';
  return <section className="portal-indexing" aria-label="KI-Verfügbarkeit">
    <ol className="portal-index-steps" aria-label="Fortschritt der Veröffentlichung">
      <li data-complete="true"><Check size={17} aria-hidden="true" /><span>Freigegeben</span></li>
      <li data-complete={ready} aria-current={active ? 'step' : undefined}>{ready ? <Check size={17} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}<span>Wird indiziert</span></li>
      <li data-complete={ready} aria-current={ready ? 'step' : undefined}>{ready ? <Check size={17} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}<span>Für KI verfügbar</span></li>
    </ol>
    <div aria-live="polite" aria-atomic="true">
      <p><strong className={`portal-badge portal-badge-${state.tone}`}>{state.label}</strong></p>
      {ready ? <><p className="portal-field-hint">Dieser Stand kann von Bots, Chat und Tools mit den passenden Berechtigungen durchsucht werden.</p><dl><dt>Indizierung abgeschlossen</dt><dd><time dateTime={indexing!.indexed_at!}>{indexingDate(indexing!.indexed_at!)}</time></dd><dt>Durchsuchbare Textabschnitte</dt><dd>{indexing!.chunk_count}</dd></dl></> : <p className="portal-field-hint">{state.hint}</p>}
    </div>
  </section>;
}
