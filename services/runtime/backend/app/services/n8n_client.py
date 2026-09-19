"""HTTP client for the 'n8n' bot provider -- hands one full chat turn over
to an n8n agent-flow webhook instead of an LLMProvider (app/services/llm.py)
whenever a bot's `model.provider == 'n8n'` (app/schemas/bot.py's N8nConfig).
See contracts/n8n-flow.md for the complete, n8n-implementer-facing contract
this module is one half of: what the webhook receives, what it must return,
how it verifies the request actually came from Weave-Runtime, and how it is
expected to use the delegation token against Weave-Tools. This module is
the CALLING half only -- Weave-Runtime never implements the n8n side of
that contract itself.

`run_flow` is the entry point app/services/chat.py's `_run_n8n_turn` (the
blocking POST /internal/chat path, and any bot with `n8n.streaming=False`
even on the streaming route) calls; `run_flow_stream` (added for contract
v2, see its own docstring below) is the one a bot with `n8n.streaming=True`
uses instead, from inside app/services/chat.py's `_stream_deferred_n8n`.
Unlike app/services/llm.py's OpenAICompatibleLLM or
app/services/retrieval_client.py's `search()`, there is no retry policy
here:

- No streaming on `run_flow`: an n8n workflow answers as one HTTP response
  once its entire flow (tool calls, an LLM step inside n8n itself, whatever
  the flow actually does) has finished. app/services/chat.py's own
  `_stream_prepared_turn`/`_stream_deferred_n8n` account for this
  explicitly: a successful, non-streaming n8n answer is emitted as exactly
  ONE `ChatStreamDeltaEvent`, never chunked the way a guard/V1-placeholder
  reply is -- there is no meaningfully smaller unit to split it into, and
  pretending otherwise would misrepresent how the answer actually arrived.
  A bot that opts into `n8n.streaming=True` gets `run_flow_stream`'s real,
  incremental deltas instead -- see that function's own docstring for the
  two wire shapes it accepts.
- No retries: same reasoning as `retrieval_client.search()`'s own docstring
  -- this sits on the hot path of an interactive chat turn, and an n8n flow
  doing real tool/search work is already the slowest hop in this pipeline
  by a wide margin (hence N8nConfig.timeout_seconds' own much longer
  default than settings.llm_timeout_seconds). Retrying a request that may
  have already triggered a real-world side effect inside the flow (a tool
  call that sends an email, books something, ...) would risk doing that
  side effect twice -- a caller that has already budgeted the configured
  timeout for one attempt wants a fast, clear signal instead
  (N8nUnavailable, mapped to 503 by app/api/internal.py -- README's
  Response Guard applies here just as much as it does to a real LLM
  provider's own failure).
"""

import hashlib
import hmac
import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.bot import BotConfig
from app.schemas.chat import ChatUser, Source
from app.services.delegation import DelegationConfigError, mint_delegation_token

logger = logging.getLogger(__name__)


class N8nError(Exception):
    """n8n was reached and answered, but the turn itself failed: any 4xx
    response (the flow itself rejected the request, e.g. a malformed body
    it validates on its own side), or a 2xx response whose body doesn't
    parse as `{"answer": <str>, "sources"?: [...]}` (see `_parse_response`).
    Never retryable by resending the identical request -- an unchanged
    request against an unchanged, misbehaving flow would only fail the same
    way again. Deliberately left UNCAUGHT by app/api/internal.py (surfacing
    as this service's default 500), the same treatment
    app/services/retrieval_client.py's RetrievalError gets and for the same
    reason: this can only mean the n8n flow itself (reachable, addressed by
    a webhook_url that already passed the SSRF allowlist at bot-load time)
    is misconfigured or broken -- a deployment bug in that flow, not a
    transient condition worth a dedicated status code the way
    N8nUnavailable below is.
    """


class N8nUnavailable(Exception):
    """n8n could not be reached at all (connection failure, timeout) or
    responded with a 5xx -- the exact same "the upstream SERVICE is the
    problem, retrying later (not now, and not by this client) might
    succeed" split app/services/retrieval_client.py's own RetrievalUnavailable
    documents for Weave-Retrieval. Mapped to 503 by app/api/internal.py,
    never a silent unsourced answer for a bot with `guard.require_sources`
    -- README's Response Guard applies to n8n-provider bots exactly like it
    does to any retrieval-backed one.
    """


@dataclass(frozen=True)
class N8nResult:
    """`run_flow`'s return value -- `answer` is the flow's finished reply
    text (never chunked further by this module, see its own docstring on
    why n8n has no streaming variant); `sources` defaults to `[]` for a flow
    response that omits the field entirely (an ordinary, unremarkable case
    -- `sources` is documented as optional on the wire, see
    contracts/n8n-flow.md), NOT a sign anything went wrong on its own.
    app/services/chat.py's `_run_n8n_turn` is the one place that turns an
    EMPTY `sources` list into a triggered guard, and only when
    `bot.guard.require_sources` is set -- this dataclass itself makes no
    such judgment.
    """

    answer: str
    sources: list[Source] = field(default_factory=list)


def _canonical_body_bytes(payload: dict) -> bytes:
    """The exact byte string sent as this request's body -- built once, by
    hand, with a deterministic encoding (`sort_keys=True,
    separators=(',', ':')`, the same convention app/services/delegation.py's
    own `_canonical_json_bytes` uses for the token payload it signs)
    specifically so the `X-Weave-Signature` header computed over these same
    bytes (see `_signature` below) covers PRECISELY what n8n's flow reads
    back off the wire -- letting httpx re-serialize a plain dict via its own
    `json=` kwarg instead would leave the signature covering bytes that were
    never actually transmitted, an easy way to accidentally ship a
    signature verification that always fails (or, worse, one a verifier
    quietly stops checking because it never validates).
    """
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _signature(body: bytes) -> str:
    """`X-Weave-Signature`: HMAC-SHA256 over the raw request body bytes,
    keyed with the SAME `WEAVE_DELEGATION_SECRET` the embedded delegation
    token is itself signed with (see app/services/delegation.py) -- a
    second, independent use of that one shared secret, deliberately: the
    delegation token proves what the calling HUMAN may read; this signature
    proves the REQUEST ITSELF genuinely came from Weave-Runtime, not from
    anyone else who merely learned this bot's webhook_url. Hex-encoded
    (`.hexdigest()`), not base64url -- an HTTP header value, no need for the
    delegation token's own URL-safety concerns. See contracts/n8n-flow.md
    for exactly how n8n's own flow is expected to verify this
    (`hmac.compare_digest`, constant-time, same discipline as
    app/core/auth.py's own bearer-token comparison).
    """
    # Fail closed on its own, independent of call order. run_flow() happens
    # to mint the delegation token (which refuses an empty secret) before
    # reaching this function, so today an unset secret can never get here --
    # but relying on that makes the guarantee a property of one call site
    # instead of this function. An empty key produces a perfectly valid HMAC,
    # so anyone who knows the wire format could forge this header.
    if not settings.weave_delegation_secret:
        raise DelegationConfigError(
            'WEAVE_DELEGATION_SECRET is not configured -- refusing to sign an n8n request'
        )
    return hmac.new(settings.weave_delegation_secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


def _parse_response(data: object, *, webhook_url: str) -> N8nResult:
    """Parse a 2xx response body as `{"answer": <str>, "sources"?: [...]}` --
    raises N8nError (see its own docstring) for anything that doesn't match,
    naming `webhook_url` so an operator can tell which bot's flow is
    misbehaving without this function's caller having to add that context
    itself.
    """
    if not isinstance(data, dict):
        raise N8nError(f'n8n webhook {webhook_url!r} returned a non-object response body')

    answer = data.get('answer')
    if not isinstance(answer, str):
        raise N8nError(f"n8n webhook {webhook_url!r} response has no string 'answer' field")

    sources_raw = data.get('sources')
    if sources_raw is None:
        sources_raw = []
    if not isinstance(sources_raw, list):
        raise N8nError(f"n8n webhook {webhook_url!r} response has a non-list 'sources' field")

    try:
        sources = [Source.model_validate(item) for item in sources_raw]
    except ValidationError as exc:
        raise N8nError(f'n8n webhook {webhook_url!r} returned an invalid source entry: {exc}') from exc

    return N8nResult(answer=answer, sources=sources)


def run_flow(
    bot: BotConfig,
    message: str,
    history: list[dict[str, str]],
    user: ChatUser,
    scope: list[str],
) -> N8nResult:
    """POST `bot.n8n.webhook_url` (already SSRF-allowlist-checked at bot
    LOAD time, see app/services/botconfig.py -- never re-checked here) with
    the body contracts/n8n-flow.md documents in full:

        {"message": ..., "history": [{"role": ..., "content": ...}, ...],
         "user": {"id": ..., "username": ..., "team": ...}, "bot_id": ...,
         "allowed_collections": <scope, unchanged>,
         "delegation_token": <freshly minted, see below>,
         "tools_base_url": settings.tools_base_url}

    `scope` is taken as-is, straight from the caller (app/services/chat.py's
    `_run_n8n_turn`, which resolves it via the exact same
    `resolve_collection_scope` every retrieval-backed bot uses) -- this
    function makes no access-control decision of its own, it only (a)
    signs `scope` into a fresh delegation token via
    `mint_delegation_token(user, scope, bot.id)` -- freshly minted on EVERY
    call, never cached/reused across turns, so its `iat`/`exp` window always
    reflects the moment THIS specific webhook call is made -- and (b)
    forwards it unchanged as `allowed_collections`, so n8n's own flow can
    see the scope without having to decode the token just to log/branch on
    it (the token remains the one place that scope is CRYPTOGRAPHICALLY
    authoritative; `allowed_collections` here is a convenience mirror of
    it, never trusted on its own by a correctly-implemented verifier on the
    Weave-Tools side -- see contracts/n8n-flow.md's "Grundregel Rechte").

    Propagates `DelegationConfigError` (app/services/delegation.py)
    unchanged if `WEAVE_DELEGATION_SECRET` isn't configured -- a deployment
    misconfiguration, left uncaught by design (see that exception's own
    docstring), never something this function silently degrades around.

    Raises N8nUnavailable for a network-level failure/timeout/5xx response,
    N8nError for any other 4xx or a 2xx body that doesn't parse per
    `_parse_response` above -- see both exceptions' own docstrings for how
    app/api/internal.py treats each.
    """
    token = mint_delegation_token(user, scope, bot.id)
    payload = {
        'message': message,
        'history': history,
        'user': {'id': user.id, 'username': user.username, 'team': user.team, 'teams': user.effective_teams},
        'bot_id': bot.id,
        'allowed_collections': scope,
        'delegation_token': token,
        'tools_base_url': settings.tools_base_url,
    }
    body = _canonical_body_bytes(payload)
    headers = {'Content-Type': 'application/json', 'X-Weave-Signature': _signature(body)}
    if bot.n8n.auth_token:
        headers['Authorization'] = f'Bearer {bot.n8n.auth_token.get_secret_value()}'
    webhook_url = bot.n8n.webhook_url  # never None here -- see BotConfig's own _n8n_block_matches_provider

    try:
        response = httpx.post(webhook_url, content=body, headers=headers, timeout=bot.n8n.timeout_seconds)
    except httpx.HTTPError as exc:
        # Never log/include `token`/`body` here -- see this module's and
        # app/services/delegation.py's own docstrings on why the token must
        # never reach a log line or an exception message.
        raise N8nUnavailable(f'n8n webhook {webhook_url!r} unreachable: {exc}') from exc

    if response.status_code >= 500:
        raise N8nUnavailable(f'n8n webhook {webhook_url!r} returned HTTP {response.status_code}')

    if response.status_code >= 400:
        raise N8nError(f'n8n webhook {webhook_url!r} returned HTTP {response.status_code}: {response.text[:500]}')

    try:
        data = response.json()
    except ValueError as exc:
        raise N8nError(f'n8n webhook {webhook_url!r} returned a non-JSON response') from exc

    return _parse_response(data, webhook_url=webhook_url)


# --- streaming (contract v2) --------------------------------------------------
#
# See contracts/n8n-flow.md's "Streaming" section for the full, n8n-
# implementer-facing contract these dataclasses/functions are one half of.
# A bot opts in via `N8nConfig.streaming=True`; everything below is unused
# for any bot that leaves it at its default False (`run_flow` above is the
# whole story for one of those, on either the blocking or the streaming
# route -- see app/services/chat.py's `_stream_deferred_n8n`).

# A small, fixed connect timeout deliberately independent of
# `bot.n8n.timeout_seconds`: THAT setting is the per-chunk IDLE budget (see
# `run_flow_stream`'s own docstring) -- a flow legitimately allowed to run
# for hours because it keeps emitting items has no bearing on how long
# merely opening the TCP/TLS connection to its webhook should ever
# reasonably take. Matches settings.retrieval_timeout_seconds' own 10s
# default (app/core/config.py) -- both are "reach a configured internal
# endpoint at all" budgets, not "do the actual work" ones.
_STREAM_CONNECT_TIMEOUT_SECONDS = 10.0

# Sent as `Accept` on the streaming request only (`run_flow` above never
# sends one -- a bot with streaming off is never expected to answer with
# anything but a single JSON body). Every content-type this module knows
# how to parse, listed so an n8n flow's own content-negotiation can pick
# whichever it actually implements; see `_parse_stream_response` for what
# each one means on the way back.
_STREAM_ACCEPT = 'text/event-stream, application/json-lines, application/x-ndjson, application/json'


@dataclass(frozen=True)
class N8nStreamDelta:
    """One incremental piece of the answer text -- the streaming analogue
    of `N8nResult.answer`, except there may be many of these instead of
    one. Concatenating every `N8nStreamDelta.text` a `run_flow_stream` call
    yields, in order, reconstructs the same answer `run_flow` would have
    returned in full for an identical, non-streaming request to the same
    flow."""

    text: str


@dataclass(frozen=True)
class N8nStreamSources:
    """The streaming analogue of `N8nResult.sources` -- sent (at most) once
    per call. Our own SSE contract (see `_iter_sse_frames`) sends this
    explicitly; n8n's OWN native streaming mode (see `_iter_ndjson_frames`)
    has no field for sources at all, so a call parsed via that path never
    yields one -- app/services/chat.py's caller must treat "no
    N8nStreamSources arrived before N8nStreamDone" as `sources=[]`, exactly
    like `N8nResult.sources`' own "omitted on the wire" default."""

    sources: list[Source] = field(default_factory=list)


@dataclass(frozen=True)
class N8nStreamDone:
    """Terminal event on a call that completed without error. Nothing
    follows it -- mirrors `ChatStreamDoneEvent` (app/schemas/chat.py) one
    layer down."""


@dataclass(frozen=True)
class N8nStreamError:
    """Terminal event in place of `N8nStreamDone` when the FLOW ITSELF
    reports failure mid-stream (our own SSE contract's `{"type": "error"}`,
    or n8n's native `{"type": "error", "content": ...}`) -- as opposed to a
    transport-level failure (connection drop, non-2xx status, malformed
    JSON), which `run_flow_stream` raises as `N8nUnavailable`/`N8nError`
    instead, exactly like `run_flow` does. `detail` is never shown to an
    end user verbatim by app/services/chat.py (it becomes a
    `ChatStreamErrorEvent.detail`, the same "no secrets/URLs beyond what
    today's messages already carry" bar every other error path in this
    module holds itself to) but this dataclass itself does no redaction --
    that is the flow author's own responsibility, exactly like n8n's answer
    text itself is."""

    detail: str


N8nStreamEvent = N8nStreamDelta | N8nStreamSources | N8nStreamDone | N8nStreamError


def _iter_sse_frames(response: httpx.Response, *, webhook_url: str, cancel: threading.Event | None) -> Iterator[N8nStreamEvent]:
    """Parse `response` as OUR OWN SSE contract (contracts/n8n-flow.md,
    `Content-Type: text/event-stream`): one `'data: <json>'` line per
    event, each JSON object carrying a `type` in `{delta, sources, done,
    error}` -- deliberately the SAME shape app/schemas/chat.py's own
    `ChatStreamEvent` union uses on the wire one layer further out, so an
    n8n flow implementing this contract needs to reason about exactly one
    event shape, not two. Blank lines and SSE comment lines (a bare `:`
    prefix -- n8n's own keepalive, if it sends one, or ours) are silently
    ignored, mirroring app/services/llm.py's `_iter_sse_deltas`. An unknown
    `type` is ignored rather than rejected -- a deliberate forward-
    compatibility allowance, exactly like `_parse_stream_response`'s JSON
    fallback below tolerates fields it doesn't recognise.

    Raises N8nError for a data line that isn't valid JSON, isn't a JSON
    object, or whose `sources` entries don't validate as `Source` --  the
    same "malformed response is a flow bug, not a transient condition"
    classification `_parse_response` already applies to the non-streaming
    contract. `cancel`, when set between two lines, ends the generator
    early (no further event is yielded, including no synthetic `done`) --
    see `run_flow_stream`'s own docstring for who sets it and why.
    """
    for raw_line in response.iter_lines():
        if cancel is not None and cancel.is_set():
            return
        line = raw_line.strip()
        if not line or line.startswith(':') or not line.startswith('data:'):
            continue
        raw_data = line[len('data:'):].strip()
        try:
            event = json.loads(raw_data)
        except ValueError as exc:
            raise N8nError(f'n8n webhook {webhook_url!r} sent a non-JSON SSE data line: {raw_data[:200]!r}') from exc
        if not isinstance(event, dict):
            raise N8nError(f'n8n webhook {webhook_url!r} sent a non-object SSE event: {raw_data[:200]!r}')

        kind = event.get('type')
        if kind == 'delta':
            text = event.get('text')
            if isinstance(text, str) and text:
                yield N8nStreamDelta(text=text)
        elif kind == 'sources':
            sources_raw = event.get('sources') or []
            if not isinstance(sources_raw, list):
                raise N8nError(f"n8n webhook {webhook_url!r} sent a non-list 'sources' field: {raw_data[:200]!r}")
            try:
                sources = [Source.model_validate(item) for item in sources_raw]
            except ValidationError as exc:
                raise N8nError(f'n8n webhook {webhook_url!r} sent an invalid source entry: {exc}') from exc
            yield N8nStreamSources(sources=sources)
        elif kind == 'done':
            yield N8nStreamDone()
            return
        elif kind == 'error':
            yield N8nStreamError(detail=str(event.get('detail') or 'n8n flow reported an error'))
            return
        # else: unknown type, ignored -- see this function's own docstring.


def _iter_ndjson_frames(response: httpx.Response, *, webhook_url: str, cancel: threading.Event | None) -> Iterator[N8nStreamEvent]:
    """Parse `response` as n8n's OWN native "Streaming response" mode
    (Webhook/Respond-to-Webhook/Chat-Trigger, response mode "Streaming") --
    `Content-Type: application/json-lines`/`application/x-ndjson`/
    `application/jsonl`, or `text/plain` sent one JSON object per line, the
    same wire shape under a content-type some n8n versions/proxies label
    generically. n8n's docs (docs.n8n.io) confirm this mode exists
    ("sends the data back to the user using streaming... trigger configured
    with Response mode Streaming") but not its exact per-chunk field names
    at the time this was written -- see contracts/n8n-flow.md's own
    "Streaming" section for the flagged-for-re-verification note. This
    parser is deliberately TOLERANT rather than hard-coded to one schema,
    per that section's own fallback: any object with a string `content`
    field and `type` in `{item, chunk}` is a delta; `type` in `{end, done,
    complete}` ends the call successfully, with NO sources (n8n's native
    mode has no sources field at all -- see `N8nStreamSources`'s own
    docstring); `type == 'error'` (with `content` as the detail) ends it as
    a flow-reported error; any other `type` (e.g. n8n's own `'begin'`) is
    ignored, exactly like an unrecognised SSE event `type` above. A line
    that isn't valid JSON raises N8nError (same "malformed is a flow bug"
    classification); a line that IS valid JSON but not an object is
    silently skipped rather than rejected -- tolerance, not validation, is
    this parser's whole reason to exist.
    """
    for raw_line in response.iter_lines():
        if cancel is not None and cancel.is_set():
            return
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise N8nError(f'n8n webhook {webhook_url!r} sent a non-JSON streaming line: {line[:200]!r}') from exc
        if not isinstance(event, dict):
            continue

        kind = event.get('type')
        content = event.get('content')
        if kind in ('item', 'chunk'):
            if isinstance(content, str) and content:
                yield N8nStreamDelta(text=content)
        elif kind in ('end', 'done', 'complete'):
            yield N8nStreamDone()
            return
        elif kind == 'error':
            yield N8nStreamError(detail=content if isinstance(content, str) and content else 'n8n flow reported an error')
            return
        # else: unknown type (e.g. 'begin'), ignored -- see this function's own docstring.


def _parse_stream_response(
    response: httpx.Response, *, content_type: str, webhook_url: str, cancel: threading.Event | None
) -> Iterator[N8nStreamEvent]:
    """Dispatch on `content_type` to one of the two streaming parsers above,
    or -- for `application/json` (or anything else this function doesn't
    otherwise recognise, including a missing header) -- fall back to
    parsing the ENTIRE body exactly like `_parse_response` does for
    `run_flow`'s non-streaming contract, then replay it as one delta (if
    the answer is non-empty) plus sources plus done. This fallback is what
    lets an operator flip `n8n.streaming=True` on a bot whose flow doesn't
    actually implement either streaming response shape yet without
    breaking that bot -- see this module's own docstring.
    """
    media_type = content_type.split(';', 1)[0].strip().lower()
    if media_type == 'text/event-stream':
        yield from _iter_sse_frames(response, webhook_url=webhook_url, cancel=cancel)
        return
    if media_type in ('application/json-lines', 'application/x-ndjson', 'application/jsonl') or media_type.startswith('text/plain'):
        yield from _iter_ndjson_frames(response, webhook_url=webhook_url, cancel=cancel)
        return

    # application/json, or anything unrecognised -- the non-streaming
    # fallback described above.
    response.read()
    try:
        data = response.json()
    except ValueError as exc:
        raise N8nError(f'n8n webhook {webhook_url!r} returned a non-JSON response') from exc
    result = _parse_response(data, webhook_url=webhook_url)
    if result.answer:
        yield N8nStreamDelta(text=result.answer)
    yield N8nStreamSources(sources=result.sources)
    yield N8nStreamDone()


def run_flow_stream(
    bot: BotConfig,
    message: str,
    history: list[dict[str, str]],
    user: ChatUser,
    scope: list[str],
    *,
    cancel: threading.Event | None = None,
) -> Iterator[N8nStreamEvent]:
    """`run_flow`'s streaming counterpart, for a bot with `n8n.streaming=
    True` (app/services/chat.py's `_stream_deferred_n8n` is the one caller).
    Builds and signs the IDENTICAL request `run_flow` does, plus one
    contract-v2 addition: `"stream": true` in the payload, added BEFORE
    `_canonical_body_bytes`/`_signature` run, so the signature covers
    exactly the bytes actually sent (same discipline `_signature`'s own
    docstring describes -- there is no separate signing path for this
    field). Sends `Accept: {_STREAM_ACCEPT!r}` so a flow that content-
    negotiates can pick whichever shape it implements; see
    `_parse_stream_response` for how each possible `Content-Type` in the
    response is handled.

    Timeouts are SPLIT, unlike `run_flow`'s single value: `connect` (and
    `write`/`pool`) stay at `_STREAM_CONNECT_TIMEOUT_SECONDS`, a small fixed
    budget for reaching the webhook at all, while `read` is
    `bot.n8n.timeout_seconds` -- httpx's own `read` timeout already applies
    PER SOCKET READ, not to the call as a whole, so this is naturally an
    IDLE/inter-chunk timeout: a flow that keeps emitting items (or even
    just its own transport-level keepalive bytes) at least that often may
    run for hours, exactly per contracts/n8n-flow.md's "Streaming" section.

    `cancel`, when given, is checked between parsed lines/events by
    whichever of `_iter_sse_frames`/`_iter_ndjson_frames` this call ends up
    using -- app/services/chat.py's `_stream_deferred_n8n` sets it when the
    CLIENT of the outer SSE response has gone away (a `GeneratorExit`
    reaching that function), so this call's own background thread notices
    and stops forwarding further events on its next parsed line rather than
    running to completion for nobody. This is a best-effort stop, not an
    instant one: a read already blocked waiting for the flow's NEXT line is
    only interrupted once that line arrives (or the idle timeout above
    fires) -- there is no lower-level hook this module reaches for to abort
    a read already in flight, exactly like `run_flow`'s own single blocking
    call has never been cancellable either. The JSON-fallback branch
    (`_parse_stream_response`'s last case) is likewise not interruptible
    mid-read -- it is exactly `run_flow`'s existing blocking behaviour by
    another name.

    Raises N8nUnavailable for a transport-level failure/non-2xx-and->=500
    response, N8nError for a 4xx or a malformed body -- identical
    classification to `run_flow`, see that function's own docstring. A
    flow-reported `{"type": "error"}` mid-stream is NOT one of these two --
    see `N8nStreamError`'s own docstring for why that is a yielded event,
    not a raised exception, here.
    """
    token = mint_delegation_token(user, scope, bot.id)
    payload = {
        'message': message,
        'history': history,
        'user': {'id': user.id, 'username': user.username, 'team': user.team, 'teams': user.effective_teams},
        'bot_id': bot.id,
        'allowed_collections': scope,
        'delegation_token': token,
        'tools_base_url': settings.tools_base_url,
        'stream': True,
    }
    body = _canonical_body_bytes(payload)
    headers = {
        'Content-Type': 'application/json',
        'Accept': _STREAM_ACCEPT,
        'X-Weave-Signature': _signature(body),
    }
    if bot.n8n.auth_token:
        headers['Authorization'] = f'Bearer {bot.n8n.auth_token.get_secret_value()}'
    webhook_url = bot.n8n.webhook_url  # never None here -- see BotConfig's own _n8n_block_matches_provider

    timeout = httpx.Timeout(
        connect=_STREAM_CONNECT_TIMEOUT_SECONDS,
        read=bot.n8n.timeout_seconds,
        write=_STREAM_CONNECT_TIMEOUT_SECONDS,
        pool=_STREAM_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        with httpx.stream('POST', webhook_url, content=body, headers=headers, timeout=timeout) as response:
            if response.status_code >= 500:
                response.read()
                raise N8nUnavailable(f'n8n webhook {webhook_url!r} returned HTTP {response.status_code}')
            if response.status_code >= 400:
                response.read()
                raise N8nError(
                    f'n8n webhook {webhook_url!r} returned HTTP {response.status_code}: {response.text[:500]}'
                )
            content_type = response.headers.get('content-type', '')
            yield from _parse_stream_response(response, content_type=content_type, webhook_url=webhook_url, cancel=cancel)
    except httpx.HTTPError as exc:
        # Never log/include `token`/`body` here -- see run_flow's identical comment.
        raise N8nUnavailable(f'n8n webhook {webhook_url!r} unreachable: {exc}') from exc
