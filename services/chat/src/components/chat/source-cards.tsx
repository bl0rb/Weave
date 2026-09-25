'use client';

import { useState } from 'react';
import { ChevronDown, FileText } from 'lucide-react';
import type { Source } from '@/types/weave-api';
import { toProxiedImageUrl } from '@/lib/portal-artifact-url';
import { translateDefault } from '@/i18n/messages';
import type { MessageKey, MessageVars } from '@/i18n/messages';
import { useI18n } from '@/i18n/provider';

type Translator = (key: MessageKey, vars?: MessageVars) => string;

// Exported so sources-panel.tsx's own richer per-source layout can reuse
// the exact same formatting rather than re-deriving it — the right-hand
// sources panel and this component's inline collapsible list render the
// same underlying `Source` fields, just laid out differently.
//
// `t` is optional on all three: plain functions, not components, so they
// have no React context of their own — SourceCards/SourcesPanel pass
// their own `useI18n().t` through, and the German default is what this
// component's existing tests (which call SourceCards with no provider)
// assert.
export function formatPages(source: Source, t: Translator = translateDefault): string {
  if (source.page_start == null) return t('chat.sources.none');
  if (source.page_end == null || source.page_end === source.page_start) return t('chat.sources.page', { page: source.page_start });
  return t('chat.sources.pageRange', { start: source.page_start, end: source.page_end });
}

export function formatVersion(source: Source, t: Translator = translateDefault): string {
  return source.document_version != null ? `v${source.document_version}` : t('chat.sources.none');
}

/** Never omitted, even for `null` — see this component's own docstring for
 * why `null` (a pre-Collections legacy document) must read as an honest,
 * explicit "ohne Collection" rather than silently dropping the field. */
export function formatCollection(source: Source, t: Translator = translateDefault): string {
  return source.collection ?? t('chat.sources.noCollection');
}

/**
 * Renders the chunks that actually backed one answer, clearly as evidence
 * cards — not folded into the answer's own running text — so a reader
 * always knows which parts of the reply are grounded in a document vs.
 * the model's own words.
 *
 * Shows each source's `collection` (Weave-Runtime's own `Source.collection`,
 * backend/app/schemas/chat.py, verified directly against that module, not
 * guessed) honestly, including the `null` case ("ohne Collection") rather
 * than omitting the field for those sources — see that field's own
 * docstring for why `null` is a legitimate, expected value (a
 * pre-Collections legacy document) and not a sign of missing data.
 */
export function SourceCards({ sources }: { sources: Source[] }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  if (sources.length === 0) return null;

  const images = Array.from(new Set(sources.flatMap((source) => source.images ?? [])));

  return (
    <div className="mt-3">
      {images.length > 0 ? (
        <div className="mb-2">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
            {t('chat.sources.imagesLabel')}
          </p>
          <div className="flex flex-wrap gap-2">
            {images.map((image) => (
              // eslint-disable-next-line @next/next/no-img-element -- proxied, per-message remote image, not a static asset
              <img
                key={image}
                src={toProxiedImageUrl(image)}
                alt=""
                className="h-20 w-20 rounded-[var(--radius-control)] border border-[var(--line)] object-cover"
              />
            ))}
          </div>
        </div>
      ) : null}
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
        className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)] hover:text-[var(--ink)]"
      >
        <FileText className="h-3.5 w-3.5" aria-hidden="true" />
        {t('chat.sourceCards.toggle', { count: sources.length })}
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>
      {expanded ? <ul className="flex flex-col gap-1.5">
        {sources.map((source) => (
          <li
            key={`${source.document_id}-${source.chunk_id}`}
            className="rounded-[var(--radius-card)] border border-[var(--line)] bg-[var(--surface-2)] p-4 text-xs"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
              <span className="font-medium text-[var(--ink)]">
                {source.original_filename ?? t('chat.sources.documentFallback', { id: source.document_id })}
              </span>
              <span className="text-[var(--muted)]">
                {formatPages(source, t)} · {formatVersion(source, t)}
                {source.score != null ? ` · ${t('chat.sources.score', { value: source.score.toFixed(2) })}` : ''}
              </span>
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-[var(--muted)]">
              {source.source ? <span>{t('chat.sourceCards.sourceLabel', { value: source.source })}</span> : null}
              <span>{t('chat.sources.collectionLabel', { collection: formatCollection(source, t) })}</span>
            </div>
          </li>
        ))}
      </ul> : null}
    </div>
  );
}
