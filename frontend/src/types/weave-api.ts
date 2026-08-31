/**
 * Shapes copied field-for-field from Weave-API / Weave-Runtime's own
 * schemas — never guessed from README prose. Sources, in order of truth:
 *
 * - Bot:            Weave-Runtime backend/app/schemas/bot.py `BotSummary`
 *                    (served verbatim by Weave-API's GET /v1/bots).
 * - Collection:     Weave-Retrieval's `CollectionOut`, served verbatim by
 *                    Weave-API's GET /v1/collections (see that route's own
 *                    docstring in Weave-API backend/app/api/collections.py).
 * - Source/*Trace:  Weave-Runtime backend/app/schemas/chat.py, and
 *                    Weave-Runtime's contracts/internal-chat.md (the
 *                    accepted contract Weave-API's own chat routes forward
 *                    close to verbatim — see backend/app/api/chat.py there).
 * - ChatStreamEvent: contracts/internal-chat.md's five SSE event types.
 * - ChatResponseBody: Weave-API backend/app/schemas/chat.py `ChatResponse`.
 *
 * Deliberately isomorphic (no server-only / next imports) — used by Route
 * Handlers, client components, and plain unit tests alike.
 */

export interface Bot {
  id: string;
  name: string;
  description: string | null;
  retrieval: { enabled: boolean };
}

/** Weave-Retrieval's CollectionOut, unchanged. `public` collections are
 * readable by anyone regardless of team. */
export interface Collection {
  slug: string;
  name: string;
  description: string | null;
  public: boolean;
}

/**
 * One chunk actually handed to the LLM for this turn — Weave-Runtime's own
 * `Source` schema (backend/app/schemas/chat.py), verified directly against
 * that module, not guessed from README prose.
 */
export interface Source {
  /** Origin system, e.g. "confluence"; "" if none was reported. */
  source: string;
  original_filename: string | null;
  page_start: number | null;
  page_end: number | null;
  document_version: number | null;
  document_id: string;
  chunk_id: number;
  /** Rerank score if available, else the plain retrieval (RRF) score. */
  score: number | null;
  /**
   * The collection this chunk belongs to; `null` for a pre-Collections
   * legacy document with no collection at all. Security-relevant for an
   * n8n-provider bot's turn in particular: Weave-Runtime's own docstring
   * for this field (Source.collection, backend/app/schemas/chat.py) notes
   * that for THAT turn kind, this value is an unverified claim from the
   * n8n flow, checked against the turn's delegation-token scope only
   * server-side — a source that failed that check is dropped before ever
   * reaching here (see `N8nTrace.dropped_sources` on `ChatTrace.n8n`
   * below). By the time a `collection` value reaches this UI it has
   * already passed that check either way, so it is safe to display
   * verbatim, honestly including `null` rather than hiding the field.
   */
  collection: string | null;
}

export interface RetrievalTrace {
  candidates: number;
  used: number;
  /** Collection slugs actually authorized for this call's search; may
   * contain the sentinel "__none__" for uncollected legacy documents. */
  collections: string[];
}

export interface GuardTrace {
  triggered: boolean;
  /**
   * "no_context": retrieval ran but found nothing usable.
   * "no_collections": caller/bot share no readable collection at all —
   * retrieval never even ran.
   */
  reason: 'no_context' | 'no_collections' | null;
}

/**
 * Diagnostics for an n8n-provider bot's turn (Weave-Runtime's own
 * `N8nTrace`, backend/app/schemas/chat.py) — present on `ChatTrace.n8n`
 * only for a turn that actually reached n8n at all; `null` covers every
 * turn that never did (any non-n8n bot, or an n8n-provider bot's turn that
 * never got that far — conversational smalltalk, guard-triggered, etc.).
 *
 * `dropped_sources` is, in Weave-Runtime's own words, "the one
 * security-relevant counter this pipeline has reason to surface": how many
 * of the sources an n8n flow reported were DISCARDED because their
 * `collection` (see `Source.collection` above) fell outside the exact
 * scope signed into that turn's delegation token. `0` is the ordinary
 * case. A nonzero value is a concrete signal that this bot's n8n flow is
 * reporting sources it was never actually granted — misconfigured, buggy,
 * or worse — and must be surfaced plainly, not folded into an incidental
 * count.
 */
export interface N8nTrace {
  dropped_sources: number;
}

export interface ChatTrace {
  intent: string;
  confidence: number;
  needs_retrieval: boolean;
  needs_tool: boolean;
  retrieval: RetrievalTrace | null;
  model: string | null;
  router_mode: string;
  timings_ms: Record<string, number>;
  guard: GuardTrace | null;
  /** `null` for every turn that never reached n8n — see `N8nTrace` above. */
  n8n: N8nTrace | null;
}

/** POST /v1/chat/stream event union (text/event-stream, one per `data:`
 * line), forwarded close to verbatim by Weave-API. Order is always
 * `trace` → (`delta`)* → (`sources` → `done`) | `error`. */
export type ChatStreamEvent =
  | { type: 'trace'; trace: ChatTrace }
  | { type: 'delta'; text: string }
  | { type: 'sources'; sources: Source[] }
  | { type: 'done' }
  | { type: 'error'; detail: string };

/** POST /v1/chat (non-streaming) response body. */
export interface ChatResponseBody {
  conversation_id: string;
  answer: string;
  sources: Source[] | null;
  trace: ChatTrace | null;
}

/** POST /v1/chat and /v1/chat/stream request body (this UI never sends
 * `context` — Weave-API's README mentions it as accepted input, but no
 * schema field or downstream use of it exists in the actual code read for
 * this task, so nothing is invented here). */
export interface ChatRequestBody {
  bot_id: string;
  message: string;
  conversation_id?: string;
}
