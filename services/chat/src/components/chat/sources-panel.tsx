'use client';

import { Lock } from 'lucide-react';
import { formatCollection, formatPages, formatVersion } from '@/components/chat/source-cards';
import { toProxiedImageUrl } from '@/lib/portal-artifact-url';
import type { Source } from '@/types/weave-api';

interface SourcesPanelProps {
  /** The sources for the currently selected answer (default: the latest
   * one — see chat-app.tsx), or `null` while none is selected yet / the
   * selected answer used no retrieval at all. Distinct from `[]`, which
   * means retrieval ran for that answer and found nothing. */
  sources: Source[] | null;
  /** The knowledge-space scope this turn's retrieval actually ran
   * against, for the trust card at the bottom — the composer's own
   * current selection label (see `scopeLabel`), not tied to any one
   * message. */
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
  const images = Array.from(new Set((sources ?? []).flatMap((source) => source.images ?? [])));

  return (
    <aside
      className="chat-sources-panel min-h-0 flex-col gap-4 overflow-y-auto border-l border-[var(--border)] bg-[var(--surface)] p-4"
      aria-labelledby="chat-sources-panel-title"
    >
      <header>
        <h2 id="chat-sources-panel-title" className="text-sm font-semibold">
          Quellen
        </h2>
        <p className="mt-0.5 text-xs text-[var(--foreground-muted)]">Belege der ausgewählten Antwort</p>
      </header>

      {images.length > 0 ? (
        <div>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-[var(--foreground-muted)]">
            Bilder aus den Quellen
          </p>
          <div className="flex flex-wrap gap-2">
            {images.map((image) => (
              // eslint-disable-next-line @next/next/no-img-element -- proxied, per-message remote image, not a static asset
              <img
                key={image}
                src={toProxiedImageUrl(image)}
                alt=""
                className="h-16 w-16 rounded-lg border border-[var(--border)] object-cover"
              />
            ))}
          </div>
        </div>
      ) : null}

      {sources === null ? (
        <p className="rounded-lg border border-dashed border-[var(--border)] px-3 py-6 text-center text-xs text-[var(--foreground-muted)]">
          Belege erscheinen hier, sobald eine Antwort auf Quellen zurückgreift.
        </p>
      ) : sources.length === 0 ? (
        <p className="rounded-lg border border-dashed border-[var(--border)] px-3 py-6 text-center text-xs text-[var(--foreground-muted)]">
          Diese Antwort stützt sich auf keine Quelle.
        </p>
      ) : (
        <ol className="flex flex-col gap-3">
          {sources.map((source, index) => (
            <li
              key={`${source.document_id}-${source.chunk_id}`}
              className="rounded-xl border border-[var(--border)] p-3 text-xs"
            >
              <div className="flex items-start gap-2">
                <span className="mt-0.5 inline-grid h-5 w-5 flex-none place-items-center rounded-md border border-[var(--border)] text-[11px] font-bold text-[var(--accent)]">
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <strong className="block truncate text-[13px] font-semibold text-[var(--foreground)]">
                    {source.original_filename ?? `Dokument ${source.document_id}`}
                  </strong>
                  <span className="text-[var(--foreground-muted)]">
                    {formatPages(source)} · {formatVersion(source)}
                    {source.score != null ? ` · Score ${source.score.toFixed(2)}` : ''}
                  </span>
                </div>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 text-[var(--foreground-muted)]">
                <span>Collection: {formatCollection(source)}</span>
                {source.source ? <span>{source.source}</span> : null}
              </div>
            </li>
          ))}
        </ol>
      )}

      <div className="mt-auto rounded-xl bg-[var(--accent-soft)] p-3.5 text-[13px] text-[var(--foreground)]">
        <h3 className="mb-1.5 flex items-center gap-1.5 text-[13px] font-semibold">
          <Lock className="h-4 w-4" aria-hidden="true" />
          Worauf der Assistent zugreift
        </h3>
        <p className="text-[var(--foreground-muted)]">
          Nur freigegebene und indexierte Dokumente aus Bereichen, die du lesen darfst. Findet er nichts Passendes,
          sagt er das — statt zu raten.
        </p>
        <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-t border-[var(--border)] pt-2.5">
          <dt className="text-[var(--foreground-muted)]">Bereiche</dt>
          <dd className="font-medium">{scopeLabel}</dd>
        </dl>
      </div>
    </aside>
  );
}
