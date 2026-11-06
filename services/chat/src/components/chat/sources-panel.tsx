'use client';

import { ExternalLink, PanelRightClose } from 'lucide-react';
import { formatCollection, formatPages, formatScore, formatVersion, getSourceLink, MemoryBadge, SourceOrigin } from '@/components/chat/source-cards';
import { toProxiedImageUrl } from '@/lib/portal-artifact-url';
import { useI18n } from '@/i18n/provider';
import { answerSpaces, spaceColor } from '@/lib/knowledge-spaces';
import { highlightHandlers, isHighlighted, spaceName, spaceStyle, useCitationHighlight } from '@/components/chat/citations';
import type { Source } from '@/types/weave-api';

interface SourcesPanelProps {
  /** The sources for the currently selected answer (default: the latest
   * one — see chat-app.tsx), or `null` while none is selected yet / the
   * selected answer used no retrieval at all. Distinct from `[]`, which
   * means retrieval ran for that answer and found nothing. */
  sources: Source[] | null;
  /** Collapses the panel (chat-app.tsx keeps that choice per browser). */
  onCollapse?: () => void;
  /** The answer these sources belong to: hovering a source marks the
   * passages it backs in that answer. */
  messageId?: string;
}

/**
 * The right-hand column showing the evidence behind ONE answer at a time
 * (see chat-app.tsx's `selectedSourceMessageId`) — hidden below 1180px in
 * favour of the inline per-answer `SourceCards` (see globals.css's
 * `.chat-sources-panel`/`.chat-inline-sources`), same as the design
 * prototype (docs/design-proposals/06-loom-rc/style.css).
 */
export function SourcesPanel({ sources, onCollapse, messageId }: SourcesPanelProps) {
  const { t, locale } = useI18n();
  const highlight = useCitationHighlight();
  const spaces = answerSpaces(sources);
  const mixed = spaces.length > 1;
  const images = Array.from(new Set((sources ?? []).flatMap((source) => source.images ?? [])));

  return (
    <aside
      className="chat-sources-panel min-h-0 flex-col gap-4 overflow-y-auto border-l border-[var(--line)] bg-[var(--surface)] p-4"
      aria-labelledby="chat-sources-panel-title"
    >
      <header className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 id="chat-sources-panel-title" className="text-sm font-semibold">
            {t('chat.sourcesPanel.title')}
          </h2>
          <p className="mt-0.5 text-xs text-[var(--muted)]">{t('chat.sourcesPanel.subtitle')}</p>
        </div>
        {onCollapse ? (
          <button
            type="button"
            onClick={onCollapse}
            aria-label={t('chat.sourcesPanel.collapse')}
            title={t('chat.sourcesPanel.collapse')}
            className="grid h-8 w-8 flex-none place-items-center rounded-[var(--radius-control)] text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
          >
            <PanelRightClose className="h-4 w-4" aria-hidden="true" />
          </button>
        ) : null}
      </header>

      {images.length > 0 ? (
        <div>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
            {t('chat.sources.imagesLabel')}
          </p>
          <div className="flex flex-wrap gap-2">
            {images.map((image) => (
              // eslint-disable-next-line @next/next/no-img-element -- proxied, per-message remote image, not a static asset
              <img
                key={image}
                src={toProxiedImageUrl(image)}
                alt=""
                className="h-16 w-16 rounded-[var(--radius-control)] border border-[var(--line)] object-cover"
              />
            ))}
          </div>
        </div>
      ) : null}

      {sources === null ? (
        <p className="rounded-[var(--radius-card)] border border-dashed border-[var(--line)] px-3 py-6 text-center text-xs text-[var(--muted)]">
          {t('chat.sourcesPanel.emptyUnknown')}
        </p>
      ) : sources.length === 0 ? (
        <p className="rounded-[var(--radius-card)] border border-dashed border-[var(--line)] px-3 py-6 text-center text-xs text-[var(--muted)]">
          {t('chat.sourcesPanel.emptyNone')}
        </p>
      ) : (
        <ol className="flex flex-col gap-2">
          {sources.map((source, index) => {
            const scoreText = formatScore(source, t);
            const link = getSourceLink(source, t);
            // The locator is what a reader needs to find the passage again:
            // the wiki page link, else the page number(s) — the rest is
            // secondary and stays small and muted.
            // Website pages: the fetch date (and a stated legal status) sit
            // next to the link, so a reader sees how current the source is.
            const fetched = source.fetched_at && !Number.isNaN(Date.parse(source.fetched_at))
              ? t('chat.sources.fetchedOn', { date: new Date(source.fetched_at).toLocaleDateString(locale === 'en' ? 'en-GB' : 'de-DE') })
              : null;
            const legal = source.legal_status ? t('chat.sources.legalStatus', { value: source.legal_status }) : null;
            const collection = mixed ? spaceName(highlight, source.collection ?? null, formatCollection(source, t)) : formatCollection(source, t);
            const meta = [source.document_version != null ? formatVersion(source, t) : null, t('chat.sources.collectionLabel', { collection }), scoreText, link ? fetched : null, link ? legal : null]
              .filter(Boolean)
              .join(' · ');
            return (
              <li
                key={`${source.document_id}-${source.chunk_id}`}
                className="source-card rounded-[var(--radius-card)] border border-[var(--line)] px-3 py-2.5 text-xs"
                style={mixed ? spaceStyle(spaceColor(spaces, source)) : undefined}
                data-mixed={mixed}
                data-active={isHighlighted(highlight, messageId, source.ref)}
                tabIndex={messageId && typeof source.ref === 'number' ? 0 : undefined}
                {...highlightHandlers(highlight, messageId, source.ref)}
              >
                <div className="flex items-start gap-2">
                  <span className="source-ref mt-0.5 inline-grid h-5 w-5 flex-none place-items-center rounded-[var(--radius-control)] border border-[var(--line)] text-[11px] font-bold text-[var(--accent)]">
                    {source.ref ?? index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <strong className="block truncate text-[13px] font-semibold text-[var(--ink)]">
                      {source.original_filename ?? t('chat.sources.documentFallback', { id: source.document_id })}
                    </strong>
                    {source.memory_kind ? <div className="mt-0.5"><MemoryBadge source={source} /></div> : null}
                    <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px] text-[var(--muted)]">
                      {link ? (
                        <a
                          href={link.href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--accent)] underline-offset-2 hover:underline"
                        >
                          {link.label}
                          <ExternalLink className="h-3 w-3" aria-hidden="true" />
                        </a>
                      ) : source.page_start != null ? (
                        <span className="text-xs font-semibold text-[var(--ink)]">{formatPages(source, t)}</span>
                      ) : null}
                      <span>{meta}</span>
                      {link ? null : <SourceOrigin source={source} />}
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </aside>
  );
}
