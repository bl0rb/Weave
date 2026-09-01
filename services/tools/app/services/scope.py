"""Scope resolution -- the single place in this whole service that decides
"whose reading rights does this request run under, and which collections
does that cover". Every tool (app/mcp_server.py's list_collections/search,
app/api/tools.py's REST mirror of both) calls resolve_scope() itself, fresh,
on every single call; nothing else in this codebase is allowed to construct
a Scope by any other path, and no caller-supplied argument (a `collection`
on `search`, or anything else a request body/tool-call might carry) is ever
treated as a grant -- see Scope's own docstring and the Grundregel Rechte
this whole contract exists to enforce.

Two ways to arrive at a Scope, chosen purely by shape of the raw bearer
token (never by a client-declared "type" field, which would just be another
unauthenticated input to trust):

1. A Delegations-Token: a short-lived, HMAC-signed credential Weave-Runtime
   mints so it can loan an external agent (n8n, an MCP client) EXACTLY the
   reading rights of the human who is currently asking, without handing
   that agent a copy of the human's own long-lived Personal-Token. The
   token IS the scope -- everything resolve_scope needs is embedded in its
   signed payload, so this path never calls another Weave service at all.

2. A Personal-Token (an end user's own long-lived API token, as issued by
   Weave-API): resolved by asking Weave-API's own introspection endpoint
   who it belongs to, then asking Weave-Retrieval which collections that
   person's team may read. Two outbound HTTP calls, every time.

Deliberately no caching of either path's result across requests: the whole
point of a scope that can be resolved fresh on every call is that revoking
a Personal-Token (Weave-API disables it) or a team's access to a collection
(Weave-Retrieval's own registry changes) takes effect on this service's very
next request, not whenever some TTL happens to expire. A Delegations-Token
already carries its own short TTL (settings.delegation_token_ttl_seconds)
for exactly the same reason -- see issue_delegation_token below.

--- Delegations-Token wire format -----------------------------------------

Deliberately dependency-free (only hmac/hashlib/json/base64.b64/time from
the standard library) -- this is NOT a JWT (no header segment, no algorithm
negotiation: there is exactly one algorithm, HMAC-SHA256, and exactly one
key, settings.weave_delegation_secret, so there is nothing for a header
segment to usefully name) and pulling in a JWT library for a two-field,
one-algorithm token would be pure surface area.

    token = b64url(json(payload)) + "." + b64url(hmac_sha256(secret, b64url(json(payload))))

`b64url` is urlsafe base64 WITHOUT padding (`=` stripped on encode, restored
on decode -- see _b64url_encode/_b64url_decode). `json(payload)` is
`json.dumps(payload, sort_keys=True, separators=(',', ':'))`: sorted keys and
no whitespace make the encoding deterministic, which matters here because
the HMAC is computed over this exact string -- two callers must arrive at
byte-identical bytes for the same payload, or a correct signature computed
by one would fail verification by the other for no security reason at all.

`payload`:
    v:           int, always 1 today (DELEGATION_TOKEN_VERSION). Bumped only
                 on a breaking wire-format change; resolve_scope() rejects
                 anything else.
    sub:         str, the human's own Weave-API user id.
    username:    str.
    team:        str | None.
    collections: list[str], the exact set of collection slugs this loan
                 covers -- MAY include NO_COLLECTION_SENTINEL
                 ("__none__", Weave-Retrieval's own sentinel for "documents
                 with no collection at all" -- see that service's
                 app/services/search.py) alongside real slugs, carried
                 through unexamined; app/services/tools.py is the one place
                 that gives this list any further meaning.
    bot:         str | None, the id of the bot/workflow this loan was minted
                 for, when applicable. Never consulted for access control by
                 this service -- purely informational/audit context, exactly
                 like `username`.
    iat:         int, unix seconds, when this token was minted.
    exp:         int, unix seconds, when this token stops being accepted.

Both `sub`/`username`/`team`/`collections` here are the issuer's (Weave-
Runtime's) own already-resolved answer to "what may this human read right
now" -- baking them into the signed payload, rather than a bare user id this
service would have to re-resolve itself, is exactly what lets this whole
path skip calling Weave-API and Weave-Retrieval a second time; resolve_scope()
trusts them exactly as far as the signature does.

--- Verification discipline ------------------------------------------------

hmac.compare_digest for the signature (constant-time; a byte-length branch
elsewhere in this function is fine since the payload's length is not a
secret, but a byte-by-byte comparison of the signature itself must never
let response timing leak how many leading bytes matched). `v == 1`, then
`exp > now` with NO clock-skew leeway at all (0 seconds) -- comfortably
under the contract's own "no window bigger than 60s" ceiling, and simpler to
reason about than picking a nonzero number for no concrete reason.

Every failure mode -- bad base64, invalid JSON, a missing/wrong-typed field,
wrong version, a bad signature, or an expired token -- raises the exact same
ScopeError with the exact same generic message. This is deliberate and
non-negotiable (see the contract): a distinguishable error ("signature
invalid" vs. "token expired" vs. "malformed payload") hands an attacker a
free oracle for probing the verifier, and there is no legitimate caller who
needs to tell these apart either -- a rejected token is a rejected token.

Before any of that: settings.weave_delegation_secret itself must be
non-empty, checked and rejected with a SEPARATE exception,
ScopeConfigurationError (503 via app/api/deps.py, not 401), before a single
byte of the token is even looked at. This is not merely one more entry in
the collapsed-ScopeError list above -- `hmac.new(key=b'', ...)` against an
empty secret is not an error at all, it is a valid, deterministic
signature. Skipping this check would mean any caller who has read this
very docstring (the wire format is documented, on purpose, right here)
could compute that signature themselves and forge a token carrying
whatever `collections` it likes, on any deployment where the secret is
merely unset -- a typo in the env, a missing entry, drift between two
deployments that must share one value. See ScopeConfigurationError's own
docstring.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_BEARER_PREFIX = 'Bearer '

# The one and only generic message every ScopeError carries -- see this
# module's own docstring ("Verification discipline") for why no failure
# path is ever allowed to say more than this, and why nothing in this
# module ever logs a raw token (see resolve_scope's docstring).
_GENERIC_AUTH_ERROR = 'invalid or expired token'

DELEGATION_TOKEN_VERSION = 1


class ScopeError(Exception):
    """Raised for any failure resolving an Authorization header to a Scope.
    Always carries `_GENERIC_AUTH_ERROR` -- see this module's own docstring.
    The FastAPI layer (app/api/deps.py) turns this into a 401; app/mcp_server.py
    turns it into a plain ValueError with the same generic text.
    """


# The one and only message every ScopeConfigurationError carries -- deliberately
# distinct from _GENERIC_AUTH_ERROR (never mentions the secret itself, but is
# free to say "misconfigured" since, unlike a rejected token, there is no
# attacker-facing oracle risk here: this fires the same way for every caller,
# regardless of what they presented, until an operator fixes the deployment).
_SERVICE_MISCONFIGURED_ERROR = 'service misconfigured'


class ScopeConfigurationError(Exception):
    """Raised when this deployment cannot safely verify ANY Delegations-Token
    because settings.weave_delegation_secret is itself unset -- a typo in the
    env, a missing entry, or drift between two deployments that are supposed
    to share one secret (see app/core/config.py's own docstring for this
    setting, and this module's own docstring for the wire format). Always
    carries `_SERVICE_MISCONFIGURED_ERROR`.

    Deliberately NOT a ScopeError, and never allowed to collapse into one:
    a ScopeError means "the caller's own credential is invalid or expired"
    (the caller's fault, and app/api/deps.py answers 401 for exactly that);
    this means "this deployment cannot verify anything right now regardless
    of what the caller presented" (an operator's fault, 503) -- the same
    fail-closed distinction app/api/deps.py's require_tools_service_token
    already draws for the coarser TOOLS_API_TOKEN gate. Confusing the two
    would let an operator's own config mistake masquerade as "your token is
    wrong" -- and on this specific path it would be worse than a confusing
    error message: `hmac.new(key=b'', ...)` against an empty secret computes
    a perfectly well-defined signature that anyone who has read this
    module's own documented wire format could compute for themselves and
    forge a token carrying whatever `collections` they like. An empty
    secret must therefore never even reach hmac.new, on either the signing
    or the verifying side -- see _sign and _verify_delegation_token below.
    """


@dataclass(frozen=True)
class Scope:
    """Everything a tool (app/services/tools.py) is allowed to know about
    who is calling it and what they may read. `allowed_collections` is
    ALWAYS a concrete list here -- never `None` -- precisely because `None`
    is Weave-Retrieval's own SearchRequest.allowed_collections sentinel for
    "no restriction at all, full visibility" (see that service's
    app/schemas/search.py), a meaning no caller of Weave-Tools is ever
    trusted with. An empty list correctly means "reads nothing" and must
    still be passed through as such, never treated as "unset".

    `kind` is 'personal' or 'delegated' -- carried for logging/observability
    only (e.g. telling apart "a human is asking directly" from "an agent is
    asking on a human's behalf" in an audit trail); no code path in this
    service is allowed to branch access-control decisions on it, since the
    whole point of a Delegations-Token is that it grants EXACTLY the
    delegating human's own rights, never a different or wider set.
    """

    kind: str
    user_id: str
    username: str
    team: str | None
    allowed_collections: list[str]
    bot_id: str | None = None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(data: str) -> bytes:
    # urlsafe_b64decode requires the input's length to be a multiple of 4;
    # the wire format strips padding on encode (see _b64url_encode), so it
    # is restored here before decoding.
    padding = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """`json(payload)` from this module's own docstring: sorted keys, no
    whitespace -- the exact bytes the HMAC is computed over, on both the
    issuing and verifying side. Any other json.dumps() call (different key
    order, a stray space after ':') would still be valid JSON but would NOT
    reproduce the same signature.
    """
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _sign(part1: str) -> bytes:
    secret = settings.weave_delegation_secret
    if not secret:
        # Never compute an HMAC with an empty key -- see ScopeConfigurationError's
        # own docstring for why. _verify_delegation_token already checks this
        # up front and never reaches here with an empty secret; this is the
        # second line of defense, and the ONLY guard for issue_delegation_token
        # below (this service's own test-token issuer -- see its docstring for
        # why the production issuer is Weave-Runtime, not this function).
        raise ScopeConfigurationError(_SERVICE_MISCONFIGURED_ERROR)
    return hmac.new(secret.encode('utf-8'), part1.encode('ascii'), hashlib.sha256).digest()


def warn_if_delegation_secret_unconfigured() -> None:
    """Log a clear, secret-free warning if settings.weave_delegation_secret is
    empty. Called once at process start by BOTH ASGI entry points
    (app/main.py, app/mcp_server.py -- see each module's own top-level call),
    not only lazily on the first verification attempt: a deployment that
    fails closed (see _verify_delegation_token) but never logs the reason
    until its first Delegations-Token arrives could sit misconfigured,
    silently rejecting everything, for a long time before anyone notices in
    an operator's own startup log.
    """
    if not settings.weave_delegation_secret:
        logger.warning(
            'WEAVE_DELEGATION_SECRET is not configured -- every Delegations-Token '
            'verification will be rejected (503) until this is set, and it must '
            'match the value Weave-Runtime signs with exactly.'
        )


def issue_delegation_token(
    *,
    user_id: str,
    username: str,
    team: str | None,
    collections: list[str],
    bot_id: str | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Mint a Delegations-Token for `user_id`'s current, already-resolved
    scope. The production issuer of these tokens is Weave-Runtime, not this
    service (see this module's own docstring) -- this function exists here
    so the wire format has exactly one implementation for both sides to stay
    byte-for-byte compatible against (Weave-Runtime's own issuer is expected
    to mirror this function exactly), and so this service's own test suite
    can construct valid/tampered tokens without duplicating the format.

    `ttl_seconds` defaults to settings.delegation_token_ttl_seconds, read at
    CALL time (not as a mutable default argument) so a test that
    monkeypatches the setting is honoured.
    """
    if ttl_seconds is None:
        ttl_seconds = settings.delegation_token_ttl_seconds

    now = int(time.time())
    payload = {
        'v': DELEGATION_TOKEN_VERSION,
        'sub': user_id,
        'username': username,
        'team': team,
        'collections': collections,
        'bot': bot_id,
        'iat': now,
        'exp': now + ttl_seconds,
    }
    part1 = _b64url_encode(_canonical_json(payload))
    part2 = _b64url_encode(_sign(part1))
    return f'{part1}.{part2}'


def _verify_delegation_token(token: str) -> dict[str, Any]:
    """Verify `token` (already known to contain exactly one '.') and return
    its decoded payload, or raise ScopeError -- see this module's own
    docstring ("Verification discipline") for why every failure mode below
    collapses into the exact same exception with the exact same message.
    `token` itself is never included in the exception, a log line, or
    anything derived from either: it is a bearer secret for as long as it
    remains valid (see the contract).

    Raises ScopeConfigurationError instead -- BEFORE any of the above even
    runs -- if settings.weave_delegation_secret is unset. This check must
    live here, ahead of the try/except below, rather than relying solely on
    _sign's own guard: _sign would raise from inside that try block, where
    the trailing `except Exception` would otherwise catch it right alongside
    every genuine token-shaped failure and collapse it into the very same
    ScopeError/401 a bad token gets -- exactly the "operator's mistake looks
    like the caller's fault" conflation ScopeConfigurationError exists to
    prevent (see its own docstring). An empty secret is a deployment fact,
    not a property of any particular token, so it is checked once, up front,
    for every verification attempt.
    """
    if not settings.weave_delegation_secret:
        raise ScopeConfigurationError(_SERVICE_MISCONFIGURED_ERROR)
    try:
        part1, part2 = token.split('.')
        expected_sig = _sign(part1)
        provided_sig = _b64url_decode(part2)
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise ScopeError(_GENERIC_AUTH_ERROR)

        payload = json.loads(_b64url_decode(part1))
        if not isinstance(payload, dict):
            raise ScopeError(_GENERIC_AUTH_ERROR)
        if payload.get('v') != DELEGATION_TOKEN_VERSION:
            raise ScopeError(_GENERIC_AUTH_ERROR)

        exp = payload.get('exp')
        # bool is an int subclass in Python; excluding it here is cheap
        # insurance against a payload where `exp` was somehow `True`
        # comparing as `1` below rather than being rejected outright.
        if not isinstance(exp, int) or isinstance(exp, bool):
            raise ScopeError(_GENERIC_AUTH_ERROR)
        # No leeway at all (see this module's own docstring) -- comfortably
        # inside the contract's "no window bigger than 60s" ceiling.
        if exp <= int(time.time()):
            raise ScopeError(_GENERIC_AUTH_ERROR)

        collections = payload.get('collections')
        if not isinstance(collections, list) or not all(isinstance(slug, str) for slug in collections):
            raise ScopeError(_GENERIC_AUTH_ERROR)
        if not isinstance(payload.get('sub'), str) or not isinstance(payload.get('username'), str):
            raise ScopeError(_GENERIC_AUTH_ERROR)
        team = payload.get('team')
        if team is not None and not isinstance(team, str):
            raise ScopeError(_GENERIC_AUTH_ERROR)
        bot_id = payload.get('bot')
        if bot_id is not None and not isinstance(bot_id, str):
            raise ScopeError(_GENERIC_AUTH_ERROR)

        return payload
    except ScopeError:
        raise
    except Exception:
        # Malformed base64, invalid JSON, a `.split('.')` that didn't
        # produce exactly two parts, ... every one of these is just another
        # shape of "not a valid token", collapsed into the same generic
        # error as a bad signature or an expired one (see module docstring).
        # Deliberately not logged with `exc_info` / the raw exception text:
        # that could echo attacker-controlled bytes (a base64-decoded
        # fragment of `token`) into the log stream.
        raise ScopeError(_GENERIC_AUTH_ERROR) from None


def _resolve_delegated_scope(token: str) -> Scope:
    payload = _verify_delegation_token(token)
    return Scope(
        kind='delegated',
        user_id=payload['sub'],
        username=payload['username'],
        team=payload.get('team'),
        allowed_collections=list(payload['collections']),
        bot_id=payload.get('bot'),
    )


def _fetch_readable_collection_slugs(team: str | None) -> list[str]:
    """GET {RETRIEVAL_BASE_URL}/api/v1/collections?team=<team> and return
    just the slugs -- the Personal-Token path's answer to "which collections
    may this team read", per the Collections contract's read-authority split
    (Weave-Retrieval's own app/services/collections.py:readable_collections).
    `team=None` is passed through as NO query parameter at all (see that
    endpoint's own docstring: omitting it entirely, not merely passing an
    empty string, is what selects "public collections only").
    """
    params = {'team': team} if team is not None else {}
    response = httpx.get(
        f'{settings.retrieval_base_url}/api/v1/collections',
        params=params,
        headers={'Authorization': f'Bearer {settings.retrieval_api_token}'},
        timeout=settings.retrieval_timeout_seconds,
    )
    response.raise_for_status()
    return [collection['slug'] for collection in response.json()]


def _resolve_personal_scope(token: str) -> Scope:
    """Introspect a raw Personal-Token against Weave-API, then resolve its
    readable collections against Weave-Retrieval. Two outbound calls, every
    time (see module docstring for why there is no cache).
    """
    try:
        response = httpx.post(
            f'{settings.weave_api_base_url}/internal/tokens/introspect',
            json={'token': token},
            headers={'Authorization': f'Bearer {settings.introspection_service_token}'},
            timeout=settings.weave_api_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError:
        # An unreachable/misbehaving Weave-API is an infrastructure failure,
        # not "this token is invalid" -- still never anything but the one
        # generic auth message reaches the caller (see get_scope, app/mcp_
        # server.py), but logged with the actual exception so an operator
        # can tell the two apart from server-side logs alone.
        logger.exception('introspection call to Weave-API failed')
        raise ScopeError(_GENERIC_AUTH_ERROR) from None

    # Weave-API's own non-enumeration discipline (see its app/api/internal.py):
    # an unknown/expired/disabled-user token comes back as plain
    # `{"active": false}`, HTTP 200 -- never a 401/404 this service would
    # have to specially interpret.
    if not data.get('active'):
        raise ScopeError(_GENERIC_AUTH_ERROR)

    team = data.get('team')
    allowed_collections = _fetch_readable_collection_slugs(team)
    return Scope(
        kind='personal',
        user_id=str(data['user_id']),
        username=data['username'],
        team=team,
        allowed_collections=allowed_collections,
    )


def resolve_scope(authorization: str | None) -> Scope:
    """Resolve one request's `Authorization` header to a Scope -- the ONLY
    entry point into this module every tool is expected to call, and it is
    called fresh on every single request (see module docstring). Never
    caches, never accepts a scope override from anywhere else.

    Dispatch between the two token kinds is purely structural: a raw bearer
    token containing EXACTLY one '.' is handled as a Delegations-Token (see
    this module's own docstring for the wire format -- `part1.part2`, and a
    Personal-Token, an opaque string from Weave-API, is never expected to
    contain a literal '.' at all); anything else goes through the
    Personal-Token/introspection path. A malformed one-dot string that
    isn't actually a valid Delegations-Token still ends up at the exact
    same generic ScopeError as any other invalid token, never a different
    one merely for guessing the wrong shape.

    One exception is not collapsed into that generic ScopeError: a one-dot
    token still raises ScopeConfigurationError, uncaught, when
    settings.weave_delegation_secret is unset -- see
    _verify_delegation_token and ScopeConfigurationError's own docstring for
    why that specific failure must stay distinguishable (503, not 401) all
    the way out to app/api/deps.py/app/mcp_server.py.
    """
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise ScopeError(_GENERIC_AUTH_ERROR)

    token = authorization[len(_BEARER_PREFIX):]
    if token.count('.') == 1:
        return _resolve_delegated_scope(token)
    return _resolve_personal_scope(token)
