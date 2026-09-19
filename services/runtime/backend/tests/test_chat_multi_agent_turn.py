"""End-to-end tests for a MULTI-subagent bot's turn (rollout plan "Schritt 3
-- LangGraph mit mehreren Subagenten", app/services/chat.py's
`_run_multi_agent_turn_blocking`/`_stream_deferred_multi_agent`,
app/services/agent_graph.py's `run_graph`) -- both POST /internal/chat
(blocking) and POST /internal/chat/stream, real HTTP round trips through the
FastAPI app, Weave-Retrieval's own HTTP calls mocked at `httpx.get`/
`httpx.post` (mirrors tests/test_chat_agent_turn.py's own style), and every
LLM call (the main bot's own planner/answer calls, plus each subagent's own
research loop) scripted via `app.services.llm.get_llm` returning ONE
dedicated `FakeLLM` per call, in `get_llm()`'s own call order -- see
`_script_get_llm_calls`'s own docstring for exactly why that order is
deterministic. See app/services/agent_graph.py's unit tests
(tests/test_agent_graph.py) for the graph's own node-level behavior (merge
dedup, gap naming, budget/follow-up handling, `Send`-fanout concurrency);
this file only exercises the chat.py <-> agent_graph.py wiring itself.
"""

import json

import yaml

from app.core.config import settings
from app.services.botconfig import list_bots
from app.services.llm import FakeLLM, LLMToolResult, ToolCall
from tests.conftest import AUTH_HEADERS, client

_MULTI_AGENT_BOT_YAML = {
    'id': 'multi-agent-bot',
    'name': 'Multi Agent Bot',
    'model': {'provider': 'fake', 'model': 'fake-chat'},
    'system_prompt': 'You answer using your subagents research.',
    'retrieval': {'enabled': True, 'collections': [], 'include_uncollected': True},
    'permissions': {'teams': []},
    'guard': {'require_sources': True, 'no_context_reply': 'Nichts gefunden.'},
    'agent': {
        'enabled': True,
        'limits': {'max_parallel': 2, 'max_followups': 1, 'budget_searches': 9, 'timeout_seconds': 30},
        'subagents': [
            {'id': 'it-support', 'name': 'IT Support', 'mission': 'IT-Fragen.', 'collections': ['it-docs']},
            {'id': 'hr-support', 'name': 'HR Support', 'mission': 'HR-Fragen.', 'collections': ['hr-docs']},
        ],
    },
}

_QUESTION = 'Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?'  # -> 'knowledge' intent (rules mode)
_BODY = {'bot_id': 'multi-agent-bot', 'message': _QUESTION, 'user': {'id': 'u-1', 'team': 'it'}}


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data


def _collection(slug: str, name: str) -> dict:
    return {'slug': slug, 'name': name, 'description': None, 'public': False}


def _readable_collections(*collections: dict):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(200, list(collections))
    return _fake_get


def _chunk(document_id: str, chunk_id: int, collection: str, text: str) -> dict:
    return {
        'chunk_id': chunk_id, 'document_id': document_id, 'text': text, 'heading_path': [],
        'page_start': 1, 'page_end': 1, 'document_version': 1, 'source': 'confluence',
        'original_filename': f'{document_id}.pdf', 'collection': collection,
        'scores': {'vector': 0.7, 'fulltext': 0.4, 'rrf': 0.6, 'rerank': 0.9},
    }


def _search_response(*results: dict) -> dict:
    return {'query': 'q', 'results': list(results), 'trace': {}}


def _search_by_collection(mapping: dict[str, dict]):
    """A `httpx.post` fake dispatching by the request body's own
    `allowed_collections`, never by call order -- `run_graph`'s own two
    `research` workers may run in EITHER order under `max_parallel=2`, so an
    order-based fake (`tests/test_chat_agent_turn.py`'s own `_search_post`)
    would be flaky here."""

    def _fake_post(url, headers=None, json=None, timeout=None):
        for collection, body in mapping.items():
            if collection in (json.get('allowed_collections') or []):
                return _FakeResponse(200, body)
        return _FakeResponse(200, _search_response())

    return _fake_post


def _script_get_llm_calls(monkeypatch, scripts: list[list[LLMToolResult]]):
    """Patches `app.services.llm.get_llm` to hand back one dedicated
    `FakeLLM(tool_responses=...)` per CALL, in order -- `app/services/
    chat.py`'s own `_prepare_turn` calls it exactly once for the main bot's
    own `llm_provider` (used for the graph's `plan`/`answer` nodes) and once
    MORE per subagent actually offered to the planner (`_available_agents_
    for_graph`, in `bot.agent.subagents`' own YAML order) -- see
    `_DeferredMultiAgentTurn`'s own docstring for why every subagent always
    gets its own fresh instance. `scripts[0]` is therefore always the main
    bot's own script, `scripts[1:]` one per subagent in that YAML order.
    """
    remaining = list(scripts)

    def _factory(provider_name=None):
        return FakeLLM(tool_responses=remaining.pop(0))

    monkeypatch.setattr('app.services.llm.get_llm', _factory)


def _write_bot(tmp_path, monkeypatch, bot_yaml=None):
    (tmp_path / 'multi-agent-bot.yaml').write_text(yaml.safe_dump(bot_yaml or _MULTI_AGENT_BOT_YAML), encoding='utf-8')
    monkeypatch.setattr(settings, 'bots_dir', str(tmp_path))
    assert {bot.id for bot in list_bots()} == {'multi-agent-bot'}


def _parse_sse_events(text: str) -> list[dict]:
    events = []
    for block in text.split('\n\n'):
        block = block.strip('\n')
        if not block or not block.startswith('data: '):
            continue
        events.append(json.loads(block[len('data: '):]))
    return events


_RESEARCH_AREA_PLAN = [ToolCall(
    id='p1', name='research_area', arguments={'agent_id': 'it-support', 'question': 'IT-Teil der Frage'},
), ToolCall(
    id='p2', name='research_area', arguments={'agent_id': 'hr-support', 'question': 'HR-Teil der Frage'},
)]


def _final_answer_json(text: str, document_id: str, chunk_id: int) -> str:
    return json.dumps({'facts': [{'text': text, 'source_refs': [{'document_id': document_id, 'chunk_id': chunk_id}]}], 'open_points': []})


# --- blocking POST /internal/chat --------------------------------------------


def test_multi_agent_turn_runs_both_subagents_and_returns_one_sourced_answer(tmp_path, monkeypatch):
    _write_bot(tmp_path, monkeypatch)
    _script_get_llm_calls(monkeypatch, [
        [LLMToolResult(content=None, tool_calls=_RESEARCH_AREA_PLAN, model='fake-chat')],  # main bot: plan
        [
            LLMToolResult(content=None, tool_calls=[ToolCall(id='s1', name='search_knowledge', arguments={'query': 'kündigung'})], model='fake-chat'),
            LLMToolResult(content=_final_answer_json('Kündigungsfrist beträgt 4 Wochen.', 'doc-it', 1), tool_calls=None, model='fake-chat'),
        ],  # it-support
        [
            LLMToolResult(content=None, tool_calls=[ToolCall(id='s2', name='search_knowledge', arguments={'query': 'urlaub'})], model='fake-chat'),
            LLMToolResult(content=_final_answer_json('Urlaubsanspruch 30 Tage.', 'doc-hr', 2), tool_calls=None, model='fake-chat'),
        ],  # hr-support
    ])
    monkeypatch.setattr(
        'app.services.retrieval_client.httpx.get',
        _readable_collections(_collection('it-docs', 'IT Docs'), _collection('hr-docs', 'HR Docs')),
    )
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_by_collection({
        'it-docs': _search_response(_chunk('doc-it', 1, 'it-docs', 'IT content')),
        'hr-docs': _search_response(_chunk('doc-hr', 2, 'hr-docs', 'HR content')),
    }))

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert {source['document_id'] for source in body['sources']} == {'doc-it', 'doc-hr'}
    assert body['trace']['agent']['mode'] == 'graph'
    assert {trace['id'] for trace in body['trace']['agent']['subagents']} == {'it-support', 'hr-support'}
    assert all(trace['status'] == 'complete' for trace in body['trace']['agent']['subagents'])
    assert body['trace']['agent']['plan'] == [
        {'agent_id': 'it-support', 'subquestion': 'IT-Teil der Frage'},
        {'agent_id': 'hr-support', 'subquestion': 'HR-Teil der Frage'},
    ]
    assert body['trace']['guard'] is None
    # '[context:2]' is FakeLLM's own marker for "two [source N] sections were
    # present" -- confirming BOTH subagents' own facts reached the main
    # bot's final-answer call.
    assert '[context:2]' in body['answer']


def test_multi_agent_turn_guard_fires_when_no_subagent_finds_anything(tmp_path, monkeypatch):
    _write_bot(tmp_path, monkeypatch)
    empty_answer = LLMToolResult(content=json.dumps({'facts': [], 'open_points': []}), tool_calls=None, model='fake-chat')
    _script_get_llm_calls(monkeypatch, [
        [LLMToolResult(content=None, tool_calls=_RESEARCH_AREA_PLAN, model='fake-chat')],
        [LLMToolResult(content=None, tool_calls=[ToolCall(id='s1', name='search_knowledge', arguments={'query': 'q'})], model='fake-chat'), empty_answer],
        [LLMToolResult(content=None, tool_calls=[ToolCall(id='s2', name='search_knowledge', arguments={'query': 'q'})], model='fake-chat'), empty_answer],
    ])
    monkeypatch.setattr(
        'app.services.retrieval_client.httpx.get',
        _readable_collections(_collection('it-docs', 'IT Docs'), _collection('hr-docs', 'HR Docs')),
    )
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_by_collection({}))

    response = client.post('/internal/chat', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body['sources'] == []
    assert body['answer'] == 'Nichts gefunden.'
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}


# --- streaming POST /internal/chat/stream ------------------------------------


def test_multi_agent_turn_streams_trace_then_deltas_then_sources_and_done(tmp_path, monkeypatch):
    _write_bot(tmp_path, monkeypatch)
    _script_get_llm_calls(monkeypatch, [
        [LLMToolResult(content=None, tool_calls=_RESEARCH_AREA_PLAN, model='fake-chat')],
        [
            LLMToolResult(content=None, tool_calls=[ToolCall(id='s1', name='search_knowledge', arguments={'query': 'q'})], model='fake-chat'),
            LLMToolResult(content=_final_answer_json('IT Fakt.', 'doc-it', 1), tool_calls=None, model='fake-chat'),
        ],
        [
            LLMToolResult(content=None, tool_calls=[ToolCall(id='s2', name='search_knowledge', arguments={'query': 'q'})], model='fake-chat'),
            LLMToolResult(content=_final_answer_json('HR Fakt.', 'doc-hr', 2), tool_calls=None, model='fake-chat'),
        ],
    ])
    monkeypatch.setattr(
        'app.services.retrieval_client.httpx.get',
        _readable_collections(_collection('it-docs', 'IT Docs'), _collection('hr-docs', 'HR Docs')),
    )
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_by_collection({
        'it-docs': _search_response(_chunk('doc-it', 1, 'it-docs', 'IT content')),
        'hr-docs': _search_response(_chunk('doc-hr', 2, 'hr-docs', 'HR content')),
    }))

    response = client.post('/internal/chat/stream', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert events[0]['type'] == 'trace'
    assert events[0]['trace']['agent'] is None  # not known yet at trace-emission time
    assert events[0]['trace']['model'] is None

    delta_events = [event for event in events if event['type'] == 'delta']
    assert delta_events
    assert events[-2]['type'] == 'sources'
    assert {source['document_id'] for source in events[-2]['sources']} == {'doc-it', 'doc-hr'}
    assert events[-1]['type'] == 'done'


def test_multi_agent_turn_stream_emits_status_events_and_streams_several_deltas(tmp_path, monkeypatch):
    # Rollout plan "Schritt 4 -- Administration und Streaming": planning,
    # each subagent's start+finish, merging, and answering must each
    # surface a status event, in that relative order, all before the FIRST
    # delta -- and the graph's own `answer` node must now stream several
    # real deltas (app/services/agent_graph.py's `_answer`) rather than one
    # blocking `chat()` call chunked after the fact.
    _write_bot(tmp_path, monkeypatch)
    _script_get_llm_calls(monkeypatch, [
        [LLMToolResult(content=None, tool_calls=_RESEARCH_AREA_PLAN, model='fake-chat')],
        [
            LLMToolResult(content=None, tool_calls=[ToolCall(id='s1', name='search_knowledge', arguments={'query': 'q'})], model='fake-chat'),
            LLMToolResult(content=_final_answer_json('IT Fakt.', 'doc-it', 1), tool_calls=None, model='fake-chat'),
        ],
        [
            LLMToolResult(content=None, tool_calls=[ToolCall(id='s2', name='search_knowledge', arguments={'query': 'q'})], model='fake-chat'),
            LLMToolResult(content=_final_answer_json('HR Fakt.', 'doc-hr', 2), tool_calls=None, model='fake-chat'),
        ],
    ])
    monkeypatch.setattr(
        'app.services.retrieval_client.httpx.get',
        _readable_collections(_collection('it-docs', 'IT Docs'), _collection('hr-docs', 'HR Docs')),
    )
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _search_by_collection({
        'it-docs': _search_response(_chunk('doc-it', 1, 'it-docs', 'IT content')),
        'hr-docs': _search_response(_chunk('doc-hr', 2, 'hr-docs', 'HR content')),
    }))

    response = client.post('/internal/chat/stream', json=_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    first_delta_index = next(i for i, event in enumerate(events) if event['type'] == 'delta')
    status_events = [event for event in events[:first_delta_index] if event['type'] == 'status']
    stages_seen = [event['stage'] for event in status_events]
    assert stages_seen[0] == 'planning'
    assert stages_seen[-1] == 'answering'
    assert 'merging' in stages_seen
    researching_agent_ids = {event['agent_id'] for event in status_events if event['stage'] == 'researching'}
    assert researching_agent_ids == {'it-support', 'hr-support'}
    # Each researching agent reports both a start (state None) and a finish
    # (state set) -- never only one of the two.
    for agent_id in ('it-support', 'hr-support'):
        states = [e['state'] for e in status_events if e['stage'] == 'researching' and e['agent_id'] == agent_id]
        assert states.count(None) == 1
        assert any(state is not None for state in states)

    delta_events = [event for event in events if event['type'] == 'delta']
    assert len(delta_events) > 1  # genuine incremental streaming, not one chunk

    for event in events:
        if event['type'] == 'status':
            blob = json.dumps(event)
            assert _QUESTION not in blob
            assert 'system_prompt' not in blob.lower()
