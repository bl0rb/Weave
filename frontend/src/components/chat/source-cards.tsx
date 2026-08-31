import { FileText } from 'lucide-react';
import type { Source } from '@/types/weave-api';

function formatPages(source: Source): string {
  if (source.page_start == null) return '–';
  if (source.page_end == null || source.page_end === source.page_start) return `S. ${source.page_start}`;
  return `S. ${source.page_start}–${source.page_end}`;
}

function formatVersion(source: Source): string {
  return source.document_version != null ? `v${source.document_version}` : '–';
}

/** Never omitted, even for `null` — see this component's own docstring for
 * why `null` (a pre-Collections legacy document) must read as an honest,
 * explicit "ohne Collection" rather than silently dropping the field. */
function formatCollection(source: Source): string {
  return source.collection ?? 'ohne Collection';
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
  if (sources.length === 0) return null;

  return (
    <div className="mt-3">
      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[var(--foreground-muted)]">
        <FileText className="h-3.5 w-3.5" aria-hidden="true" />
        Belege ({sources.length})
      </p>
      <ul className="flex flex-col gap-1.5">
        {sources.map((source) => (
          <li
            key={`${source.document_id}-${source.chunk_id}`}
            className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 text-xs"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
              <span className="font-medium text-[var(--foreground)]">
                {source.original_filename ?? `Dokument ${source.document_id}`}
              </span>
              <span className="text-[var(--foreground-muted)]">
                {formatPages(source)} · {formatVersion(source)}
                {source.score != null ? ` · Score ${source.score.toFixed(2)}` : ''}
              </span>
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-[var(--foreground-muted)]">
              {source.source ? <span>Quelle: {source.source}</span> : null}
              <span>Collection: {formatCollection(source)}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
