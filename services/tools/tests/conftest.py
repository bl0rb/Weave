"""Shared test fixtures: a fully-configured `settings` object (every test
gets one automatically -- the fail-closed-when-unconfigured paths are each
tested explicitly by unsetting just the one setting they care about), a
REST TestClient, and small helpers for building fake upstream (Weave-API /
Weave-Retrieval) HTTP responses and valid/tampered Delegations-Tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.services.scope import _b64url_encode, _canonical_json, issue_delegation_token

TOOLS_API_TOKEN = 'test-tools-api-token'
INTROSPECTION_SERVICE_TOKEN = 'test-introspection-service-token'
RETRIEVAL_API_TOKEN = 'test-retrieval-api-token'
WEAVE_DELEGATION_SECRET = 'test-weave-delegation-secret'

WEAVE_API_BASE_URL = 'http://weave-api.test'
RETRIEVAL_BASE_URL = 'http://weave-retrieval.test'

TOOLS_SERVICE_HEADERS = {'X-Tools-Service-Token': TOOLS_API_TOKEN}

client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def _configured_settings(monkeypatch):
    monkeypatch.setattr(settings, 'tools_api_token', TOOLS_API_TOKEN)
    monkeypatch.setattr(settings, 'introspection_service_token', INTROSPECTION_SERVICE_TOKEN)
    monkeypatch.setattr(settings, 'retrieval_api_token', RETRIEVAL_API_TOKEN)
    monkeypatch.setattr(settings, 'weave_delegation_secret', WEAVE_DELEGATION_SECRET)
    monkeypatch.setattr(settings, 'weave_api_base_url', WEAVE_API_BASE_URL)
    monkeypatch.setattr(settings, 'retrieval_base_url', RETRIEVAL_BASE_URL)


def fake_response(status_code: int, json_body, *, method: str = 'GET', url: str = 'http://upstream.test/') -> httpx.Response:
    """A real httpx.Response with a request attached, so `.raise_for_status()`
    behaves exactly like a genuine one instead of raising the SDK's own
    "request instance has not been set" RuntimeError.
    """
    return httpx.Response(status_code=status_code, json=json_body, request=httpx.Request(method, url))


def make_delegation_token(
    *,
    user_id: str = 'user-1',
    username: str = 'alice',
    team: str | None = 'kundenservice',
    collections: list[str] | None = None,
    bot_id: str | None = None,
    ttl_seconds: int | None = 300,
) -> str:
    return issue_delegation_token(
        user_id=user_id,
        username=username,
        team=team,
        collections=collections if collections is not None else ['handbuch', 'faq'],
        bot_id=bot_id,
        ttl_seconds=ttl_seconds,
    )


def forge_delegation_token_with_empty_secret_key(**payload_overrides) -> str:
    """Build a Delegations-Token exactly the way an attacker who has read
    app/services/scope.py's own documented wire format could, WITHOUT ever
    calling issue_delegation_token (which itself refuses to sign with an
    empty secret -- see that module's _sign) -- signed directly with an
    empty HMAC key, the exact forgery an unconfigured
    settings.weave_delegation_secret would otherwise accept. Used to prove
    the fail-closed fix for that specific security bug: an unset secret on
    the VERIFYING side must reject this, never resolve it into a Scope.
    """
    now = int(time.time())
    payload = {
        'v': 1,
        'sub': 'attacker-controlled',
        'username': 'attacker-controlled',
        'team': None,
        'collections': ['streng-geheime-collection'],
        'bot': None,
        'iat': now,
        'exp': now + 300,
    }
    payload.update(payload_overrides)
    part1 = _b64url_encode(_canonical_json(payload))
    part2 = _b64url_encode(hmac.new(b'', part1.encode('ascii'), hashlib.sha256).digest())
    return f'{part1}.{part2}'


def tamper_payload(token: str, **overrides) -> str:
    """Take a validly-signed Delegations-Token and rewrite its payload
    without recomputing the signature -- exactly the "manipulierter
    Payload" attack the contract's verifier must reject via the plain
    signature mismatch, not via any special-cased tamper detection.
    """
    part1, part2 = token.split('.')
    padding = '=' * (-len(part1) % 4)
    payload = json.loads(base64.urlsafe_b64decode(part1 + padding))
    payload.update(overrides)
    new_json = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    new_part1 = base64.urlsafe_b64encode(new_json).rstrip(b'=').decode('ascii')
    return f'{new_part1}.{part2}'
