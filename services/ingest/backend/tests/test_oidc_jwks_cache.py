"""JWKS caching in app/services/oidc.py: one download per TTL, an immediate
refetch for a rotated key, and no refetch storm for made-up key ids."""

import json
from types import SimpleNamespace

import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from app.services import oidc

URI = 'https://idp.example.com/jwks'


@pytest.fixture
def idp(monkeypatch):
    keys = {'current': [RSAKey.generate_key(2048, parameters={'kid': 'k1'})]}
    calls: list[str] = []

    def safe_fetch(url):
        calls.append(url)
        body = json.dumps(KeySet(keys['current']).as_dict(private=False)).encode()
        return SimpleNamespace(status_code=200, body=body)

    monkeypatch.setattr(oidc, 'safe_fetch', safe_fetch)
    monkeypatch.setattr(oidc, '_jwks_cache', {})
    clock = {'now': 1_000_000.0}
    monkeypatch.setattr(oidc.time, 'time', lambda: clock['now'])
    return SimpleNamespace(keys=keys, calls=calls, clock=clock)


def _token(key: RSAKey) -> str:
    return jwt.encode({'alg': 'RS256', 'kid': key.kid}, {'sub': 'x'}, key)


def test_keys_are_downloaded_once_per_ttl(idp) -> None:
    oidc.fetch_jwks(URI, kid='k1')
    oidc.fetch_jwks(URI, kid='k1')
    oidc.fetch_jwks(URI)
    assert len(idp.calls) == 1

    idp.clock['now'] += oidc._JWKS_CACHE_TTL_SECONDS + 1
    oidc.fetch_jwks(URI, kid='k1')
    assert len(idp.calls) == 2


def test_a_rotated_key_is_fetched_immediately_but_unknown_kids_do_not_storm(idp) -> None:
    oidc.fetch_jwks(URI, kid='k1')
    rotated = RSAKey.generate_key(2048, parameters={'kid': 'k2'})
    idp.keys['current'] = [idp.keys['current'][0], rotated]

    # Within the first minute a kid miss does not refetch ...
    oidc.fetch_jwks(URI, kid='k2')
    assert len(idp.calls) == 1
    # ... after it, the miss triggers exactly one refetch.
    idp.clock['now'] += oidc._JWKS_FORCED_REFRESH_MIN_SECONDS + 1
    key_set = oidc.fetch_jwks(URI, kid=oidc.token_kid(_token(rotated)))
    assert len(idp.calls) == 2
    assert any(key.kid == 'k2' for key in key_set.keys)
    for _ in range(5):
        oidc.fetch_jwks(URI, kid='made-up')
    assert len(idp.calls) == 2


def test_token_kid_tolerates_garbage() -> None:
    assert oidc.token_kid('not-a-jwt') is None
    assert oidc.token_kid('äöü.x.y') is None
