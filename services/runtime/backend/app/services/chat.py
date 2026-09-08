"""Chat pipeline orchestration for POST /internal/chat and
POST /internal/chat/stream (see app/api/internal.py). This is the module
that finally fills in the 501 placeholder route()'s own docstring described:
bot lookup, permission enforcement, intent routing (app/services/router.py),
retrieval (app/services/retrieval_client.py), LLM generation
(app/services/llm.py) and the response guard (README's "Response Guard"),
tied together into one ChatResponse (app/schemas/chat.py) -- or, for the
streaming route, the equivalent sequence of ChatStreamEvents.

Two public entry points share every one of the steps below up to (never
including) the actual LLM-generation call: `handle_chat` (blocking, one
`ChatResponse`) and `handle_chat_stream` (an iterator of `ChatStreamEvent`,
app/schemas/chat.py) -- both built on the same `_prepare_turn`, which runs
steps 1-5 below in full and stops exactly at the point a caller would call
the LLM. See `_PreparedTurn`/`_prepare_turn`/`handle_chat_stream`'s own
docstrings for how that split keeps `handle_chat` itself byte-for-byte
unchanged while letting the streaming route commit to `200 OK`/
`text/event-stream` only once steps 1-5 have already succeeded.

Pipeline, in order:

1. `load_bot(request.bot_id)` -- BotNotFoundError (app/services/botconfig.py,
   itself a KeyError subclass) propagates unchanged to app/api/internal.py,
   which maps it to 404.
2. Permission check: `BotConfig.permissions.teams` (app/schemas/bot.py) --
   empty means every team may use this bot (see that field's own
   docstring); non-empty and the caller's `user.team` isn't a member raises
   `BotPermissionDenied` below, mapped to 403 by app/api/internal.py.
3. `router.route(message, bot, settings.router_mode, llm_call)` --
   `llm_call` is only ever built (see `_router_llm_call`) when
   `settings.router_mode == 'llm'`, mirroring route()'s own "llm_call is
   only consulted when mode == 'llm'" contract; there is nothing to build
   it FROM otherwise, since building it needs an LLMProvider this pipeline
   would rather not instantiate/configure for a mode that will never call
   it. `bot.model.provider == 'n8n'` is a further, deliberate exception to
   this: an n8n bot has no LLMProvider at all (see step 3a below), so
   `llm_call` stays None for such a bot even when `settings.router_mode ==
   'llm'` -- `router.route()` already treats a 'llm'-mode call with no
   `llm_call` as any other 'llm'-mode failure (a RULES decision with
   `router_fallback=True`, see that module's own docstring), which is
   exactly the fallback an n8n bot needs here: there is no separate,
   non-n8n LLMProvider configured on such a bot to classify intent with.

3a. `bot.model.provider == 'n8n'` (see app/schemas/bot.py's N8nConfig,
   app/services/n8n_client.py, contracts/n8n-flow.md): this bot's ENTIRE
   turn is handled by `_run_n8n_turn` instead of steps 4-5 below --
   permissions (step 2) and Collections-scope resolution (step 5a's
   `resolve_collection_scope`, reused UNCHANGED) still run exactly as they
   do for any other bot, since the resolved scope is exactly what gets
   signed into the delegation token n8n's flow receives (README's "leiht
   ... GENAU die Leserechte") -- but no LLM call and no direct
   `retrieval_client.search()` call ever happen on THIS side for such a
   bot; an n8n flow performs its own tool/search calls against Weave-Tools,
   authenticated with that token, entirely outside this process. The
   router (step 3) still runs first and its intent decides whether this
   turn is worth an n8n round-trip AT ALL: a deliberate, documented choice
   -- see `_run_n8n_turn`'s own docstring for exactly which intents do
   and don't, and why. When it does run, n8n's own returned `sources` are
   FIRST checked against the exact scope signed into that turn's own
   delegation token (`_filter_n8n_sources_by_scope` -- a reported source
   whose `collection` falls outside that scope is dropped, never trusted
   on the strength of n8n's own claim alone; see contracts/n8n-flow.md's
   "Quellen sind eine Behauptung, keine Berechtigung"), and only THEN
   subject to `bot.guard.require_sources` exactly like a knowledge turn's
   retrieved chunks are (empty -- after that filtering -- `sources` +
   `require_sources` -> the same `no_context_reply`/`GuardTrace` outcome,
   `reason='no_context'`) -- the response guard's promise ("never a
   silent, unsourced answer for a bot that requires one") is not weakened
   just because the answer came from an external agent flow instead of
   this pipeline's own LLM call, NOR by that external flow overclaiming
   what it actually found.
4. Per the resulting intent:
   - 'document' / 'action' / 'complex': none of these are handled by this
     stage's pipeline yet (V1) -- see `_V1_UNSUPPORTED_REPLY` below.
     Deliberately no LLM call and no retrieval call for these either, even
     though 'complex' can carry `needs_retrieval=True` (see
     router.py's `_flags_for_intent`) -- a retrieval call whose result would
     never be shown to anyone is pure waste, and the trace's own
     `retrieval=None` here is a deliberate, documented exception to
     ChatTrace's general "None exactly when needs_retrieval is False"
     invariant (app/schemas/chat.py), scoped to precisely this
     not-yet-implemented branch.
   - 'conversational': `bot.system_prompt` + `history` + `message` straight
     to the LLM, no retrieval, `sources=[]`.
   - 'knowledge' (or 'complex' with retrieval both needed AND enabled --
     see step 5's own note on why "needed" alone is not sufcient): retrieval
     runs, its chunks become a numbered context block, and the guard
     (`bot.guard`) can override the LLM's answer entirely -- see
     `_run_knowledge_turn`.

5. Retrieval is only ever actually called when BOTH
   `decision.needs_retrieval` AND `bot.retrieval.enabled` hold. The first
   alone is not sufficient: 'llm' router mode's classifier decides
   'knowledge' from the message text alone (see router.py's `_decide_llm`),
   completely independent of THIS bot's own `retrieval.enabled` -- and
   `_flags_for_intent` then sets `needs_retrieval=True` for ANY 'knowledge'
   classification, retrieval-backed bot or not (see that function's own
   docstring: "so a bot with retrieval disabled can never end up with
   needs_retrieval=True" is only actually guaranteed for RULES mode, since
   the RULES fallback branch is itself gated on `bot.retrieval.enabled`;
   'llm' mode has no such gate on the classifier's own INTENT choice, only
   on the flags derived from whatever intent it returns). A bot whose YAML
   turns retrieval off must never end up calling Weave-Retrieval regardless
   of what an LLM classifier guesses about the message -- that bot is
   answered exactly like 'conversational' instead: straight to the LLM, no
   context, no guard, `sources=[]`.

5a. Collections (the cross-service Collections contract): before a
   'knowledge' turn's retrieval call is actually made, `_run_knowledge_turn`
   resolves `resolve_collection_scope(bot, user)` -- this bot's own
   `bot.retrieval.collections` intersected against whatever `user.team` may
   actually read (`retrieval_client.list_collections`), see that function's
   own docstring. An empty REAL intersection -- the bot names Collections
   none of which the caller may read, or the caller may read no Collections
   at all -- is answered exactly like the 'no_context' guard case below
   (`bot.guard.no_context_reply`, `sources=[]`), but with `trace.guard.
   reason='no_collections'` instead: `retrieval_client.search()` is never
   even called in that case, `require_sources` notwithstanding -- a
   retrieval-backed bot must never search without SOME concrete Collections
   boundary in force (one narrow, documented exception: a pure Altbestand-
   only search when the bot names no restriction of its own AND the caller
   reads no Collections at all AND `bot.retrieval.include_uncollected` is
   True -- see `resolve_collection_scope`'s own docstring). A non-empty
   result is forwarded as `retrieval_client.search()`'s own
   `allowed_collections` argument alongside `allowed_teams` -- see
   `_allowed_teams` immediately below for the analogous, longer-standing
   team-scoping decision this mirrors.

   `bot.retrieval.include_uncollected` (app/schemas/bot.py, default `True`)
   additionally controls whether `resolve_collection_scope` appends
   NO_COLLECTION_SENTINEL (`'__none__'`, this module's own constant, mirrors
   Weave-Retrieval's identical one) to that non-empty result, so documents
   with NO collection at all (`Document.collection_slug IS NULL` --
   pre-Collections legacy content) stay visible on top of whatever real
   Collections were resolved. This defaults to True specifically so that
   creating the very FIRST Collection in the system can never silently make
   the entire pre-Collections corpus invisible to every retrieval-backed
   bot -- see that config field's own docstring for the full reasoning. The
   sentinel, when present, flows unchanged into `trace.retrieval.
   collections` below (see RetrievalTrace's own docstring, app/schemas/
   chat.py) -- a caller can see directly from the trace whether an
   Altbestand-inclusive search was actually made.

   `ChatRequest.collections` (the "Collection-Filter pro Anfrage" contract,
   that field's own docstring, app/schemas/chat.py): once the above --
   `_resolve_rights_scope`, this bot+caller's own read-authority -- is fully
   resolved, `_run_knowledge_turn` narrows it once more with
   `_apply_collections_filter` against whatever this ONE request's own
   `collections` field named (`None`, the default, changes nothing at all).
   Strictly a further restriction, applied strictly AFTER the above, never
   before and never additive: a slug this filter names that the caller/bot
   combination was never entitled to is silently dropped, never surfaced,
   never widening the boundary rights resolution already drew. Emptying an
   otherwise non-empty rights scope this way is its OWN guard reason,
   `trace.guard.reason='filter_excluded_all'` -- deliberately distinct from
   `'no_collections'` above, since "your own filter excluded everything"
   and "you have no access here at all" are different, differently
   actionable conditions for whoever is asking (see `_FILTER_EXCLUDED_ALL_
   REPLY`'s and `GuardTrace`'s own docstrings). `trace.retrieval.
   requested_collections` echoes this request's own filter verbatim,
   alongside `trace.retrieval.collections`'s existing "what was effectively
   searched" -- see RetrievalTrace's own docstring for both fields side by
   side.

RetrievalUnavailable (app/services/retrieval_client.py) propagates
unchanged out of this module to app/api/internal.py, which maps it to 503 --
"the upstream SERVICE is the problem, retrying later might succeed", per
that module's own docstring, is exactly the signal a caller needs instead of
this pipeline quietly answering as if no context had been requested at all
(README's Response Guard: never a silent, unsourced answer for a bot that
requires one). This applies identically whether it was `retrieval_client.
list_collections()` (step 5a above) or `retrieval_client.search()` itself
that raised it -- from app/api/internal.py's perspective these are the same
"Weave-Retrieval, the upstream service, is unavailable" condition regardless
of which of that client's two functions hit it. RetrievalError (a 4xx --
Weave-Retrieval rejected the request itself, e.g. an invalid top_k/final_k
combination) is deliberately left UNCAUGHT here, surfacing as this service's
default 500: `top_k`/`final_k` come straight from a validated `BotConfig`
with no cross-field check between them (app/schemas/bot.py's
RetrievalConfig), so a RetrievalError can only mean the bot's own YAML is
misconfigured -- a deployment bug to fix, not a transient condition worth a
dedicated error path the way RetrievalUnavailable is.

An LLM generation failure (a real `OpenAICompatibleLLM` provider raising
`LLMError`, app/services/llm.py) is likewise left uncaught for the same
reason RetrievalError is: this stage's only exercised provider is `FakeLLM`,
which never raises by construction, and a real provider's failure handling
(retry-elsewhere, a fallback provider, ...) is a later stage's concern, not
something to half-implement here speculatively.

An n8n-provider bot's turn (step 3a above, `_run_n8n_turn`) fails the same
two ways, mapped the same two ways: `n8n_client.N8nUnavailable` (n8n
unreachable, timed out, or 5xx) propagates uncaught out of this module to
app/api/internal.py, mapped to `503` exactly like `RetrievalUnavailable` --
the same "upstream SERVICE is the problem" signal, never a silent unsourced
answer. `n8n_client.N8nError` (n8n rejected the request with a 4xx, or
answered 200 with a body that doesn't parse per that module's own contract)
is deliberately left UNCAUGHT here too, surfacing as this service's default
500 -- exactly the RetrievalError treatment, and for the same reason: this
service already validated the bot's own configuration (permissions,
Collections scope, the webhook_url's SSRF allowlist) before ever calling
n8n, so a 4xx/malformed-response failure at this point can only mean the
n8n FLOW ITSELF is misconfigured or broken, a deployment bug in that flow
to fix rather than a transient condition worth a dedicated status code.
`delegation.mint_delegation_token`'s own `DelegationConfigError`
(`WEAVE_DELEGATION_SECRET` unconfigured, app/services/delegation.py --
called internally by `n8n_client.run_flow` before it ever POSTs anything)
is likewise left uncaught, for the same "deployment misconfiguration, not a
request-shaped failure" reasoning.
"""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass

from app.core.config import settings
from app.schemas.bot import BotConfig
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatStreamDeltaEvent,
    ChatStreamDoneEvent,
    ChatStreamErrorEvent,
    ChatStreamEvent,
    ChatStreamSourcesEvent,
    ChatStreamTraceEvent,
    ChatTrace,
    ChatUser,
    GuardTrace,
    N8nTrace,
    RetrievalTrace,
    Source,
)
from app.services import llm as llm_service
from app.services.chat_config_client import fetch_chat_provider
from app.services import n8n_client
from app.services import retrieval_client
from app.services import router as router_service
from app.services.botconfig import load_bot
from app.services.retrieval_client import RetrievedChunk

logger = logging.getLogger(__name__)

# Mirrors Weave-Retrieval's own NO_COLLECTION_SENTINEL (app/services/search.py
# in that service) byte-for-byte -- there is no shared Python module to import
# this from (a different service, reached only over HTTP; see
# app/services/retrieval_client.py's own docstring), so the exact string is
# duplicated here instead. Never a real collection slug (Weave-Ingest's
# Collections contract reserves it system-wide for this sentinel meaning);
# `resolve_collection_scope` below appends it to mean "documents with NO
# collection at all (`Document.collection_slug IS NULL` -- pre-Collections
# legacy content) are ALSO allowed", exactly as Weave-Retrieval's
# apply_filters() interprets it on the other end of this HTTP call.
NO_COLLECTION_SENTINEL = '__none__'

# Kept in this stage's own module rather than app/schemas/bot.py or
# app/services/botconfig.py: this is a chat-pipeline-level authorization
# decision (does THIS caller get to use THIS bot at all), not a bot-config
# validation concern (that's BotConfigError) or a bot-registry lookup
# concern (that's BotNotFoundError).
class BotPermissionDenied(Exception):
    """Raised by `_check_permissions` when `bot.permissions.teams` is
    non-empty and the caller's `user.team` is not a member of it -- mapped
    to 403 by app/api/internal.py. `bot_id`/`user_team` are carried
    separately (not just baked into the message) so a caller building its
    own error response/log line doesn't have to parse them back out."""

    def __init__(self, message: str, *, bot_id: str, user_team: str | None) -> None:
        super().__init__(message)
        self.bot_id = bot_id
        self.user_team = user_team


# The three intents this pipeline does not act on yet -- see this module's
# own docstring, step 4. 'complex' is only unsupported when it cannot fall
# back to a best-effort knowledge turn (retrieval needed AND enabled --
# docstring step 5 and contracts/internal-chat.md both promise that case
# runs retrieval).
_V1_UNSUPPORTED_INTENTS = frozenset({'document', 'action', 'complex'})

# User-facing (German, like every other end-user-visible string in this
# codebase -- see app/schemas/bot.py's own note on why THIS kind of string
# stays German while code/comments stay English). Deliberately one single
# reply for all three unsupported intents rather than a per-intent message:
# from the caller's perspective the distinction between "that's a document-
# processing request" and "that's a multi-step request" is an internal
# routing detail, not something a V1 user needs explained -- "not yet, later
# version" is the whole message.
_V1_UNSUPPORTED_REPLY = (
    'Diese Art von Anfrage kann ich in dieser Version noch nicht bearbeiten '
    '(Dokumentverarbeitung, Aktionen und mehrstufige Anfragen sind für eine '
    'spätere Version -- V2/V3 -- vorgesehen). Bitte stelle mir stattdessen '
    'eine einfache Frage.'
)

# German, user-facing -- see this module's own docstring note on why (same
# reasoning as _V1_UNSUPPORTED_REPLY above). Used ONLY by `_run_n8n_turn`,
# for an n8n-provider bot's 'conversational' intent -- see that function's
# own docstring for why small talk deliberately never reaches n8n at all.
_N8N_SMALLTALK_REPLY = (
    'Hallo! Für Fragen mit Datensuche oder für Aktionen nutze ich einen '
    'Automatisierungs-Workflow -- stell mir dazu gerne eine konkrete Frage '
    'oder Aufgabe.'
)

# German, user-facing (same reasoning as _V1_UNSUPPORTED_REPLY above) --
# `GuardTrace.reason == 'filter_excluded_all'`'s own fixed reply (see that
# reason's own docstring, app/schemas/chat.py). Deliberately NOT
# `bot.guard.no_context_reply`: that text is this bot's own answer to "you
# have no read-authority here at all" (`'no_context'`/`'no_collections'`),
# which is misleading for a caller who DOES have read-authority but simply
# filtered its own request down to nothing useful -- a different, and
# unlike the other two, caller-fixable condition. Not a per-bot YAML field
# either: the caller supplied the excluding filter itself, there is nothing
# bot-specific left to configure about this particular reply.
_FILTER_EXCLUDED_ALL_REPLY = (
    'Deine Collections-Auswahl für diese Anfrage enthält keine Collection, auf die du bei diesem Bot Zugriff hast '
    '-- passe deine Auswahl an und versuche es erneut.'
)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _check_permissions(bot: BotConfig, user: ChatUser) -> None:
    """Enforce `bot.permissions.teams` (app/schemas/bot.py) -- an empty list
    means every team may use this bot (see that field's own docstring), so
    this is a no-op in that case regardless of `user.team` (including when
    `user.team` itself is unset -- an anonymous/system-initiated chat is
    still allowed onto an unrestricted bot). A non-empty list requires
    `user.team` to be a member; `None` (no team propagated at all) never
    matches a non-empty list, the same as any other value that isn't in it.
    """
    allowed_teams = bot.permissions.teams
    if not allowed_teams:
        return
    if not set(user.effective_teams).intersection(allowed_teams):
        raise BotPermissionDenied(
            f'bot {bot.id!r} is restricted to teams {allowed_teams!r}; caller team is {user.team!r}',
            bot_id=bot.id,
            user_team=user.team,
        )


def _allowed_teams(bot: BotConfig, user: ChatUser) -> list[str] | None:
    """The `allowed_teams` argument for `retrieval_client.search()` --
    Weave-Retrieval's own team-scoped access control on the documents a
    query may even see (app/services/retrieval_client.py's docstring;
    that service's SearchRequest.allowed_teams: `None` = unrestricted,
    `[]` = literally zero teams authorized, zero results -- see
    Weave-Retrieval's app/schemas/search.py).

    Design choice, spelled out here because both inputs are plausible and
    the task deliberately leaves the pick to this stage:

    Prefers the CALLING USER's own team (`[user.team]`) when the gateway
    propagated one. That is the more precise, least-privilege scope --
    ADR-0002's per-request team-membership propagation is what lets two
    members of DIFFERENT teams get different retrieval visibility through
    the exact same bot (e.g. two 'legal-support' users, one 'legal' and one
    'management', should not necessarily see identical documents just
    because both are permitted to use the bot at all).

    Falls back to the BOT's own `permissions.teams` only when no user team
    is known at all (`user.team is None` -- an anonymous/system-initiated
    chat, see ChatUser's own docstring in app/schemas/chat.py) --
    `permissions.teams` is this pipeline's next-best proxy for "which teams'
    documents a caller of this bot should see" when there is no more
    specific signal to use instead.

    One deliberate wrinkle in that fallback: an EMPTY `permissions.teams`
    means "every team may use this bot" (app/schemas/bot.py's own
    docstring), NOT "no team may" -- but forwarded as-is to
    `allowed_teams`, an empty list means the exact opposite on
    Weave-Retrieval's side ("zero teams authorized, zero results"). Passing
    `bot.permissions.teams` straight through unchanged would silently turn
    an unrestricted bot's retrieval into one that always returns nothing.
    `or None` below closes that gap: an empty `permissions.teams` becomes
    `None` (unrestricted), matching what "every team may" actually means.
    """
    if user.teams is not None:
        return user.effective_teams
    if user.team:
        return [user.team]
    return bot.permissions.teams or None


def _resolve_rights_scope(bot: BotConfig, user: ChatUser) -> list[str]:
    """This bot+caller's own Collections READ-AUTHORITY -- everything
    `resolve_collection_scope` below resolves BEFORE that function's own
    per-request `collections` filter (`ChatRequest.collections`) is ever
    applied. Split into its own function (rather than inlined into
    `resolve_collection_scope`) specifically so `_run_knowledge_turn` can
    tell apart the two ways a filtered scope can come back empty: this
    function's OWN result was already empty (`'no_collections'`), or it
    wasn't but the caller's filter excluded everything anyway
    (`'filter_excluded_all'`) -- see that function's own docstring. Every
    other caller of Collections scope (`resolve_collection_scope` itself,
    hence indirectly `_run_n8n_turn` too) never needs that distinction and
    calls the public wrapper instead.

    Unlike `_allowed_teams`, this NEVER returns `None`. Weave-Retrieval
    reserves `allowed_collections=None` for a caller trusted with full,
    unrestricted Collections visibility ("service-internal callers", per
    that field's own docstring in Weave-Retrieval's app/schemas/search.py)
    -- and Weave-Runtime, resolving scope for one end user's own chat turn,
    is never that caller. This function always resolves a concrete
    (possibly empty) list of slugs instead, built in two stages:

    Stage 1 -- the REAL slugs ("`real_scope`" below), exactly as before
    `include_uncollected` existed:

    - `retrieval_client.list_collections(user.team)` -- what `user.team`
      may read at all, straight from Weave-Retrieval's own Collections
      read-authority (that client function's own docstring covers the
      `user.team is None` case: only PUBLIC collections come back).
    - Intersected with this bot's own `bot.retrieval.collections`
      (app/schemas/bot.py) -- unless that list is empty, in which case the
      bot names no Collections restriction of its own and the caller's full
      readable set is used unfiltered ("Leere Bot-Liste = alle lesbaren",
      RetrievalConfig.collections' own docstring).

    An empty `real_scope` here -- the bot's own Collections list and the
    caller's readable Collections share nothing, or the caller can read no
    Collections at all -- is normally `_run_knowledge_turn`'s own signal to
    skip `retrieval_client.search()` entirely and take the guard path
    instead (`trace.guard.reason == 'no_collections'`, see this module's own
    docstring, step 5a): forwarding an empty list on to `search()` unchanged
    would produce the exact same zero-result outcome on Weave-Retrieval's
    own side (`allowed_collections=[]` there means "zero Collections
    authorized" too), but indistinguishable there from an ordinary "the
    query just didn't match anything" -- a materially different, worth-
    tracing condition on this side, and one this pipeline must never paper
    over by searching without a Collections boundary at all instead.

    Stage 2 -- `bot.retrieval.include_uncollected` (app/schemas/bot.py,
    default `True`): whether NO_COLLECTION_SENTINEL is appended to
    `real_scope` so Weave-Retrieval's own apply_filters() also admits
    documents with NO collection at all (pre-Collections legacy content)
    alongside whatever real slugs `real_scope` resolved to. Deliberately
    applied AFTER, never folded into, the `real_scope` emptiness check
    above -- the sentinel augments a genuine Collections scope, it must
    never MANUFACTURE one: a bot whose real Collections intersection is
    empty still has to hit the `no_collections` guard, `include_uncollected`
    notwithstanding, or a caller who was granted no real Collections
    visibility at all would end up searching anyway just because a
    sentinel was appended on top of nothing.

    One narrow, deliberate exception to that rule: when the bot names NO
    Collections restriction of its own (`bot.retrieval.collections` empty)
    AND the caller may read NO Collections at all (`readable` empty) AND
    `include_uncollected` is True, there is no real Collections axis in
    play for this call at all -- an unrestricted bot, a caller with zero
    Collections read access whatsoever -- so a pure Altbestand-only search
    (`[NO_COLLECTION_SENTINEL]` alone, no real slugs) is allowed rather than
    guarded off: there IS legacy content this combination is entitled to
    see, and refusing to search at all here would be strictly less correct
    than this pipeline's own pre-Collections behavior. Any bot that DOES
    name a Collections restriction of its own is never covered by this
    exception -- for that bot, an empty real intersection always means the
    guard fires, exactly per this module's own docstring, step 5a.

    Propagates `retrieval_client.list_collections()`'s own
    RetrievalUnavailable/RetrievalError unchanged -- see this module's
    docstring for how `app/api/internal.py` maps each.
    """
    readable = [collection.slug for collection in retrieval_client.list_collections(user.teams if user.teams is not None else user.team)]
    bot_collections = bot.retrieval.collections
    if bot_collections:
        readable_set = set(readable)
        real_scope = [slug for slug in bot_collections if slug in readable_set]
    else:
        real_scope = readable

    if not real_scope:
        # The one exception carved out above: no bot-side restriction, no
        # caller-side readable Collections at all, but legacy content is
        # still wanted -- a pure Altbestand-only search is legitimate here,
        # unlike every other empty-`real_scope` case (which must guard).
        if not bot_collections and not readable and bot.retrieval.include_uncollected:
            return [NO_COLLECTION_SENTINEL]
        return []

    if bot.retrieval.include_uncollected:
        return [*real_scope, NO_COLLECTION_SENTINEL]
    return real_scope


def _apply_collections_filter(rights_scope: list[str], requested_collections: list[str] | None) -> list[str]:
    """Intersects `rights_scope` (`_resolve_rights_scope`'s own result --
    this bot+caller's own Collections read-authority, already fully
    resolved) with `requested_collections` (`ChatRequest.collections`,
    verbatim) -- the one and only place the "Collection-Filter pro Anfrage"
    contract's central rule is enforced: a filter can ONLY narrow, never
    widen, whatever `rights_scope` already granted, and a filter naming a
    slug outside that scope is silently dropped rather than surfaced as an
    error (the caller never learns from this response alone whether that
    slug even exists, exactly like every other scope-boundary check in this
    module -- see `_filter_n8n_sources_by_scope`'s own docstring for the
    identical posture on the n8n side).

    `requested_collections is None` (no filter sent at all, `ChatRequest.
    collections`'s own default) is the identity case: returns `rights_scope`
    completely unchanged -- this is what keeps every pre-existing caller of
    `resolve_collection_scope` (below) byte-for-byte unaffected by this
    filter's existence. Any other value, INCLUDING `[]` (an explicit
    "match nothing" filter, deliberately distinct from "no filter" -- see
    `ChatRequest.collections`'s own docstring), intersects for real: the
    result keeps only `rights_scope` members also named in
    `requested_collections`, in `rights_scope`'s own order, and adds
    nothing that was not already in `rights_scope` even if
    `requested_collections` names it.

    NO_COLLECTION_SENTINEL needs no special-casing here at all -- it is
    just one more ordinary string as far as this intersection is concerned,
    which is exactly what gives the Collection-Filter-Vertrag's own Sentinel
    rule for free: a `requested_collections` naming only real slugs drops
    the sentinel out of the intersection precisely because it isn't among
    them (the caller wants exactly those Collections, Altbestand not
    included); a `requested_collections` that itself names
    `NO_COLLECTION_SENTINEL` can keep it, but only when `rights_scope`
    already contains it -- a filter can no more grant Altbestand visibility
    than it can grant any other Collection `_resolve_rights_scope` didn't
    already resolve.
    """
    if requested_collections is None:
        return rights_scope
    requested_set = set(requested_collections)
    return [slug for slug in rights_scope if slug in requested_set]


def resolve_collection_scope(
    bot: BotConfig, user: ChatUser, requested_collections: list[str] | None = None
) -> list[str]:
    """The `allowed_collections` argument for `retrieval_client.search()` --
    the Collections-contract analogue of `_allowed_teams` above, for the
    other axis of Weave-Retrieval's access control (app/services/
    retrieval_client.py's `search()` docstring; that service's own
    `SearchRequest.allowed_collections`).

    A thin wrapper, `_resolve_rights_scope(bot, user)` (this bot+caller's
    own read-authority -- see that function's own docstring for the full
    two-stage real-scope/Altbestand-sentinel resolution, unchanged here)
    then narrowed by `_apply_collections_filter` against
    `requested_collections` (`ChatRequest.collections`, this module's
    docstring on the "Collection-Filter pro Anfrage" contract) -- the
    request-level filter is applied strictly AFTER rights are resolved,
    never before, and never adds anything rights didn't already grant (see
    both of those functions' own docstrings for exactly how). Callers that
    only need the final, already-filtered scope (this function, and hence
    `_run_n8n_turn`) use this wrapper directly; `_run_knowledge_turn` calls
    the two steps separately instead, since it additionally needs to tell
    apart the two distinct ways the result can come back empty (`rights_
    scope` itself was empty, vs. the filter is what emptied it) to pick the
    right `GuardTrace.reason` -- see that function's own docstring.

    `requested_collections` defaults to `None` (no filter) specifically so
    every pre-existing call site/test written before this filter existed
    keeps behaving byte-for-byte identically without being touched.

    Like `_resolve_rights_scope`, this NEVER returns `None` -- Weave-
    Retrieval reserves `allowed_collections=None` for a caller trusted with
    full, unrestricted Collections visibility ("service-internal callers",
    per that field's own docstring in Weave-Retrieval's app/schemas/
    search.py), and Weave-Runtime, resolving scope for one end user's own
    chat turn, is never that caller.

    Propagates `retrieval_client.list_collections()`'s own
    RetrievalUnavailable/RetrievalError unchanged -- see this module's
    docstring for how `app/api/internal.py` maps each.
    """
    return _apply_collections_filter(_resolve_rights_scope(bot, user), requested_collections)


def _filter_n8n_sources_by_scope(
    sources: list[Source], scope: list[str], *, bot_id: str
) -> tuple[list[Source], int]:
    """Enforce contracts/n8n-flow.md's "Quellen sind eine Behauptung, keine
    Berechtigung" against n8n's OWN reported `sources`, once the webhook
    call has already returned (called from `_run_n8n_turn`, see its own
    docstring for exactly where) -- `n8n_client.run_flow` itself makes no
    such check (see that function's own docstring: it only signs and
    forwards `scope`, never re-examines what n8n hands back). This call
    already proved it reached an n8n endpoint this deployment's own YAML
    explicitly allowlisted (app/services/botconfig.py) and that the
    RESPONSE at least parses per contract (`n8n_client._parse_response`) --
    neither of those says anything about whether the FLOW's own tool calls
    actually stayed within the one scope THIS turn's delegation token
    granted. A misconfigured, buggy, or compromised flow could report a
    source from a Collection the currently asking human was never entitled
    to read, and nothing on the wire between n8n and this pipeline would
    catch it otherwise.

    `scope` must be the EXACT list `resolve_collection_scope` resolved for
    THIS turn -- the same one `_run_n8n_turn` also hands to `n8n_client.
    run_flow`, which signs it into the delegation token n8n's flow
    received. Never re-derived here, never widened.

    A source's `collection` (`Source.collection`, see that field's own
    docstring) is checked exactly like Weave-Retrieval's own
    `allowed_collections` enforcement: a real slug must be a member of
    `scope`; `None` (the source claims no collection at all -- ordinarily
    meaning pre-Collections legacy content) is accepted only when
    NO_COLLECTION_SENTINEL is ITSELF a member of `scope` -- the identical
    Altbestand rule `resolve_collection_scope` applies when deciding
    whether to append that sentinel in the first place, applied here in
    reverse to decide whether an uncollected CLAIM should be believed. A
    source failing either check is DROPPED -- never the whole n8n answer,
    only that one source (contracts/n8n-flow.md: "leeres Ergebnis, kein
    Fehler, kein Hinweis auf deren Existenz", the same "quietly narrow, never
    fail loud" posture as every other scope-boundary check this contract
    describes).

    Returns `(kept, dropped_count)`: `kept` preserves the original order,
    containing every source whose `collection` passed; `dropped_count` is
    `len(sources) - len(kept)`, surfaced by the caller as `N8nTrace.
    dropped_sources`. Logs exactly one WARNING, naming `bot_id` and the
    count ONLY -- never a dropped source's own content (document id, chunk
    id, text, the claimed collection itself) -- when `dropped_count > 0`;
    silent otherwise, since dropping nothing is the ordinary, unremarkable
    case.
    """
    scope_set = set(scope)
    kept = [
        source
        for source in sources
        if (NO_COLLECTION_SENTINEL in scope_set if source.collection is None else source.collection in scope_set)
    ]

    dropped_count = len(sources) - len(kept)
    if dropped_count:
        logger.warning(
            'n8n bot %r reported %d source(s) outside the Collections scope signed into its delegation token '
            '-- dropped without failing the turn (see contracts/n8n-flow.md, "Quellen sind eine Behauptung, '
            'keine Berechtigung")',
            bot_id,
            dropped_count,
        )
    return kept, dropped_count


def _run_n8n_turn(
    bot: BotConfig,
    request: ChatRequest,
    decision: router_service.RouterDecision,
    timings_ms: dict[str, float],
    total_start: float,
) -> '_PreparedTurn':
    """The entire turn for a `bot.model.provider == 'n8n'` bot -- called
    once `_prepare_turn` has already resolved `decision` (the router's own
    step 3) and checked permissions (step 2); this function is the whole of
    this module's own docstring step 3a.

    Deliberate, documented routing choice for WHICH intents actually reach
    n8n at all (contracts/n8n-flow.md's own worked example covers this too):

    - `'conversational'` (greetings/small talk, see router.py's own RULES
      patterns): answered directly with the fixed `_N8N_SMALLTALK_REPLY`
      text, exactly like `_V1_UNSUPPORTED_REPLY` is for an unsupported
      intent elsewhere in this module -- NO n8n call, no Collections-scope
      resolution, no delegation token minted. Rationale: an n8n agent flow
      exists to do tool/search WORK (this bot's whole reason to prefer n8n
      over a plain LLMProvider); paying for a webhook round-trip -- with
      its own latency budget and its own failure surface, N8nUnavailable
      included -- to answer "Hallo" would make every greeting as slow and
      as fragile as a full agentic turn, for zero benefit to the caller.
    - Every other intent (`'knowledge'`, `'document'`, `'action'`,
      `'complex'`): sent to n8n. This deliberately LIFTS
      `_V1_UNSUPPORTED_INTENTS` for an n8n-provider bot specifically:
      `'document'`/`'action'`/`'complex'` are exactly the intents an n8n
      agent flow's own tool access exists to handle, which is the entire
      point of offering n8n as a bot provider at all -- an n8n bot that
      could only ever receive `'knowledge'` turns would be strictly less
      capable than what this provider is FOR.

    Permissions (`_check_permissions`, step 2) already ran in `_prepare_turn`
    before this function was ever called, identically to every other bot --
    this function does not re-check them. Collections-scope resolution
    (`resolve_collection_scope`, step 5a) DOES run again here, for every
    intent that reaches n8n, via the exact same function every retrieval-
    backed bot's knowledge turn uses -- its result becomes the delegation
    token's own signed scope (`n8n_client.run_flow` mints that token
    internally, see its own docstring), independent of `bot.retrieval.
    enabled` (which this function never even reads: n8n bots do not need
    Weave-Runtime's own direct `retrieval_client.search()` at all, they get
    their OWN Weave-Tools-mediated search via that token instead).

    Unlike the retrieval path's own `_run_knowledge_turn`, an EMPTY resolved
    scope here does NOT by itself trigger the `'no_collections'` guard or
    skip the n8n call -- that guard exists specifically because
    `_run_knowledge_turn` is about to make ONE PARTICULAR call
    (`retrieval_client.search()`) that must never run without a concrete
    Collections boundary. An n8n flow is not limited to that one call: it
    may perform actions or searches unrelated to Collections entirely, so
    an empty scope here still faithfully means "this token grants no
    Collections read access" (enforced on the Weave-Tools side per
    contracts/n8n-flow.md's "Grundregel Rechte") without this function
    itself presuming nothing useful could possibly happen. The ONE guard
    this function does enforce, unconditionally for every non-conversational
    n8n turn, is `bot.guard.require_sources` against n8n's OWN returned
    `sources`, exactly as this module's own docstring, step 3a, documents.

    Every branch below always ends with `final_answer` set (never `None`)
    -- an n8n-provider bot's turn is, by construction, always fully decided
    by the time this function returns; see `_PreparedTurn`'s own docstring
    for why that matters (neither `handle_chat` nor `_stream_prepared_turn`
    ever needs an `llm_provider` for such a bot).

    Once n8n actually answers, `_filter_n8n_sources_by_scope` (see its own
    docstring) checks n8n's OWN reported `sources` against `allowed_
    collections` -- the exact scope THIS turn's own delegation token was
    signed with -- and drops any source whose `collection` falls outside
    it, BEFORE the `require_sources` guard below ever looks at the list:
    n8n's reported sources are a claim from an external, only-signature-
    verified flow, never a proof of what the asking human was actually
    entitled to see (contracts/n8n-flow.md's "Quellen sind eine
    Behauptung, keine Berechtigung"). `bot.guard.require_sources` is
    therefore checked against the FILTERED list, not n8n's raw one -- a
    flow that reported sources entirely outside its own granted scope must
    trigger the guard exactly as if it had reported none at all, per this
    module's own docstring, step 3a.
    """
    if decision.intent == 'conversational':
        return _PreparedTurn(
            bot=bot,
            llm_provider=None,
            decision=decision,
            timings_ms=timings_ms,
            total_start=total_start,
            sources=[],
            retrieval_trace=None,
            guard=None,
            final_answer=_N8N_SMALLTALK_REPLY,
            messages=None,
            n8n_single_delta=False,
        )

    # `request.collections` (the per-request Collection-Filter, see
    # ChatRequest's own docstring) is threaded through exactly like it is
    # for the retrieval path's own `_run_knowledge_turn` -- a filter must
    # give an n8n agent flow LESS than its unfiltered rights scope would
    # have, via the exact same `resolve_collection_scope` (never MORE), so
    # whatever the caller filtered out never gets signed into this turn's
    # own delegation token either.
    allowed_collections = resolve_collection_scope(bot, request.user, request.collections)
    history = [{'role': turn.role, 'content': turn.content} for turn in request.history]

    n8n_start = time.perf_counter()
    result = n8n_client.run_flow(bot, request.message, history, request.user, allowed_collections)
    timings_ms['n8n_ms'] = _elapsed_ms(n8n_start)

    kept_sources, dropped_count = _filter_n8n_sources_by_scope(result.sources, allowed_collections, bot_id=bot.id)
    n8n_trace = N8nTrace(dropped_sources=dropped_count)

    if bot.guard.require_sources and not kept_sources:
        return _PreparedTurn(
            bot=bot,
            llm_provider=None,
            decision=decision,
            timings_ms=timings_ms,
            total_start=total_start,
            sources=[],
            retrieval_trace=None,
            guard=GuardTrace(triggered=True, reason='no_context'),
            final_answer=bot.guard.no_context_reply,
            messages=None,
            n8n_single_delta=False,
            n8n_trace=n8n_trace,
        )

    return _PreparedTurn(
        bot=bot,
        llm_provider=None,
        decision=decision,
        timings_ms=timings_ms,
        total_start=total_start,
        sources=kept_sources,
        retrieval_trace=None,
        guard=None,
        final_answer=result.answer,
        messages=None,
        n8n_single_delta=True,
        n8n_trace=n8n_trace,
    )


def _router_llm_call(provider: llm_service.LLMProvider) -> router_service.LLMCallable:
    """Adapt an LLMProvider (app/services/llm.py) into the plain
    `(messages, model) -> str` callable router.route()'s 'llm' mode expects
    (router_service.LLMCallable) -- see that module's own docstring for why
    it depends on this narrow Protocol instead of importing app/services/llm.py
    directly. Any LLMError this provider raises propagates through
    unchanged; router.py's own `_decide_llm` already treats ANY exception
    from this callable as a router-level failure and falls back to RULES
    (`RouterDecision.router_fallback=True`) -- this adapter has nothing to
    add to that handling.
    """

    def _call(messages: list[dict[str, str]], model: str) -> str:
        return provider.chat(messages, model=model).content

    return _call


def _context_block(chunks: list[RetrievedChunk]) -> str:
    """One system-role message's content: `chunks`, each as a numbered
    `[source N]` section (task spec: "nummerierte Abschnitte mit source+page")
    followed by that chunk's own text. `[source N]` (case-insensitive, N a
    positive integer, at the START of a line) is also the exact convention
    FakeLLM's own `_SOURCE_MARKER_RE` looks for (app/services/llm.py) to
    produce its `' [context:N]'` suffix -- this is how
    tests/test_chat_e2e.py can assert "retrieval actually reached the LLM
    call, with exactly this many sources" without a real LLM provider in
    the loop.

    English labels ("p." for page), not German: unlike `_V1_UNSUPPORTED_REPLY`
    or a bot's own `guard.no_context_reply`, this text is never shown to an
    end user directly -- it's LLM-prompt plumbing, not user-facing copy (see
    this module's own docstring note on why THOSE two stay German).
    """
    sections = []
    for index, chunk in enumerate(chunks, start=1):
        location = chunk.source or chunk.document_id
        if chunk.page_start is not None:
            page = f'p. {chunk.page_start}'
            if chunk.page_end is not None and chunk.page_end != chunk.page_start:
                page += f'-{chunk.page_end}'
            location = f'{location}, {page}'
        sections.append(f'[source {index}] {location}\n{chunk.text}')
    return '\n\n'.join(sections)


def _score_for(chunk: RetrievedChunk) -> float | None:
    """The single `Source.score` (app/schemas/chat.py) for `chunk`, chosen
    per `RetrievedChunkScores`' own docstring (app/services/retrieval_client.py):
    "which signal -- rerank if present, else rrf -- becomes 'the' score on a
    chat response" is explicitly left to THIS stage to decide. `rerank` when
    a reranker actually scored this chunk, `rrf` (the fused vector+fulltext
    rank, always present whenever there is a result at all) otherwise.
    """
    scores = chunk.scores
    return scores.rerank if scores.rerank is not None else scores.rrf


def _to_source(chunk: RetrievedChunk) -> Source:
    return Source(
        # chunk.source is optional at the retrieval layer (a chunk whose
        # document was never tagged with a source system) but Source.source
        # is a required str on this contract -- '' is the least-surprising
        # fill-in for "no source label", keeping every caller's `Source`
        # object uniformly a plain str instead of forcing an extra optional
        # case downstream for what is, for both of this repo's example
        # bots' documents, a case that never actually occurs.
        source=chunk.source or '',
        original_filename=chunk.original_filename,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        document_version=chunk.document_version,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        score=_score_for(chunk),
        collection=chunk.collection,
    )


@dataclass(frozen=True)
class _KnowledgeTurnResult:
    """What `_run_knowledge_turn` hands back to `handle_chat` -- either an
    LLM answer with sources, or the guard's own `no_context_reply` in place
    of one. `messages` is the (possibly context-extended) transcript the
    caller still needs to hand to the LLM itself when `guard_triggered` is
    False -- `_run_knowledge_turn` only ever calls retrieval, never the LLM,
    keeping the one `llm_provider.chat(...)` call site in `handle_chat`
    itself for both the 'knowledge' and 'conversational' paths alike.

    `guard_reason` is only meaningful when `guard_triggered` is True --
    `'no_context'` (retrieval ran, found nothing, `require_sources`),
    `'no_collections'` (`_resolve_rights_scope` found nothing at all,
    retrieval never ran), or `'filter_excluded_all'` (`_resolve_rights_scope`
    found something, but `ChatRequest.collections` excluded every bit of it,
    retrieval likewise never ran) -- see this module's own docstring, step
    5a, and `GuardTrace`'s own docstring (app/schemas/chat.py) for all
    three.
    """

    messages: list[dict[str, str]]
    sources: list[Source]
    retrieval_trace: RetrievalTrace
    guard_triggered: bool
    guard_reason: str | None = None


def _run_knowledge_turn(
    bot: BotConfig,
    user: ChatUser,
    message: str,
    messages: list[dict[str, str]],
    requested_collections: list[str] | None,
) -> _KnowledgeTurnResult:
    """Collection-scope resolution, retrieval, and the numbered context
    block for a 'knowledge' (or retrieval-eligible 'complex') turn -- called
    only once `handle_chat` has already confirmed `bot.retrieval.enabled`
    (see this module's own docstring, step 5). `requested_collections` is
    `ChatRequest.collections` verbatim -- this function calls
    `_resolve_rights_scope`/`_apply_collections_filter` separately (rather
    than the `resolve_collection_scope` wrapper both of them sit behind)
    specifically so it can tell apart the two distinct ways the resulting
    scope can come back empty and pick the right `GuardTrace.reason` for
    each -- see `_KnowledgeTurnResult`'s own docstring. Raises
    `retrieval_client.RetrievalUnavailable`/`RetrievalError` straight
    through, from either `_resolve_rights_scope` or
    `retrieval_client.search()` itself -- see this module's docstring for
    how `app/api/internal.py` handles each.
    """
    rights_scope = _resolve_rights_scope(bot, user)
    allowed_collections = _apply_collections_filter(rights_scope, requested_collections)
    if not allowed_collections:
        # Two distinct empty-scope causes, two distinct guard reasons (see
        # GuardTrace's own docstring, app/schemas/chat.py): `rights_scope`
        # itself was already empty (this caller/bot combination has no
        # Collections read-authority at all -- `requested_collections` is
        # irrelevant, it could only ever narrow further) versus
        # `rights_scope` was NOT empty but `requested_collections` (a real
        # filter, not the `None` "no filter" default) excluded every member
        # of it. Checking `requested_collections is not None` here, not
        # merely truthy, matters: an explicit `[]` filter ("match nothing")
        # is still a real filter for this purpose, distinct from sending no
        # `collections` field at all.
        guard_reason = (
            'filter_excluded_all' if rights_scope and requested_collections is not None else 'no_collections'
        )
        return _KnowledgeTurnResult(
            messages=messages,
            sources=[],
            retrieval_trace=RetrievalTrace(
                candidates=0, used=0, collections=[], requested_collections=requested_collections
            ),
            guard_triggered=True,
            guard_reason=guard_reason,
        )

    chunks = retrieval_client.search(
        query=message,
        # exclude_none avoids sending an all-null filters object for a bot
        # whose YAML sets no `retrieval.filters` at all -- `None` and "every
        # field null" mean the identical "no constraint" thing on
        # Weave-Retrieval's own SearchFilters, but the former reads far
        # more clearly in a request log/trace than the latter.
        filters=bot.retrieval.filters.model_dump(exclude_none=True) or None,
        allowed_teams=_allowed_teams(bot, user),
        allowed_collections=allowed_collections,
        top_k=bot.retrieval.top_k,
        final_k=bot.retrieval.final_k,
    )
    # `used` == `candidates` here: every chunk Weave-Retrieval returned (it
    # already trimmed to `final_k` on its own side) is the exact set this
    # pipeline hands to the LLM/reports as `sources` below -- there is no
    # further filtering step on this side that would make the two diverge.
    retrieval_trace = RetrievalTrace(
        candidates=len(chunks), used=len(chunks), collections=allowed_collections,
        requested_collections=requested_collections,
    )

    if not chunks and bot.guard.require_sources:
        return _KnowledgeTurnResult(
            messages=messages,
            sources=[],
            retrieval_trace=retrieval_trace,
            guard_triggered=True,
            guard_reason='no_context',
        )

    if not chunks:
        return _KnowledgeTurnResult(
            messages=messages, sources=[], retrieval_trace=retrieval_trace, guard_triggered=False
        )

    messages_with_context = [*messages, {'role': 'system', 'content': _context_block(chunks)}]
    sources = [_to_source(chunk) for chunk in chunks]
    return _KnowledgeTurnResult(
        messages=messages_with_context, sources=sources, retrieval_trace=retrieval_trace, guard_triggered=False
    )


@dataclass(frozen=True)
class _PreparedTurn:
    """Output of `_prepare_turn` -- the bot-load/permission/router/
    retrieval/guard pipeline stage `handle_chat` and `handle_chat_stream`
    BOTH build on, stopping just short of the one remaining step neither
    caller can share: actually generating the answer text, since one wants
    a single blocking `llm_provider.chat(...)` call and the other an
    incremental `llm_service.iter_chat_stream(...)` (see this module's own
    docstring for the full pipeline `_prepare_turn` runs in full before
    either caller sees it).

    Exactly one of `final_answer`/`messages` is meaningful, discriminated by
    whether `final_answer` is None:

    - `final_answer is not None`: generation is ALREADY decided -- a V1-
      unsupported intent (`_V1_UNSUPPORTED_REPLY`), a guard-triggered
      knowledge turn (`bot.guard.no_context_reply`), or ANY turn at all for
      an n8n-provider bot (`_run_n8n_turn` -- see its own docstring for why
      such a bot's turn is always fully decided by the time this dataclass
      is built, never leaving generation for `handle_chat`/
      `_stream_prepared_turn` to still perform) -- and neither caller is
      meant to invoke an LLM for this turn at all. `messages` is None in
      this case; `llm_provider` is `None` too whenever this outcome came
      from `_run_n8n_turn` (there is no LLMProvider for such a bot -- see
      `_prepare_turn`'s own docstring), though it may still be a real
      provider for a V1-unsupported/guard-triggered outcome from an
      ordinary bot, simply unused by either caller in that case. `sources`
      is `[]` for every one of these EXCEPT a successful (non-guard-
      triggered) n8n answer, which carries n8n's own returned sources
      instead; `guard`/`retrieval` are whatever this outcome's own trace
      fields should be (see `ChatTrace`/`GuardTrace`'s own docstrings).
    - `final_answer is None`: the ordinary case -- `messages` is the fully
      assembled, ready-to-send transcript (system prompt + history +
      optional numbered context block + the user's own message, in that
      order) for the caller to hand to the LLM itself, exactly once, by
      whichever means (blocking or streaming) it needs. `llm_provider` is
      always a real provider here (never `None`) -- this branch is never
      reached for an n8n-provider bot at all.

    `n8n_single_delta` is meaningful only alongside a `final_answer` that
    came from a SUCCESSFUL n8n call (`_run_n8n_turn`'s own last branch) --
    `False` (the default) for every other outcome, including an n8n bot's
    OWN guard-triggered/smalltalk replies, which stream chunked exactly
    like any other fixed text (see `_stream_prepared_turn`'s own docstring
    for why only a genuine n8n answer gets the single-delta treatment).

    `timings_ms` already carries `router_ms`, and `retrieval_ms`/`n8n_ms`
    when a knowledge turn/n8n turn actually ran, respectively --
    `llm_ms`/`total_ms` are each caller's own concern to add once ITS OWN
    generation step (blocking or streaming) finishes, which is exactly why
    neither key is present here yet (an n8n turn's `final_answer` needs no
    further generation step at all -- see above -- so `llm_ms` is never
    added for one, by either caller; `n8n_ms` already stands in its place).

    `n8n_trace` is set (never `None`) for every `_run_n8n_turn` outcome that
    actually called n8n at all -- i.e. every branch except the
    conversational-smalltalk one, see that function's own docstring -- and
    stays at its own default (`None`) for every non-n8n bot and for that one
    conversational exception, exactly mirroring `N8nTrace`'s own docstring.
    """

    bot: BotConfig
    llm_provider: llm_service.LLMProvider | None
    decision: router_service.RouterDecision
    timings_ms: dict[str, float]
    total_start: float
    sources: list[Source]
    retrieval_trace: RetrievalTrace | None
    guard: GuardTrace | None
    final_answer: str | None
    messages: list[dict[str, str]] | None
    n8n_single_delta: bool = False
    n8n_trace: N8nTrace | None = None


def _prepare_turn(request: ChatRequest) -> _PreparedTurn:
    """Bot lookup through the response guard -- everything this module's own
    docstring describes EXCEPT the final LLM-generation call itself. Raises
    exactly what that docstring says `handle_chat` raises/propagates
    (BotNotFoundError, BotPermissionDenied, retrieval_client.
    RetrievalUnavailable/RetrievalError, and -- for an n8n-provider bot,
    see step 3a -- n8n_client.N8nUnavailable/N8nError/delegation.
    DelegationConfigError); both `handle_chat` and `handle_chat_stream` call
    this directly (not from inside a generator) so those exceptions surface
    to app/api/internal.py exactly the same way for either route, before
    either one has produced so much as its first byte of response.
    """
    timings_ms: dict[str, float] = {}
    total_start = time.perf_counter()

    bot = load_bot(request.bot_id)
    _check_permissions(bot, request.user)

    # An n8n-provider bot has no LLMProvider at all (see this module's own
    # docstring, step 3a) -- `llm_provider` stays None for one rather than
    # calling `llm_service.get_llm('n8n')`, which would raise ValueError
    # ('n8n' is not a provider name app/services/llm.py's own get_llm()
    # knows, deliberately: n8n is not a chat-completion API, it is a
    # completely different generation mechanism this module has to
    # special-case BEFORE ever reaching that factory).
    is_n8n_bot = bot.model.provider == 'n8n'
    central_provider = None if is_n8n_bot else fetch_chat_provider()
    if central_provider is not None and central_provider.enabled:
        resolved_temperature = (
            central_provider.temperature
            if central_provider.temperature is not None
            else bot.model.temperature
        )
        bot = bot.model_copy(update={
            'model': bot.model.model_copy(update={
                'provider': 'openai',
                'model': central_provider.model,
                'temperature': resolved_temperature,
            })
        })
        llm_provider = llm_service.OpenAICompatibleLLM(
            base_url=central_provider.base_url,
            api_key=central_provider.api_key,
            model=central_provider.model,
            timeout=central_provider.timeout_seconds,
        )
    else:
        llm_provider = None if is_n8n_bot else llm_service.get_llm(bot.model.provider)

    router_start = time.perf_counter()
    # `llm_call` stays None whenever there is no real LLMProvider to build
    # one from (an n8n bot, see above) even if settings.router_mode ==
    # 'llm' -- router.route() already treats that combination (mode='llm',
    # llm_call=None) as any other 'llm'-mode failure, falling back to RULES
    # with `router_fallback=True` (see router.py's own docstring), exactly
    # the behaviour an n8n bot needs here.
    llm_call = _router_llm_call(llm_provider) if (settings.router_mode == 'llm' and llm_provider is not None) else None
    decision = router_service.route(request.message, bot, settings.router_mode, llm_call)
    timings_ms['router_ms'] = _elapsed_ms(router_start)

    if is_n8n_bot:
        return _run_n8n_turn(bot, request, decision, timings_ms, total_start)

    complex_knowledge_fallback = (
        decision.intent == 'complex' and decision.needs_retrieval and bot.retrieval.enabled
    )
    if decision.intent in _V1_UNSUPPORTED_INTENTS and not complex_knowledge_fallback:
        return _PreparedTurn(
            bot=bot,
            llm_provider=llm_provider,
            decision=decision,
            timings_ms=timings_ms,
            total_start=total_start,
            sources=[],
            retrieval_trace=None,
            guard=None,
            final_answer=_V1_UNSUPPORTED_REPLY,
            messages=None,
        )

    messages: list[dict[str, str]] = [{'role': 'system', 'content': bot.system_prompt}]
    messages.extend({'role': turn.role, 'content': turn.content} for turn in request.history)

    retrieval_trace: RetrievalTrace | None = None
    sources: list[Source] = []

    # Both conditions required -- see this module's own docstring, step 5,
    # for why `needs_retrieval` alone is not enough.
    if decision.needs_retrieval and bot.retrieval.enabled:
        retrieval_start = time.perf_counter()
        result = _run_knowledge_turn(bot, request.user, request.message, messages, request.collections)
        timings_ms['retrieval_ms'] = _elapsed_ms(retrieval_start)

        retrieval_trace = result.retrieval_trace
        if result.guard_triggered:
            # `'filter_excluded_all'` gets its OWN fixed reply
            # (_FILTER_EXCLUDED_ALL_REPLY), never `bot.guard.no_context_reply`
            # -- see that constant's own docstring for why the two must stay
            # visibly distinct to whatever's showing this reply to a human.
            guard_reply = (
                _FILTER_EXCLUDED_ALL_REPLY
                if result.guard_reason == 'filter_excluded_all'
                else bot.guard.no_context_reply
            )
            return _PreparedTurn(
                bot=bot,
                llm_provider=llm_provider,
                decision=decision,
                timings_ms=timings_ms,
                total_start=total_start,
                sources=[],
                retrieval_trace=retrieval_trace,
                guard=GuardTrace(triggered=True, reason=result.guard_reason),
                final_answer=guard_reply,
                messages=None,
            )
        messages = result.messages
        sources = result.sources

    messages.append({'role': 'user', 'content': request.message})

    return _PreparedTurn(
        bot=bot,
        llm_provider=llm_provider,
        decision=decision,
        timings_ms=timings_ms,
        total_start=total_start,
        sources=sources,
        retrieval_trace=retrieval_trace,
        guard=None,
        final_answer=None,
        messages=messages,
    )


def handle_chat(request: ChatRequest) -> ChatResponse:
    """The one entry point app/api/internal.py's POST /internal/chat calls.
    See this module's own docstring for the full pipeline and every
    exception this raises/propagates (BotNotFoundError, BotPermissionDenied,
    retrieval_client.RetrievalUnavailable/RetrievalError) for that route to
    map to a status code -- `_prepare_turn` below performs that entire
    pipeline up to (never including) the LLM-generation step this function
    itself still owns, exactly once, as its own single blocking
    `llm_provider.chat(...)` call.
    """
    prepared = _prepare_turn(request)
    timings_ms = prepared.timings_ms

    if prepared.final_answer is not None:
        timings_ms['total_ms'] = _elapsed_ms(prepared.total_start)
        return ChatResponse(
            answer=prepared.final_answer,
            sources=prepared.sources,
            trace=ChatTrace(
                intent=prepared.decision.intent,
                confidence=prepared.decision.confidence,
                needs_retrieval=prepared.decision.needs_retrieval,
                needs_tool=prepared.decision.needs_tool,
                retrieval=prepared.retrieval_trace,
                model=None,
                router_mode=settings.router_mode,
                timings_ms=timings_ms,
                guard=prepared.guard,
                n8n=prepared.n8n_trace,
            ),
        )

    assert prepared.messages is not None  # see _PreparedTurn's own docstring
    # An n8n-provider bot's `final_answer` is never None (see _PreparedTurn's
    # own docstring), so reaching this line already means `llm_provider` is
    # a real provider, not the n8n-only None case.
    assert prepared.llm_provider is not None
    llm_start = time.perf_counter()
    llm_result = prepared.llm_provider.chat(
        prepared.messages, model=prepared.bot.model.model, temperature=prepared.bot.model.temperature
    )
    timings_ms['llm_ms'] = _elapsed_ms(llm_start)
    timings_ms['total_ms'] = _elapsed_ms(prepared.total_start)

    return ChatResponse(
        answer=llm_result.content,
        sources=prepared.sources,
        trace=ChatTrace(
            intent=prepared.decision.intent,
            confidence=prepared.decision.confidence,
            needs_retrieval=prepared.decision.needs_retrieval,
            needs_tool=prepared.decision.needs_tool,
            retrieval=prepared.retrieval_trace,
            model=llm_result.model,
            router_mode=settings.router_mode,
            timings_ms=timings_ms,
            # `guard` stays at its own default (None) here -- reaching this
            # final return already means the guard did NOT fire (the
            # triggered case returns its own ChatTrace earlier, with
            # guard=GuardTrace(triggered=True, ...)), matching GuardTrace's
            # own docstring: "False/None for every other outcome".
        ),
    )


def handle_chat_stream(request: ChatRequest) -> Iterator[ChatStreamEvent]:
    """The one entry point app/api/internal.py's POST /internal/chat/stream
    calls -- see contracts/internal-chat.md's stream section for the full
    event contract this produces (`trace` once, then zero or more `delta`,
    then either `sources`+`done` or a single terminal `error`).

    Deliberately NOT a generator function itself (nothing in this function's
    own body uses `yield`): `_prepare_turn(request)` -- bot lookup,
    permissions, intent routing, retrieval, the Collections scope, and the
    response guard, ALL of it, per this module's own docstring -- runs to
    completion right here, synchronously, before this function returns
    anything at all. That is what lets app/api/internal.py catch
    BotNotFoundError/BotPermissionDenied/retrieval_client.
    RetrievalUnavailable from THIS call, exactly like it does for
    `handle_chat`, and answer with a normal 401/403/404/503 -- BEFORE any
    StreamingResponse (and the `200 OK` it commits to on its very first
    chunk) has been constructed at all. Had this function been written as a
    generator instead, none of `_prepare_turn`'s own body would run until
    the route started iterating it -- i.e., after the response had already
    started streaming as `200 OK`.

    Only `_stream_prepared_turn` below -- called here, its generator handed
    back unstarted -- ever actually yields an event; a failure from THAT
    point on (an LLMError a real provider raises mid-generation) can no
    longer become an HTTP status the way `_prepare_turn`'s own exceptions
    can, so it becomes an in-band `ChatStreamErrorEvent` instead, exactly
    per contracts/internal-chat.md's own documented distinction between
    "error before the stream" and "error during the stream".
    """
    prepared = _prepare_turn(request)
    return _stream_prepared_turn(prepared)


def _stream_prepared_turn(prepared: _PreparedTurn) -> Iterator[ChatStreamEvent]:
    """The actual event generator behind `handle_chat_stream`, operating
    purely on an already-fully-prepared `_PreparedTurn` -- see that
    function's own docstring for why the two are split apart at all (so
    `_prepare_turn`'s exceptions surface before this generator's first
    `next()` rather than after).

    `trace.model` is set to `prepared.bot.model.model` (the model this turn
    is ABOUT to request) rather than left to whatever the provider's own
    LLMResult/stream ultimately reports, unlike ChatResponse.trace.model on
    the non-streaming response -- there is no way to report the latter
    without delaying the `trace` event past the first `delta`, which
    contracts/internal-chat.md's ordering guarantee forbids outright. This
    is `None` instead, exactly like the non-streaming response, whenever
    `final_answer` is already decided (no LLM call happens at all for this
    turn) -- which, per `_PreparedTurn`'s own docstring, is ALWAYS the case
    for an n8n-provider bot, so `trace.model` is always `None` for one here
    too, same as on the non-streaming response.

    `prepared.n8n_single_delta` (see `_PreparedTurn`'s own docstring) picks
    between two different ways of turning `final_answer` into `delta`
    events, documented in full where each is built below: this is the ONE
    place streaming actually distinguishes an n8n-provider bot's turn from
    any other, everywhere else in this function n8n's outcome is handled by
    the exact same `final_answer is not None` branch a guard/V1-unsupported
    outcome already uses.
    """
    trace = ChatTrace(
        intent=prepared.decision.intent,
        confidence=prepared.decision.confidence,
        needs_retrieval=prepared.decision.needs_retrieval,
        needs_tool=prepared.decision.needs_tool,
        retrieval=prepared.retrieval_trace,
        model=None if prepared.final_answer is not None else prepared.bot.model.model,
        router_mode=settings.router_mode,
        timings_ms=prepared.timings_ms,
        guard=prepared.guard,
        n8n=prepared.n8n_trace,
    )
    yield ChatStreamTraceEvent(trace=trace)

    if prepared.n8n_single_delta:
        # A SUCCESSFUL n8n answer (`_run_n8n_turn`'s own last branch) --
        # unlike every other `final_answer` outcome, this text did not come
        # from this pipeline's own fixed copy, it is n8n's finished, already
        # complete reply: n8n has no streaming contract of its own (see
        # app/services/n8n_client.py's docstring), so there is no smaller,
        # meaningful unit to split it into the way `iter_text_deltas` splits
        # an LLM answer or a fixed guard/placeholder text into word-sized
        # pieces below. Emitted as exactly ONE delta instead (zero deltas
        # for an empty answer, matching `iter_text_deltas`' own "empty text
        # yields nothing" contract) -- a caller sees a stream that
        # genuinely reflects how the answer arrived, rather than this
        # pipeline pretending to a granularity n8n never actually provided.
        deltas: Iterator[str] = iter([prepared.final_answer] if prepared.final_answer else [])
    elif prepared.final_answer is not None:
        # A V1-unsupported intent, a triggered guard (retrieval-backed OR
        # n8n-backed, see _run_n8n_turn), or an n8n bot's own smalltalk
        # reply: every one of these is this pipeline's OWN fixed copy, never
        # touching an LLM (or, for the guard/smalltalk cases, n8n) at all
        # (see _PreparedTurn's own docstring) -- streamed via the exact same
        # chunker (`llm_service.iter_text_deltas`) an LLM answer's own
        # deltas below are built from, so a caller sees ordinary `delta`
        # events either way. The response-guard's sourcing requirement is
        # not weakened by streaming: `prepared.sources` is already `[]`
        # here, same as the non-streaming response for this exact outcome.
        deltas = llm_service.iter_text_deltas(prepared.final_answer)
    else:
        assert prepared.messages is not None  # see _PreparedTurn's own docstring
        # Never an n8n-provider bot here -- see _PreparedTurn's own
        # docstring (`final_answer` is always set for one).
        assert prepared.llm_provider is not None
        deltas = llm_service.iter_chat_stream(
            prepared.llm_provider,
            prepared.messages,
            model=prepared.bot.model.model,
            temperature=prepared.bot.model.temperature,
        )

    try:
        for delta in deltas:
            yield ChatStreamDeltaEvent(text=delta)
    except llm_service.LLMError as exc:
        # Mid-stream failure -- `200 OK`/`text/event-stream` is already
        # committed to the client by now, so this is the only way left to
        # signal it; no `sources`/`done` follow. See this module's own
        # docstring and contracts/internal-chat.md's stream section.
        yield ChatStreamErrorEvent(detail=str(exc))
        return

    yield ChatStreamSourcesEvent(sources=prepared.sources)
    yield ChatStreamDoneEvent()
