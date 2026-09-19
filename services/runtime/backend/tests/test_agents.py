"""Unit tests for app/services/agents.py's `run_subagent` -- the bounded
`search_knowledge` tool-calling loop for one subagent, exercised entirely
with `FakeLLM`'s own scripted `tool_responses` (app/services/llm.py) and a
monkeypatched `app.services.retrieval_client.search`, no chat pipeline, no
HTTP, no real LLM involved at all.
"""

from unittest.mock import patch

from app.schemas.bot import BotConfig, SubagentConfig, SubagentLimits
from app.services import agents
from app.services.llm import FakeLLM, LLMError, LLMToolResult, ToolCall
from app.services.retrieval_client import RetrievalError, RetrievedChunk, RetrievedChunkScores

_BOT = BotConfig.model_validate({
    'id': 'agent-bot',
    'name': 'Agent Bot',
    'model': {'provider': 'fake', 'model': 'fake-chat'},
    'system_prompt': 'You are a bot.',
})


def _subagent(**overrides) -> SubagentConfig:
    defaults = dict(id='it-support', name='IT Support', mission='Answer IT questions.', collections=['it-docs'])
    defaults.update(overrides)
    return SubagentConfig.model_validate(defaults)


def _chunk(**overrides) -> RetrievedChunk:
    defaults = dict(
        chunk_id=1,
        document_id='doc-1',
        text='VPN setup instructions.',
        source='confluence',
        original_filename='vpn.pdf',
        page_start=1,
        page_end=1,
        document_version=1,
        heading_path=['IT', 'VPN'],
        scores=RetrievedChunkScores(rrf=0.9),
        collection='it-docs',
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


def _tool_call(name: str = 'search_knowledge', arguments: dict | None = None, call_id: str = '1') -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments or {'query': 'vpn setup'})


def _final_answer(facts: list[dict] | None = None, open_points: list[str] | None = None) -> str:
    import json

    return json.dumps({'facts': facts or [], 'open_points': open_points or []})


# --- search -> refine -> answer happy path ------------------------------------

def test_search_then_answer_returns_complete_status_with_facts_and_hits():
    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call()], model='fake-chat'),
        LLMToolResult(
            content=_final_answer([
                {'text': 'VPN uses WireGuard.', 'source_refs': [{'document_id': 'doc-1', 'chunk_id': 1}]}
            ]),
            tool_calls=None, model='fake-chat',
        ),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]) as mock_search:
        result = agents.run_subagent(_BOT, _subagent(), 'How does VPN work?', ['it-docs'], fake_llm, model='fake-chat')

    assert result.status == 'complete'
    assert result.searches_used == 1
    assert len(result.hits) == 1
    assert result.facts[0].text == 'VPN uses WireGuard.'
    assert result.facts[0].source_refs == [agents.SourceRef(document_id='doc-1', chunk_id=1, collection='it-docs')]
    mock_search.assert_called_once()
    assert mock_search.call_args.kwargs['allowed_collections'] == ['it-docs']


def test_search_refine_then_answer_uses_two_searches():
    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call(call_id='1', arguments={'query': 'vpn'})], model='fake-chat'),
        LLMToolResult(content=None, tool_calls=[_tool_call(call_id='2', arguments={'query': 'vpn setup steps'})], model='fake-chat'),
        LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat'),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]) as mock_search:
        result = agents.run_subagent(_BOT, _subagent(), 'q', ['it-docs'], fake_llm, model='fake-chat')

    assert result.status == 'complete'
    assert result.searches_used == 2
    assert mock_search.call_count == 2


# --- facts' source_refs are validated against actually-retrieved hits --------

def test_fact_source_refs_not_among_retrieved_hits_are_dropped():
    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call()], model='fake-chat'),
        LLMToolResult(
            content=_final_answer([
                {'text': 'Claims something', 'source_refs': [{'document_id': 'never-retrieved', 'chunk_id': 99}]}
            ]),
            tool_calls=None, model='fake-chat',
        ),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]):
        result = agents.run_subagent(_BOT, _subagent(), 'q', ['it-docs'], fake_llm, model='fake-chat')

    assert result.facts[0].source_refs == []


# --- budget exhaustion --------------------------------------------------------

def test_budget_exhaustion_yields_partial_status_once_the_loop_terminates():
    # The model keeps calling the tool forever -- max_searches=1 means only
    # the first real search actually runs; the loop is still bounded by its
    # own attempt cap (never hangs) and ends with status='partial' because
    # at least one hit was already gathered before the budget ran out.
    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call(call_id=str(i))], model='fake-chat') for i in range(20)
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]):
        result = agents.run_subagent(
            _BOT, _subagent(limits=SubagentLimits(max_searches=1)), 'q', ['it-docs'], fake_llm, model='fake-chat'
        )

    assert result.status == 'partial'
    assert result.searches_used == 1
    assert len(result.hits) == 1


def test_budget_exhaustion_asks_once_more_for_a_final_summary_when_the_queue_is_exactly_spent():
    # Exactly as many scripted tool-calls as the loop's own attempt cap
    # consumes, so the one EXTRA "summarize now" call (tools=[]) finds the
    # scripted queue empty and falls back to FakeLLM's own plain chat()
    # reply instead of one more tool call -- exercising the "ask once more
    # for a final summary" branch directly (see run_subagent's own
    # docstring: `max_attempts = max(1, max_searches) * 2 + 2`).
    limits = SubagentLimits(max_searches=1)
    max_attempts = max(1, limits.max_searches) * 2 + 2
    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call(call_id=str(i))], model='fake-chat')
        for i in range(max_attempts)
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]):
        result = agents.run_subagent(_BOT, _subagent(limits=limits), 'q', ['it-docs'], fake_llm, model='fake-chat')

    assert result.status == 'partial'
    # The fallback chat() reply echoes the exhaustion prompt back -- not
    # JSON, so it becomes one raw-text fact rather than an empty list.
    assert len(result.facts) == 1


def test_budget_exhaustion_with_nothing_gathered_yields_failed_status():
    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call(call_id=str(i))], model='fake-chat') for i in range(10)
    ] + [LLMToolResult(content='not valid json', tool_calls=None, model='fake-chat')]
    fake_llm = FakeLLM(tool_responses=scripted)

    def _always_fail(**kwargs):
        raise RetrievalError('boom', status_code=400)

    with patch('app.services.retrieval_client.search', side_effect=_always_fail):
        result = agents.run_subagent(
            _BOT, _subagent(limits=SubagentLimits(max_searches=1)), 'q', ['it-docs'], fake_llm, model='fake-chat'
        )

    assert result.status == 'failed'
    assert result.hits == []


def test_timeout_stops_the_loop_and_returns_partial():
    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call(call_id=str(i))], model='fake-chat') for i in range(5)
    ] + [LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat')]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]):
        with patch('app.services.agents.time.monotonic', side_effect=[0, 100, 100, 100, 100, 100, 100, 100]):
            result = agents.run_subagent(
                _BOT, _subagent(limits=SubagentLimits(max_searches=3, timeout_seconds=1)),
                'q', ['it-docs'], fake_llm, model='fake-chat',
            )

    assert result.status in {'partial', 'failed'}
    assert result.searches_used == 0


# --- invalid tool arguments: corrected by the model within retries -----------

def test_invalid_tool_arguments_produce_a_tool_error_the_model_can_recover_from():
    scripted = [
        # First call smuggles a forbidden identity field -- rejected.
        LLMToolResult(content=None, tool_calls=[_tool_call(arguments={'query': 'x', 'user_id': 'u-1'})], model='fake-chat'),
        # Second call is well-formed.
        LLMToolResult(content=None, tool_calls=[_tool_call(call_id='2', arguments={'query': 'vpn'})], model='fake-chat'),
        LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat'),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]) as mock_search:
        result = agents.run_subagent(
            _BOT, _subagent(limits=SubagentLimits(max_searches=3)), 'q', ['it-docs'], fake_llm, model='fake-chat'
        )

    assert result.status == 'complete'
    # The invalid call counted against budget; the valid one triggered the
    # one real search.
    mock_search.assert_called_once()
    assert mock_search.call_args.kwargs['query'] == 'vpn'


# --- allowed_teams: the mandatory team-rights axis, on top of Collections ---

def test_search_knowledge_forwards_the_callers_allowed_teams():
    # Mirrors _run_knowledge_turn's own allowed_teams=_allowed_teams(bot,
    # user) call in chat.py -- a subagent search must never silently drop
    # this axis (Document.team is a separate column from
    # Document.collection_slug; see this module's own docstring).
    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call()], model='fake-chat'),
        LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat'),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]) as mock_search:
        agents.run_subagent(
            _BOT, _subagent(), 'q', ['it-docs'], fake_llm, model='fake-chat', allowed_teams=['sales'],
        )

    mock_search.assert_called_once()
    assert mock_search.call_args.kwargs['allowed_teams'] == ['sales']


def test_search_knowledge_without_allowed_teams_does_not_return_a_hit_outside_scope():
    # A document belonging to a team outside the caller's own scope must
    # not surface just because a subagent forwarded no team restriction --
    # verified here at the retrieval-client boundary this module owns: a
    # `allowed_teams` value IS forwarded, and only hits Weave-Retrieval
    # itself returns (already filtered server-side by that value) end up in
    # the result.
    other_team_chunk = _chunk(document_id='doc-2', chunk_id=2, collection='it-docs')

    def _fake_search(**kwargs):
        assert kwargs['allowed_teams'] == ['sales']
        return [_chunk()]  # server-side filtering already excluded other_team_chunk

    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call()], model='fake-chat'),
        LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat'),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', side_effect=_fake_search):
        result = agents.run_subagent(
            _BOT, _subagent(), 'q', ['it-docs'], fake_llm, model='fake-chat', allowed_teams=['sales'],
        )

    assert other_team_chunk not in result.hits
    assert len(result.hits) == 1


# --- filters: bot-level and subagent-level metadata filters both apply ------

def test_search_knowledge_merges_bot_and_subagent_filters_with_subagent_narrowing():
    from app.schemas.bot import RetrievalConfig, RetrievalFilters

    bot = _BOT.model_copy(update={
        'retrieval': RetrievalConfig(filters=RetrievalFilters(department='hr', source='confluence'))
    })
    subagent = _subagent(filters=RetrievalFilters(source='sharepoint'))

    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call()], model='fake-chat'),
        LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat'),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', return_value=[_chunk()]) as mock_search:
        agents.run_subagent(bot, subagent, 'q', ['it-docs'], fake_llm, model='fake-chat')

    # bot's own 'department' pin survives; subagent's 'source' narrows over
    # the bot's own 'source' rather than being dropped in favor of it.
    assert mock_search.call_args.kwargs['filters'] == {'department': 'hr', 'source': 'sharepoint'}


# --- scope enforcement: an out-of-scope collection searches nothing ----------

def test_search_knowledge_narrowed_to_a_collection_outside_effective_scope_returns_no_hits():
    captured = {}

    def _fake_search(**kwargs):
        captured.update(kwargs)
        return []

    scripted = [
        LLMToolResult(
            content=None,
            tool_calls=[_tool_call(arguments={'query': 'x', 'collection': 'not-allowed'})],
            model='fake-chat',
        ),
        LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat'),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', side_effect=_fake_search):
        result = agents.run_subagent(_BOT, _subagent(), 'q', ['it-docs'], fake_llm, model='fake-chat')

    # 'not-allowed' isn't in effective_scope (['it-docs']) -- the call's own
    # allowed_collections narrows to [], never widens beyond effective_scope.
    assert captured['allowed_collections'] == []
    assert result.hits == []


def test_empty_effective_scope_means_every_search_returns_nothing():
    def _fake_search(**kwargs):
        assert kwargs['allowed_collections'] == []
        return []

    scripted = [
        LLMToolResult(content=None, tool_calls=[_tool_call()], model='fake-chat'),
        LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat'),
    ]
    fake_llm = FakeLLM(tool_responses=scripted)

    with patch('app.services.retrieval_client.search', side_effect=_fake_search):
        result = agents.run_subagent(_BOT, _subagent(), 'q', [], fake_llm, model='fake-chat')

    assert result.hits == []
    assert result.status == 'complete'


# --- LLM failure ---------------------------------------------------------------

def test_llm_error_during_the_loop_ends_with_partial_or_failed_status():
    class _RaisingLLM:
        supports_tools = True

        def chat_with_tools(self, messages, tools, model=None, temperature=None):
            raise LLMError('boom')

    result = agents.run_subagent(_BOT, _subagent(), 'q', ['it-docs'], _RaisingLLM(), model='fake-chat')
    assert result.status == 'failed'
    assert result.facts == []


# --- schema ------------------------------------------------------------------

def test_search_knowledge_tool_schema_has_no_identity_or_permission_fields():
    schema = agents.search_knowledge_tool_schema()
    properties = schema['function']['parameters']['properties']
    assert set(properties) == {'query', 'collection', 'top_k'}
    assert schema['function']['parameters']['additionalProperties'] is False
