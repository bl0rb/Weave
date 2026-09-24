'use client';

'use client';

import { Check, Circle } from 'lucide-react';
import { currentReleaseStatus, indexingDate, isIndexReady, publicationState, type IndexingItem } from '@/lib/indexing-status';
import type { Publication } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';

export function IndexingProgress({ release, live }: { release: Publication; live?: IndexingItem }) {
  const { t, locale } = useI18n();
  const { delivery, indexing } = currentReleaseStatus(release, live);
  const state = publicationState(delivery, indexing, locale);
  const ready = isIndexReady(indexing);
  const active = indexing?.state === 'pending';
  return <section className="portal-indexing" aria-label={t('portal.indexingProgress.ariaLabel')}>
    <ol className="portal-index-steps" aria-label={t('portal.indexingProgress.stepsAria')}>
      <li data-complete="true"><Check size={17} aria-hidden="true" /><span>{t('portal.indexing.released.label')}</span></li>
      <li data-complete={ready} aria-current={active ? 'step' : undefined}>{ready ? <Check size={17} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}<span>{t('portal.indexing.pending.label')}</span></li>
      <li data-complete={ready} aria-current={ready ? 'step' : undefined}>{ready ? <Check size={17} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}<span>{t('portal.indexing.ready.label')}</span></li>
    </ol>
    <div aria-live="polite" aria-atomic="true">
      <p><strong className={`portal-badge portal-badge-${state.tone}`}>{state.label}</strong></p>
      {ready ? <><p className="portal-field-hint">{t('portal.indexingProgress.readyHint')}</p><dl><dt>{t('portal.indexingProgress.completedLabel')}</dt><dd><time dateTime={indexing!.indexed_at!}>{indexingDate(indexing!.indexed_at!, locale)}</time></dd><dt>{t('portal.indexingProgress.chunksLabel')}</dt><dd>{indexing!.chunk_count}</dd></dl></> : <p className="portal-field-hint">{state.hint}</p>}
    </div>
  </section>;
}
