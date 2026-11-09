// @vitest-environment jsdom
//
// Covers FINDING 3: `ChatTrace.n8n` must reach the UI, and a nonzero
// `dropped_sources` must render as a clearly-flagged warning, not an
// incidental number. See vitest.config.ts's own docstring for why this
// file opts into jsdom via a per-file pragma while the rest of the suite
// stays plain Node.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { TracePanel } from '@/components/chat/trace-panel';
import type { ChatTrace, RetrievalHit } from '@/types/weave-api';

function makeTrace(overrides: Partial<ChatTrace>): ChatTrace {
  return {
    intent: 'knowledge',
    confidence: 0.9,
    needs_retrieval: true,
    needs_tool: false,
    retrieval: null,
    model: 'gpt-4o',
    router_mode: 'rules',
    timings_ms: {},
    guard: null,
    n8n: null,
    agent: null,
    ...overrides,
  };
}

function renderOpen(trace: ChatTrace) {
  render(<TracePanel trace={trace} />);
  fireEvent.click(screen.getByRole('button', { name: /Trace/ }));
}

describe('TracePanel', () => {
  afterEach(() => cleanup());

  it('renders nothing n8n-related for a turn that never reached n8n (n8n: null)', () => {
    renderOpen(makeTrace({}));
    expect(screen.queryByText(/n8n/)).toBeNull();
  });

  it('shows a plain line when no sources were dropped', () => {
    renderOpen(makeTrace({ n8n: { dropped_sources: 0 } }));
    expect(screen.getByText(/keine Quellen außerhalb des erlaubten Umfangs verworfen/)).toBeTruthy();
  });

  it('flags a nonzero dropped_sources count as a warning with a German explanation, not just a number', () => {
    renderOpen(makeTrace({ n8n: { dropped_sources: 3 } }));
    const warning = screen.getByText(/3 Quelle\(n\) verworfen\./);
    expect(warning).toBeTruthy();
    // Rendered in the warning-styled container, not a plain <Tag>.
    expect(warning.closest('[class*="warning"]')).not.toBeNull();
    expect(screen.getByText(/außerhalb seines erlaubten Collection-Umfangs/)).toBeTruthy();
  });

  it('renders per-subagent tags for a single-mode agent turn, without the graph-only followup/budget tag', () => {
    renderOpen(
      makeTrace({
        agent: {
          mode: 'single',
          subagents: [{ id: 'it-support', status: 'complete', searches_used: 2, hits: 5 }],
          budget: 0,
          plan: null,
          followups: 0,
          budget_used: 0,
        },
      })
    );
    expect(screen.getByText(/Agent \(single\)/)).toBeTruthy();
    expect(screen.getByText(/it-support: complete · 2 Suchen · 5 Treffer/)).toBeTruthy();
    expect(screen.queryByText(/Folgerunde/)).toBeNull();
  });

  it('renders the graph-mode followup/budget tag alongside per-subagent tags', () => {
    renderOpen(
      makeTrace({
        agent: {
          mode: 'graph',
          subagents: [
            { id: 'it-support', status: 'complete', searches_used: 2, hits: 5 },
            { id: 'hr', status: 'failed', searches_used: 1, hits: 0 },
          ],
          budget: 10,
          plan: null,
          followups: 2,
          budget_used: 7,
        },
      })
    );
    expect(screen.getByText(/Agent \(graph\)/)).toBeTruthy();
    expect(screen.getByText(/it-support: complete · 2 Suchen · 5 Treffer/)).toBeTruthy();
    expect(screen.getByText(/hr: failed · 1 Suchen · 0 Treffer/)).toBeTruthy();
    expect(screen.getByText(/2 Folgerunde\(n\) · Budget 7\/10/)).toBeTruthy();
  });
  it('renders older retrieval traces without the new optional fields unchanged', () => {
    renderOpen(makeTrace({ retrieval: { candidates: 4, used: 2, collections: ['legal'] } }));
    expect(screen.getByText(/2\/4 Treffer verwendet/)).toBeTruthy();
    expect(screen.queryByText(/top_k/)).toBeNull();
    expect(screen.queryByText(/Suche:/)).toBeNull();
    expect(screen.queryByText(/Treffer:/)).toBeNull();
  });

  it('renders search params, search stats and a compact hit list with formatted scores', () => {
    const hit: RetrievalHit = {
      rank: 1,
      document_id: 'doc-1',
      chunk_id: 11,
      original_filename: 'handbuch.pdf',
      title: 'Handbuch',
      collection: 'legal',
      heading_path: ['Kapitel 1', 'Urlaub'],
      page_start: 3,
      page_end: 4,
      document_version: 2,
      chunk_index: 5,
      language: 'de',
      document_type: 'policy',
      quality_grade: 'A',
      scores: { vector: 0.712, fulltext: 0.1234, rrf: 0.03125, rerank: 0.9187 },
      used: true,
    };
    const dropped: RetrievalHit = {
      ...hit,
      rank: 2,
      chunk_id: 12,
      original_filename: 'alt.pdf',
      title: null,
      heading_path: [],
      page_start: 7,
      page_end: 7,
      document_version: null,
      scores: { vector: 0.5, fulltext: null, rrf: 0.016, rerank: null },
      used: false,
    };
    renderOpen(
      makeTrace({
        retrieval: {
          candidates: 2,
          used: 1,
          collections: ['legal'],
          top_k: 20,
          final_k: 5,
          rerank: true,
          search: {
            vector_candidates: 20,
            fulltext_candidates: 12,
            fused: 25,
            reranked: 25,
            returned: 2,
            rerank_error: true,
            rerank_provider: 'cohere',
            rerank_model: 'rerank-v3',
            embedding_model: 'text-embedding-3-small',
            rrf_k: 60,
            semantic_weight: 1,
            lexical_weight: 1,
            timings_ms: {},
          },
          hits: [hit, dropped],
        },
      })
    );
    expect(screen.getByText('top_k: 20')).toBeTruthy();
    expect(screen.getByText('final_k: 5')).toBeTruthy();
    expect(screen.getByText('Rerank: an')).toBeTruthy();
    expect(screen.getByText(/Vektor 20 · Volltext 12 · fusioniert 25 · gererankt 25 · zurückgegeben 2/)).toBeTruthy();
    expect(screen.getByText('Reranker: cohere/rerank-v3')).toBeTruthy();
    expect(screen.getByText('Embedding: text-embedding-3-small')).toBeTruthy();
    expect(screen.getByText(/Reranking ist fehlgeschlagen/).closest('[class*="warning"]')).not.toBeNull();

    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain('#1 handbuch.pdf · Handbuch · Kapitel 1 › Urlaub · S. 3–4 · v2');
    expect(items[0].textContent).toContain('Vektor 0.71 · Volltext 0.12 · RRF 0.031 · Rerank 0.92');
    expect(items[0].getAttribute('data-used')).toBe('true');
    expect(items[1].textContent).toContain('#2 alt.pdf · S. 7');
    expect(items[1].textContent).toContain('Vektor 0.50 · RRF 0.016');
    expect(items[1].textContent).not.toContain('Volltext');
    expect(items[1].textContent).toContain('nicht verwendet');
    expect(items[1].className).toContain('opacity-60');
  });

  it('shows rerank off and omits reranker tag when no provider ran', () => {
    renderOpen(
      makeTrace({
        retrieval: { candidates: 0, used: 0, collections: ['legal'], top_k: 3, final_k: 3, rerank: false, hits: [] },
      })
    );
    expect(screen.getByText('Rerank: aus')).toBeTruthy();
    expect(screen.queryByText(/Reranker:/)).toBeNull();
  });
});
