"""OIDC discovery, authorization-code exchange, and ID-token validation for
this gateway's own browser-session login (app/api/auth.py) -- see that
module's docstring for how these fit into the login/callback flow. Follows
Weave-Ingest's own app/services/oidc.py (this module's template) approach
and libraries (joserfc for ID-token signature/claim verification) rather
than inventing something new.

Plain httpx, unlike Weave-Ingest's safe_fetch-hardened version: `OIDC_ISSUER`
is a single, operator-set deployment constant (settings.oidc_issuer, read
once at request time, never taken from a request body/query string), not an
admin-configurable, potentially attacker-influenceable URL the way
Weave-Ingest's own per-tenant `AuthProvider.issuer_url` is -- same "trusted
internal caller, not attacker-influenced-URL territory" reasoning
app/services/runtime_client.py's own module docstring already gives for
skipping that hardening against `RUNTIME_BASE_URL`.

`_client()` is factored out (rather than inlined into each fetch function)
purely so tests can monkeypatch it -- or the `httpx.Client` class it
constructs -- to return a MockTransport-backed client with no real network
I/O, exactly like app/services/runtime_client.py's own `_client()` and its
tests already do (see tests/test_oidc_login.py).
"""

from __future__ import annotations

import time

import httpx
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import KeySet

# Discovery documents change rarely; caching in-process for 15min avoids an
# extra round trip (DNS + TLS handshake) on every single OIDC login without
# going stale for long. Per-process cache is fine even with multiple
# replicas -- worst case each pod re-fetches independently.
_DISCOVERY_CACHE_TTL_SECONDS = 15 * 60
_discovery_cache: dict[str, tuple[float, dict]] = {}

_SUPPORTED_ID_TOKEN_ALGORITHMS = ['RS256', 'ES256']

_HTTP_TIMEOUT_SECONDS = 10.0


class OIDCError(Exception):
    """Raised for any OIDC protocol failure: unreachable discovery/token/
    JWKS endpoint, malformed response, or ID-token validation failure."""


def _client() -> httpx.Client:
    return httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS)


def get_discovery_document(issuer_url: str) -> dict:
    cached = _discovery_cache.get(issuer_url)
    if cached is not None:
        cached_at, document = cached
        if time.time() - cached_at < _DISCOVERY_CACHE_TTL_SECONDS:
            return document

    discovery_url = issuer_url.rstrip('/') + '/.well-known/openid-configuration'
    try:
        with _client() as client:
            response = client.get(discovery_url)
    except httpx.RequestError as exc:
        raise OIDCError(f'OIDC discovery failed for {issuer_url!r}: {exc}') from exc
    if response.status_code != 200:
        raise OIDCError(f'OIDC discovery for {issuer_url!r} returned HTTP {response.status_code}')
    try:
        document = response.json()
    except ValueError as exc:
        raise OIDCError(f'OIDC discovery document for {issuer_url!r} is not valid JSON') from exc

    _discovery_cache[issuer_url] = (time.time(), document)
    return document


def exchange_code_for_tokens(token_endpoint: str, **form_params: str) -> dict:
    """POST a form-encoded body to the token endpoint (authorization_code
    grant) and return the parsed JSON response. Never logs `form_params` --
    it carries the client secret and the authorization code."""
    try:
        with _client() as client:
            response = client.post(token_endpoint, data=form_params)
    except httpx.RequestError as exc:
        raise OIDCError(f'token exchange failed: {exc}') from exc
    if response.status_code != 200:
        raise OIDCError(f'token endpoint returned HTTP {response.status_code}: {response.text[:500]!r}')
    try:
        return response.json()
    except ValueError as exc:
        raise OIDCError('token endpoint response is not valid JSON') from exc


def fetch_jwks(jwks_uri: str) -> KeySet:
    try:
        with _client() as client:
            response = client.get(jwks_uri)
    except httpx.RequestError as exc:
        raise OIDCError(f'JWKS fetch failed: {exc}') from exc
    if response.status_code != 200:
        raise OIDCError(f'JWKS endpoint returned HTTP {response.status_code}')
    try:
        jwks_dict = response.json()
    except ValueError as exc:
        raise OIDCError('JWKS response is not valid JSON') from exc
    return KeySet.import_key_set(jwks_dict)


def validate_id_token(
    id_token: str,
    *,
    key_set: KeySet,
    issuer: str,
    audience: str,
    nonce: str,
) -> dict:
    """Verify signature (against `key_set`) and standard claims (iss/aud/
    exp) via joserfc, plus the OIDC nonce (bound to our own /login call, not
    a registered JWT claim joserfc validates itself). Returns the claim set
    on success; raises OIDCError on any failure -- signature, claims, or
    nonce."""
    try:
        token = joserfc_jwt.decode(id_token, key_set, algorithms=_SUPPORTED_ID_TOKEN_ALGORITHMS)
    except Exception as exc:  # noqa: BLE001 -- any decode/signature failure is fatal here
        raise OIDCError(f'ID token signature validation failed: {exc}') from exc

    claims = token.claims
    registry = joserfc_jwt.JWTClaimsRegistry(
        iss={'essential': True, 'values': [issuer]},
        aud={'essential': True, 'values': [audience]},
        exp={'essential': True},
    )
    try:
        registry.validate(claims)
    except Exception as exc:  # noqa: BLE001 -- any claim validation failure is fatal here
        raise OIDCError(f'ID token claim validation failed: {exc}') from exc

    if claims.get('nonce') != nonce:
        raise OIDCError('ID token nonce mismatch')

    return claims
