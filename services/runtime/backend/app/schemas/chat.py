"""Request/response shapes for POST /internal/chat (see app/api/internal.py).

The route itself is a 501 placeholder for now -- these schemas exist so
Weave-API (the eventual caller) can integration-test the request/response
contract and the auth layer (app/core/auth.py) before the actual intent
routing / retrieval / LLM / response-guard pipeline (see README's "Zweck")
is filled in during a later stage, exactly like Weave-Retrieval's own
app/schemas/search.py did for that service's `/api/v1/search` route.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal['user', 'assistant']
    content: str


class ChatUser(BaseModel):
    # All three optional: an anonymous/system-initiated chat has no user id,
    # and `team` is what BotConfig.permissions.teams (app/schemas/bot.py) is
    # checked against once bot-selection/permission enforcement is
    # implemented -- unset means the caller didn't propagate a team, not
    # "member of every team".
    id: str | None = None
    team: str | None = None
    teams: list[str] | None = None

    @property
    def effective_teams(self) -> list[str]:
        return list(self.teams) if self.teams is not None else ([self.team] if self.team else [])

    # A human-readable display name, propagated (optionally -- unset is a
    # normal, common case, not just an anonymous chat) alongside `id`/`team`
    # by whatever gateway resolved this caller's identity. Unlike `id`/
    # `team`, no part of THIS service's own pipeline (routing, retrieval,
    # permissions) ever reads it -- it exists purely for
    # app/services/delegation.py's `mint_delegation_token` to embed as the
    # delegation-token payload's own `username` field (see that module's
    # docstring), for an external agent flow (n8n, contracts/n8n-flow.md)
    # to show/log/audit against, since a bare user id is rarely a useful
    # label for a human reading an agent-flow's own execution log.
    username: str | None = None


class ChatRequest(BaseModel):
    bot_id: str
    message: str = Field(min_length=1)
    history: list[ChatMessage] = Field(default_factory=list)
    user: ChatUser = Field(default_factory=ChatUser)
    # Per-request Collections FILTER, never a grant -- see
    # app/services/chat.py's `resolve_collection_scope` docstring for the
    # exact contract. `None` (the default) means "no filter": today's
    # behaviour, byte-for-byte unchanged. A non-`None` list is intersected
    # AFTER this bot+caller's own read-authority has already been resolved
    # (`bot.retrieval.collections ∩ what user.team may read`) -- it can only
    # ever narrow that scope further, never widen it, and a slug named here
    # that already falls outside that scope is silently dropped (never an
    # error, never a hint that the slug even exists). `[]` is a valid,
    # distinct filter of its own -- "match nothing" -- not the same as
    # omitting the field entirely.
    collections: list[str] | None = None


class Source(BaseModel):
    """One retrieved chunk backing an answer. Mirrors the caller-relevant
    fields of Weave-Retrieval's own SearchResult (app/schemas/search.py in
    that service) -- `document_id`/`chunk_id` keep that service's exact
    types (`str`/`int`) since a Source's data IS a SearchResult's, just
    reshaped for a chat response instead of a raw search response.
    """

    source: str
    original_filename: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    document_version: int | None = None
    document_id: str
    chunk_id: int
    score: float | None = None
    # Mirrors Weave-Retrieval's own SearchResult.collection (app/schemas/
    # search.py in that service) -- `None` for a pre-Collections legacy
    # document with no collection at all, exactly like that field. For a
    # retrieval-backed knowledge turn this is filled in by
    # app/services/chat.py's `_to_source` from `RetrievedChunk.collection`
    # (app/services/retrieval_client.py), itself echoing Weave-Retrieval's
    # own field verbatim -- see that client's own docstring.
    #
    # For an n8n-provider bot's turn this field carries whatever an n8n
    # flow's own `sources` entry claims (contracts/n8n-flow.md) -- and
    # UNLIKE a retrieval-backed Source, that claim is never pre-verified by
    # anything on this side before it reaches this model: n8n's flow could
    # be misconfigured, buggy, or compromised, so `_run_n8n_turn` checks
    # every reported source's `collection` against the exact scope signed
    # into that turn's own delegation token AFTER parsing (see that
    # function's own docstring and contracts/n8n-flow.md's "Quellen sind
    # eine Behauptung, keine Berechtigung") -- a source whose `collection`
    # falls outside that scope is dropped before ever reaching a caller,
    # never trusted on the strength of this field alone.
    collection: str | None = None


class RetrievalTrace(BaseModel):
    # candidates: how many chunks Weave-Retrieval returned for this bot's
    # query. used: how many of those actually backed the returned answer
    # (e.g. after the LLM/guard discarded some as irrelevant) -- used <=
    # candidates always.
    candidates: int
    used: int
    # The Collections-contract slugs actually authorized for this call
    # (app/services/chat.py's `resolve_collection_scope`) -- what was sent
    # as Weave-Retrieval's own `SearchRequest.allowed_collections`. `[]`
    # exactly when the guard fired with `reason='no_collections'` (see
    # GuardTrace below) -- `resolve_collection_scope` resolved zero
    # collections the caller may read, so `candidates`/`used` are also both
    # `0` in that case, and no search call was made at all. Never `null`:
    # this pipeline never calls Weave-Retrieval without SOME concrete
    # collection boundary once retrieval actually runs -- see that
    # function's own docstring for why "unrestricted" is never one of this
    # field's values here.
    #
    # May additionally contain `'__none__'` (app/services/chat.py's
    # NO_COLLECTION_SENTINEL, mirroring Weave-Retrieval's identically-named
    # one) alongside any real slugs -- present exactly when
    # `bot.retrieval.include_uncollected` (app/schemas/bot.py, default
    # `True`) kept pre-Collections legacy documents (`Document.
    # collection_slug IS NULL`) in scope for this call. Its presence here is
    # what lets a caller tell, purely from the trace, whether an
    # Altbestand-inclusive search was actually made for this turn.
    collections: list[str] = Field(default_factory=list)
    # `ChatRequest.collections` verbatim, unchanged -- what the CALLER asked
    # to be filtered to for this turn, as opposed to `collections` above
    # (what was actually, effectively searched once that request is
    # intersected with this bot+caller's own read-authority -- see
    # app/services/chat.py's `resolve_collection_scope`). `None` exactly
    # when the caller sent no filter at all, matching `ChatRequest.
    # collections`'s own "None = no filter" default -- distinguishable from
    # `[]`, an explicit "match nothing" filter. A caller can tell from these
    # two fields together whether a filter was requested at all, and -- when
    # `guard.reason == "filter_excluded_all"` -- exactly which filter is
    # responsible for the empty `collections` result.
    requested_collections: list[str] | None = None


class GuardTrace(BaseModel):
    """Whether BotConfig.guard (app/schemas/bot.py) forced a fixed reply
    instead of the LLM's own answer for this turn. Three possible `reason`s:

    - `'no_context'`: retrieval actually ran but returned nothing usable
      (`bot.guard.require_sources` and zero chunks came back). Replies with
      `bot.guard.no_context_reply`.
    - `'no_collections'`: `resolve_collection_scope` (app/services/chat.py)
      found no Collection this caller may read at all that this bot is also
      configured to use -- retrieval never even ran in that case (see
      RetrievalTrace above). Unlike `'no_context'`, this one fires
      regardless of `bot.guard.require_sources`: a retrieval-backed bot must
      never search without a concrete Collections boundary in force, so
      there is nothing for `require_sources` to opt out of here. Also
      replies with `bot.guard.no_context_reply` -- same reply text as
      `'no_context'`, since both mean "nothing to search/found" from this
      caller's own read-authority alone, before any request-level filter is
      even considered.
    - `'filter_excluded_all'`: this caller/bot COULD read at least one
      Collection (the `'no_collections'` case above did NOT apply), but
      `ChatRequest.collections` (see that field's own docstring) named only
      Collections outside that scope, so the caller's OWN filter -- not a
      lack of read-authority -- is what emptied the result.
      Deliberately a DIFFERENT `reason` from `'no_collections'`, and a
      DIFFERENT fixed reply text (not `bot.guard.no_context_reply`, see
      app/services/chat.py's own `_FILTER_EXCLUDED_ALL_REPLY`): "you have no
      access at all" and "your own selection doesn't overlap with what
      you're allowed to see" are different, actionable conditions for
      whatever surface is showing this reply to the human on the other end
      -- the former is nothing the caller can fix by picking differently,
      the latter is. See RetrievalTrace's own `requested_collections` field
      for how a caller can tell exactly which filter was responsible.

    `False`/`None` for every other outcome (guard disabled for this bot, or
    sources were found and the LLM's answer was returned as-is)."""

    triggered: bool = False
    reason: str | None = None


class N8nTrace(BaseModel):
    """Diagnostics for an n8n-provider bot's turn (contracts/n8n-flow.md,
    app/services/chat.py's `_run_n8n_turn`) -- currently just the one
    security-relevant counter this pipeline has reason to surface: how many
    of the `sources` an n8n flow reported back were DROPPED because their
    `collection` fell outside the exact scope signed into that turn's own
    delegation token (see contracts/n8n-flow.md's "Quellen sind eine
    Behauptung, keine Berechtigung" and `Source.collection`'s own
    docstring). `0` is the ordinary case -- every reported source's
    collection (including a source reporting no collection at all, checked
    against the NO_COLLECTION_SENTINEL rule) was already within scope, so
    nothing was dropped. A caller/operator seeing a nonzero value here
    across many turns for one bot has a concrete signal that bot's n8n flow
    is misconfigured, buggy, or worse -- reporting sources its own
    delegation token was never actually granted.

    Present on `ChatTrace.n8n` ONLY for a turn that actually reached n8n at
    all (i.e. never for a V1-unsupported/guard-triggered-before-n8n/
    conversational-smalltalk outcome of an n8n-provider bot, and never for
    any non-n8n bot) -- `None` on `ChatTrace.n8n` covers every one of those
    "there was nothing to check" cases uniformly, exactly like `retrieval`
    being `None` covers "no retrieval call was made" above.
    """

    dropped_sources: int = 0


class ChatTrace(BaseModel):
    intent: str
    confidence: float
    needs_retrieval: bool
    needs_tool: bool
    # None exactly when needs_retrieval is False -- no retrieval call was
    # made, so there is nothing to report candidates/used for.
    retrieval: RetrievalTrace | None = None
    model: str | None = None
    router_mode: str
    timings_ms: dict[str, float] = Field(default_factory=dict)
    guard: GuardTrace | None = None
    # See N8nTrace's own docstring -- None for every non-n8n turn and for an
    # n8n-provider bot's own outcomes that never actually reached n8n
    # (conversational smalltalk, any pipeline failure before the webhook
    # call).
    n8n: N8nTrace | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source] = Field(default_factory=list)
    trace: ChatTrace


# --- POST /internal/chat/stream ------------------------------------------
#
# The streaming counterpart to ChatResponse above -- see
# app/services/chat.py's `handle_chat_stream`/`_stream_prepared_turn` and
# app/api/internal.py's own `/internal/chat/stream` route for how these are
# produced, and contracts/internal-chat.md for the full event-ordering
# guarantee (`trace` once, before any `delta`; then zero or more `delta`;
# then either `sources` + `done`, or a single terminal `error` in place of
# both). Each is serialized as exactly one SSE line, `'data: <json>\n\n'`,
# by the route itself -- these models are the JSON payload of that line,
# not the SSE framing.
#
# Five distinct models (a discriminated union via each one's own literal
# `type`) rather than one model with a bunch of optional fields: a `delta`
# event has no business carrying a `trace`/`sources`/`detail` field at all,
# and modelling that with Optionals would let a caller construct/receive a
# nonsensical event (e.g. `type='delta'` with a `sources` list attached)
# that this schema itself should simply make unrepresentable.


class ChatStreamTraceEvent(BaseModel):
    """Sent exactly once, before any `ChatStreamDeltaEvent` -- the same
    `ChatTrace` shape ChatResponse.trace carries, with one deliberate
    difference: `trace.timings_ms` here can only ever hold the metrics
    already known at this point in the pipeline (`router_ms`, and
    `retrieval_ms` when a knowledge turn actually ran) -- `llm_ms`/
    `total_ms` both depend on generation finishing, which by definition
    hasn't happened yet when THIS event goes out (see ChatTrace's own
    docstring, which describes the non-streaming response's stricter
    "always present" guarantee for both keys; that guarantee does not carry
    over to this event)."""

    type: Literal['trace'] = 'trace'
    trace: ChatTrace


class ChatStreamDeltaEvent(BaseModel):
    """One incremental piece of the answer text, in emission order.
    Concatenating every `ChatStreamDeltaEvent.text` from one stream, in the
    order received, reconstructs the exact same string ChatResponse.answer
    would have carried for an identical, non-streaming request -- a
    streamed turn never changes WHAT is said, only how it is delivered."""

    type: Literal['delta'] = 'delta'
    text: str


class ChatStreamSourcesEvent(BaseModel):
    """Sent exactly once, after the last `ChatStreamDeltaEvent` (there is no
    later point at which sources could still be sent, since `done` follows
    immediately) -- the same `Source` list ChatResponse.sources would have
    carried for an identical, non-streaming request (`[]` for a guard-
    triggered or V1-unsupported turn, exactly like that response)."""

    type: Literal['sources'] = 'sources'
    sources: list[Source] = Field(default_factory=list)


class ChatStreamDoneEvent(BaseModel):
    """Terminal event on a stream that completed without error. Nothing
    follows it."""

    type: Literal['done'] = 'done'


class ChatStreamErrorEvent(BaseModel):
    """Sent INSTEAD OF `sources`+`done` when generation fails partway
    through a stream that has already committed to `200 OK`/
    `text/event-stream` (a real LLM provider's LLMError, raised mid-stream,
    app/services/llm.py) -- the one failure mode this contract cannot map to
    an HTTP status the way every /internal/chat error response does, simply
    because the response is no longer in a position to change its status by
    the time generation can fail (see app/api/internal.py's own docstring
    and contracts/internal-chat.md's stream section). Nothing follows this
    event either."""

    type: Literal['error'] = 'error'
    detail: str


# The full discriminated union `handle_chat_stream` yields and the route
# serializes one-event-per-SSE-line -- named for exactly what it is instead
# of e.g. `ChatEvent`, since `ChatMessage` above is already a similarly-named
# but unrelated shape (one turn of `ChatRequest.history`, not a stream event).
ChatStreamEvent = (
    ChatStreamTraceEvent | ChatStreamDeltaEvent | ChatStreamSourcesEvent | ChatStreamDoneEvent | ChatStreamErrorEvent
)
