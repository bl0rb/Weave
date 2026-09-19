"""Unit tests for app/services/agent_graph.py's `run_graph` -- the LangGraph
orchestrator-worker flow for a multi-subagent turn (rollout plan "Schritt 3
-- LangGraph mit mehreren Subagenten"). Exercised entirely with `FakeLLM`'s
own scripted `tool_responses` (one instance per subagent PLUS one for the
main bot's own planner/answer calls, see this module's own `_providers`
helper) and a monkeypatched `app.services.retrieval_client.search` -- no
chat pipeline, no HTTP, no real LLM anywhere in these tests.
"""

import json
import threading
import time
from unittest.mock import patch

from app.schemas.bot import BotConfig, SubagentConfig
from app.services import agent_graph as ag
from app.services import agents as agents_service
from app.services.llm import FakeLLM, LLMError, LLMToolResult, ToolCall
from app.services.retrieval_client import RetrievedChunk, RetrievedChunkScores


class _FailingLLM:
    """A minimal LLMProvider stand-in whose `chat_with_tools` always raises
    -- used to force a deterministic `status='failed'` subagent (rather than
    a merely factless-but-`'complete'` one, a different, legitimate outcome
    `agents.run_subagent` also produces -- see that dataclass's own
    docstring on the difference) without relying on budget/timeout
    exhaustion, which `FakeLLM`'s own scripted queue cannot force on its
    own."""

    supports_tools = True

    def chat_with_tools(self, messages, tools, model=None, temperature=None):
        raise LLMError('simulated subagent LLM failure')

    def chat(self, messages, model=None, temperature=None):
        raise LLMError('simulated subagent LLM failure')

    def chat_stream(self, messages, model=None, temperature=None):
        raise LLMError('simulated subagent LLM failure')


def _bot(**agent_overrides) -> BotConfig:
    agent = {
        'enabled': True,
        'limits': {'max_parallel': 3, 'max_followups': 1, 'budget_searches': 9, 'timeout_seconds': 30},
        'subagents': [
            {'id': 'it', 'name': 'IT', 'mission': 'IT-Fragen.', 'collections': ['it-docs']},
            {'id': 'hr', 'name': 'HR', 'mission': 'HR-Fragen.', 'collections': ['hr-docs']},
        ],
    }
    agent.update(agent_overrides)
    return BotConfig.model_validate({
        'id': 'multi-bot',
        'name': 'Multi Bot',
        'model': {'provider': 'fake', 'model': 'fake-chat'},
        'system_prompt': 'Answer using the research below.',
        'guard': {'require_sources': True, 'no_context_reply': 'Nichts gefunden.'},
        'agent': agent,
    })


def _chunk(document_id: str, chunk_id: int, collection: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id, document_id=document_id, text=f'{collection} fact text', source='s',
        original_filename=None, page_start=None, page_end=None, document_version=1, heading_path=[],
        scores=RetrievedChunkScores(rrf=0.5), collection=collection,
    )


def _tool_call(call_id: str, query: str = 'q') -> ToolCall:
    return ToolCall(id=call_id, name='search_knowledge', arguments={'query': query})


def _final_answer(facts: list[dict] | None = None, open_points: list[str] | None = None) -> str:
    return json.dumps({'facts': facts or [], 'open_points': open_points or []})


def _planner(*calls: tuple[str, str]) -> FakeLLM:
    """A planner FakeLLM whose one scripted `chat_with_tools` reply calls
    `research_area` once per `(agent_id, question)` pair -- several pairs
    means several subquestions (rollout plan step 3)."""
    tool_calls = [
        ToolCall(id=f'p{i}', name=ag.RESEARCH_AREA_TOOL, arguments={'agent_id': agent_id, 'question': question})
        for i, (agent_id, question) in enumerate(calls)
    ]
    return FakeLLM(tool_responses=[LLMToolResult(content=None, tool_calls=tool_calls, model='fake-chat')])


def _available(*agent_ids: str) -> list[ag.AvailableAgent]:
    return [{'agent_id': agent_id, 'effective_scope': [f'{agent_id}-docs'], 'allowed_teams': None} for agent_id in agent_ids]


def _search_by_collection(mapping: dict[str, list[RetrievedChunk]]):
    def _search(query, filters, allowed_teams, allowed_collections, top_k, final_k):
        for collection, chunks in mapping.items():
            if collection in allowed_collections:
                return chunks
        return []
    return _search


# --- two-area question: both subagents run, merged sourced answer -----------

def test_two_subagents_run_and_merge_into_one_sourced_answer():
    planner = _planner(('it', 'IT question'), ('hr', 'HR question'))
    it_llm = FakeLLM(tool_responses=[
        LLMToolResult(content=None, tool_calls=[_tool_call('s1')], model='fake-chat'),
        LLMToolResult(
            content=_final_answer([{'text': 'IT fact.', 'source_refs': [{'document_id': 'd1', 'chunk_id': 1}]}]),
            tool_calls=None, model='fake-chat',
        ),
    ])
    hr_llm = FakeLLM(tool_responses=[
        LLMToolResult(content=None, tool_calls=[_tool_call('s2')], model='fake-chat'),
        LLMToolResult(
            content=_final_answer([{'text': 'HR fact.', 'source_refs': [{'document_id': 'd2', 'chunk_id': 2}]}]),
            tool_calls=None, model='fake-chat',
        ),
    ])

    with patch(
        'app.services.retrieval_client.search',
        side_effect=_search_by_collection({'it-docs': [_chunk('d1', 1, 'it-docs')], 'hr-docs': [_chunk('d2', 2, 'hr-docs')]}),
    ):
        outcome = ag.run_graph(
            _bot(), 'cross-area question', [], _available('it', 'hr'),
            {s.id: s for s in _bot().agent.subagents}, {'it': it_llm, 'hr': hr_llm}, planner,
        )

    assert not outcome.guard_triggered
    assert {source.document_id for source in outcome.sources} == {'d1', 'd2'}
    assert outcome.agent_trace.mode == 'graph'
    assert {trace.id for trace in outcome.agent_trace.subagents} == {'it', 'hr'}
    assert all(trace.status == 'complete' for trace in outcome.agent_trace.subagents)
    assert outcome.agent_trace.plan == [
        {'agent_id': 'it', 'subquestion': 'IT question'}, {'agent_id': 'hr', 'subquestion': 'HR question'}
    ]


# --- one subagent fails: answer still names the gap --------------------------

def test_one_subagent_failing_names_the_gap_while_the_other_still_answers():
    planner = _planner(('it', 'IT question'), ('hr', 'HR question'))
    it_llm = FakeLLM(tool_responses=[
        LLMToolResult(content=None, tool_calls=[_tool_call('s1')], model='fake-chat'),
        LLMToolResult(
            content=_final_answer([{'text': 'IT fact.', 'source_refs': [{'document_id': 'd1', 'chunk_id': 1}]}]),
            tool_calls=None, model='fake-chat',
        ),
    ])
    # HR's own subagent LLM fails outright (status='failed', no facts, no
    # hits) -- and its own follow-up round budget is fully suppressed by
    # setting max_followups=0 on the bot, so this is a genuine, final gap.
    hr_llm = _FailingLLM()

    with patch('app.services.retrieval_client.search', side_effect=_search_by_collection({'it-docs': [_chunk('d1', 1, 'it-docs')]})):
        outcome = ag.run_graph(
            _bot(limits={'max_parallel': 3, 'max_followups': 0, 'budget_searches': 9, 'timeout_seconds': 30}),
            'cross-area question', [], _available('it', 'hr'),
            {s.id: s for s in _bot().agent.subagents}, {'it': it_llm, 'hr': hr_llm}, planner,
        )

    assert not outcome.guard_triggered
    assert len(outcome.sources) == 1
    assert 'Für den Bereich HR liegen keine Ergebnisse vor; die Recherche war unvollständig.' in outcome.final_answer
    hr_trace = next(trace for trace in outcome.agent_trace.subagents if trace.id == 'hr')
    assert hr_trace.status in ('failed', 'partial')


# --- every subagent fails: the existing require_sources guard applies -------

def test_all_subagents_failing_triggers_the_require_sources_guard():
    planner = _planner(('it', 'IT question'), ('hr', 'HR question'))
    empty_answer = LLMToolResult(content=_final_answer([], []), tool_calls=None, model='fake-chat')
    it_llm = FakeLLM(tool_responses=[LLMToolResult(content=None, tool_calls=[_tool_call('s1')], model='fake-chat'), empty_answer])
    hr_llm = FakeLLM(tool_responses=[LLMToolResult(content=None, tool_calls=[_tool_call('s2')], model='fake-chat'), empty_answer])

    with patch('app.services.retrieval_client.search', return_value=[]):
        outcome = ag.run_graph(
            _bot(limits={'max_parallel': 3, 'max_followups': 0, 'budget_searches': 9, 'timeout_seconds': 30}),
            'cross-area question', [], _available('it', 'hr'),
            {s.id: s for s in _bot().agent.subagents}, {'it': it_llm, 'hr': hr_llm}, planner,
        )

    assert outcome.guard_triggered
    assert outcome.sources == []
    assert outcome.final_answer == 'Nichts gefunden.'


# --- shared budget exhaustion -------------------------------------------------

def test_shared_budget_exhaustion_prevents_any_real_search():
    # `AgentLimits.budget_searches=0` -- the shared, turn-wide budget --
    # caps every dispatched subagent's own `SubagentLimits.max_searches` to
    # 0 for this round (`_research`'s own `budget_cap` logic), so its very
    # first `search_knowledge` call must get a budget-exhausted tool error
    # instead of a real `retrieval_client.search()` call, regardless of its
    # own, otherwise-larger, per-subagent budget.
    bot = _bot(limits={'max_parallel': 1, 'max_followups': 0, 'budget_searches': 0, 'timeout_seconds': 30})
    planner = _planner(('it', 'IT question'))
    it_llm = FakeLLM(tool_responses=[
        LLMToolResult(content=None, tool_calls=[_tool_call('s1')], model='fake-chat'),
        LLMToolResult(content=_final_answer([], ['budget exhausted before any search']), tool_calls=None, model='fake-chat'),
    ])

    with patch('app.services.retrieval_client.search', return_value=[_chunk('d1', 1, 'it-docs')]) as mock_search:
        outcome = ag.run_graph(
            bot, 'IT question', [], _available('it'), {s.id: s for s in bot.agent.subagents}, {'it': it_llm}, planner,
        )

    assert mock_search.call_count == 0
    assert outcome.agent_trace.budget_used == 0
    # No facts/sources came back at all -- the existing require_sources
    # guard applies exactly as it would for a direct-RAG turn.
    assert outcome.guard_triggered


# --- follow-up round triggered by an open point ------------------------------

def test_open_point_triggers_a_follow_up_round_for_that_agent():
    bot = _bot(limits={'max_parallel': 1, 'max_followups': 1, 'budget_searches': 9, 'timeout_seconds': 30})
    planner = _planner(('it', 'IT question'))
    it_llm = FakeLLM(tool_responses=[
        LLMToolResult(content=None, tool_calls=[_tool_call('s1')], model='fake-chat'),
        # First round: partial, with an open point -- triggers follow-up.
        LLMToolResult(content=_final_answer([], ['need more detail']), tool_calls=None, model='fake-chat'),
        # Follow-up round: this time it actually finds something.
        LLMToolResult(content=None, tool_calls=[_tool_call('s2')], model='fake-chat'),
        LLMToolResult(
            content=_final_answer([{'text': 'IT fact after follow-up.', 'source_refs': [{'document_id': 'd1', 'chunk_id': 1}]}]),
            tool_calls=None, model='fake-chat',
        ),
    ])

    with patch('app.services.retrieval_client.search', return_value=[_chunk('d1', 1, 'it-docs')]) as mock_search:
        outcome = ag.run_graph(
            bot, 'IT question', [], _available('it'),
            {s.id: s for s in bot.agent.subagents}, {'it': it_llm}, planner,
        )

    assert mock_search.call_count == 2  # one search per round
    assert outcome.agent_trace.followups == 1
    assert not outcome.guard_triggered
    assert len(outcome.sources) == 1


# --- simple question: single-subagent path -----------------------------------

def test_simple_question_with_no_tool_call_falls_back_to_single_best_matching_agent():
    # An unscripted `chat_with_tools` call (no `tool_responses` queued at
    # all) falls back to FakeLLM's own deterministic `chat()` reply with no
    # tool_calls -- exactly the "simple path" (rollout plan step 3).
    planner = FakeLLM()
    it_llm = FakeLLM(tool_responses=[
        LLMToolResult(content=None, tool_calls=[_tool_call('s1')], model='fake-chat'),
        LLMToolResult(content=_final_answer([{'text': 'IT fact.', 'source_refs': [{'document_id': 'd1', 'chunk_id': 1}]}]), tool_calls=None, model='fake-chat'),
    ])
    hr_llm = FakeLLM()  # must never be called at all

    with patch('app.services.retrieval_client.search', return_value=[_chunk('d1', 1, 'it-docs')]) as mock_search:
        outcome = ag.run_graph(
            _bot(), 'simple question', [], _available('it', 'hr'),
            {s.id: s for s in _bot().agent.subagents}, {'it': it_llm, 'hr': hr_llm}, planner,
        )

    assert mock_search.call_count == 1
    assert outcome.agent_trace.plan == [{'agent_id': 'it', 'subquestion': 'simple question'}]
    assert {trace.id for trace in outcome.agent_trace.subagents} == {'it'}


# --- a subagent cannot return hits outside its own effective scope ----------

def test_subagent_search_is_bounded_by_its_own_effective_scope():
    planner = _planner(('it', 'IT question'))
    it_llm = FakeLLM(tool_responses=[
        LLMToolResult(content=None, tool_calls=[_tool_call('s1')], model='fake-chat'),
        LLMToolResult(content=_final_answer(), tool_calls=None, model='fake-chat'),
    ])
    captured_scopes = []

    def _search(query, filters, allowed_teams, allowed_collections, top_k, final_k):
        captured_scopes.append(allowed_collections)
        return []

    bot = _bot()
    with patch('app.services.retrieval_client.search', side_effect=_search):
        # Only 'it-docs' is in this agent's own effective_scope -- never
        # 'hr-docs' or anything else, no matter what the subagent's own
        # search_knowledge call requests.
        ag.run_graph(
            bot, 'IT question', [], _available('it'),
            {s.id: s for s in bot.agent.subagents}, {'it': it_llm}, planner,
        )

    assert captured_scopes == [['it-docs']]


# --- Send fan-out respects max_concurrency -----------------------------------

def test_research_fanout_never_exceeds_max_parallel():
    bot = _bot(limits={'max_parallel': 2, 'max_followups': 0, 'budget_searches': 20, 'timeout_seconds': 30})
    agent_ids = ['a', 'b', 'c', 'd']
    planner = _planner(*[(agent_id, f'question {agent_id}') for agent_id in agent_ids])

    concurrency = {'current': 0, 'max': 0}
    lock = threading.Lock()

    def _fake_run_subagent(bot, subagent, question, effective_scope, llm_provider, *, model, temperature=None, allowed_teams=None, cancel=None):
        with lock:
            concurrency['current'] += 1
            concurrency['max'] = max(concurrency['max'], concurrency['current'])
        time.sleep(0.05)
        with lock:
            concurrency['current'] -= 1
        return agents_service.SubagentResult(
            agent_id=subagent.id, status='complete', facts=[], open_points=[], hits=[], searches_used=0,
        )

    subagents_by_id = {
        agent_id: SubagentConfig.model_validate(
            {'id': agent_id, 'name': agent_id.upper(), 'mission': 'm', 'collections': [f'{agent_id}-docs']}
        )
        for agent_id in agent_ids
    }
    providers = {agent_id: FakeLLM() for agent_id in agent_ids}

    with patch('app.services.agents.run_subagent', side_effect=_fake_run_subagent):
        ag.run_graph(bot, 'q', [], _available(*agent_ids), subagents_by_id, providers, planner)

    assert concurrency['max'] <= 2
    assert concurrency['max'] > 1  # actually ran at least two in parallel at some point
