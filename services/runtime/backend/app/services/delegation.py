"""Delegation tokens: how Weave-Runtime lends an external agent (n8n today,
any MCP client tomorrow) EXACTLY the read-scope of the human who is
currently asking a question, for a short, signed window -- never more, and
never anything the caller wasn't already entitled to on the SAME chat turn
that requested the delegation (see app/services/chat.py's `_run_n8n_turn`,
which resolves that scope through the exact same
`resolve_collection_scope` every retrieval-backed bot uses, BEFORE ever
minting a token from it). The scope lives SIGNED INSIDE the token, not in
whatever arguments the delegate later calls a tool with -- see
contracts/n8n-flow.md's own "Grundregel Rechte" for why a collection name
supplied by a tool call/model output is a restriction WITHIN this scope,
never a grant of it.

Format (deliberately dependency-free -- only `hashlib`/`hmac`/`json`/
`base64` from the standard library, per this token's own contract):

    token = b64url(json(payload)) + "." + b64url(hmac_sha256(secret, b64url(json(payload))))

`b64url` is URL-safe base64 WITHOUT padding (`=` stripped, see
`_b64url_encode` below) -- the padding-free JWT convention, chosen so the
token is safe to embed unescaped in a header value or a URL query
component. `json(payload)` is `json.dumps(payload, sort_keys=True,
separators=(',', ':'))` -- deterministic byte-for-byte for the same
payload, which matters here because the HMAC covers exactly those bytes;
two independently-serialized-but-logically-equal payloads must produce
byte-identical signatures for a verifier (Weave-Tools, a different service,
never implemented in this repo) to be able to recompute the same MAC.

`payload` (every key always present, see `mint_delegation_token`):

    {"v": 1, "sub": <user id, str>, "username": <str>, "team": <str | null>,
     "collections": <list[str], may include the "__none__" sentinel --
     app/services/chat.py's NO_COLLECTION_SENTINEL, forwarded unchanged>,
     "bot": <bot id, str | null>, "iat": <int, unix seconds>,
     "exp": <int, unix seconds>}

This module only ever MINTS a token -- it is Weave-Runtime's half of the
contract; VERIFYING one (signature check via `hmac.compare_digest`, then
`v == 1`, then `exp > now` with no tolerance window larger than 60s, then
"any single failure -> one generic error, never distinguishing which check
failed" -- see contracts/n8n-flow.md) is Weave-Tools' own responsibility,
on the other side of an HTTP call this service never makes directly. Both
sides share exactly one secret (`WEAVE_DELEGATION_SECRET`,
`settings.weave_delegation_secret`) -- see that setting's own docstring in
app/core/config.py.

This is a bearer secret exactly like `settings.runtime_api_token`
(app/core/auth.py) or `settings.retrieval_api_token`: whoever holds a
still-valid token can act with the scope embedded in it. NEVER log it, put
it in an exception message, or otherwise let it reach a trace -- see
app/services/n8n_client.py's own docstring for where the minted token
actually travels (a request body field, never a header value that a proxy
might log by default) and this module's own tests for the explicit
regression guard on that point.
"""

import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings
from app.schemas.chat import ChatUser

_TOKEN_VERSION = 1


class DelegationConfigError(RuntimeError):
    """Raised by `mint_delegation_token` when `settings.weave_delegation_secret`
    is not configured. A hard failure by design, per this token's own
    contract ("Nicht konfiguriert -> Ausstellen schlaegt hart fehl -- kein
    unsigniertes Token, kein Fallback"): there is no such thing as an
    unsigned delegation token, so an unconfigured secret can only ever mean
    "mint nothing at all", never "mint one nobody can verify" or "mint one
    signed with an empty/guessable key". Deliberately left uncaught
    anywhere in this service (see app/api/internal.py) -- it surfaces as
    this service's default 500, exactly like the other deployment-
    misconfiguration failures this codebase leaves uncaught on purpose
    (app/services/chat.py's own docstring on RetrievalError/LLMError).
    """


def _b64url_encode(raw: bytes) -> str:
    """URL-safe base64 with the trailing `=` padding stripped -- see this
    module's own docstring for why: padding is redundant (the decoder below
    reconstructs it) and unsafe to embed unescaped in some contexts (a URL
    query component, an HTTP header) without percent-encoding it first."""
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _canonical_json_bytes(payload: dict) -> bytes:
    """`json.dumps(payload, sort_keys=True, separators=(',', ':'))`, encoded
    -- see this module's own docstring for why this exact, deterministic
    serialization matters (the HMAC below covers precisely these bytes)."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')


def mint_delegation_token(user: ChatUser, collections: list[str], bot_id: str | None) -> str:
    """Issue one delegation token embedding exactly `collections` (already
    resolved by the caller -- app/services/chat.py's `_run_n8n_turn` calls
    the SAME `resolve_collection_scope` every retrieval-backed bot uses,
    BEFORE calling this function; this function itself makes no access-
    control decision, it only signs the one it is handed) as this token's
    read-scope, valid for `settings.delegation_token_ttl_seconds` (default
    300) from the moment of minting.

    `user.id`/`user.username` both fall back to `''` when unset (an
    anonymous/system-initiated chat, see ChatUser's own docstring in
    app/schemas/chat.py) -- the payload's own `sub`/`username` fields are
    typed as plain (never-null) strings per this token's contract, so
    `None` is never a valid value to embed for either; `username` falls
    back to `user.id` first, THEN `''`, on the reasoning that an id is a
    strictly more useful debugging/audit label than nothing at all when a
    caller propagated one but not the other. `user.team`/`bot_id` stay
    `None` unchanged when unset -- both are typed nullable on the payload
    itself (an anonymous chat genuinely has no team; a token minted
    independently of any one specific bot genuinely has no `bot_id`, even
    though every CURRENT caller -- `_run_n8n_turn` -- always has one).

    Raises DelegationConfigError (see its own docstring) when
    `settings.weave_delegation_secret` is empty -- checked first, before
    anything else in this function does any work, so a misconfigured
    deployment fails exactly the same way regardless of `user`/
    `collections`/`bot_id`.
    """
    if not settings.weave_delegation_secret:
        raise DelegationConfigError(
            'WEAVE_DELEGATION_SECRET is not configured -- refusing to mint a delegation token (see '
            'app/services/delegation.py: no unsigned token, no fallback)'
        )

    issued_at = int(time.time())
    payload = {
        'v': _TOKEN_VERSION,
        'sub': user.id or '',
        'username': user.username or user.id or '',
        'team': user.team,
        'teams': user.effective_teams,
        'collections': collections,
        'bot': bot_id,
        'iat': issued_at,
        'exp': issued_at + settings.delegation_token_ttl_seconds,
    }

    payload_b64 = _b64url_encode(_canonical_json_bytes(payload))
    signature = hmac.new(
        settings.weave_delegation_secret.encode('utf-8'),
        payload_b64.encode('ascii'),
        hashlib.sha256,
    ).digest()
    return f'{payload_b64}.{_b64url_encode(signature)}'
