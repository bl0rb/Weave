'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, ShieldAlert } from 'lucide-react';
import type { ChatTrace } from '@/types/weave-api';

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-md bg-[var(--surface-muted)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--foreground-muted)]">
      {children}
    </span>
  );
}

/**
 * The debug trace, collapsed by default but always available per
 * assistant turn — exactly the fields Weave-Runtime's own `ChatTrace`
 * carries (contracts/internal-chat.md), nothing summarized away: which
 * intent the router picked and how confident it was, whether/how much
 * retrieval actually ran and against which collections, guard state, the
 * model used, per-step timings, and (for an n8n-provider bot's turn)
 * `n8n.dropped_sources` — see that field's own render below for why it
 * gets a warning treatment instead of just another tag.
 */
export function TracePanel({ trace }: { trace: ChatTrace }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-3 border-t border-[var(--border)] pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs font-medium text-[var(--foreground-muted)] hover:text-[var(--foreground)]"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />}
        Trace
      </button>

      {open ? (
        <div className="mt-2 flex flex-col gap-2 text-xs text-[var(--foreground-muted)]">
          <div className="flex flex-wrap gap-1.5">
            <Tag>Intent: {trace.intent}</Tag>
            <Tag>Konfidenz: {(trace.confidence * 100).toFixed(0)}%</Tag>
            <Tag>Router: {trace.router_mode}</Tag>
            <Tag>Retrieval nötig: {trace.needs_retrieval ? 'ja' : 'nein'}</Tag>
            <Tag>Tool nötig: {trace.needs_tool ? 'ja' : 'nein'}</Tag>
            {trace.model ? <Tag>Modell: {trace.model}</Tag> : null}
          </div>

          {trace.retrieval ? (
            <div>
              <span className="font-medium">Retrieval: </span>
              {trace.retrieval.used}/{trace.retrieval.candidates} Treffer verwendet
              {trace.retrieval.collections.length > 0 ? (
                <> · durchsucht: {trace.retrieval.collections.join(', ')}</>
              ) : (
                <> · keine Collection durchsucht</>
              )}
            </div>
          ) : (
            <div>Kein Retrieval-Aufruf für diesen Turn.</div>
          )}

          {trace.guard?.triggered ? (
            <div>
              <span className="font-medium">Guard: </span>
              ausgelöst ({trace.guard.reason ?? 'unbekannter Grund'})
            </div>
          ) : null}

          {trace.n8n ? (
            trace.n8n.dropped_sources > 0 ? (
              // Weave-Runtime's own docstring for this counter calls it
              // "the one security-relevant counter this pipeline has
              // reason to surface": a nonzero value means the n8n flow
              // reported sources outside its own delegation-token scope,
              // which Weave already discarded — this must read as a
              // warning, not an incidental number next to the other tags.
              <div className="flex items-start gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning-soft)] px-3 py-2 text-[var(--warning)]">
                <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
                <span>
                  <span className="font-medium">{trace.n8n.dropped_sources} Quelle(n) verworfen.</span>{' '}
                  Der n8n-Flow dieses Bots hat Quellen außerhalb seines erlaubten Collection-Umfangs gemeldet;
                  Weave hat sie verworfen, bevor sie diese Antwort erreichen konnten. Das deutet auf eine
                  fehlerhafte oder kompromittierte n8n-Konfiguration hin.
                </span>
              </div>
            ) : (
              <div>
                <span className="font-medium">n8n: </span>
                keine Quellen außerhalb des erlaubten Umfangs verworfen
              </div>
            )
          ) : null}

          {Object.keys(trace.timings_ms).length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(trace.timings_ms).map(([key, ms]) => (
                <Tag key={key}>
                  {key}: {ms.toFixed(0)} ms
                </Tag>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
