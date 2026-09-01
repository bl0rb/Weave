import type { ChatStreamEvent } from '@/types/weave-api';

/**
 * Parses a `text/event-stream` body shaped exactly like Weave-Runtime's
 * chat stream, forwarded close to verbatim by Weave-API's
 * POST /v1/chat/stream (see contracts/internal-chat.md): one `data: <json>`
 * line per event, a blank line between events, no other SSE fields (no
 * `event:`/`id:` — every event already names its own kind via its own
 * `type` key).
 *
 * Deliberately tolerant of chunk boundaries: a `ReadableStream` gives no
 * guarantee that one `data: ...\n\n` frame arrives whole in a single
 * `read()` — it can be split anywhere, including mid-line or mid-JSON.
 * Buffers across reads and only ever parses once a full block boundary
 * (`\n\n`) has actually arrived, plus a final best-effort flush for
 * whatever partial bytes are left when the stream simply ends.
 *
 * Deliberately does NOT throw on a malformed/unrecognised line or block —
 * one bad frame is skipped rather than aborting the whole turn. Ending
 * without ever yielding a `done` or `error` event (a dropped connection,
 * exactly as Weave-API's own `_stream_and_persist` can end) is not an
 * error at this layer either: the generator just stops. Callers that need
 * to tell "cleanly finished" apart from "cut off" must do so themselves,
 * by which terminal event (if any) they actually saw — see
 * `consumeChatStream` in `run-chat-stream.ts`.
 */
export async function* parseSseStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<ChatStreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let separatorIndex = buffer.indexOf('\n\n');
      while (separatorIndex !== -1) {
        const block = buffer.slice(0, separatorIndex);
        buffer = buffer.slice(separatorIndex + 2);
        const event = parseEventBlock(block);
        if (event) yield event;
        separatorIndex = buffer.indexOf('\n\n');
      }
    }

    // The connection ended — flush one last time in case a final frame
    // arrived without its trailing blank line (a mid-frame drop).
    const trailing = parseEventBlock(buffer);
    if (trailing) yield trailing;
  } finally {
    reader.releaseLock();
  }
}

function parseEventBlock(block: string): ChatStreamEvent | null {
  for (const line of block.split('\n')) {
    if (!line.startsWith('data:')) continue;
    const jsonText = line.slice('data:'.length).trimStart();
    if (!jsonText) continue;
    return parseEventJson(jsonText);
  }
  return null;
}

function parseEventJson(jsonText: string): ChatStreamEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonText);
  } catch {
    return null;
  }
  return isChatStreamEvent(parsed) ? parsed : null;
}

function isChatStreamEvent(value: unknown): value is ChatStreamEvent {
  if (typeof value !== 'object' || value === null || !('type' in value)) return false;
  const type = (value as { type: unknown }).type;
  return type === 'trace' || type === 'delta' || type === 'sources' || type === 'done' || type === 'error';
}

/** Builds a `ReadableStream<Uint8Array>` from plain text chunks — the test
 * double for a fetch `Response.body`. Exported alongside the parser (not
 * hidden in a test file) since it is equally useful for exercising
 * anything else that consumes a streamed fetch body. */
export function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[index]));
      index += 1;
    },
  });
}
