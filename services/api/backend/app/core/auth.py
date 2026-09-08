"""End-user authentication for this gateway: EITHER `Authorization: Bearer
<token>` -> sha256 -> ApiToken lookup, OR a session cookie (set by a
successful OIDC login, app/api/auth.py) -> sha256 -> Session lookup -- both
resolve to the exact same `current_user` dependency, and app/core/ratelimit.py's
`enforce_rate_limit` applies identically regardless of which one a given
request used (it only ever sees the resolved `User`, never which credential
got it there). Also holds the SEPARATE service-token check for
POST /internal/tokens/introspect (a different secret, a different caller
class -- see `require_introspection_service_token` below).

Mirrors Weave-Ingest's own dual-credential dependency (that service's
app/api/deps.py:get_current_user) -- Bearer, when present, is tried FIRST
and the cookie path is skipped entirely (a caller presenting a bearer
credential is never confused with a browser session); sha256(token) lookup
either way, reject an expired credential or a disabled user with the same
401 (never reveal which failure applies), opportunistically touch a
last-used/last-seen timestamp.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.security import hash_session_token
from app.models.models import ApiToken, User
from app.models.models import Session as SessionModel
from app.services.ingest_identity import IngestIdentityError, fetch_identity

logger = logging.getLogger(__name__)

_BEARER_PREFIX = 'Bearer '

# Set by a successful OIDC login (app/api/auth.py's `_create_session`) and
# read here on every request that carries no Bearer token. Defined here
# (not in app/api/auth.py, which only WRITES it) so this module -- the one
# every authenticated route already depends on -- has no import-time
# dependency on the OIDC router module.
SESSION_COOKIE_NAME = 'weave_api_session'

# Bearer tokens touch last_used_at at most this often, to bound write volume
# for a token used on every request of a hot integration (same threshold
# Weave-Ingest's own API_TOKEN_TOUCH_THRESHOLD uses).
_LAST_USED_TOUCH_THRESHOLD = timedelta(seconds=60)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    """sqlite has no real tz-aware storage: DateTime(timezone=True) columns
    round-trip as naive datetimes on that dialect (every write in this
    codebase uses datetime.now(timezone.utc), so a naive value here is
    always implicitly UTC). Attach tzinfo before doing python-side
    arithmetic against datetime.now(timezone.utc), or the comparison below
    raises TypeError. No-op on postgres, which already returns aware
    datetimes for these columns.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _refresh_ingest_identity(db: Session, user: User) -> User | None:
    prefix = 'weave-ingest:'
    if not user.oidc_subject or not user.oidc_subject.startswith(prefix):
        return user
    try:
        identity = fetch_identity(user.oidc_subject[len(prefix):])
    except IngestIdentityError:
        raise HTTPException(status_code=503, detail='Identity authority unavailable') from None
    if identity is None:
        return None
    if user.team != identity.team or user.teams != identity.effective_teams or user.is_admin != identity.is_admin:
        user.team = identity.team
        user.teams = identity.effective_teams
        user.is_admin = identity.is_admin
        db.commit()
    return user


def resolve_api_token(db: Session, raw_token: str) -> User | None:
    """Resolve a raw bearer token to its User, or `None` if it's unknown,
    expired, or belongs to a disabled user -- every failure mode collapsed
    into the same `None` on purpose (the same non-enumeration discipline
    Weave-Ingest's own auth path follows), so a caller can't distinguish
    "no such token" from "that token has expired" from "that account is
    disabled". Opportunistically touches `last_used_at` on success, exactly
    as this function's two callers previously did inline.

    Shared by `get_current_user` below (which turns `None` into a generic
    401 for THIS gateway's own end-user routes) and
    POST /internal/tokens/introspect (app/api/internal.py, which turns
    `None` into `{"active": false}` instead -- a different transport for
    the same "no" answer, never a different one for whether the token is
    valid).
    """
    token = db.scalar(select(ApiToken).where(ApiToken.token_sha256 == _hash_token(raw_token)))
    if token is None:
        return None

    now = datetime.now(timezone.utc)
    if token.expires_at is not None and _aware_utc(token.expires_at) <= now:
        return None

    user = db.get(User, token.user_id)
    if user is None or user.disabled:
        return None

    if token.last_used_at is None or now - _aware_utc(token.last_used_at) > _LAST_USED_TOUCH_THRESHOLD:
        token.last_used_at = now
        db.commit()

    return _refresh_ingest_identity(db, user)


def _authenticate_session_cookie(request: Request, db: Session) -> User:
    """The cookie counterpart to `resolve_api_token` above: sha256(cookie
    value) -> Session lookup, with lazy expiry deletion (this is the
    "periodically OR when checking" half of expired-session cleanup --
    there is no separate cron job here, every check that finds an expired
    row removes it on the spot) and the same disabled-user check
    `resolve_api_token` already does for a Bearer token. Every failure mode
    -- no cookie, unknown/expired session, disabled user -- raises the same
    401 with a generic detail message, never distinguishing which for the
    caller (same non-enumeration discipline as `get_current_user` below).
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')

    session = db.scalar(select(SessionModel).where(SessionModel.token_hash == hash_session_token(token)))
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')

    now = datetime.now(timezone.utc)
    if _aware_utc(session.expires_at) <= now:
        # Lazy delete: this row is expired and can never become valid again.
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Session expired')

    user = db.get(User, session.user_id)
    if user is None or user.disabled:
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')

    user = _refresh_ingest_identity(db, user)
    if user is None:
        raise HTTPException(status_code=401, detail='Not authenticated')
    return user


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: resolve and return the User for a valid Bearer
    token OR a valid session cookie (in that order -- see module
    docstring). A well-formed `Authorization: Bearer <token>` header, when
    present, is tried first and any failure there (unknown token, expired
    token, disabled user) raises immediately, WITHOUT falling back to the
    cookie -- a caller presenting a bearer credential is never silently
    re-authenticated a different way. A missing or non-Bearer-shaped
    header (nothing at all, `Basic ...`, an empty value) falls through to
    `_authenticate_session_cookie` instead of raising here directly, so a
    browser request with no `Authorization` header at all still gets a
    chance to authenticate via its session cookie.
    """
    if authorization and authorization.startswith(_BEARER_PREFIX):
        raw_token = authorization[len(_BEARER_PREFIX):]
        user = resolve_api_token(db, raw_token)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')
        return user

    return _authenticate_session_cookie(request, db)


def require_introspection_service_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency enforcing `Authorization: Bearer
    <INTROSPECTION_SERVICE_TOKEN>` on POST /internal/tokens/introspect
    (app/api/internal.py). Mirrors Weave-Retrieval's own
    `require_service_token` (that service's app/core/auth.py) bit-for-bit:

    1. An unconfigured (empty) `settings.introspection_service_token` is
       refused with 503, never treated as "auth disabled" -- falling
       through to the comparison below would let
       `hmac.compare_digest('', '')` succeed for a caller that also sends
       an empty bearer token, silently turning a deployment misconfiguration
       into an open endpoint.
    2. A missing or non-Bearer `Authorization` header -> 401.
    3. A Bearer token that doesn't match, compared with
       `hmac.compare_digest` (constant-time, so response latency can't be
       used to brute-force the token byte-by-byte) -> 401.

    This is a SEPARATE secret from any end-user Personal-API-Token above --
    `get_current_user`/`resolve_api_token` are never consulted here, so a
    valid Personal-API-Token is just an arbitrary wrong string as far as
    this check is concerned and gets the same 401 as any other mismatch.
    This endpoint hands back OTHER users' identity information; a
    Personal-API-Token has no business unlocking that.
    """
    if not settings.introspection_service_token:
        logger.error('INTROSPECTION_SERVICE_TOKEN is not configured; refusing all introspection requests')
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='introspection service token not configured',
        )

    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='missing or malformed bearer token')

    provided = authorization[len(_BEARER_PREFIX):]
    if not hmac.compare_digest(provided, settings.introspection_service_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid service token')
