"""Shared crypto primitives for the OIDC browser-session login
(app/api/auth.py, app/models/models.py's Session) -- a minimal subset of
Weave-Ingest's own app/services/security.py, scoped to what THIS gateway's
single-static-provider OIDC flow actually needs. Deliberately omits that
module's password hashing (Weave-API has no local password login, only
Bearer tokens and OIDC), its Redis-backed rate limiter (app/core/ratelimit.py
already covers that here, in-process), and its Fernet-at-rest client-secret
encryption (`OIDC_CLIENT_SECRET` is a single deployment-time env var here,
never stored in this service's own database the way Weave-Ingest's
per-tenant `AuthProvider.client_secret_encrypted` is).
"""

import hashlib
import hmac
import secrets
import time

from app.core.config import settings

# --- Session tokens ----------------------------------------------------------
#
# Opaque, DB-backed session cookie (not a JWT): the raw token only ever
# lives in the client's cookie and this function's return value; the
# database stores sha256(token) in sessions.token_hash (app/models/models.py's
# Session). A leaked database row alone can't be replayed as a cookie, and
# revocation (logout / disabled user / row deletion) takes effect instantly
# since every lookup hits the database -- same discipline as
# app/core/auth.py's ApiToken hashing, just a separate function/column pair
# for a separate credential class.


def generate_session_token() -> str:
    """Generate a new opaque bearer token for the session cookie."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """sha256 hex digest of a session token, as stored in
    sessions.token_hash."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


# --- Session-handoff exchange codes (app/api/auth.py's cross-origin OIDC
# post-login handoff, app/models/models.py's SessionExchangeCode) ----------
#
# A separate generate/hash pair rather than reusing generate_session_token/
# hash_session_token directly (even though the shape -- opaque
# secrets.token_urlsafe(32), sha256 hex digest at rest -- is identical):
# every other credential class in this module (session tokens above,
# app/core/auth.py's own `_hash_token` for ApiToken) gets its own named
# pair, so the hash column a given value belongs to is always clear from
# the function name alone at the call site, never from context.


def generate_exchange_code() -> str:
    """Generate a new opaque, single-use session-handoff code (the `code`
    query parameter GET /v1/auth/oidc/callback redirects the browser to
    `return_to` with)."""
    return secrets.token_urlsafe(32)


def hash_exchange_code(code: str) -> str:
    """sha256 hex digest of an exchange code, as stored in
    session_exchange_codes.code_hash -- the raw code itself is never
    persisted anywhere (see that table's own docstring), only ever held in
    memory for the one redirect/request that carries it."""
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


# --- Short-lived signed cookie values (OIDC state/nonce/PKCE) ----------------
#
# Plain HMAC-SHA256 rather than a JWT/itsdangerous dependency: stateless
# (needs no shared cache) and cheap since these cookies are opaque and
# short-lived (~10min, app/api/auth.py's `_OIDC_STATE_TTL`), not bearer
# credentials in their own right -- unlike the session token above, there is
# nothing to revoke, just a signature to verify wasn't tampered with between
# the authorize redirect and the callback.
#
# The issuance time is bound INTO the signed payload itself (not left as a
# side channel) so `unsign_value` can enforce a maximum age server-side, on
# its own, regardless of what a browser's `Max-Age` cookie attribute does.
# `Max-Age` is advisory only -- it governs whether the BROWSER still sends
# the cookie back, not whether this service still accepts the value if it
# arrives some other way (a proxy access log, a Referer header, a browser
# that simply chooses not to expire it). Without the timestamp inside the
# signature, a value captured once would stay valid forever, since the HMAC
# alone only proves "this service produced this bytes-for-bytes", never
# "and it did so recently".

_DEFAULT_MAX_AGE_SECONDS = 600.0  # 10 minutes -- app/api/auth.py's own
# `_OIDC_STATE_TTL` is the one caller today, and it passes that value to
# `unsign_value` explicitly rather than leaning on this default; the default
# only matters for a future caller that doesn't have its own opinion.


def sign_value(value: str) -> str:
    """HMAC-sign an opaque string, together with its own issuance time, as
    "<issued-at>:<value>.<hex-hmac-sha256>" -- `issued-at` is whole seconds
    since the Unix epoch (UTC), part of what gets signed (not appended
    afterwards), so `unsign_value` can reject a value that's simply too old
    without trusting anything outside the signature itself. See this
    section's own docstring above for why that matters."""
    issued_at = int(time.time())
    payload = f'{issued_at}:{value}'
    mac = hmac.new(settings.secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{payload}.{mac}'


def unsign_value(signed_value: str, *, max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS) -> str | None:
    """Verify a value produced by `sign_value` with a constant-time compare,
    AND that its embedded issuance time is no more than `max_age_seconds`
    in the past (measured against wall-clock time right now, at
    verification -- not against anything the caller supplies). See this
    section's own docstring above for why the cookie's `Max-Age` attribute
    alone isn't enough.

    Returns the original value passed to `sign_value`, or None if the
    signature is missing, malformed, doesn't match, or the embedded
    issuance time is unparseable or too old. The timestamp is covered by
    the same HMAC as the value -- rolling it back to dodge the age check
    invalidates the signature just as surely as editing the value would.
    """
    payload, separator, mac = signed_value.rpartition('.')
    if not separator or not payload or not mac:
        return None
    expected = hmac.new(settings.secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None

    issued_at_raw, separator, value = payload.partition(':')
    if not separator or not value:
        return None
    try:
        issued_at = int(issued_at_raw)
    except ValueError:
        return None
    if time.time() - issued_at > max_age_seconds:
        return None
    return value
