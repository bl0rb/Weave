import { describe, expect, it } from 'vitest';
import { streamFromChunks } from '@/lib/sse';
import { consumeChatStream } from '@/lib/run-chat-stream';
import type { ChatTrace, Source } from '@/types/weave-api';

const SAMPLE_TRACE: ChatTrace = {
  intent: 'knowledge',
  confidence: 0.9,
  needs_retrieval: true,
  needs_tool: false,
  retrieval: { candidates: 1, used: 1, collections: ['legal-2026'] },
  model: 'gpt',
  router_mode: 'rules',
  timings_ms: { router_ms: 1 },
  guard: null,
  // Regel-Router, kein n8n-Turn -- genau der Fall, den `null` abdeckt.
  n8n: null,
};

const SAMPLE_SOURCE: Source = {
  source: 'confluence',
  original_filename: 'AV.pdf',
  page_start: 3,
  page_end: 3,
  document_version: 2,
  document_id: 'doc-1',
  chunk_id: 7,
  score: 0.9,
  // Passt zu retrieval.collections des SAMPLE_TRACE oben.
  collection: 'legal-2026',
};

describe('consumeChatStream', () => {
  it('accumulates multiple deltas and reports a clean "done" outcome', async () => {
    const chunks = [
      `data: ${JSON.stringify({ type: 'trace', trace: SAMPLE_TRACE })}\n\n`,
      `data: ${JSON.stringify({ type: 'delta', text: 'Laut ' })}\n\n`,
      `data: ${JSON.stringify({ type: 'delta', text: 'Vertrag.' })}\n\n`,
      `data: ${JSON.stringify({ type: 'sources', sources: [SAMPLE_SOURCE] })}\n\n`,
      `data: ${JSON.stringify({ type: 'done' })}\n\n`,
    ];

    let text = '';
    let trace: ChatTrace | null = null;
    let sources: Source[] | null = null;

    const outcome = await consumeChatStream(streamFromChunks(chunks), {
      onTrace: (t) => (trace = t),
      onDelta: (t) => (text += t),
      onSources: (s) => (sources = s),
    });

    expect(outcome).toEqual({ status: 'done' });
    expect(text).toBe('Laut Vertrag.');
    expect(trace).toEqual(SAMPLE_TRACE);
    expect(sources).toEqual([SAMPLE_SOURCE]);
  });

  it('reports the in-band error event, with its detail, as the outcome', async () => {
    const chunks = [
      `data: ${JSON.stringify({ type: 'trace', trace: SAMPLE_TRACE })}\n\n`,
      `data: ${JSON.stringify({ type: 'delta', text: 'partial' })}\n\n`,
      `data: ${JSON.stringify({ type: 'error', detail: 'LLM provider timed out' })}\n\n`,
    ];

    let text = '';
    const outcome = await consumeChatStream(streamFromChunks(chunks), { onDelta: (t) => (text += t) });

    expect(outcome).toEqual({ status: 'error', detail: 'LLM provider timed out' });
    // Whatever text streamed in before the error is still visible to the
    // caller via onDelta — it's the OUTCOME that must say "not a real
    // finished answer", not the accumulated text itself.
    expect(text).toBe('partial');
  });

  it('reports "interrupted" when the connection ends with no done/error at all', async () => {
    const chunks = [
      `data: ${JSON.stringify({ type: 'trace', trace: SAMPLE_TRACE })}\n\n`,
      `data: ${JSON.stringify({ type: 'delta', text: 'cut off mid-' })}\n\n`,
      // stream just ends here
    ];

    const outcome = await consumeChatStream(streamFromChunks(chunks), {});
    expect(outcome).toEqual({ status: 'interrupted' });
  });

  it('never calls onSources/onDelta after a terminal event has already ended the turn', async () => {
    // Not a realistic upstream stream (nothing follows `done` per the
    // ordering guarantee), but the consumer must not choke on it either —
    // it stops looking at the very first terminal event.
    const chunks = [
      `data: ${JSON.stringify({ type: 'done' })}\n\n`,
      `data: ${JSON.stringify({ type: 'delta', text: 'should never be seen' })}\n\n`,
    ];

    let sawDelta = false;
    const outcome = await consumeChatStream(streamFromChunks(chunks), { onDelta: () => (sawDelta = true) });
    expect(outcome).toEqual({ status: 'done' });
    expect(sawDelta).toBe(false);
  });
});
