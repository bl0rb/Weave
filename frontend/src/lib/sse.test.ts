import { describe, expect, it } from 'vitest';
import { parseSseStream, streamFromChunks } from '@/lib/sse';
import type { ChatStreamEvent } from '@/types/weave-api';

async function collect(chunks: string[]): Promise<ChatStreamEvent[]> {
  const events: ChatStreamEvent[] = [];
  for await (const event of parseSseStream(streamFromChunks(chunks))) {
    events.push(event);
  }
  return events;
}

describe('parseSseStream', () => {
  it('parses a full trace → delta* → sources → done turn from one chunk per frame', async () => {
    const events = await collect([
      'data: {"type":"trace","trace":{"intent":"knowledge","confidence":0.9,"needs_retrieval":true,"needs_tool":false,"retrieval":{"candidates":2,"used":2,"collections":["legal-2026"]},"model":"gpt","router_mode":"rules","timings_ms":{"router_ms":1.2},"guard":null}}\n\n',
      'data: {"type":"delta","text":"Laut "}\n\n',
      'data: {"type":"delta","text":"Vertrag..."}\n\n',
      'data: {"type":"sources","sources":[{"source":"confluence","original_filename":"AV.pdf","page_start":3,"page_end":4,"document_version":2,"document_id":"doc-1","chunk_id":7,"score":0.87}]}\n\n',
      'data: {"type":"done"}\n\n',
    ]);

    expect(events.map((e) => e.type)).toEqual(['trace', 'delta', 'delta', 'sources', 'done']);
    const deltas = events.filter((e): e is Extract<ChatStreamEvent, { type: 'delta' }> => e.type === 'delta');
    expect(deltas.map((d) => d.text).join('')).toBe('Laut Vertrag...');
  });

  it('reassembles a single event split arbitrarily across many chunk boundaries', async () => {
    const frame = 'data: {"type":"delta","text":"hello world"}\n\n';
    // Split into single-character chunks — the parser must buffer across
    // reads, never assuming one `read()` carries a whole line or frame.
    const chunks = frame.split('');
    const events = await collect(chunks);
    expect(events).toEqual([{ type: 'delta', text: 'hello world' }]);
  });

  it('yields a terminal error event and stops', async () => {
    const events = await collect(['data: {"type":"trace","trace":{"intent":"knowledge","confidence":0.6,"needs_retrieval":true,"needs_tool":false,"retrieval":null,"model":null,"router_mode":"rules","timings_ms":{}}}\n\n', 'data: {"type":"error","detail":"LLM provider timed out"}\n\n']);
    expect(events).toHaveLength(2);
    expect(events[1]).toEqual({ type: 'error', detail: 'LLM provider timed out' });
  });

  it('an abruptly closed stream (no done, no error) just ends — no throw, no fabricated terminal event', async () => {
    const events = await collect([
      'data: {"type":"trace","trace":{"intent":"knowledge","confidence":0.8,"needs_retrieval":true,"needs_tool":false,"retrieval":null,"model":null,"router_mode":"rules","timings_ms":{}}}\n\n',
      'data: {"type":"delta","text":"partial answer"}\n\n',
      // connection drops here — no `sources`, no `done`, no `error`
    ]);
    expect(events.map((e) => e.type)).toEqual(['trace', 'delta']);
  });

  it('flushes a final frame that arrives without its trailing blank line', async () => {
    // A drop mid-frame can still leave a syntactically complete `data:`
    // line with no \n\n after it.
    const events = await collect(['data: {"type":"delta","text":"still readable"}\n']);
    expect(events).toEqual([{ type: 'delta', text: 'still readable' }]);
  });

  it('skips one malformed frame instead of aborting the whole stream', async () => {
    const events = await collect([
      'data: not valid json at all\n\n',
      'data: {"type":"delta","text":"after the bad frame"}\n\n',
    ]);
    expect(events).toEqual([{ type: 'delta', text: 'after the bad frame' }]);
  });

  it('ignores an event whose type is not one of the five known kinds', async () => {
    const events = await collect(['data: {"type":"ping"}\n\n', 'data: {"type":"done"}\n\n']);
    expect(events).toEqual([{ type: 'done' }]);
  });
});
