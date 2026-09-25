'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, ShieldAlert } from 'lucide-react';
import { useI18n } from '@/i18n/provider';
import type { ChatTrace } from '@/types/weave-api';

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-[var(--radius-control)] bg-[var(--surface-2)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--muted)]">
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
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-3 border-t border-[var(--line)] pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs font-medium text-[var(--muted)] hover:text-[var(--ink)]"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />}
        {t('chat.trace.toggle')}
      </button>

      {open ? (
        <div className="mt-2 flex flex-col gap-2 text-xs text-[var(--muted)]">
          <div className="flex flex-wrap gap-1.5">
            <Tag>{t('chat.trace.intent', { value: trace.intent })}</Tag>
            <Tag>{t('chat.trace.confidence', { value: (trace.confidence * 100).toFixed(0) })}</Tag>
            <Tag>{t('chat.trace.router', { value: trace.router_mode })}</Tag>
            <Tag>{t('chat.trace.needsRetrieval', { value: trace.needs_retrieval ? t('chat.trace.yes') : t('chat.trace.no') })}</Tag>
            <Tag>{t('chat.trace.needsTool', { value: trace.needs_tool ? t('chat.trace.yes') : t('chat.trace.no') })}</Tag>
            {trace.model ? <Tag>{t('chat.trace.model', { value: trace.model })}</Tag> : null}
          </div>

          {trace.retrieval ? (
            <div>
              <span className="font-medium">{t('chat.trace.retrieval.label')}</span>
              {t('chat.trace.retrieval.hits', { used: trace.retrieval.used, candidates: trace.retrieval.candidates })}
              {trace.retrieval.collections.length > 0 ? (
                <> · {t('chat.trace.retrieval.searched', { collections: trace.retrieval.collections.join(', ') })}</>
              ) : (
                <> · {t('chat.trace.retrieval.noneSearched')}</>
              )}
              {trace.retrieval.requested_collections ? (
                <>
                  {' '}
                  ·{' '}
                  {t('chat.trace.retrieval.ownFilter', {
                    value:
                      trace.retrieval.requested_collections.length > 0
                        ? trace.retrieval.requested_collections.join(', ')
                        : t('chat.trace.retrieval.filterMatchesNone'),
                  })}
                </>
              ) : null}
            </div>
          ) : (
            <div>{t('chat.trace.retrieval.none')}</div>
          )}

          {trace.guard?.triggered ? (
            <div>
              <span className="font-medium">{t('chat.trace.guard.label')}</span>
              {t('chat.trace.guard.triggered', { reason: trace.guard.reason ?? t('chat.trace.guard.unknownReason') })}
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
              <div className="warning-callout flex items-start gap-2 rounded-[var(--radius-control)] border border-[var(--warn)]/30 bg-[var(--warn-bg)] px-3 py-2 text-[var(--warn)]">
                <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
                <span>
                  <span className="font-medium">{t('chat.trace.n8n.dropped', { count: trace.n8n.dropped_sources })}</span>{' '}
                  {t('chat.trace.n8n.droppedExplanation')}
                </span>
              </div>
            ) : (
              <div>
                <span className="font-medium">{t('chat.trace.n8n.label')}</span>
                {t('chat.trace.n8n.noneDropped')}
              </div>
            )
          ) : null}

          {trace.agent ? (
            <div className="flex flex-col gap-1">
              <span className="font-medium">{t('chat.trace.agent.label', { mode: trace.agent.mode })}</span>
              <div className="flex flex-wrap gap-1.5">
                {trace.agent.subagents.map((subagent) => (
                  <Tag key={subagent.id}>
                    {t('chat.trace.agent.subagent', {
                      id: subagent.id,
                      status: subagent.status,
                      searches: subagent.searches_used,
                      hits: subagent.hits,
                    })}
                  </Tag>
                ))}
                {trace.agent.mode === 'graph' ? (
                  <Tag>
                    {t('chat.trace.agent.followups', {
                      count: trace.agent.followups,
                      used: trace.agent.budget_used,
                      budget: trace.agent.budget,
                    })}
                  </Tag>
                ) : null}
              </div>
            </div>
          ) : null}

          {Object.keys(trace.timings_ms).length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(trace.timings_ms).map(([key, ms]) => (
                <Tag key={key}>{t('chat.trace.timing', { key, ms: ms.toFixed(0) })}</Tag>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
