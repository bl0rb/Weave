import type { ChatRequestBody, ChatStreamStatusEvent, ChatTrace, Source, StoredMessage } from '@/types/weave-api';
import type { MappedError } from '@/lib/errors';

/** One subagent's own latest known state within a still-streaming
 * agent-mode turn — the UI's own tiny per-chip reduction of every
 * `status: 'researching'` event seen so far for this turn (see
 * chat-app.tsx's `onStatus` handling). `state: null` means "started, not
 * finished yet". */
export interface UiAgentStatus {
  agentId: string;
  agentName: string;
  state: 'complete' | 'partial' | 'failed' | null;
}

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
  /** True once a streaming turn has gone >30s without a `delta` event —
   * the placeholder switches from "Antwort wird erzeugt…" to "Der
   * Assistent arbeitet noch …" so a long-running n8n agent turn (which
   * can now legitimately take minutes between keepalive-covered idle
   * gaps) doesn't look stuck. Reset to false whenever content starts
   * arriving; see chat-app.tsx's per-turn idle timer. */
  slowResponse: boolean;
  /** The latest agent-mode progress line (Weave-Runtime's `status` event
   * `message`) for this still-streaming turn, or `null` before the first
   * one arrives / once real answer text starts (see chat-app.tsx's
   * `onDelta`, which clears it exactly like `slowResponse`). Rendered in
   * place of the ordinary "Antwort wird erzeugt…" placeholder while an
   * agent-mode turn's research is still running. */
  progressLine: string | null;
  /** Every subagent this turn has reported a status for so far, in
   * first-seen order — rendered as small chips (see message-bubble.tsx).
   * Never cleared mid-turn: a finished agent's chip stays visible
   * alongside a still-researching one. */
  agentStatuses: UiAgentStatus[];
}

export function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function userMessage(content: string): UiMessage {
  return {
    id: newId(), role: 'user', content, streaming: false, sources: null, trace: null, error: null,
    viaFallback: false, slowResponse: false, progressLine: null, agentStatuses: [],
  };
}

export function pendingAssistantMessage(): UiMessage {
  return {
    id: newId(), role: 'assistant', content: '', streaming: true, sources: null, trace: null, error: null,
    viaFallback: false, slowResponse: false, progressLine: null, agentStatuses: [],
  };
}

/** Folds one `status: 'researching'` event into `agentStatuses` — updates
 * that agent's own entry in place if it was already seen this turn
 * (a finish event superseding its own start), else appends a new one.
 * Pulled out of chat-app.tsx so this reduction is directly testable
 * without a full component render. */
export function applyAgentStatus(statuses: UiAgentStatus[], event: ChatStreamStatusEvent): UiAgentStatus[] {
  if (event.stage !== 'researching' || !event.agent_id) return statuses;
  const entry: UiAgentStatus = { agentId: event.agent_id, agentName: event.agent_name ?? event.agent_id, state: event.state };
  const index = statuses.findIndex((status) => status.agentId === entry.agentId);
  if (index === -1) return [...statuses, entry];
  const next = [...statuses];
  next[index] = entry;
  return next;
}

/** Turns one already-persisted `StoredMessage` (GET
 * /v1/conversations/{id}, types/weave-api.ts) into the same `UiMessage`
 * shape a live turn produces — used when the history sidebar loads a
 * past conversation's transcript back into view. Always finished
 * (`streaming: false`, `error: null`): a stored message is by definition
 * one that already completed. */
export function uiMessageFromStored(message: StoredMessage): UiMessage {
  return {
    id: String(message.id),
    role: message.role,
    content: message.content,
    streaming: false,
    sources: message.sources,
    trace: message.trace,
    error: null,
    viaFallback: false,
    slowResponse: false,
    progressLine: null,
    agentStatuses: [],
  };
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
