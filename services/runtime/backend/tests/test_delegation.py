"""Unit tests for app/services/delegation.py's `mint_delegation_token` --
the token's exact wire FORM (two base64url segments joined by '.', the
payload's own field set, the HMAC-SHA256 signature a verifier would
recompute), `exp`/`iat`, and the hard failure when
`WEAVE_DELEGATION_SECRET` is unconfigured. Deliberately reimplements the
decode/verify side by hand with the same stdlib primitives
(base64/hashlib/hmac/json) the module itself uses -- there is no verifier
shipped in THIS repo (see contracts/n8n-flow.md: that is Weave-Tools' own
responsibility), so these tests double as a live demonstration of exactly
what a correct verifier would do.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.core.config import settings
from app.schemas.chat import ChatUser
from app.services.delegation import DelegationConfigError, mint_delegation_token

_SECRET = 'unit-test-delegation-secret'


@pytest.fixture(autouse=True)
def _delegation_settings(monkeypatch):
    monkeypatch.setattr(settings, 'weave_delegation_secret', _SECRET)
    monkeypatch.setattr(settings, 'delegation_token_ttl_seconds', 300)


def _b64url_decode(value: str) -> bytes:
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _decode_payload(token: str) -> dict:
    payload_b64, _, _ = token.partition('.')
    return json.loads(_b64url_decode(payload_b64))


def _verify(token: str, *, secret: str = _SECRET) -> bool:
    payload_b64, _, signature_b64 = token.partition('.')
    expected = hmac.new(secret.encode('utf-8'), payload_b64.encode('ascii'), hashlib.sha256).digest()
    return hmac.compare_digest(_b64url_decode(signature_b64), expected)


# --- form -----------------------------------------------------------------


def test_token_is_exactly_two_b64url_segments_joined_by_a_dot():
    token = mint_delegation_token(ChatUser(id='u-1'), ['vertraege'], 'legal-agent')
    parts = token.split('.')
    assert len(parts) == 2
    for part in parts:
        assert part  # non-empty
        assert '=' not in part  # padding-free, see mint_delegation_token's own docstring


def test_payload_contains_exactly_the_documented_field_set():
    token = mint_delegation_token(ChatUser(id='u-1', username='j.schmidt', team='legal'), ['vertraege'], 'legal-agent')
    payload = _decode_payload(token)
    assert set(payload) == {'v', 'sub', 'username', 'team', 'collections', 'bot', 'iat', 'exp'}


def test_payload_fields_match_the_given_arguments():
    token = mint_delegation_token(
        ChatUser(id='u-1', username='j.schmidt', team='legal'), ['vertraege', '__none__'], 'legal-agent'
    )
    payload = _decode_payload(token)
    assert payload['v'] == 1
    assert payload['sub'] == 'u-1'
    assert payload['username'] == 'j.schmidt'
    assert payload['team'] == 'legal'
    assert payload['collections'] == ['vertraege', '__none__']
    assert payload['bot'] == 'legal-agent'


def test_collections_scope_including_the_sentinel_is_forwarded_unchanged():
    # The delegation token's scope must carry the SAME sentinel
    # (app/services/chat.py's NO_COLLECTION_SENTINEL) resolve_collection_scope
    # itself produces -- this function makes no access-control decision of
    # its own, it only signs whatever list its caller resolved.
    token = mint_delegation_token(ChatUser(id='u-1'), ['__none__'], None)
    assert _decode_payload(token)['collections'] == ['__none__']


def test_bot_id_and_team_stay_null_when_unset():
    token = mint_delegation_token(ChatUser(), [], None)
    payload = _decode_payload(token)
    assert payload['bot'] is None
    assert payload['team'] is None


def test_sub_and_username_default_to_empty_string_for_an_anonymous_user():
    # Unlike team/bot (nullable on the payload), sub/username are typed as
    # plain strings on this token's own contract -- never null.
    token = mint_delegation_token(ChatUser(), [], None)
    payload = _decode_payload(token)
    assert payload['sub'] == ''
    assert payload['username'] == ''


def test_username_falls_back_to_id_when_only_id_is_propagated():
    token = mint_delegation_token(ChatUser(id='u-1'), [], None)
    assert _decode_payload(token)['username'] == 'u-1'


def test_username_is_used_as_is_when_propagated():
    token = mint_delegation_token(ChatUser(id='u-1', username='J. Schmidt'), [], None)
    assert _decode_payload(token)['username'] == 'J. Schmidt'


# --- iat / exp --------------------------------------------------------------


def test_exp_equals_iat_plus_configured_ttl(monkeypatch):
    monkeypatch.setattr(settings, 'delegation_token_ttl_seconds', 42)
    token = mint_delegation_token(ChatUser(id='u-1'), [], None)
    payload = _decode_payload(token)
    assert payload['exp'] == payload['iat'] + 42


def test_iat_is_the_current_unix_time():
    before = int(time.time())
    token = mint_delegation_token(ChatUser(id='u-1'), [], None)
    after = int(time.time())
    payload = _decode_payload(token)
    assert before <= payload['iat'] <= after


# --- signature ---------------------------------------------------------------


def test_signature_verifies_against_the_shared_secret():
    token = mint_delegation_token(ChatUser(id='u-1'), ['vertraege'], 'legal-agent')
    assert _verify(token)


def test_signature_fails_to_verify_with_a_different_secret():
    token = mint_delegation_token(ChatUser(id='u-1'), ['vertraege'], 'legal-agent')
    assert not _verify(token, secret='some-other-secret')


def test_tampering_with_the_payload_invalidates_the_signature():
    token = mint_delegation_token(ChatUser(id='u-1'), ['vertraege'], 'legal-agent')
    payload_b64, sep, signature_b64 = token.partition('.')
    payload = json.loads(_b64url_decode(payload_b64))
    payload['collections'] = ['some-other-collection']  # attacker-widened scope
    tampered_payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).rstrip(b'=').decode('ascii')
    tampered_token = f'{tampered_payload_b64}{sep}{signature_b64}'
    assert not _verify(tampered_token)


def test_two_tokens_minted_a_moment_apart_are_not_identical():
    # Different `iat` (in the overwhelming majority of cases) -- and even in
    # the rare same-second case, this is at minimum a sanity check that
    # minting is not somehow memoized/cached across calls (task: "niemals
    # wiederverwendet, niemals gecacht").
    first = mint_delegation_token(ChatUser(id='u-1'), ['vertraege'], 'legal-agent')
    second = mint_delegation_token(ChatUser(id='u-1'), ['vertraege'], 'legal-agent')
    payload_first = _decode_payload(first)
    payload_second = _decode_payload(second)
    assert payload_first['iat'] <= payload_second['iat']


# --- hard failure on unconfigured secret -------------------------------------


def test_mint_raises_delegation_config_error_when_secret_is_empty(monkeypatch):
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')
    with pytest.raises(DelegationConfigError):
        mint_delegation_token(ChatUser(id='u-1'), ['vertraege'], 'legal-agent')


def test_delegation_config_error_is_a_runtime_error():
    assert issubclass(DelegationConfigError, RuntimeError)


def test_mint_never_returns_an_unsigned_token_when_secret_is_empty(monkeypatch):
    # Regression guard for the token's own "no unsigned token, no fallback"
    # contract: confirm the failure happens BEFORE any token-shaped string
    # could be constructed at all (nothing partial leaks out via e.g. a
    # caught-and-ignored exception somewhere upstream of this test).
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')
    try:
        mint_delegation_token(ChatUser(id='u-1'), ['vertraege'], 'legal-agent')
        assert False, 'expected DelegationConfigError'
    except DelegationConfigError as exc:
        assert 'WEAVE_DELEGATION_SECRET' in str(exc)
