import { parseSseStream } from '@/lib/sse';
import type { ChatStreamStatusEvent, ChatTrace, Source } from '@/types/weave-api';

export interface ChatStreamCallbacks {
  onTrace?: (trace: ChatTrace) => void;
  onDelta?: (text: string) => void;
  onSources?: (sources: Source[]) => void;
  /** One agent-mode progress line (Weave-Runtime's `status` event) — see
   * `ChatStreamStatusEvent`'s own docstring. Never carries a prompt/query/
   * token; always safe to render `message` verbatim. */
  onStatus?: (event: ChatStreamStatusEvent) => void;
  /** The answer is complete and persisted. The stream may still carry a
   * `title` event after this; the outcome resolves only when it ends. */
  onDone?: () => void;
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
  let done = false;
  for await (const event of parseSseStream(body)) {
    // After `done` only a `title` may still come; anything else is ignored.
    if (done && event.type !== 'title') continue;
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
      case 'status':
        callbacks.onStatus?.(event);
        break;
      case 'done':
        // Keep reading: Weave-API sends the first answer's `title` after
        // `done` and then closes the stream.
        done = true;
        callbacks.onDone?.();
        break;
      case 'title':
        // Nothing to render here -- the conversation list refresh after
        // the turn picks the stored title up.
        break;
      case 'error':
        return { status: 'error', detail: event.detail };
    }
  }
  if (done) return { status: 'done' };
  // The generator ran out on its own — the connection ended without a
  // terminal event ever arriving.
  return { status: 'interrupted' };
}
