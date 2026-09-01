import { parseSseStream } from '@/lib/sse';
import type { ChatTrace, Source } from '@/types/weave-api';

export interface ChatStreamCallbacks {
  onTrace?: (trace: ChatTrace) => void;
  onDelta?: (text: string) => void;
  onSources?: (sources: Source[]) => void;
}

export type ChatStreamOutcome =
  | { status: 'done' }
  | { status: 'error'; detail: string }
  | { status: 'interrupted' };

/**
 * Drives one `POST /v1/chat/stream` body to completion, calling back into
 * the chat UI as each event arrives and returning how the turn actually
 * ended. Mirrors Weave-API's own three-way ending for this stream (see
 * that service's `_stream_and_persist` docstring):
 *
 * - `done`        — a normal, complete answer.
 * - `error`       — Weave-Runtime's generation failed mid-stream; `detail`
 *                    is the raw upstream text (map it through
 *                    `errorForStreamEvent` before showing it to a user).
 * - `interrupted` — the stream just ended (transport cut, proxy timeout,
 *                    ...) without ever sending `done` or `error`. Whatever
 *                    partial text already arrived via `onDelta` should be
 *                    treated the same way Weave-API itself treats it: not
 *                    persisted/trusted as a finished answer.
 */
export async function consumeChatStream(
  body: ReadableStream<Uint8Array>,
  callbacks: ChatStreamCallbacks
): Promise<ChatStreamOutcome> {
  for await (const event of parseSseStream(body)) {
    switch (event.type) {
      case 'trace':
        callbacks.onTrace?.(event.trace);
        break;
      case 'delta':
        callbacks.onDelta?.(event.text);
        break;
      case 'sources':
        callbacks.onSources?.(event.sources);
        break;
      case 'done':
        return { status: 'done' };
      case 'error':
        return { status: 'error', detail: event.detail };
    }
  }
  // The generator ran out on its own — the connection ended without a
  // terminal event ever arriving.
  return { status: 'interrupted' };
}
