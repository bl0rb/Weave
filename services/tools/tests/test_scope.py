"""app/services/scope.py: the Delegations-Token verifier, the
Personal-Token/introspection path, and the non-enumeration/non-leak
discipline both are required to uphold.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.services.scope import (
    _GENERIC_AUTH_ERROR,
    _SERVICE_MISCONFIGURED_ERROR,
    Scope,
    ScopeConfigurationError,
    ScopeError,
    issue_delegation_token,
    resolve_scope,
    warn_if_delegation_secret_unconfigured,
)
from tests.conftest import (
    fake_response,
    forge_delegation_token_with_empty_secret_key,
    make_delegation_token,
    tamper_payload,
)

# --- missing / malformed Authorization header -------------------------------


def test_resolve_scope_without_header_raises():
    with pytest.raises(ScopeError):
        resolve_scope(None)


def test_resolve_scope_without_bearer_prefix_raises():
    with pytest.raises(ScopeError):
        resolve_scope('not-a-bearer-token')


# --- Delegations-Token: valid -----------------------------------------------


def test_valid_delegation_token_resolves_expected_scope():
    token = make_delegation_token(
        user_id='user-42', username='alice', team='kundenservice', collections=['handbuch', 'faq'], bot_id='bot-1'
    )
    scope = resolve_scope(f'Bearer {token}')

    assert scope == Scope(
        kind='delegated',
        user_id='user-42',
        username='alice',
        team='kundenservice',
        allowed_collections=['handbuch', 'faq'],
        bot_id='bot-1',
    )


def test_valid_delegation_token_may_carry_none_team_and_none_bot():
    token = make_delegation_token(team=None, bot_id=None)
    scope = resolve_scope(f'Bearer {token}')
    assert scope.team is None
    assert scope.bot_id is None


def test_valid_delegation_token_may_carry_no_collection_sentinel():
    token = make_delegation_token(collections=['__none__', 'handbuch'])
    scope = resolve_scope(f'Bearer {token}')
    assert scope.allowed_collections == ['__none__', 'handbuch']


# --- Delegations-Token: expired ----------------------------------------------


def test_expired_delegation_token_raises_generic_error():
    token = make_delegation_token(ttl_seconds=-1)
    with pytest.raises(ScopeError) as excinfo:
        resolve_scope(f'Bearer {token}')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


def test_delegation_token_expiring_exactly_now_is_rejected():
    # exp <= now is rejected (strict "> now", no leeway) -- a token minted
    # with ttl_seconds=0 expires the same second it was issued.
    token = issue_delegation_token(user_id='u', username='u', team=None, collections=[], ttl_seconds=0)
    with pytest.raises(ScopeError):
        resolve_scope(f'Bearer {token}')


# --- Delegations-Token: wrong signature --------------------------------------


def test_delegation_token_signed_with_wrong_secret_raises_generic_error(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'weave_delegation_secret', 'a-completely-different-secret')
    token = make_delegation_token()
    # Verify with the ORIGINAL secret restored -- simulating a token forged
    # with a secret the verifier doesn't hold.
    monkeypatch.setattr(settings, 'weave_delegation_secret', 'the-real-secret')
    with pytest.raises(ScopeError) as excinfo:
        resolve_scope(f'Bearer {token}')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


def test_delegation_token_with_corrupted_signature_raises_generic_error():
    token = make_delegation_token()
    part1, part2 = token.split('.')
    corrupted = part1 + '.' + part2[::-1]
    with pytest.raises(ScopeError):
        resolve_scope(f'Bearer {corrupted}')


# --- Delegations-Token: manipulated payload ----------------------------------


def test_delegation_token_with_tampered_payload_raises_generic_error():
    token = make_delegation_token(collections=['handbuch'])
    tampered = tamper_payload(token, collections=['handbuch', 'geheime-collection'])
    with pytest.raises(ScopeError) as excinfo:
        resolve_scope(f'Bearer {tampered}')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


def test_delegation_token_with_wrong_version_raises_generic_error():
    # A properly-signed token for a future/foreign wire-format version (2,
    # not the DELEGATION_TOKEN_VERSION this service verifies against) --
    # built directly against the private signing helpers so the signature
    # is genuinely valid FOR v=2, proving the `v == 1` check itself (not
    # just the signature check) rejects it.
    from app.services.scope import _b64url_encode, _canonical_json, _sign

    now = int(time.time())
    payload = {
        'v': 2,
        'sub': 'u',
        'username': 'u',
        'team': None,
        'collections': [],
        'bot': None,
        'iat': now,
        'exp': now + 300,
    }
    part1 = _b64url_encode(_canonical_json(payload))
    part2 = _b64url_encode(_sign(part1))
    token = f'{part1}.{part2}'

    with pytest.raises(ScopeError) as excinfo:
        resolve_scope(f'Bearer {token}')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


def test_delegation_token_with_malformed_payload_shape_raises_generic_error():
    # A properly-signed token whose payload still has the wrong SHAPE
    # (`collections` is a string, not a list) -- Python doesn't enforce
    # issue_delegation_token's own type hints at runtime, so this is built
    # directly (not via tamper_payload, which would break the signature
    # first and mask whether the shape check below it is even reachable).
    # Proves the payload's own field-shape validation in
    # _verify_delegation_token is real, not merely unreachable dead code
    # behind the signature check.
    token = issue_delegation_token(user_id='u', username='u', team=None, collections='not-a-list')  # type: ignore[arg-type]
    with pytest.raises(ScopeError) as excinfo:
        resolve_scope(f'Bearer {token}')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


def test_malformed_one_dot_token_raises_same_generic_error():
    # Contains exactly one '.' (so resolve_scope's dispatch treats it as a
    # Delegations-Token attempt) but isn't valid base64/JSON at all.
    with pytest.raises(ScopeError) as excinfo:
        resolve_scope('Bearer not-base64-at-all.also-not-base64')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


# --- every Delegations-Token failure collapses into ONE generic message ------


@pytest.mark.parametrize(
    'build_bad_token',
    [
        lambda: make_delegation_token(ttl_seconds=-1),
        lambda: make_delegation_token().split('.')[0] + '.' + 'x' * 40,
        lambda: tamper_payload(make_delegation_token(), v=99),
        lambda: tamper_payload(make_delegation_token(collections=['a']), collections=['a', 'b']),
    ],
)
def test_every_delegation_failure_mode_raises_identical_message(build_bad_token):
    with pytest.raises(ScopeError) as excinfo:
        resolve_scope(f'Bearer {build_bad_token()}')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


# --- Delegations-Token: unconfigured secret is fail-CLOSED, never fail-open --
#
# The security bug this section guards against: _verify_delegation_token used
# to hand settings.weave_delegation_secret straight to hmac.new() with no
# check that it was even set. `hmac.new(key=b'', ...)` against an empty
# secret is not an error -- it is a perfectly well-defined, deterministic
# signature that anyone who has read this module's own documented wire
# format (see its docstring) could compute for themselves, so a deployment
# with an unset secret (a typo in the env, a missing entry, drift between
# two deployments that must share one value) would accept a token an
# attacker forged from nothing, carrying whatever `collections` they liked.
# These tests prove that path now raises ScopeConfigurationError -- a type
# distinct from ScopeError precisely so app/api/deps.py can answer 503
# (service misconfigured) instead of 401 (your token is wrong), never
# silently resolving a Scope at all.


def test_unconfigured_secret_rejects_an_otherwise_validly_signed_token(monkeypatch):
    # A token minted by a properly-configured issuer (Weave-Runtime, in
    # production) BEFORE this deployment's own secret went missing --
    # exactly the "config drift between two deployments" scenario from this
    # module's own docstring. Even though the token's signature would
    # verify fine against the ORIGINAL secret, this deployment must never
    # fall back to treating an empty local secret as "matches anything".
    token = make_delegation_token(collections=['handbuch'])
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')

    with pytest.raises(ScopeConfigurationError) as excinfo:
        resolve_scope(f'Bearer {token}')
    assert str(excinfo.value) == _SERVICE_MISCONFIGURED_ERROR
    assert not isinstance(excinfo.value, ScopeError)


def test_unconfigured_secret_rejects_a_self_forged_token_with_arbitrary_collections(monkeypatch):
    # The actual exploit: an empty local secret would let anyone -- with no
    # inside knowledge beyond this module's own published wire format --
    # mint a token for THEMSELVES, granting THEMSELVES read access to any
    # collection they name. Must be refused with the same
    # ScopeConfigurationError as any other unconfigured-secret call, never
    # resolved into a working Scope.
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')
    forged = forge_delegation_token_with_empty_secret_key(collections=['every-collection-ever', 'streng-geheim'])

    with pytest.raises(ScopeConfigurationError) as excinfo:
        resolve_scope(f'Bearer {forged}')
    assert str(excinfo.value) == _SERVICE_MISCONFIGURED_ERROR


def test_configured_secret_still_verifies_the_same_forged_shape_as_invalid(monkeypatch):
    # Sanity check that the fix didn't just make ScopeConfigurationError fire
    # unconditionally: the SAME empty-key-signed token is rejected as a plain
    # bad-signature ScopeError (not ScopeConfigurationError) once the secret
    # IS configured, exactly like any other forged signature.
    monkeypatch.setattr(settings, 'weave_delegation_secret', 'a-real-secret')
    forged = forge_delegation_token_with_empty_secret_key()

    with pytest.raises(ScopeError) as excinfo:
        resolve_scope(f'Bearer {forged}')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


def test_issue_delegation_token_also_refuses_to_sign_with_unconfigured_secret(monkeypatch):
    # _sign's own guard -- the second line of defense behind
    # _verify_delegation_token's up-front check -- covers issue_delegation_
    # token too, so this service's own test-token issuer can never itself
    # produce an empty-key-signed token by accident.
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')
    with pytest.raises(ScopeConfigurationError):
        issue_delegation_token(user_id='u', username='u', team=None, collections=[])


def test_configured_secret_delegation_flow_is_unaffected_by_the_fix():
    # A fully-configured secret (the autouse fixture in conftest.py) must
    # keep resolving a valid Delegations-Token exactly as before -- the
    # fail-closed check must never fire for a properly configured
    # deployment.
    token = make_delegation_token(user_id='user-9', username='dana', team='vertrieb', collections=['handbuch'])
    scope = resolve_scope(f'Bearer {token}')
    assert scope == Scope(
        kind='delegated',
        user_id='user-9',
        username='dana',
        team='vertrieb',
        allowed_collections=['handbuch'],
        bot_id=None,
    )


def test_service_misconfigured_error_message_never_contains_the_secret(monkeypatch):
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')
    token = forge_delegation_token_with_empty_secret_key()
    with pytest.raises(ScopeConfigurationError) as excinfo:
        resolve_scope(f'Bearer {token}')
    # There is no secret value to leak here (that is the whole point), but
    # the message is still checked against a stand-in "secret" to prove the
    # exception carries only the fixed, generic string -- never anything
    # built from settings.weave_delegation_secret at all.
    assert 'a-real-secret' not in str(excinfo.value)
    assert str(excinfo.value) == _SERVICE_MISCONFIGURED_ERROR


def test_startup_warning_logs_when_secret_unconfigured_without_revealing_it(caplog, monkeypatch):
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')
    with caplog.at_level(logging.WARNING):
        warn_if_delegation_secret_unconfigured()

    assert any('WEAVE_DELEGATION_SECRET' in record.message for record in caplog.records)
    assert 'a-real-secret' not in caplog.text


def test_startup_warning_silent_when_secret_configured(caplog):
    with caplog.at_level(logging.WARNING):
        warn_if_delegation_secret_unconfigured()

    assert caplog.records == []


# --- Personal-Token: valid ----------------------------------------------------


def test_valid_personal_token_resolves_scope_via_introspection_and_collections():
    introspect_resp = fake_response(
        200, {'active': True, 'user_id': 'user-7', 'username': 'bob', 'team': 'support', 'is_admin': False}
    )
    collections_resp = fake_response(
        200,
        [
            {'slug': 'handbuch', 'name': 'Handbuch', 'description': None, 'public': True},
            {'slug': 'intern', 'name': 'Intern', 'description': 'nur Support', 'public': False},
        ],
    )

    with patch('app.services.scope.httpx.post', return_value=introspect_resp) as mock_post, patch(
        'app.services.scope.httpx.get', return_value=collections_resp
    ) as mock_get:
        scope = resolve_scope('Bearer some-personal-token-abc123')

    assert scope == Scope(
        kind='personal',
        user_id='user-7',
        username='bob',
        team='support',
        allowed_collections=['handbuch', 'intern'],
    )

    # The introspection call carries the SERVICE credential, never the raw
    # end-user token, as its own Authorization header.
    _, post_kwargs = mock_post.call_args
    assert post_kwargs['headers']['Authorization'] == 'Bearer test-introspection-service-token'
    assert post_kwargs['json'] == {'token': 'some-personal-token-abc123'}

    _, get_kwargs = mock_get.call_args
    assert get_kwargs['params'] == {'team': 'support'}


def test_personal_token_with_no_team_omits_team_query_param():
    introspect_resp = fake_response(
        200, {'active': True, 'user_id': 'user-8', 'username': 'carol', 'team': None, 'is_admin': False}
    )
    collections_resp = fake_response(200, [])

    with patch('app.services.scope.httpx.post', return_value=introspect_resp), patch(
        'app.services.scope.httpx.get', return_value=collections_resp
    ) as mock_get:
        scope = resolve_scope('Bearer some-token')

    assert scope.team is None
    _, get_kwargs = mock_get.call_args
    assert get_kwargs['params'] == {}


# --- Personal-Token: inactive -------------------------------------------------


def test_inactive_personal_token_raises_generic_error():
    introspect_resp = fake_response(200, {'active': False})
    with patch('app.services.scope.httpx.post', return_value=introspect_resp):
        with pytest.raises(ScopeError) as excinfo:
            resolve_scope('Bearer an-unknown-or-expired-or-disabled-token')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


def test_introspection_transport_failure_raises_generic_error():
    with patch('app.services.scope.httpx.post', side_effect=httpx.ConnectError('boom')):
        with pytest.raises(ScopeError) as excinfo:
            resolve_scope('Bearer some-token')
    assert str(excinfo.value) == _GENERIC_AUTH_ERROR


# --- the token itself never appears in a log record --------------------------


def test_delegation_token_failure_never_logs_the_raw_token(caplog):
    marker = 'SENTINEL-DELEGATION-VALUE-should-never-be-logged'
    bad_token = f'{marker}.also-a-marker-{marker}'

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ScopeError) as excinfo:
            resolve_scope(f'Bearer {bad_token}')

    assert marker not in str(excinfo.value)
    assert marker not in caplog.text


def test_personal_token_failure_never_logs_the_raw_token(caplog):
    marker = 'SENTINEL-PERSONAL-TOKEN-should-never-be-logged'

    # A realistic transport failure message (connection refused) never
    # itself contains the request body/token -- this proves resolve_scope's
    # OWN code (the `except httpx.HTTPError` branch in _resolve_personal_scope,
    # which logs the caught exception but never `token` itself) is what
    # keeps the marker out, not a coincidence of what the fake failure text
    # happens to say.
    with caplog.at_level(logging.DEBUG):
        with patch('app.services.scope.httpx.post', side_effect=httpx.ConnectError('connection refused')):
            with pytest.raises(ScopeError):
                resolve_scope(f'Bearer {marker}')

    assert marker not in caplog.text


def test_inactive_personal_token_failure_never_logs_the_raw_token(caplog):
    marker = 'SENTINEL-INACTIVE-TOKEN-should-never-be-logged'
    introspect_resp = fake_response(200, {'active': False})

    with caplog.at_level(logging.DEBUG):
        with patch('app.services.scope.httpx.post', return_value=introspect_resp):
            with pytest.raises(ScopeError):
                resolve_scope(f'Bearer {marker}')

    assert marker not in caplog.text


def test_expired_delegation_token_ttl_uses_configured_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'delegation_token_ttl_seconds', 1)
    before = int(time.time())
    token = issue_delegation_token(user_id='u', username='u', team=None, collections=[])
    part1 = token.split('.')[0]
    import base64
    import json

    padding = '=' * (-len(part1) % 4)
    payload = json.loads(base64.urlsafe_b64decode(part1 + padding))
    assert payload['exp'] - payload['iat'] == 1
    assert payload['iat'] >= before
