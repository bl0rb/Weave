'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, ShieldAlert } from 'lucide-react';
import { useI18n } from '@/i18n/provider';
import type { ChatTrace, RetrievalHit, RetrievalTrace } from '@/types/weave-api';

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-[var(--radius-control)] bg-[var(--surface-2)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--muted)]">
      {children}
    </span>
  );
}

type Translate = ReturnType<typeof useI18n>['t'];

function formatPage(hit: RetrievalHit): string | null {
  if (hit.page_start == null) return null;
  if (hit.page_end != null && hit.page_end !== hit.page_start) return `${hit.page_start}–${hit.page_end}`;
  return String(hit.page_start);
}

const SCORE_DIGITS: Record<keyof RetrievalHit['scores'], number> = { vector: 2, fulltext: 2, rrf: 3, rerank: 2 };

function formatScores(hit: RetrievalHit, t: Translate): string {
  return (Object.keys(SCORE_DIGITS) as (keyof RetrievalHit['scores'])[])
    .filter((key) => hit.scores?.[key] != null)
    .map((key) => `${t(`chat.trace.hit.score.${key}`)} ${(hit.scores[key] as number).toFixed(SCORE_DIGITS[key])}`)
    .join(' · ');
}

/**
 * The retrieval call's own search parameters, Weave-Retrieval's search
 * diagnostics and a compact per-hit list (rank, source, heading path, page,
 * version, scores — never chunk text). Every field is optional: traces
 * persisted before these existed simply render nothing extra.
 */
function RetrievalDetails({ retrieval, t }: { retrieval: RetrievalTrace; t: Translate }) {
  const search = retrieval.search;
  const hits = retrieval.hits ?? [];
  const hasParams = retrieval.top_k != null || retrieval.final_k != null || retrieval.rerank != null;
  return (
    <>
      {hasParams ? (
        <div className="flex flex-wrap gap-1.5">
          {retrieval.top_k != null ? <Tag>{t('chat.trace.retrieval.topK', { value: retrieval.top_k })}</Tag> : null}
          {retrieval.final_k != null ? <Tag>{t('chat.trace.retrieval.finalK', { value: retrieval.final_k })}</Tag> : null}
          {retrieval.rerank != null ? (
            <Tag>{retrieval.rerank ? t('chat.trace.retrieval.rerankOn') : t('chat.trace.retrieval.rerankOff')}</Tag>
          ) : null}
        </div>
      ) : null}

      {search ? (
        <div className="flex flex-col gap-1">
          <div>
            <span className="font-medium">{t('chat.trace.search.label')}</span>
            {t('chat.trace.search.stats', {
              vector: search.vector_candidates,
              fulltext: search.fulltext_candidates,
              fused: search.fused,
              reranked: search.reranked,
              returned: search.returned,
            })}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {search.rerank_provider ? (
              <Tag>
                {t('chat.trace.search.reranker', {
                  value: search.rerank_model ? `${search.rerank_provider}/${search.rerank_model}` : search.rerank_provider,
                })}
              </Tag>
            ) : null}
            {search.embedding_model ? <Tag>{t('chat.trace.search.embedding', { value: search.embedding_model })}</Tag> : null}
          </div>
          {search.rerank_error ? (
            <div className="warning-callout flex items-start gap-2 rounded-[var(--radius-control)] border border-[var(--warn)]/30 bg-[var(--warn-bg)] px-3 py-2 text-[var(--warn)]">
              <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
              <span>{t('chat.trace.search.rerankError')}</span>
            </div>
          ) : null}
        </div>
      ) : null}

      {hits.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className="font-medium">{t('chat.trace.hits.label')}</span>
          <ol className="flex flex-col gap-1">
            {hits.map((hit) => {
              const name = hit.original_filename ?? hit.title ?? hit.document_id;
              const page = formatPage(hit);
              const scores = formatScores(hit, t);
              const parts = [
                hit.title && hit.title !== name ? hit.title : null,
                hit.heading_path?.length ? hit.heading_path.join(' › ') : null,
                page ? t('chat.trace.hit.page', { page }) : null,
                hit.document_version != null ? t('chat.trace.hit.version', { version: hit.document_version }) : null,
              ].filter(Boolean);
              return (
                <li
                  key={`${hit.rank}-${hit.chunk_id}`}
                  data-used={hit.used ? 'true' : 'false'}
                  className={hit.used ? 'text-[var(--ink)]' : 'opacity-60'}
                >
                  <span className="font-medium">#{hit.rank}</span> {name}
                  {parts.length > 0 ? <> · {parts.join(' · ')}</> : null}
                  {scores ? <span className="block text-[var(--muted)]">{scores}</span> : null}
                  {!hit.used ? <span className="block italic">{t('chat.trace.hit.unused')}</span> : null}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </>
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

          {trace.retrieval ? <RetrievalDetails retrieval={trace.retrieval} t={t} /> : null}

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
