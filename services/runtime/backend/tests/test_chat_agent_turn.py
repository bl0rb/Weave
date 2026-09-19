"""End-to-end tests for an agent-mode bot's turn (rollout plan "Schritt 2 --
Tool-Calls und ein Subagent", app/services/chat.py's `_run_agent_turn_blocking`/
`_stream_deferred_agent`) -- both POST /internal/chat (blocking) and
POST /internal/chat/stream, real HTTP round trips through the FastAPI app,
Weave-Retrieval's own HTTP calls mocked at `httpx.get`/`httpx.post` (mirrors
tests/test_chat_e2e.py/test_chat_stream.py's own style), and the subagent's
own LLM scripted via `app.services.llm.get_llm` returning a `FakeLLM`
pre-loaded with `tool_responses` (see that class's own docstring) -- no real
network/LLM anywhere in these tests.
"""

import json
from unittest.mock import Mock

import yaml

from app.core.config import settings
from app.services import agents as agents_service
from app.services import retrieval_client
from app.services.botconfig import list_bots
from app.services.llm import FakeLLM, LLMToolResult, ToolCall
from tests.conftest import AUTH_HEADERS, client

_AGENT_BOT_YAML = {
    'id': 'agent-bot',
    'name': 'Agent Bot',
    'model': {'provider': 'fake', 'model': 'fake-chat'},
    'system_prompt': 'You answer using your subagent research.',
    'retrieval': {'enabled': True, 'collections': [], 'include_uncollected': True},
    'permissions': {'teams': []},
    'guard': {'require_sources': True, 'no_context_reply': 'Nichts gefunden.'},
    'agent': {
        'enabled': True,
        'subagents': [
            {
                'id': 'it-support',
                'name': 'IT Support',
                'mission': 'Answer IT/helpdesk questions.',
                'collections': ['it-docs'],
            }
        ],
    },
}

_KNOWLEDGE_QUESTION = 'Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?'  # -> 'knowledge' intent (rules mode)
_BODY = {'bot_id': 'agent-bot', 'message': _KNOWLEDGE_QUESTION, 'user': {'id': 'u-1', 'team': 'it'}}


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


def _collection(**overrides) -> dict:
    collection = {'slug': 'it-docs', 'name': 'IT Docs', 'description': None, 'public': False}
    collection.update(overrides)
    return collection


def _readable_collections(*collections: dict):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(200, list(collections) or [_collection()])

    return _fake_get


def _chunk(**overrides) -> dict:
    chunk = {
        'chunk_id': 1,
        'document_id': 'doc-1',
        'text': 'VPN nutzt WireGuard und erfordert ein Client-Zertifikat.',
        'heading_path': ['IT', 'VPN'],
        'page_start': 1,
        'page_end': 1,
        'document_version': 1,
        'source': 'confluence',
        'original_filename': 'vpn.pdf',
        'collection': 'it-docs',
        'scores': {'vector': 0.7, 'fulltext': 0.4, 'rrf': 0.6, 'rerank': 0.9},
    }
    chunk.update(overrides)
    return chunk


def _search_response(*results: dict) -> dict:
    return {'query': 'q', 'results': list(results), 'trace': {}}


def _search_post(*response_bodies: dict):
    """One `httpx.post` fake per call, in order -- used both for the
    subagent's own `search_knowledge` calls (POST /api/v1/search)."""
    responses = [_FakeResponse(200, body) for body in response_bodies]

    def _fake_post(url, headers=None, json=None, timeout=None):
        return responses.pop(0)

    return _fake_post


_TOOL_CALL = ToolCall(id='call-1', name='search_knowledge', arguments={'query': 'vpn setup'})
_FINAL_ANSWER_JSON = json.dumps({
    'facts': [{'text': 'VPN nutzt WireGuard.', 'source_refs': [{'document_id': 'doc-1', 'chunk_id': 1}]}],
    'open_points': [],
})


def _script_fake_llm(monkeypatch, tool_responses):
    def _factory(provider_name=None):
        return FakeLLM(tool_responses=list(tool_responses))

    monkeypatch.setattr('app.services.llm.get_llm', _factory)


def _write_bot(tmp_path, monkeypatch, bot_yaml=None):
    (tmp_path / 'agent-bot.yaml').write_text(yaml.safe_dump(bot_yaml or _AGENT_BOT_YAML), encoding='utf-8')
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path))
    assert {bot.id for bot in list_bots()} == {'agent-bot'}


def _parse_sse_events(text: str) -> list[dict]:
    events = []
    for block in text.split('\n\n'):
        block = block.strip('\n')
        if not block or not block.startswith('data: '):
            continue
        events.append(json.loads(block[len('data: '):]))
    return events


# --- blocking POST /internal/chat --------------------------------------------


def test_agent_turn_searches_then_answers_with_combined_sources_and_trace(tmp_path, monkeypatch):
    _write_bot(tmp_path, monkeypatch)
    _script_fake_llm(monkeypatch, [
        LLMToolResult(content=None, tool_calls=[_TOOL_CALL], model='fake-chat'),
        LLMToolResult(content=_FINAL_ANSWER_JSON, tool_calls=None, model='fake-chat'),
    ])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_post(_search_response(_chunk())))

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body['sources'][0]['document_id'] == 'doc-1'
    assert body['sources'][0]['chunk_id'] == 1
    assert body['sources'][0]['collection'] == 'it-docs'
    assert body['trace']['agent']['mode'] == 'single'
    assert body['trace']['agent']['subagents'][0]['id'] == 'it-support'
    assert body['trace']['agent']['subagents'][0]['status'] == 'complete'
    assert body['trace']['agent']['subagents'][0]['searches_used'] == 1
    assert body['trace']['guard'] is None
    # Final answer came from the main bot's own LLM call (FakeLLM, its
    # scripted queue already spent on the subagent loop) fed the facts as
    # a numbered context block -- '[context:1]' is FakeLLM's own marker for
    # "one [source N] section was present", confirming the fact reached
    # the final-answer call.
    assert '[context:1]' in body['answer']


def test_agent_turn_search_forwards_the_callers_allowed_teams(tmp_path, monkeypatch):
    # Mirrors _run_knowledge_turn's own allowed_teams=_allowed_teams(bot,
    # user) -- a subagent's search_knowledge call must carry the SAME
    # mandatory team-rights axis as a direct-RAG turn, on top of Collections
    # scope (chat.py's `_allowed_teams` docstring; services/retrieval's
    # `apply_filters()` treats `Document.team` as a separate axis).
    _write_bot(tmp_path, monkeypatch)
    _script_fake_llm(monkeypatch, [
        LLMToolResult(content=None, tool_calls=[_TOOL_CALL], model='fake-chat'),
        LLMToolResult(content=_FINAL_ANSWER_JSON, tool_calls=None, model='fake-chat'),
    ])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    captured_bodies = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured_bodies.append(json)
        return _FakeResponse(200, _search_response(_chunk()))

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert len(captured_bodies) == 1
    # _BODY's user carries team='it' -- _allowed_teams prefers the calling
    # user's own team over the bot's (empty, "every team") permissions.teams.
    assert captured_bodies[0]['allowed_teams'] == ['it']


def test_agent_turn_deduplicates_sources_by_document_and_chunk_id(tmp_path, monkeypatch):
    _write_bot(tmp_path, monkeypatch)
    duplicate_call = ToolCall(id='call-1', name='search_knowledge', arguments={'query': 'vpn'})
    second_call = ToolCall(id='call-2', name='search_knowledge', arguments={'query': 'vpn again'})
    _script_fake_llm(monkeypatch, [
        LLMToolResult(content=None, tool_calls=[duplicate_call], model='fake-chat'),
        LLMToolResult(content=None, tool_calls=[second_call], model='fake-chat'),
        LLMToolResult(content=_FINAL_ANSWER_JSON, tool_calls=None, model='fake-chat'),
    ])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    # The exact same chunk comes back twice (two searches) -- combined
    # sources must still list it once.
    monkeypatch.setattr(
        'app.services.retrieval_client.httpx.post',
        _search_post(_search_response(_chunk()), _search_response(_chunk())),
    )

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert len(body['sources']) == 1


def test_agent_turn_guard_fires_when_subagent_finds_nothing(tmp_path, monkeypatch):
    _write_bot(tmp_path, monkeypatch)
    _script_fake_llm(monkeypatch, [
        LLMToolResult(content=None, tool_calls=[_TOOL_CALL], model='fake-chat'),
        LLMToolResult(content=json.dumps({'facts': [], 'open_points': ['nothing found']}), tool_calls=None, model='fake-chat'),
    ])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_post(_search_response()))

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body['sources'] == []
    assert body['answer'] == 'Nichts gefunden.'
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}


def test_agent_turn_scope_enforcement_user_cannot_read_subagent_collection(tmp_path, monkeypatch):
    """The subagent is configured for 'it-docs', but this caller's own
    readable collections (GET /api/v1/collections) don't include it at
    all -- the effective scope narrows to [], so search_knowledge must
    never actually be called with 'it-docs' in scope, and the turn ends up
    guarded exactly like "nothing found"."""
    _write_bot(tmp_path, monkeypatch)
    _script_fake_llm(monkeypatch, [
        LLMToolResult(content=None, tool_calls=[_TOOL_CALL], model='fake-chat'),
        LLMToolResult(content=json.dumps({'facts': [], 'open_points': []}), tool_calls=None, model='fake-chat'),
    ])
    # This caller may only read 'other-collection', never 'it-docs'.
    monkeypatch.setattr(
        'app.services.retrieval_client.httpx.get',
        _readable_collections(_collection(slug='other-collection', name='Other')),
    )

    captured_payloads = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured_payloads.append(json)
        return _FakeResponse(200, _search_response())

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body['sources'] == []
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert len(captured_payloads) == 1
    assert captured_payloads[0]['allowed_collections'] == []


# --- streaming POST /internal/chat/stream ------------------------------------


def test_agent_turn_streams_trace_then_deltas_then_sources_and_done(tmp_path, monkeypatch):
    _write_bot(tmp_path, monkeypatch)
    _script_fake_llm(monkeypatch, [
        LLMToolResult(content=None, tool_calls=[_TOOL_CALL], model='fake-chat'),
        LLMToolResult(content=_FINAL_ANSWER_JSON, tool_calls=None, model='fake-chat'),
    ])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_post(_search_response(_chunk())))

    response = client.post('/internal/chat/stream', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert events[0]['type'] == 'trace'
    assert events[0]['trace']['agent'] is None  # not known yet at trace-emission time -- see AgentTrace's docstring
    assert events[0]['trace']['model'] is None

    delta_events = [event for event in events if event['type'] == 'delta']
    assert delta_events  # the final answer streamed incrementally
    assert events[-2]['type'] == 'sources'
    assert events[-2]['sources'][0]['document_id'] == 'doc-1'
    assert events[-1]['type'] == 'done'


def test_agent_turn_stream_emits_status_events_between_trace_and_first_delta(tmp_path, monkeypatch):
    # Rollout plan "Schritt 4 -- Administration und Streaming": a short,
    # transient progress line (e.g. 'IT Support wird durchsucht') must
    # appear before the subagent's research finishes, and it must never
    # leak a prompt, a query, or private reasoning -- only the fixed,
    # pre-rendered German message plus a handful of structured fields.
    _write_bot(tmp_path, monkeypatch)
    _script_fake_llm(monkeypatch, [
        LLMToolResult(content=None, tool_calls=[_TOOL_CALL], model='fake-chat'),
        LLMToolResult(content=_FINAL_ANSWER_JSON, tool_calls=None, model='fake-chat'),
    ])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_post(_search_response(_chunk())))

    response = client.post('/internal/chat/stream', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    first_delta_index = next(i for i, event in enumerate(events) if event['type'] == 'delta')
    status_events = [event for event in events[:first_delta_index] if event['type'] == 'status']
    # researching (started) + researching (complete) + answering (started)
    assert len(status_events) == 3
    assert status_events[0]['stage'] == 'researching'
    assert status_events[0]['agent_id'] == 'it-support'
    assert status_events[0]['state'] is None
    assert status_events[0]['message'] == 'IT Support wird durchsucht'
    assert status_events[1]['stage'] == 'researching'
    assert status_events[1]['state'] == 'complete'
    assert status_events[2]['stage'] == 'answering'

    for event in events:
        if event['type'] == 'status':
            blob = json.dumps(event)
            assert _KNOWLEDGE_QUESTION not in blob
            assert 'system_prompt' not in blob.lower()


def test_agent_turn_stream_guard_fires_when_subagent_finds_nothing(tmp_path, monkeypatch):
    _write_bot(tmp_path, monkeypatch)
    _script_fake_llm(monkeypatch, [
        LLMToolResult(content=None, tool_calls=[_TOOL_CALL], model='fake-chat'),
        LLMToolResult(content=json.dumps({'facts': [], 'open_points': []}), tool_calls=None, model='fake-chat'),
    ])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_post(_search_response()))

    response = client.post('/internal/chat/stream', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert events[0]['type'] == 'trace'
    text = ''.join(event['text'] for event in events if event['type'] == 'delta')
    assert text == 'Nichts gefunden.'
    assert events[-2] == {'type': 'sources', 'sources': []}
    assert events[-1]['type'] == 'done'


# --- central-provider override must re-check tool-call support --------------

def test_central_provider_override_without_tool_support_falls_back_to_direct_rag(tmp_path, monkeypatch):
    # The bot's own YAML declares provider='fake' (agent.enabled's own
    # load-time validator, app/schemas/bot.py, is satisfied by that alone).
    # A central-provider override (fetch_chat_provider()) then swaps
    # bot.model to a DIFFERENT provider/model without re-running that
    # validator and without setting model.supports_tools -- so the
    # now-effective model's tool-call support was never actually checked.
    # Agent mode must not run against it; the turn instead falls back to
    # the ordinary direct-RAG path.
    from app.services.chat_config_client import ChatProviderSnapshot

    _write_bot(tmp_path, monkeypatch)
    monkeypatch.setattr(
        'app.services.chat.fetch_chat_provider',
        lambda: ChatProviderSnapshot(
            enabled=True, base_url='https://central.example/v1', model='central-model',
            api_key='central-key', timeout_seconds=33, temperature=0.1,
        ),
    )
    # If agent mode ran anyway, this scripted tool-call queue would be
    # consumed; the direct-RAG path never calls chat_with_tools at all, so
    # leaving no scripted responses here would surface a clear failure if
    # the fallback did not take effect.
    _script_fake_llm(monkeypatch, [])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    search_response = _FakeResponse(200, _search_response(_chunk()))
    chat_response = Mock(status_code=200, text='')
    chat_response.json.return_value = {'choices': [{'message': {'content': 'Zentrale Antwort'}}], 'model': 'central-model'}

    # `retrieval_client` and `llm` both import the same shared `httpx`
    # module, so `httpx.post` must be ONE fake dispatching by URL here --
    # patching each module's own `.httpx.post` separately would just have
    # the second patch silently clobber the first.
    def _fake_post(url, *args, **kwargs):
        return chat_response if 'chat/completions' in url else search_response

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    # No agent trace at all -- the direct-RAG path ran instead.
    assert body['trace'].get('agent') is None
    assert body['answer'] == 'Zentrale Antwort'


def test_central_provider_override_with_same_label_model_swap_falls_back_to_direct_rag(tmp_path, monkeypatch):
    # Narrower residual of the FIX-1 re-check: the bot's own YAML already
    # declares provider='openai' with supports_tools=True for its own
    # 'gpt-original' model -- a central-provider override that keeps the
    # 'openai' label but swaps `model.model` to a DIFFERENT central model
    # must NOT let that inherited `supports_tools=True` (declared only for
    # 'gpt-original', never for the swapped-in model name) leak through.
    # The override always sets its own `supports_tools` from the central
    # provider's own declaration (default False here, since the snapshot
    # below doesn't pass one), so agent mode must not run against it.
    from app.services.chat_config_client import ChatProviderSnapshot

    bot_yaml = {
        **_AGENT_BOT_YAML,
        'model': {'provider': 'openai', 'model': 'gpt-original', 'supports_tools': True},
    }
    _write_bot(tmp_path, monkeypatch, bot_yaml)
    monkeypatch.setattr(
        'app.services.chat.fetch_chat_provider',
        lambda: ChatProviderSnapshot(
            enabled=True, base_url='https://central.example/v1', model='central-model',
            api_key='central-key', timeout_seconds=33, temperature=0.1,
        ),
    )
    # If agent mode ran anyway, this scripted tool-call queue would be
    # consumed; the direct-RAG path never calls chat_with_tools at all, so
    # leaving no scripted responses here would surface a clear failure if
    # the fallback did not take effect.
    _script_fake_llm(monkeypatch, [])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    search_response = _FakeResponse(200, _search_response(_chunk()))
    chat_response = Mock(status_code=200, text='')
    chat_response.json.return_value = {'choices': [{'message': {'content': 'Zentrale Antwort'}}], 'model': 'central-model'}

    def _fake_post(url, *args, **kwargs):
        return chat_response if 'chat/completions' in url else search_response

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body['trace'].get('agent') is None
    assert body['answer'] == 'Zentrale Antwort'


def test_central_provider_override_with_declared_tool_support_runs_agent_mode(tmp_path, monkeypatch):
    # Mirror image of the two fallback tests above: when the central
    # provider itself declares `supports_tools=True`, the override's
    # effective model carries that flag through (see chat.py's
    # `model_copy(update={..., 'supports_tools': central_provider.
    # supports_tools})`) and agent mode runs normally instead of falling
    # back to direct RAG -- a centrally managed LLM bot must be able to use
    # agent mode once the admin has declared its provider tool-capable.
    from app.services.chat_config_client import ChatProviderSnapshot

    bot_yaml = {
        **_AGENT_BOT_YAML,
        'model': {'provider': 'openai', 'model': 'gpt-original', 'supports_tools': True},
    }
    _write_bot(tmp_path, monkeypatch, bot_yaml)
    monkeypatch.setattr(
        'app.services.chat.fetch_chat_provider',
        lambda: ChatProviderSnapshot(
            enabled=True, base_url='https://central.example/v1', model='central-model',
            api_key='central-key', timeout_seconds=33, temperature=0.1, supports_tools=True,
        ),
    )
    hit = retrieval_client.RetrievedChunk(
        chunk_id=1, document_id='doc-1', text='VPN nutzt WireGuard.', source=None, original_filename=None,
        page_start=1, page_end=1, document_version=1, heading_path=['IT', 'VPN'],
        scores=retrieval_client.RetrievedChunkScores(), collection='it-docs',
    )
    canned_result = agents_service.SubagentResult(
        agent_id='it-support', status='complete',
        facts=[], open_points=[], hits=[hit], searches_used=1,
    )
    monkeypatch.setattr('app.services.agents.run_subagent', lambda *args, **kwargs: canned_result)
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _readable_collections())
    chat_response = Mock(status_code=200, text='')
    chat_response.json.return_value = {'choices': [{'message': {'content': 'Agenten-Antwort'}}], 'model': 'central-model'}
    monkeypatch.setattr('app.services.llm.httpx.post', lambda *args, **kwargs: chat_response)

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body['trace']['agent'] is not None
    assert body['trace']['agent']['subagents'][0]['id'] == 'it-support'
    assert body['answer'] == 'Agenten-Antwort'
