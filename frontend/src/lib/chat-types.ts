import type { ChatRequestBody, ChatTrace, Source } from '@/types/weave-api';
import type { MappedError } from '@/lib/errors';

/** One transcript entry as the UI renders it — a superset of what any
 * single Weave-API response carries, since an assistant entry accumulates
 * across several SSE events before it is "finished". */
export interface UiMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** True while an assistant entry is still receiving `delta` events. */
  streaming: boolean;
  sources: Source[] | null;
  trace: ChatTrace | null;
  /** Set once the streaming/non-streaming call for this turn failed or
   * was cut short — rendered as a distinct banner, not disguised as a
   * normal finished answer. */
  error: MappedError | null;
  /** True when this answer came from the non-streaming POST /v1/chat
   * fallback (used once the stream itself could not be reached) rather
   * than word-by-word. */
  viaFallback: boolean;
}

export function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function userMessage(content: string): UiMessage {
  return { id: newId(), role: 'user', content, streaming: false, sources: null, trace: null, error: null, viaFallback: false };
}

export function pendingAssistantMessage(): UiMessage {
  return { id: newId(), role: 'assistant', content: '', streaming: true, sources: null, trace: null, error: null, viaFallback: false };
}

/**
 * Builds the outgoing `ChatRequestBody` for one turn — pulled out of
 * chat-app.tsx so the "empty selection means no filter at all, not an
 * explicit empty one" rule (see `ChatRequestBody.collections`'s own
 * docstring in types/weave-api.ts) is a plain, directly-testable function
 * rather than something only exercisable through a full component render.
 * `collections` is omitted from the returned object entirely whenever
 * `selectedCollections` is empty — never sent as `[]`, which Weave-API's
 * own contract treats as a real, if unusual, "matches nothing" filter.
 */
export function buildChatRequestBody(params: {
  botId: string;
  message: string;
  conversationId: string | null;
  selectedCollections: string[];
}): ChatRequestBody {
  const { botId, message, conversationId, selectedCollections } = params;
  return {
    bot_id: botId,
    message,
    ...(conversationId ? { conversation_id: conversationId } : {}),
    ...(selectedCollections.length > 0 ? { collections: selectedCollections } : {}),
  };
}
