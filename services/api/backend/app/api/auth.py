"""OIDC login/callback + session logout for this gateway's own browser-
session auth path (ADR-0002). Personal-API-Tokens (app/core/auth.py,
app/cli.py) remain the only auth path for machine callers; this router adds
a SECOND path for a human sitting in a browser, backed by a server-side
Session row (app/models/models.py) instead of a bearer token -- see
app/core/auth.py's `get_current_user` for how a request authenticates with
EITHER credential from here on.

One statically-configured OIDC provider via env vars (`OIDC_ISSUER`/
`OIDC_CLIENT_ID`/..., app/core/config.py), unlike Weave-Ingest's own
admin-managed, DB-backed multi-provider table (that service's
app/models/models.py `AuthProvider`) -- this gateway has exactly one
identity provider to talk to per deployment, not a tenant-configurable
list, so there is no admin CRUD surface here, just config. Otherwise
follows that service's approach and libraries (authlib for the
state/PKCE/nonce authorize dance, joserfc for ID-token verification against
the provider's JWKS) rather than inventing something new -- see
app/services/oidc.py for the discovery/token-exchange/validation helpers
this router calls.

Every endpoint below 404s outright (`require_oidc_enabled`, applied at the
router level) unless BOTH `OIDC_ISSUER` and `OIDC_CLIENT_ID` are configured
-- an unconfigured deployment must look exactly like this router was never
mounted at all, not like a feature that exists but always fails, and
Personal-API-Tokens remain the only way in (README's OIDC-setup section).

Security properties, all deliberately NOT abbreviated relative to
Weave-Ingest's own template:

- `state` + PKCE `code_verifier` + `nonce` are generated in `oidc_login`,
  bundled into one JSON payload, HMAC-signed (app/core/security.py's
  `sign_value`) and stored in a short-lived, httpOnly, SameSite=Lax cookie
  -- never in server-side storage, so there is nothing to clean up if a
  login is abandoned before reaching the callback. `oidc_callback` verifies
  the signature, then the `state` value itself, before doing anything else;
  the cookie is deleted (`delete_cookie`) on every path out of the callback
  that got far enough to read it, success or failure, making both `state`
  and `nonce` effectively single-use -- a replayed callback request finds
  no cookie and fails at the very first check.
- The ID token's signature (against the provider's own JWKS), issuer,
  audience, and expiry are all verified by `validate_id_token`
  (app/services/oidc.py) -- never skipped, never optional.
- The post-login redirect target is the fixed constant
  `_POST_LOGIN_REDIRECT` below UNLESS the login carries a `return_to` that
  matches `OIDC_POST_LOGIN_ALLOWED_URLS` (app/core/config.py) -- see
  `_resolve_return_to`/`_return_to_matches_base` below for exactly how that
  allowlist check works, and the "cross-origin handoff" section further
  down for what happens instead of a plain redirect when it matches. Any
  `return_to` that does NOT match is silently ignored, never surfaced as an
  error -- there is no response shape here that would tell a caller whether
  their candidate URL was well-formed but merely disallowed, which is
  exactly the information an open-redirect probe would want.
- The session cookie is httpOnly + SameSite=Lax always, and Secure whenever
  the request itself is HTTPS (`_is_https_request` below) -- which in any
  real deployment behind TLS means always, and only ever false for local
  plain-HTTP development.
- Neither the client secret nor any token (access/id/session) is ever
  logged -- see app/services/oidc.py's own `exchange_code_for_tokens`
  docstring for the one place the client secret passes through. The
  cross-origin handoff's own one-time `code` (see below) gets the same
  treatment: never passed to `logger`, never included in any exception
  message this router raises.

Cross-origin post-login handoff (`return_to`, GET /v1/auth/oidc/login +
GET /v1/auth/oidc/callback, POST /v1/auth/session/exchange):

The session cookie `_create_session` sets is scoped to THIS gateway's own
origin -- exactly what a caller on the SAME origin needs and nothing else.
A chat UI served from a DIFFERENT origin can't read that cookie at all, so
it has no way to end up authenticated against this gateway just by having
its user's browser bounce through the OIDC dance above. `return_to` closes
that gap without ever putting a bearer credential in front of that UI's own
browser-side JavaScript:

1. The UI sends its user to `GET /v1/auth/oidc/login?return_to=<its own
   URL>`. `_resolve_return_to` checks that URL against
   `OIDC_POST_LOGIN_ALLOWED_URLS` (app/core/config.py) -- scheme/host/port
   compared EXACTLY, path as a boundary-respecting prefix only afterwards,
   dot-segments rejected outright (`_return_to_matches_base`, mirroring
   Weave-Runtime's own `_base_url_matches`/N8N_ALLOWED_BASE_URLS -- see that
   function's docstring for why a raw `str.startswith()` here would be an
   open-redirect bypass). A match is folded into the SAME signed, httpOnly
   `_OIDC_STATE_COOKIE` payload as `state`/`nonce`/`code_verifier` -- there
   is no separate cookie or server-side row for it, so it's exactly as
   tamper-proof and exactly as single-use as those three already are. A
   non-match (or no `return_to` at all) is silently dropped; nothing
   downstream ever sees a difference between "no return_to was given" and
   "one was given but didn't match".
2. On a successful callback, `oidc_callback` mints a `SessionExchangeCode`
   (app/models/models.py) -- a single-use, 60-second-lived, sha256-at-rest
   code -- and redirects to `<return_to>?code=<code>` INSTEAD of
   `_POST_LOGIN_REDIRECT`. The gateway's own session cookie is still set on
   this very response either way (`_create_session` runs unconditionally),
   so a same-origin deployment that never sets `return_to` at all keeps
   working byte-for-byte as before.
3. The UI's OWN backend calls `POST /v1/auth/session/exchange {code}`
   SERVER-SIDE (never from browser JS) to trade that code for a real
   `{session_token, expires_at}` -- see that endpoint's own docstring for
   the atomic, race-free redemption and the deliberately generic failure
   response (unknown/expired/already-used codes are indistinguishable).
   The UI then sets `session_token` in its OWN httpOnly cookie, exactly as
   it would have handled a Personal-API-Token.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from authlib.common.security import generate_token
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.db import get_db
from app.core.security import (
    generate_exchange_code,
    generate_session_token,
    hash_exchange_code,
    hash_session_token,
    sign_value,
    unsign_value,
)
from app.models.models import Session as SessionModel
from app.models.models import SessionExchangeCode, User
from app.schemas.auth import SessionExchangeRequest, SessionExchangeResponse
from app.services.oidc import (
    OIDCError,
    exchange_code_for_tokens,
    fetch_jwks,
    get_discovery_document,
    validate_id_token,
)

logger = logging.getLogger(__name__)

_OIDC_STATE_COOKIE = 'weave_api_oidc_state'
_OIDC_STATE_TTL = timedelta(minutes=10)
# Scoped to the OIDC endpoints only -- this cookie has no business being
# sent on every other request the way SESSION_COOKIE_NAME (path='/') is.
_OIDC_STATE_COOKIE_PATH = '/v1/auth/oidc'

# Fixed lifetime, not a sliding window -- see app/models/models.py's
# Session docstring for why that's enough here.
_SESSION_LIFETIME = timedelta(days=7)

# The fallback post-login redirect target: used whenever the login carries
# no `return_to` at all, or one that doesn't match
# `OIDC_POST_LOGIN_ALLOWED_URLS` (see `_resolve_return_to` below) -- i.e.
# this gateway's original, and still default, behaviour before that setting
# existed. Never itself derived from caller input.
_POST_LOGIN_REDIRECT = '/'

# Lifetime of a cross-origin handoff code (`SessionExchangeCode`,
# app/models/models.py) -- long enough for the UI's own backend to receive
# the callback redirect and immediately call POST /v1/auth/session/exchange
# server-side (a same-process round trip, not a human-paced one), short
# enough that a code sitting unredeemed in a browser's address bar/history/
# a proxy access log stops being worth anything within the time it'd take
# to notice and copy it.
_EXCHANGE_CODE_TTL = timedelta(seconds=60)


def oidc_enabled() -> bool:
    """OIDC is considered configured exactly when both an issuer and a
    client id are set (app/core/config.py's own docstring on these two
    settings) -- `OIDC_CLIENT_SECRET`/`OIDC_REDIRECT_URL` are validated
    lazily, by the provider itself rejecting a malformed authorize/token
    request, rather than gating this flag."""
    return bool(settings.oidc_issuer and settings.oidc_client_id)


def require_oidc_enabled() -> None:
    """Router-level dependency: every route below 404s, exactly as if it
    were never registered at all, unless `oidc_enabled()` -- see module
    docstring. Deliberately re-evaluated on every request (not cached at
    import/startup time) so tests can toggle `settings.oidc_issuer`/
    `oidc_client_id` per-test against the one shared app instance
    (tests/conftest.py) without needing a process restart."""
    if not oidc_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


router = APIRouter(prefix='/v1/auth', tags=['auth'], dependencies=[Depends(require_oidc_enabled)])


def _is_https_request(request: Request) -> bool:
    """Whether to mark the session/state cookie `Secure`. Honours
    `X-Forwarded-Proto` unconditionally (unlike Weave-Ingest's own
    trusted-proxy-gated version, app/services/security.py there) -- this
    gateway has no `trusted_proxy_ips` allowlist setting (yet) to gate it
    on. The failure direction of trusting this header is the safe one: a
    forged `https` value only makes the cookie MORE restrictive (browsers
    then withhold it on a genuine plain-HTTP connection), never less."""
    if request.headers.get('x-forwarded-proto', '').lower() == 'https':
        return True
    return request.url.scheme == 'https'


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite='lax',
        secure=_is_https_request(request),
        path='/',
        max_age=int(_SESSION_LIFETIME.total_seconds()),
    )


def _create_session(db: Session, request: Request, response: Response, user: User) -> None:
    token = generate_session_token()
    now = datetime.now(timezone.utc)
    session = SessionModel(
        user_id=user.id,
        token_hash=hash_session_token(token),
        created_at=now,
        expires_at=now + _SESSION_LIFETIME,
    )
    db.add(session)
    db.commit()
    _set_session_cookie(response, token, request)


# --- OIDC: claim resolution ---------------------------------------------------
#
# Weave-API's User has no `email` column at all (unlike Weave-Ingest's own
# User) -- just `username`, so there is only one thing to resolve out of the
# ID token's claims: a readable username seed, in the same priority order
# Weave-Ingest's own `_username_base_from_claims` uses (minus the
# email-specific branches that don't apply here), falling back to the
# claim guaranteed to exist at all, `sub`.


def _username_base_from_claims(claims: dict) -> str:
    preferred_username = claims.get('preferred_username')
    if isinstance(preferred_username, str) and preferred_username:
        return preferred_username
    upn = claims.get('upn')
    if isinstance(upn, str) and upn:
        return upn
    unique_name = claims.get('unique_name')
    if isinstance(unique_name, str) and unique_name:
        return unique_name
    name = claims.get('name')
    if isinstance(name, str) and name:
        return name
    return str(claims['sub'])


def _resolve_team(claims: dict) -> str | None:
    """`OIDC_TEAM_CLAIM` (app/core/config.py), if configured, names the
    claim carrying this user's team. A list-valued claim (e.g. Keycloak/
    Entra 'groups') contributes its first element; anything else that isn't
    a non-empty string is treated as "no team claimed", exactly like the
    claim being absent."""
    claim_name = settings.oidc_team_claim
    if not claim_name:
        return None
    value = claims.get(claim_name)
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], str) else None
    if isinstance(value, str) and value:
        return value
    return None


def _generate_unique_username(db: Session, base: str) -> str:
    """`base`, or `base-2`/`base-3`/... on collision -- mirrors
    Weave-Ingest's own `_generate_unique_username` (that service's
    app/api/auth.py). `User.username` is unique, and unlike an OIDC
    subject, a claim-derived display name has no reason to be globally
    unique on its own."""
    candidate = base
    suffix = 2
    while db.scalar(select(User.id).where(User.username == candidate)) is not None:
        candidate = f'{base}-{suffix}'
        suffix += 1
    return candidate


# --- Cross-origin handoff: return_to allowlist --------------------------------
#
# Same scheme/host/port-exact + boundary-respecting-path-prefix matching as
# Weave-Runtime's own N8N_ALLOWED_BASE_URLS check (that service's
# app/services/botconfig.py `_base_url_matches`) -- ported here rather than
# imported (these are two separate services/deployments with no shared
# dependency between them) but deliberately kept behaviourally identical,
# dot-segment rejection included, since it's solving the exact same
# "a raw str.startswith() lets a merely-string-prefixed host/port through"
# problem, just for a UI origin instead of an n8n webhook base.


def _has_unambiguous_url_syntax(url: str) -> bool:
    """Whether `url` is safe to hand to `urlsplit` (RFC 3986) AT ALL for an
    allowlist decision -- i.e. it contains nothing that a real browser
    (WHATWG URL spec) could read differently than RFC 3986 does. This
    check exists because `_return_to_matches_base` below is an
    open-redirect gate: a server-side "yes" is worthless if the browser
    that actually navigates afterwards ends up somewhere else entirely.

    The concrete, exploitable divergence: for a special scheme (http/https
    among them) WHATWG treats a backslash '\\' exactly like a forward
    slash '/' -- a real authority/path separator a browser navigates
    through -- while RFC 3986 (and therefore `urlsplit`) treats '\\' as an
    ordinary, structurally meaningless character. Concretely:
    `https://evil.example\\@allowed.example/path` -- `urlsplit` sees no
    delimiter before the '@', so it reads the WHOLE
    `evil.example\\@allowed.example` span as one netloc and returns
    hostname `allowed.example`, an allowlist MATCH -- while a browser
    treats the backslash as '/', terminates the authority there, and
    navigates to host `evil.example`, never going near `allowed.example`
    at all. There is no placement of '\\' (single, repeated, mixed with
    '/') this check could prove safe, so every one is disqualifying.

    ASCII tab/newline/carriage-return are rejected outright too, rather
    than trusted to whatever stripping a given Python/urllib version
    happens to perform internally -- this check has to hold on its own,
    not depend on `urlsplit`'s own incidental behaviour (see this module's
    "Cross-origin handoff" docstring section).

    Used for BOTH the `return_to` candidate and each configured allowlist
    `base` (`_split_or_none` below applies it unconditionally to whatever
    it's given) -- a check that only distrusted caller input while
    trusting config to already be well-formed would stop being correct the
    moment that assumption changed.
    """
    return not any(character in url for character in ('\\', '\t', '\n', '\r'))


def _split_or_none(url: str):
    """`urlsplit(url)`, except: (1) `url` is rejected outright, before
    `urlsplit` ever sees it, when `_has_unambiguous_url_syntax` flags it --
    see that function's docstring for exactly which byte sequences that
    covers and why; (2) a netloc whose port isn't a plain non-negative
    integer raises `ValueError` only once `.port` is actually read --
    forced here so that failure becomes `None` too. Either way, `None`
    means "doesn't parse into something with a well-defined, unambiguous
    host/port at all", exactly as disqualifying for the allowlist as any
    other mismatch, and never reaches the caller as an exception."""
    if not _has_unambiguous_url_syntax(url):
        return None
    try:
        split = urlsplit(url)
        _ = split.port
    except ValueError:
        return None
    return split


def _return_to_matches_base(candidate: str, base: str) -> bool:
    """Whether `candidate` (a `return_to` query value) is covered by one
    `base` entry from `settings.oidc_post_login_allowed_urls`. See this
    module's own "Cross-origin handoff" docstring section and
    Weave-Runtime's `_base_url_matches` (referenced there) for the full
    reasoning; summarised:

    1. scheme compared case-insensitively;
    2. hostname compared EXACTLY (already lowercased by `urlsplit`, and
       critically the part of the netloc AFTER any `user:pass@` userinfo
       prefix has been stripped -- `https://allowed.example:443@evil.
       example/` has `.hostname == 'evil.example'`, not the allowed host);
    3. port compared EXACTLY as the parsed integer (never as a netloc
       substring, which is what lets `:5678` match `:56789...`);
    4. only once 1-3 all agree, `candidate`'s path is checked as a
       boundary-respecting prefix of `base`'s own path (a `/` immediately
       after where `base`'s path ends, or nothing at all) -- and dot-
       segments in `candidate`'s path are rejected outright, since a
       browser/HTTP client normalising `/ui/../../evil` before actually
       requesting it would otherwise let a path-scoped allowlist entry be
       satisfied by a candidate that ends up somewhere else entirely.
    """
    parsed = _split_or_none(candidate)
    base_parsed = _split_or_none(base)
    if parsed is None or base_parsed is None:
        return False

    if parsed.scheme.lower() != base_parsed.scheme.lower():
        return False

    if parsed.hostname is None or base_parsed.hostname is None or parsed.hostname != base_parsed.hostname:
        return False

    if parsed.port != base_parsed.port:
        return False

    base_path = base_parsed.path or '/'
    candidate_path = parsed.path or '/'
    # Decode before testing for dot-segments: a browser normalises
    # '/ui/%2e%2e/evil' down to '/evil' before it ever navigates, so a check
    # against the still-encoded path would read the segment as literal
    # '%2e%2e', wave it through, and leave the path scope of this allowlist
    # entry defeated on the same host -- delivering the one-time exchange
    # code to a path the operator never allowed. Weave-Runtime's
    # `_base_url_matches`, which this was ported from, decodes here too;
    # the port had dropped that step.
    if any(segment in ('.', '..') for segment in unquote(candidate_path).split('/')):
        return False
    if not candidate_path.startswith(base_path):
        return False
    remainder = candidate_path[len(base_path):]
    return base_path.endswith('/') or remainder == '' or remainder.startswith('/')


def _resolve_return_to(candidate: str | None) -> str | None:
    """`candidate` itself, if it matches at least one entry in
    `settings.oidc_post_login_allowed_urls`; `None` otherwise (including
    when `candidate` is `None`/empty, or the allowlist itself is empty --
    which is the setting's default, so the handoff is opt-in per
    deployment). Never raises and never distinguishes "malformed" from
    "well-formed but not allowlisted" -- both collapse into "ignore it" (see
    this module's docstring on why)."""
    if not candidate:
        return None
    if any(_return_to_matches_base(candidate, base) for base in settings.oidc_post_login_allowed_urls):
        return candidate
    return None


def _append_code_param(return_to: str, code: str) -> str:
    """`<return_to>?code=<code>`, preserving any query string `return_to`
    already carries (merged in, not clobbered) rather than assuming it has
    none -- the contract only ever names the no-existing-query case
    explicitly, but a UI's own landing route is free to have one."""
    split = urlsplit(return_to)
    query_pairs = parse_qsl(split.query, keep_blank_values=True)
    query_pairs.append(('code', code))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_pairs), split.fragment))


# --- public: OIDC login/callback + logout -------------------------------------


@router.get('/oidc/login')
def oidc_login(request: Request, return_to: str | None = None) -> RedirectResponse:
    """Starts the authorization-code + PKCE dance: fetches the provider's
    discovery document, generates `state`/`nonce`/PKCE `code_verifier`,
    redirects the browser to the provider's `authorization_endpoint`, and
    stashes the three generated values -- plus `return_to`, resolved
    against `OIDC_POST_LOGIN_ALLOWED_URLS` right here (`_resolve_return_to`)
    rather than deferred to the callback, so a later allowlist change can
    never retroactively change what an in-flight login resolves to -- in a
    signed, short-lived, httpOnly cookie for `oidc_callback` below to verify
    and consume."""
    resolved_return_to = _resolve_return_to(return_to)
    try:
        discovery = get_discovery_document(settings.oidc_issuer)
    except OIDCError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    authorization_endpoint = discovery.get('authorization_endpoint')
    if not authorization_endpoint:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail='Provider discovery is missing authorization_endpoint'
        )

    state = generate_token(32)
    nonce = generate_token(32)
    code_verifier = generate_token(64)
    code_challenge = create_s256_code_challenge(code_verifier)

    query = urlencode(
        {
            'response_type': 'code',
            'client_id': settings.oidc_client_id,
            'redirect_uri': settings.oidc_redirect_url,
            'scope': settings.oidc_scopes,
            'state': state,
            'nonce': nonce,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }
    )

    redirect_response = RedirectResponse(url=f'{authorization_endpoint}?{query}', status_code=status.HTTP_302_FOUND)
    state_payload = json.dumps(
        {'state': state, 'nonce': nonce, 'code_verifier': code_verifier, 'return_to': resolved_return_to}
    )
    redirect_response.set_cookie(
        key=_OIDC_STATE_COOKIE,
        value=sign_value(state_payload),
        httponly=True,
        samesite='lax',
        secure=_is_https_request(request),
        path=_OIDC_STATE_COOKIE_PATH,
        max_age=int(_OIDC_STATE_TTL.total_seconds()),
    )
    return redirect_response


@router.get('/oidc/callback')
def oidc_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Verifies `state`, exchanges `code` for tokens (with the matching PKCE
    `code_verifier`), fully validates the returned ID token (signature
    against the provider's JWKS, issuer, audience, expiry, nonce -- see
    app/services/oidc.py's `validate_id_token`), resolves the local `User`
    by `oidc_subject` (provisioning a new one on first login), creates a
    Session, and redirects to `_POST_LOGIN_REDIRECT` -- or, if the login's
    own `return_to` (stashed in the state cookie by `oidc_login`, already
    allowlist-checked there) resolved to something, to that URL instead,
    with a fresh `SessionExchangeCode` appended as its `code` query param
    (see this module's "Cross-origin handoff" docstring section). Either
    way the session cookie itself is still set on this very response.

    Everything from the `try` below to the end of the function is one
    block: the state cookie was just proven present (the check right
    above this docstring is the only exit that ISN'T inside it, precisely
    because it's the one path that never actually read the cookie). Every
    other exit -- the happy path's own `redirect_response`, or any
    `HTTPException` raised anywhere below for a state mismatch, a
    provider error, an invalid id_token, a disabled user, a missing
    `code`, ... -- deletes the cookie before returning, exactly as this
    module's own docstring already promises ("deleted on every path out
    of the callback that got far enough to read it, success or failure").
    Without this, a failed attempt would leave `state`/`nonce`/
    `code_verifier` (and any resolved `return_to`) sitting in a still-valid
    cookie for the rest of `_OIDC_STATE_TTL`, letting a second, later
    request reuse exactly what the first, failed one already stashed.
    """
    raw_state_cookie = request.cookies.get(_OIDC_STATE_COOKIE)
    if not raw_state_cookie:
        # No state cookie means this request didn't originate from our own
        # /oidc/login redirect -- the classic IdP-initiated login pattern,
        # or a replay of an already-consumed callback (the cookie is
        # deleted below on every path out of a first, successful pass).
        # Fail closed: there's no state/nonce to validate against, so there
        # is nothing safe to do here. Nothing was read yet, so there is
        # nothing to delete either.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Missing OIDC state (IdP-initiated login is not supported)',
        )

    try:
        unsigned = unsign_value(raw_state_cookie, max_age_seconds=_OIDC_STATE_TTL.total_seconds())
        if unsigned is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid OIDC state cookie')

        try:
            state_payload = json.loads(unsigned)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid OIDC state cookie')

        if not isinstance(state_payload, dict) or not state or state_payload.get('state') != state:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='OIDC state mismatch')

        if error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f'OIDC provider returned an error: {error}'
            )
        if not code:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing authorization code')

        try:
            discovery = get_discovery_document(settings.oidc_issuer)
            tokens = exchange_code_for_tokens(
                discovery['token_endpoint'],
                grant_type='authorization_code',
                code=code,
                redirect_uri=settings.oidc_redirect_url,
                client_id=settings.oidc_client_id,
                client_secret=settings.oidc_client_secret,
                code_verifier=state_payload['code_verifier'],
            )
            id_token = tokens.get('id_token')
            if not id_token:
                raise OIDCError('token response did not include an id_token')
            key_set = fetch_jwks(discovery['jwks_uri'])
            claims = validate_id_token(
                id_token,
                key_set=key_set,
                issuer=discovery.get('issuer', settings.oidc_issuer),
                audience=settings.oidc_client_id,
                nonce=state_payload['nonce'],
            )
        except (OIDCError, KeyError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'OIDC login failed: {exc}') from exc

        subject = claims.get('sub')
        if not subject:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='ID token is missing sub claim')

        user = db.scalar(select(User).where(User.oidc_subject == subject))
        if user is None:
            # No silent linking onto an existing local account by username or
            # any other claim -- a brand-new (provider is fixed/singular here,
            # so just) subject always provisions a brand-new User, exactly like
            # Weave-Ingest's own oidc_callback: linking an OIDC identity onto a
            # pre-existing account is not something a login flow should ever do
            # implicitly.
            username_base = _username_base_from_claims(claims)
            user = User(
                username=_generate_unique_username(db, username_base),
                team=_resolve_team(claims),
                oidc_subject=subject,
                disabled=False,
            )
            db.add(user)
            try:
                db.commit()
            except IntegrityError:
                # Concurrent first-login callback for the same OIDC identity:
                # fall back to the row that won the race instead of surfacing a
                # 500.
                db.rollback()
                user = db.scalar(select(User).where(User.oidc_subject == subject))
                if user is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT, detail='OIDC account could not be provisioned'
                    )

        if user.disabled:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Account is disabled')

        logger.info('oidc login succeeded for user %s', user.id)

        # `return_to` was already resolved against OIDC_POST_LOGIN_ALLOWED_URLS
        # by oidc_login itself, before it went into this signed cookie -- never
        # re-validated here, since the signature just verified above already
        # proves it's exactly what THIS gateway put there, unmodified.
        return_to = state_payload.get('return_to')
        if isinstance(return_to, str) and return_to:
            raw_exchange_code = generate_exchange_code()
            now = datetime.now(timezone.utc)
            db.add(
                SessionExchangeCode(
                    user_id=user.id,
                    code_hash=hash_exchange_code(raw_exchange_code),
                    created_at=now,
                    expires_at=now + _EXCHANGE_CODE_TTL,
                )
            )
            db.commit()
            redirect_target = _append_code_param(return_to, raw_exchange_code)
        else:
            redirect_target = _POST_LOGIN_REDIRECT

        redirect_response = RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)
        redirect_response.delete_cookie(key=_OIDC_STATE_COOKIE, path=_OIDC_STATE_COOKIE_PATH)
        # Set unconditionally, exactly as before `return_to` existed -- the
        # same-origin caller that never sends `return_to` at all still gets a
        # working cookie, and the cross-origin caller above ALSO gets one
        # (redundant for it, since it can't read this origin's cookie jar, but
        # harmless -- see module docstring on why both paths run side by side).
        _create_session(db, request, redirect_response, user)
        return redirect_response
    except HTTPException as exc:
        # Rebuilds the exact response shape FastAPI's own default
        # HTTPException handler would have produced -- same status code,
        # same `{"detail": ...}` body, any caller-supplied headers -- and
        # just adds the state cookie's deletion to it (see this function's
        # own docstring above for why every exit from here down needs
        # that). Returning a plain Response from a route whose declared
        # return type is `RedirectResponse` is fine: FastAPI passes an
        # already-constructed Response through untouched, exactly as it
        # does for the success `redirect_response` right above.
        error_response = JSONResponse(status_code=exc.status_code, content={'detail': exc.detail}, headers=exc.headers)
        error_response.delete_cookie(key=_OIDC_STATE_COOKIE, path=_OIDC_STATE_COOKIE_PATH)
        return error_response


@router.post('/session/exchange', response_model=SessionExchangeResponse)
def exchange_session_code(payload: SessionExchangeRequest, db: Session = Depends(get_db)) -> SessionExchangeResponse:
    """Trades a cross-origin handoff's one-time `code` (this module's
    "Cross-origin handoff" docstring section; minted by `oidc_callback`
    above) for a real session token -- meant to be called SERVER-SIDE by
    the UI's own backend, never from browser JS: the raw token this returns
    is exactly as sensitive as the value `_set_session_cookie` puts in this
    gateway's own cookie.

    Redemption is one atomic `UPDATE ... WHERE used_at IS NULL AND
    expires_at > now()`, not a read-then-write -- two concurrent requests
    presenting the SAME code race for that single UPDATE; at most one can
    ever affect a row; the loser's WHERE clause simply no longer matches
    once the winner's update is visible (ordinary read-committed-or-
    stronger isolation, the same "claim atomically via an UPDATE, check
    rowcount" pattern as any other single-use-token redemption), so there
    is no window in which both requests could observe "not yet used" and
    both go on to mint a session from the same code.

    An unknown `code_hash`, an expired row, and an already-used one all
    fail that WHERE clause identically -- `rowcount != 1` can't tell them
    apart, and deliberately never tries to: every failure gets the exact
    same generic 400 below, never revealing which of the three applied
    (the same non-enumeration discipline app/core/auth.py's own credential
    checks already follow for a bad Bearer token / session cookie).

    A hash-then-equality DB lookup, exactly like `Session.token_hash`/
    `ApiToken.token_sha256` elsewhere in this codebase -- no additional
    constant-time comparison layered on top of that: unlike
    `require_introspection_service_token`'s `hmac.compare_digest` (a fixed
    secret compared byte-for-byte against caller input in Python), nothing
    here ever compares the raw code itself in application code -- only its
    already-collapsed sha256 digest is used as an index lookup key, same as
    every other hashed credential this service resolves.
    """
    code_hash = hash_exchange_code(payload.code)
    now = datetime.now(timezone.utc)

    result = db.execute(
        update(SessionExchangeCode)
        .where(
            SessionExchangeCode.code_hash == code_hash,
            SessionExchangeCode.used_at.is_(None),
            SessionExchangeCode.expires_at > now,
        )
        .values(used_at=now)
    )
    db.commit()

    if result.rowcount != 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='invalid or expired code')

    record = db.scalar(select(SessionExchangeCode).where(SessionExchangeCode.code_hash == code_hash))
    user = db.get(User, record.user_id) if record is not None else None
    if user is None or user.disabled:
        # The code is already burned (used_at set above) regardless, so
        # this branch -- account deleted/disabled in the few seconds
        # between the callback minting it and it being redeemed -- can
        # never be retried into success either. Same generic response as
        # every other failure above; a caller can't distinguish "no such
        # code" from "that code's account is now disabled".
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='invalid or expired code')

    token = generate_session_token()
    session = SessionModel(
        user_id=user.id,
        token_hash=hash_session_token(token),
        created_at=now,
        expires_at=now + _SESSION_LIFETIME,
    )
    db.add(session)
    db.commit()

    return SessionExchangeResponse(session_token=token, expires_at=session.expires_at)


@router.post('/logout')
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    """Deletes the Session row behind the caller's session cookie (if any)
    and clears the cookie either way -- a caller with no cookie, or one
    that no longer matches any row, still gets a clean `{"status": "ok"}`
    and a cleared cookie rather than a 401, since the end state ("this
    browser is logged out") is identical to actually finding and deleting a
    live session."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session = db.scalar(select(SessionModel).where(SessionModel.token_hash == hash_session_token(token)))
        if session is not None:
            db.delete(session)
            db.commit()

    response.delete_cookie(key=SESSION_COOKIE_NAME, path='/')
    return {'status': 'ok'}
