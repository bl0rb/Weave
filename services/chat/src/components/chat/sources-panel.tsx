'use client';

import { ExternalLink, Lock } from 'lucide-react';
import { formatCollection, formatPages, formatScore, formatVersion, getSourceLink, SourceOrigin } from '@/components/chat/source-cards';
import { toProxiedImageUrl } from '@/lib/portal-artifact-url';
import { useI18n } from '@/i18n/provider';
import type { Source } from '@/types/weave-api';

interface SourcesPanelProps {
  /** The sources for the currently selected answer (default: the latest
   * one — see chat-app.tsx), or `null` while none is selected yet / the
   * selected answer used no retrieval at all. Distinct from `[]`, which
   * means retrieval ran for that answer and found nothing. */
  sources: Source[] | null;
  /** The knowledge-space scope the shown answer's retrieval actually ran
   * against, for the trust card at the bottom — derived from that
   * answer's own trace (see chat-app.tsx's `sourcesScopeLabel`). */
  scopeLabel: string;
}

/**
 * The right-hand column showing the evidence behind ONE answer at a time
 * (see chat-app.tsx's `selectedSourceMessageId`) — hidden below 1180px in
 * favour of the inline per-answer `SourceCards` (see globals.css's
 * `.chat-sources-panel`/`.chat-inline-sources`), same as the design
 * prototype (docs/design-proposals/06-loom-rc/style.css).
 */
export function SourcesPanel({ sources, scopeLabel }: SourcesPanelProps) {
  const { t } = useI18n();
  const images = Array.from(new Set((sources ?? []).flatMap((source) => source.images ?? [])));

  return (
    <aside
      className="chat-sources-panel min-h-0 flex-col gap-4 overflow-y-auto border-l border-[var(--line)] bg-[var(--surface)] p-4"
      aria-labelledby="chat-sources-panel-title"
    >
      <header>
        <h2 id="chat-sources-panel-title" className="text-sm font-semibold">
          {t('chat.sourcesPanel.title')}
        </h2>
        <p className="mt-0.5 text-xs text-[var(--muted)]">{t('chat.sourcesPanel.subtitle')}</p>
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
            const meta = [source.document_version != null ? formatVersion(source, t) : null, t('chat.sources.collectionLabel', { collection: formatCollection(source, t) }), scoreText]
              .filter(Boolean)
              .join(' · ');
            return (
              <li
                key={`${source.document_id}-${source.chunk_id}`}
                className="rounded-[var(--radius-card)] border border-[var(--line)] px-3 py-2.5 text-xs"
              >
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 inline-grid h-5 w-5 flex-none place-items-center rounded-[var(--radius-control)] border border-[var(--line)] text-[11px] font-bold text-[var(--accent)]">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <strong className="block truncate text-[13px] font-semibold text-[var(--ink)]">
                      {source.original_filename ?? t('chat.sources.documentFallback', { id: source.document_id })}
                    </strong>
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

      <div className="mt-auto rounded-[var(--radius-card)] bg-[var(--accent-soft)] p-4 text-[13px] text-[var(--ink)]">
        <h3 className="mb-1.5 flex items-center gap-1.5 text-[13px] font-semibold">
          <Lock className="h-4 w-4" aria-hidden="true" />
          {t('chat.sourcesPanel.trustTitle')}
        </h3>
        <p className="text-[var(--muted)]">{t('chat.sourcesPanel.trustBody')}</p>
        <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-t border-[var(--line)] pt-2.5">
          <dt className="text-[var(--muted)]">{t('chat.sourcesPanel.scopeDt')}</dt>
          <dd className="font-medium">{scopeLabel}</dd>
        </dl>
      </div>
    </aside>
  );
}
