"""HTTP client for the 'n8n' bot provider -- hands one full chat turn over
to an n8n agent-flow webhook instead of an LLMProvider (app/services/llm.py)
whenever a bot's `model.provider == 'n8n'` (app/schemas/bot.py's N8nConfig).
See contracts/n8n-flow.md for the complete, n8n-implementer-facing contract
this module is one half of: what the webhook receives, what it must return,
how it verifies the request actually came from Weave-Runtime, and how it is
expected to use the delegation token against Weave-Tools. This module is
the CALLING half only -- Weave-Runtime never implements the n8n side of
that contract itself.

`run_flow` is the one entry point app/services/chat.py's `_run_n8n_turn`
calls. Unlike app/services/llm.py's OpenAICompatibleLLM or
app/services/retrieval_client.py's `search()`, there is no streaming
variant and no retry policy here:

- No streaming: an n8n workflow answers as one HTTP response once its
  entire flow (tool calls, an LLM step inside n8n itself, whatever the flow
  actually does) has finished -- there is no SSE/incremental response
  contract for an n8n webhook the way app/services/llm.py's `chat_stream`
  has for an OpenAI-compatible endpoint. app/services/chat.py's own
  `_stream_prepared_turn` accounts for this explicitly: a successful n8n
  answer is emitted as exactly ONE `ChatStreamDeltaEvent`, never chunked
  the way a guard/V1-placeholder reply is (see that module's own comment on
  `n8n_single_delta`) -- there is no meaningfully smaller unit to split it
  into, and pretending otherwise would misrepresent how the answer actually
  arrived.
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
