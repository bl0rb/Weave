"""Unit tests for app/services/n8n_client.py's `run_flow` -- httpx.post
mocked (mirrors tests/test_llm.py's own OpenAICompatibleLLM tests and
tests/test_retrieval_client.py's `_FakeResponse` pattern), no real network
call anywhere in this file. Covers: the request body/headers actually sent
(scope, delegation token, signature), response parsing (success, missing/
invalid `sources`), and the N8nUnavailable/N8nError split for failure
responses -- plus the explicit regression guard the task calls for: the
delegation token never appears in a log record or in any exception message
this module raises.
"""

import base64
import hashlib
import hmac
import json
import logging

import httpx
import pytest

from app.core.config import settings
from app.schemas.bot import BotConfig
from app.schemas.chat import ChatUser
from app.services.delegation import DelegationConfigError
from app.services.n8n_client import N8nError, N8nUnavailable, run_flow

_SECRET = 'unit-test-delegation-secret'
_WEBHOOK_URL = 'https://n8n.example.test/webhook/agent'


@pytest.fixture(autouse=True)
def _n8n_settings(monkeypatch):
    monkeypatch.setattr(settings, 'weave_delegation_secret', _SECRET)
    monkeypatch.setattr(settings, 'delegation_token_ttl_seconds', 300)
    monkeypatch.setattr(settings, 'tools_base_url', 'https://weave-tools.internal.example.com')


def _bot(*, timeout_seconds: int = 120) -> BotConfig:
    return BotConfig.model_validate(
        {
            'id': 'n8n-agent',
            'name': 'n8n Agent',
            'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
            'system_prompt': 'Delegates to n8n.',
            'n8n': {'webhook_url': _WEBHOOK_URL, 'timeout_seconds': timeout_seconds},
        }
    )


class _FakeResponse:
    """Mirrors tests/test_retrieval_client.py's/tests/test_llm.py's own
    identical helper -- just enough of an httpx.Response for this module's
    own parsing."""

    def __init__(self, status_code: int, json_data=None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


def _source(**overrides) -> dict:
    source = {'document_id': 'doc-1', 'chunk_id': 1, 'source': 'confluence', 'score': 0.9}
    source.update(overrides)
    return source


# --- request body / headers ---------------------------------------------------


def test_run_flow_posts_to_the_bots_own_webhook_url(monkeypatch):
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['url'] = url
        captured['timeout'] = timeout
        return _FakeResponse(200, {'answer': 'hi'})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)
    run_flow(_bot(timeout_seconds=45), 'hello', [], ChatUser(id='u-1'), ['vertraege'])

    assert captured['url'] == _WEBHOOK_URL
    assert captured['timeout'] == 45  # bot.n8n.timeout_seconds, not settings.llm_timeout_seconds


def test_run_flow_body_carries_message_history_user_bot_id_and_scope(monkeypatch):
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['body'] = json.loads(content)
        return _FakeResponse(200, {'answer': 'hi'})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)
    history = [{'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'second'}]
    user = ChatUser(id='u-1', username='j.schmidt', team='legal')

    run_flow(_bot(), 'what next', history, user, ['vertraege', '__none__'])

    body = captured['body']
    assert body['message'] == 'what next'
    assert body['history'] == history
    assert body['user'] == {'id': 'u-1', 'username': 'j.schmidt', 'team': 'legal', 'teams': ['legal']}
    assert body['bot_id'] == 'n8n-agent'
    assert body['allowed_collections'] == ['vertraege', '__none__']
    assert body['tools_base_url'] == 'https://weave-tools.internal.example.com'
    assert isinstance(body['delegation_token'], str) and body['delegation_token']


def test_run_flow_delegation_token_embeds_the_same_scope_and_bot_id(monkeypatch):
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['body'] = json.loads(content)
        return _FakeResponse(200, {'answer': 'hi'})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)
    run_flow(_bot(), 'hi', [], ChatUser(id='u-1', team='legal'), ['vertraege'])

    token = captured['body']['delegation_token']
    payload_b64 = token.split('.')[0]
    padding = '=' * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    assert payload['collections'] == ['vertraege']
    assert payload['bot'] == 'n8n-agent'
    assert payload['sub'] == 'u-1'
    assert payload['team'] == 'legal'


def test_run_flow_signature_header_verifies_against_the_exact_body_bytes(monkeypatch):
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['content'] = content
        captured['headers'] = headers
        return _FakeResponse(200, {'answer': 'hi'})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)
    run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), ['vertraege'])

    signature = captured['headers']['X-Weave-Signature']
    expected = hmac.new(_SECRET.encode('utf-8'), captured['content'], hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)


def test_run_flow_signature_does_not_verify_with_a_different_secret(monkeypatch):
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['content'] = content
        captured['headers'] = headers
        return _FakeResponse(200, {'answer': 'hi'})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)
    run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), ['vertraege'])

    signature = captured['headers']['X-Weave-Signature']
    wrong = hmac.new(b'wrong-secret', captured['content'], hashlib.sha256).hexdigest()
    assert not hmac.compare_digest(signature, wrong)


def test_run_flow_content_type_header_is_json(monkeypatch):
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['headers'] = headers
        return _FakeResponse(200, {'answer': 'hi'})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)
    run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])
    assert captured['headers']['Content-Type'] == 'application/json'


def test_run_flow_propagates_delegation_config_error_when_secret_missing(monkeypatch):
    monkeypatch.setattr(settings, 'weave_delegation_secret', '')

    def _unexpected_post(*args, **kwargs):
        raise AssertionError('must never call the webhook without a valid delegation token')

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _unexpected_post)
    with pytest.raises(DelegationConfigError):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


# --- response parsing ---------------------------------------------------------


def test_run_flow_parses_answer_and_sources(monkeypatch):
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        lambda *a, **k: _FakeResponse(200, {'answer': 'the answer', 'sources': [_source(chunk_id=1), _source(chunk_id=2)]}),
    )
    result = run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])
    assert result.answer == 'the answer'
    assert len(result.sources) == 2
    assert result.sources[0].document_id == 'doc-1'


def test_run_flow_missing_sources_field_defaults_to_empty_list(monkeypatch):
    monkeypatch.setattr('app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(200, {'answer': 'hi'}))
    result = run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])
    assert result.sources == []


def test_run_flow_null_sources_field_defaults_to_empty_list(monkeypatch):
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(200, {'answer': 'hi', 'sources': None})
    )
    result = run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])
    assert result.sources == []


def test_run_flow_non_dict_response_raises_n8n_error(monkeypatch):
    monkeypatch.setattr('app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(200, ['not', 'a', 'dict']))
    with pytest.raises(N8nError):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


def test_run_flow_missing_answer_field_raises_n8n_error(monkeypatch):
    monkeypatch.setattr('app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(200, {'sources': []}))
    with pytest.raises(N8nError):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


def test_run_flow_non_string_answer_field_raises_n8n_error(monkeypatch):
    monkeypatch.setattr('app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(200, {'answer': 42}))
    with pytest.raises(N8nError):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


def test_run_flow_non_list_sources_field_raises_n8n_error(monkeypatch):
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(200, {'answer': 'hi', 'sources': 'nope'})
    )
    with pytest.raises(N8nError):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


def test_run_flow_source_entry_missing_required_fields_raises_n8n_error(monkeypatch):
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        lambda *a, **k: _FakeResponse(200, {'answer': 'hi', 'sources': [{'source': 'confluence'}]}),
    )
    with pytest.raises(N8nError):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


def test_run_flow_non_json_response_raises_n8n_error(monkeypatch):
    monkeypatch.setattr('app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(200, text='not json'))
    with pytest.raises(N8nError):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


# --- unavailable vs. error split ----------------------------------------------


def test_run_flow_connection_error_raises_n8n_unavailable(monkeypatch):
    def _connection_refused(*args, **kwargs):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _connection_refused)
    with pytest.raises(N8nUnavailable):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


def test_run_flow_timeout_raises_n8n_unavailable(monkeypatch):
    def _timeout(*args, **kwargs):
        raise httpx.TimeoutException('timed out')

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _timeout)
    with pytest.raises(N8nUnavailable):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


@pytest.mark.parametrize('status_code', [500, 502, 503])
def test_run_flow_5xx_raises_n8n_unavailable(monkeypatch, status_code):
    monkeypatch.setattr('app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(status_code, text='boom'))
    with pytest.raises(N8nUnavailable):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


@pytest.mark.parametrize('status_code', [400, 401, 404, 422])
def test_run_flow_4xx_raises_n8n_error(monkeypatch, status_code):
    monkeypatch.setattr('app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(status_code, text='bad'))
    with pytest.raises(N8nError):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), [])


# --- the delegation token must never leak into a log or an error message ----


def test_run_flow_token_never_appears_in_the_n8n_unavailable_message(monkeypatch, caplog):
    captured = {}

    def _connection_refused(url, content=None, headers=None, timeout=None):
        captured['content'] = content
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _connection_refused)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(N8nUnavailable) as exc_info:
            run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), ['vertraege'])

    token = json.loads(captured['content'])['delegation_token']
    assert token not in str(exc_info.value)
    for record in caplog.records:
        assert token not in record.getMessage()


def test_run_flow_token_never_appears_in_the_n8n_error_message(monkeypatch, caplog):
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['content'] = content
        return _FakeResponse(400, text='bad request')

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(N8nError) as exc_info:
            run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), ['vertraege'])

    token = json.loads(captured['content'])['delegation_token']
    assert token not in str(exc_info.value)
    for record in caplog.records:
        assert token not in record.getMessage()


def test_run_flow_token_never_appears_in_any_log_record_on_success(monkeypatch, caplog):
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['content'] = content
        return _FakeResponse(200, {'answer': 'hi'})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)
    with caplog.at_level(logging.DEBUG):
        run_flow(_bot(), 'hi', [], ChatUser(id='u-1'), ['vertraege'])

    token = json.loads(captured['content'])['delegation_token']
    for record in caplog.records:
        assert token not in record.getMessage()


def test_signature_refuses_an_empty_delegation_secret(monkeypatch):
    # Regression: an empty key still produces a valid HMAC, so _signature
    # must fail closed on its own rather than relying on run_flow minting a
    # token first.
    from app.services import n8n_client
    from app.services.delegation import DelegationConfigError

    monkeypatch.setattr(n8n_client.settings, 'weave_delegation_secret', '')
    with pytest.raises(DelegationConfigError):
        n8n_client._signature(b'{"any":"body"}')
