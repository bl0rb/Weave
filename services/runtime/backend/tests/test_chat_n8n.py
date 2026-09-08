"""End-to-end tests for the n8n bot-provider pipeline (contracts/n8n-flow.md,
app/services/chat.py's `_run_n8n_turn`, app/services/n8n_client.py) --
mirrors tests/test_chat_e2e.py's own style (real HTTP round trips through
the FastAPI app, Weave-Retrieval's own HTTP calls mocked at the exact seam
app/services/retrieval_client.py talks to) but against an isolated
tmp_path bots directory carrying ONE n8n-provider bot, since the real
bots/ directory intentionally ships its own n8n example as
bots/n8n-agent.yaml.example -- inert on purpose, see that file's own
comment and tests/test_botconfig.py's test_bot_files_ignore_yaml_example_suffixed_files
-- rather than a real, loadable third bot (which would change the exact
roster tests/test_internal_api.py and tests/test_health.py assert).

`httpx.post` is mocked GLOBALLY (both app/services/retrieval_client.py and
app/services/n8n_client.py import the same `httpx` module) -- `_dispatch_post`
below routes a call to either the n8n webhook or Weave-Retrieval's own
`/api/v1/search` by URL, exactly the split a real deployment's two actual
endpoints would themselves provide.
"""

import base64
import json
import logging

import httpx
import pytest
import yaml

from app.core.config import settings
from app.services.botconfig import list_bots
from tests.conftest import AUTH_HEADERS, client

_WEBHOOK_URL = 'https://n8n.example.test/webhook/agent'
_SECRET = 'chat-n8n-test-secret'

_N8N_BOT_YAML = {
    'id': 'n8n-agent',
    'name': 'n8n Agent',
    'description': 'Delegiert an n8n.',
    'model': {'provider': 'n8n', 'model': 'n8n-agent-flow'},
    'system_prompt': 'Delegates to n8n.',
    'n8n': {'webhook_url': _WEBHOOK_URL, 'timeout_seconds': 90},
    'retrieval': {'enabled': True, 'collections': ['vertraege']},
    'permissions': {'teams': []},
    'guard': {'require_sources': True, 'no_context_reply': 'Der Agentenflow hat nichts gefunden.'},
}

# A second bot, `include_uncollected: False`, so `resolve_collection_scope`
# never appends NO_COLLECTION_SENTINEL to its resolved scope -- used only by
# the source-scope-check tests below that need a scope WITHOUT the sentinel,
# to exercise the "no collection claimed, sentinel also not granted -> drop"
# half of the Sentinel-Regel (see `_filter_n8n_sources_by_scope`'s own
# docstring, app/services/chat.py).
_N8N_BOT_NO_ALTBESTAND_YAML = {
    **_N8N_BOT_YAML,
    'id': 'n8n-agent-no-altbestand',
    'retrieval': {'enabled': True, 'collections': ['vertraege'], 'include_uncollected': False},
}


@pytest.fixture(autouse=True)
def _n8n_bot_environment(tmp_path, monkeypatch):
    (tmp_path / 'n8n-agent.yaml').write_text(yaml.safe_dump(_N8N_BOT_YAML), encoding='utf-8')
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'weave_delegation_secret', _SECRET)
    monkeypatch.setattr(settings, 'delegation_token_ttl_seconds', 300)
    monkeypatch.setattr(settings, 'n8n_allowed_base_urls', ['https://n8n.example.test/webhook/'])
    monkeypatch.setattr(settings, 'tools_base_url', 'https://weave-tools.internal.example.com')
    # Sanity check the fixture bot itself loads -- a failure here would
    # otherwise show up as confusing 500s in every test below instead.
    assert [bot.id for bot in list_bots()] == ['n8n-agent']


class _FakeResponse:
    """Mirrors tests/test_chat_e2e.py's identical helper."""

    def __init__(self, status_code: int, json_data=None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


def _collection(**overrides) -> dict:
    collection = {'slug': 'vertraege', 'name': 'Vertraege', 'description': None, 'public': False}
    collection.update(overrides)
    return collection


def _readable_collections(*collections: dict):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(200, list(collections) or [_collection()])

    return _fake_get


def _n8n_response(webhook_body: dict, *, status_code: int = 200):
    def _fake_post(url, content=None, json=None, headers=None, timeout=None):
        if url == _WEBHOOK_URL:
            return _FakeResponse(status_code, webhook_body)
        raise AssertionError(f'unexpected POST to {url!r} -- an n8n turn must never call Weave-Retrieval search()')

    return _fake_post


def _decode_token_payload(token: str) -> dict:
    payload_b64 = token.split('.')[0]
    padding = '=' * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))


_KNOWLEDGE_MESSAGE = 'Welche Vertraege laufen naechsten Monat aus?'  # no RULES keyword match -> 'knowledge'
_ACTION_MESSAGE = 'Bitte buche dafuer einen Termin naechste Woche.'  # matches the 'buche' RULES pattern -> 'action'


# --- conversational: never reaches n8n ---------------------------------------


def test_conversational_intent_never_calls_n8n_and_answers_with_the_smalltalk_reply(monkeypatch):
    def _unexpected_post(*args, **kwargs):
        raise AssertionError('a conversational n8n-bot turn must never call the n8n webhook')

    def _unexpected_get(*args, **kwargs):
        raise AssertionError('a conversational n8n-bot turn must never resolve Collections scope')

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _unexpected_post)
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _unexpected_get)

    resp = client.post('/internal/chat', json={'bot_id': 'n8n-agent', 'message': 'Hallo!'}, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body['trace']['intent'] == 'conversational'
    assert body['trace']['model'] is None
    assert body['trace']['n8n'] is None  # n8n was never called -- nothing to report
    assert body['sources'] == []
    assert body['answer']  # the fixed smalltalk reply -- exact text is chat.py's own concern


# --- a non-conversational intent delegates to n8n -----------------------------


def test_knowledge_intent_delegates_to_n8n_and_returns_its_answer_and_sources(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        _n8n_response(
            {
                'answer': 'Drei Vertraege laufen aus.',
                'sources': [{'document_id': 'doc-1', 'chunk_id': 1, 'source': 'sharepoint', 'score': 0.8}],
            }
        ),
    )

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['trace']['intent'] == 'knowledge'
    assert body['answer'] == 'Drei Vertraege laufen aus.'
    assert len(body['sources']) == 1
    assert body['sources'][0]['document_id'] == 'doc-1'
    assert body['trace']['guard'] is None
    assert body['trace']['model'] is None  # no LLM call for an n8n turn
    assert body['trace']['retrieval'] is None  # Weave-Runtime itself never calls search() for n8n
    assert body['trace']['n8n'] == {'dropped_sources': 0}  # nothing to drop -- the reported source is in scope
    assert 'n8n_ms' in body['trace']['timings_ms']
    assert 'llm_ms' not in body['trace']['timings_ms']


def test_action_intent_also_delegates_to_n8n(monkeypatch):
    # Lifts the V1_UNSUPPORTED_INTENTS restriction specifically for an
    # n8n-provider bot -- see chat.py's own docstring, step 3a.
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post', _n8n_response({'answer': 'Termin LEGAL-482 angelegt.', 'sources': []})
    )

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _ACTION_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['trace']['intent'] == 'action'
    # guard.require_sources is True but this bot's flow returned [] sources
    # -- must trigger the guard exactly like the dedicated test below, this
    # just confirms 'action' is not somehow exempt from it.
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert body['answer'] == 'Der Agentenflow hat nichts gefunden.'


# --- scope resolution feeds the delegation token ------------------------------


def test_delegation_token_embeds_the_resolved_collection_scope(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['body'] = json.loads(content)
        return _FakeResponse(200, {'answer': 'ok', 'sources': []})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'username': 'j.schmidt', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200  # guard fires (no sources) but the request itself is well-formed

    body = captured['body']
    # bot.retrieval.collections == ['vertraege'], readable == ['vertraege']
    # (from _readable_collections()' default) -> real_scope == ['vertraege'],
    # plus the NO_COLLECTION_SENTINEL (include_uncollected defaults True).
    assert body['allowed_collections'] == ['vertraege', '__none__']
    assert body['user'] == {'id': 'u-1', 'username': 'j.schmidt', 'team': 'legal', 'teams': ['legal']}
    assert body['bot_id'] == 'n8n-agent'
    assert body['tools_base_url'] == 'https://weave-tools.internal.example.com'

    token_payload = _decode_token_payload(body['delegation_token'])
    assert token_payload['collections'] == ['vertraege', '__none__']
    assert token_payload['bot'] == 'n8n-agent'
    assert token_payload['sub'] == 'u-1'
    assert token_payload['username'] == 'j.schmidt'
    assert token_payload['team'] == 'legal'


def test_a_request_level_filter_narrows_the_scope_signed_into_the_delegation_token(monkeypatch):
    # Collection-Filter pro Anfrage: `ChatRequest.collections` must give an
    # n8n agent flow LESS than its unfiltered rights scope, via the exact
    # same `resolve_collection_scope` the retrieval path uses -- never MORE.
    # Unfiltered here would be ['vertraege', '__none__'] (see the test
    # above); filtering the request down to 'vertraege' alone drops the
    # Altbestand sentinel out of what actually gets signed.
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['body'] = json.loads(content)
        return _FakeResponse(200, {'answer': 'ok', 'sources': []})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)

    resp = client.post(
        '/internal/chat',
        json={
            'bot_id': 'n8n-agent',
            'message': _KNOWLEDGE_MESSAGE,
            'user': {'id': 'u-1', 'team': 'legal'},
            'collections': ['vertraege'],
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200

    body = captured['body']
    assert body['allowed_collections'] == ['vertraege']
    token_payload = _decode_token_payload(body['delegation_token'])
    assert token_payload['collections'] == ['vertraege']


def test_a_request_level_filter_naming_only_foreign_slugs_signs_an_empty_scope(monkeypatch):
    # Unlike the retrieval path's own guard, an n8n turn does NOT refuse to
    # run just because the resolved (filtered) scope is empty -- see
    # `_run_n8n_turn`'s own docstring -- but the filter must still never let
    # a foreign slug into the signed token: an empty scope is signed
    # instead, exactly like a caller with no real rights at all would get.
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    captured = {}

    def _fake_post(url, content=None, headers=None, timeout=None):
        captured['body'] = json.loads(content)
        return _FakeResponse(200, {'answer': 'ok', 'sources': []})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)

    resp = client.post(
        '/internal/chat',
        json={
            'bot_id': 'n8n-agent',
            'message': _KNOWLEDGE_MESSAGE,
            'user': {'id': 'u-1', 'team': 'legal'},
            'collections': ['geheimprojekt'],
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200

    body = captured['body']
    assert body['allowed_collections'] == []
    token_payload = _decode_token_payload(body['delegation_token'])
    assert token_payload['collections'] == []


# --- guard: n8n returns no sources, require_sources is True ------------------


def test_no_sources_with_require_sources_triggers_the_guard(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.n8n_client.httpx.post', _n8n_response({'answer': 'Ich habe etwas gefunden.'}))

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert body['answer'] == 'Der Agentenflow hat nichts gefunden.'  # bot.guard.no_context_reply, not n8n's own text
    assert body['sources'] == []


def test_sources_present_never_triggers_the_guard(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        _n8n_response({'answer': 'belegte Antwort', 'sources': [{'document_id': 'doc-9', 'chunk_id': 3, 'source': 'confluence'}]}),
    )

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    body = resp.json()
    assert body['trace']['guard'] is None
    assert body['answer'] == 'belegte Antwort'


# --- source-scope check: n8n's reported `sources` are a claim, not a grant --
# (contracts/n8n-flow.md's "Quellen sind eine Behauptung, keine Berechtigung",
# app/services/chat.py's `_filter_n8n_sources_by_scope`) -- these exercise it
# through the full HTTP pipeline, unlike the direct unit tests in
# tests/test_chat_service.py.


def test_a_source_outside_the_signed_scope_is_dropped_but_the_turn_still_succeeds(monkeypatch):
    # bot.retrieval.collections == ['vertraege'], readable == ['vertraege']
    # -> resolved/signed scope == ['vertraege', '__none__']. n8n reports one
    # in-scope and one out-of-scope source -- only the latter is dropped.
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        _n8n_response(
            {
                'answer': 'Antwort mit gemischten Quellen.',
                'sources': [
                    {'document_id': 'doc-1', 'chunk_id': 1, 'source': 'sharepoint', 'collection': 'vertraege'},
                    {'document_id': 'doc-2', 'chunk_id': 2, 'source': 'sharepoint', 'collection': 'geheim'},
                ],
            }
        ),
    )

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['trace']['guard'] is None  # one source survived -- require_sources is satisfied
    assert body['answer'] == 'Antwort mit gemischten Quellen.'
    assert [source['document_id'] for source in body['sources']] == ['doc-1']
    assert body['trace']['n8n'] == {'dropped_sources': 1}


def test_when_every_reported_source_is_out_of_scope_the_guard_fires_anyway(monkeypatch):
    # Unlike test_no_sources_with_require_sources_triggers_the_guard above,
    # n8n DID report a source here -- it just doesn't survive the scope
    # check, which must trigger the exact same guard outcome as reporting
    # none at all.
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        _n8n_response(
            {
                'answer': 'Sollte nicht beim Aufrufer ankommen.',
                'sources': [{'document_id': 'doc-9', 'chunk_id': 9, 'source': 'sharepoint', 'collection': 'geheim'}],
            }
        ),
    )

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert body['answer'] == 'Der Agentenflow hat nichts gefunden.'
    assert body['sources'] == []
    assert body['trace']['n8n'] == {'dropped_sources': 1}


def test_a_source_reporting_no_collection_is_dropped_when_the_scope_grants_no_altbestand_access(
    monkeypatch, tmp_path
):
    # The Sentinel-Regel's other half: a source claiming NO collection at
    # all is only believed when the signed scope itself includes
    # NO_COLLECTION_SENTINEL -- this bot's own `include_uncollected: False`
    # means it never does.
    (tmp_path / 'n8n-agent-no-altbestand.yaml').write_text(
        yaml.safe_dump(_N8N_BOT_NO_ALTBESTAND_YAML), encoding='utf-8'
    )
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        _n8n_response({'answer': 'ok', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'x'}]}),
    )

    resp = client.post(
        '/internal/chat',
        json={
            'bot_id': 'n8n-agent-no-altbestand',
            'message': _KNOWLEDGE_MESSAGE,
            'user': {'id': 'u-1', 'team': 'legal'},
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert body['trace']['n8n'] == {'dropped_sources': 1}


def test_dropped_sources_are_logged_with_bot_id_and_count_but_never_their_content(monkeypatch, caplog):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        _n8n_response(
            {
                'answer': 'ok',
                'sources': [
                    {'document_id': 'doc-1', 'chunk_id': 1, 'source': 'sharepoint', 'collection': 'vertraege'},
                    {'document_id': 'geheim-doc-XYZ', 'chunk_id': 2, 'source': 'confluence', 'collection': 'geheim'},
                ],
            }
        ),
    )

    with caplog.at_level(logging.WARNING):
        resp = client.post(
            '/internal/chat',
            json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
            headers=AUTH_HEADERS,
        )
    assert resp.status_code == 200

    messages = [record.getMessage() for record in caplog.records]
    assert any('n8n-agent' in message and '1' in message for message in messages)
    for message in messages:
        assert 'geheim-doc-XYZ' not in message
        assert 'geheim' not in message


# --- n8n unreachable -> 503 ----------------------------------------------------


def test_n8n_unreachable_returns_503(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())

    def _connection_refused(*args, **kwargs):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _connection_refused)

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 503
    assert resp.json()['detail']


def test_n8n_5xx_returns_503(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post', lambda *a, **k: _FakeResponse(502, text='bad gateway')
    )

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 503


def test_n8n_malformed_response_is_left_uncaught_as_a_deployment_bug(monkeypatch):
    # N8nError is deliberately NOT caught by app/api/internal.py (see
    # chat.py's own docstring: this can only mean the n8n FLOW itself is
    # misconfigured, the same treatment RetrievalError already gets) -- it
    # surfaces as this service's default 500 in a real deployment. Starlette's
    # TestClient re-raises an unhandled server exception instead of turning
    # it into a response object by default, so this is asserted the same way
    # a real 500 for an uncaught exception would be observed in-process --
    # there is no existing precedent in this suite for a real HTTP-level 500
    # assertion either, for the same reason (RetrievalError/LLMError are
    # never exercised via the TestClient in this codebase).
    from app.services.n8n_client import N8nError

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.n8n_client.httpx.post', _n8n_response({'no_answer_field': True}))

    with pytest.raises(N8nError):
        client.post(
            '/internal/chat',
            json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
            headers=AUTH_HEADERS,
        )


# --- streaming: single delta for a genuine n8n answer -------------------------


def _parse_sse_events(text: str) -> list[dict]:
    events = []
    for block in text.split('\n\n'):
        block = block.strip('\n')
        if not block:
            continue
        assert block.startswith('data: ')
        events.append(json.loads(block[len('data: '):]))
    return events


def test_streaming_successful_n8n_answer_is_emitted_as_exactly_one_delta(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr(
        'app.services.n8n_client.httpx.post',
        _n8n_response({'answer': 'Eine ganze mehrwortige Antwort in einem Stueck.', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'confluence'}]}),
    )

    resp = client.post(
        '/internal/chat/stream',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    types = [event['type'] for event in events]
    assert types[0] == 'trace'
    assert types[-1] == 'done'
    assert types[-2] == 'sources'

    delta_events = [event for event in events if event['type'] == 'delta']
    assert len(delta_events) == 1  # the whole n8n answer, unchunked -- see contracts/n8n-flow.md
    assert delta_events[0]['text'] == 'Eine ganze mehrwortige Antwort in einem Stueck.'

    trace = events[0]['trace']
    assert 'n8n_ms' in trace['timings_ms']
    assert trace['model'] is None


def test_streaming_guard_triggered_text_is_still_chunked_into_multiple_deltas(monkeypatch):
    # Unlike a genuine n8n answer, the guard's own fixed no_context_reply
    # goes through the ordinary word-chunker, exactly like the retrieval
    # path's own guard text does.
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.n8n_client.httpx.post', _n8n_response({'answer': 'kein Beleg', 'sources': []}))

    resp = client.post(
        '/internal/chat/stream',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    events = _parse_sse_events(resp.text)
    delta_events = [event for event in events if event['type'] == 'delta']
    reconstructed = ''.join(event['text'] for event in delta_events)
    assert reconstructed == 'Der Agentenflow hat nichts gefunden.'
    assert len(delta_events) > 1


def test_streaming_conversational_smalltalk_reply_is_chunked_not_a_single_delta(monkeypatch):
    def _unexpected_post(*args, **kwargs):
        raise AssertionError('conversational must never call n8n')

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _unexpected_post)

    resp = client.post(
        '/internal/chat/stream', json={'bot_id': 'n8n-agent', 'message': 'Hallo!'}, headers=AUTH_HEADERS
    )
    events = _parse_sse_events(resp.text)
    delta_events = [event for event in events if event['type'] == 'delta']
    assert len(delta_events) > 1


# --- the delegation token never leaks into a response or a log record -------


def test_delegation_token_never_appears_in_the_response_body(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    captured = {}

    def _fake_post(url, content=None, json=None, headers=None, timeout=None):
        captured['content'] = content
        return _FakeResponse(200, {'answer': 'ok', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'confluence'}]})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
        headers=AUTH_HEADERS,
    )
    token = json.loads(captured['content'])['delegation_token']
    assert token not in resp.text


def test_delegation_token_never_appears_in_any_log_record(monkeypatch, caplog):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    captured = {}

    def _fake_post(url, content=None, json=None, headers=None, timeout=None):
        captured['content'] = content
        return _FakeResponse(200, {'answer': 'ok', 'sources': [{'document_id': 'd', 'chunk_id': 1, 'source': 'confluence'}]})

    monkeypatch.setattr('app.services.n8n_client.httpx.post', _fake_post)

    with caplog.at_level(logging.DEBUG):
        client.post(
            '/internal/chat',
            json={'bot_id': 'n8n-agent', 'message': _KNOWLEDGE_MESSAGE, 'user': {'id': 'u-1', 'team': 'legal'}},
            headers=AUTH_HEADERS,
        )

    token = json.loads(captured['content'])['delegation_token']
    for record in caplog.records:
        assert token not in record.getMessage()
