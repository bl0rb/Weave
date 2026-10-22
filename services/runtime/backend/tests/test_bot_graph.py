"""Structure tests for app/services/bot_graph.py's compiled turn graph --
the behaviour itself is covered end to end by test_chat_e2e/test_chat_stream/
test_chat_n8n*/test_chat_agent_turn/test_chat_multi_agent_turn, which ran
unchanged across the switch from chat.py's hand-rolled paths to this graph.
"""

import threading
from dataclasses import replace

from app.schemas.bot import BotConfig
from app.schemas.chat import ChatUser
from app.services import bot_graph
from app.services import router as router_service
from app.services.llm import FakeLLM


def _bot(**overrides) -> BotConfig:
    data = {
        'id': 'graph-bot', 'name': 'Graph Bot', 'model': {'model': 'fake-chat'},
        'system_prompt': 'Du bist ein Test-Bot.', 'retrieval': {'enabled': True},
    }
    data.update(overrides)
    return BotConfig.model_validate(data)


def _decision(intent: str, *, needs_retrieval: bool) -> router_service.RouterDecision:
    return router_service.RouterDecision(
        intent=intent, confidence=0.9, needs_retrieval=needs_retrieval, needs_tool=intent in ('action', 'complex'),
    )


def test_graph_topology_matches_the_documented_pipeline():
    graph = bot_graph.COMPILED.get_graph()
    nodes = set(graph.nodes) - {'__start__', '__end__'}
    assert nodes == {
        'route', 'n8n_smalltalk', 'n8n_scope', 'agent_prepare', 'unsupported', 'blocked', 'intake', 'overview', 'knowledge',
        'conversational', 'trace', 'n8n_call', 'agent_single', 'agent_multi', 'generate', 'finish',
    }
    edges = {(edge.source, edge.target) for edge in graph.edges}
    # Every pre-trace branch feeds the single `trace` node; every post-trace
    # worker ends in `generate` -> `finish` -> END.
    for node in bot_graph._PRE_TRACE_NODES:
        assert ('route', node) in edges and (node, 'trace') in edges
    for node in bot_graph._POST_TRACE_NODES:
        assert ('trace', node) in edges
    for node in ('n8n_call', 'agent_single', 'agent_multi'):
        assert (node, 'generate') in edges
    assert ('generate', 'finish') in edges and ('finish', '__end__') in edges
    assert graph.draw_mermaid()


def test_branch_selection_per_bot_kind_and_intent():
    plain = _bot()
    assert bot_graph._choose_branch(plain, _decision('conversational', needs_retrieval=False)) == 'conversational'
    assert bot_graph._choose_branch(plain, _decision('knowledge', needs_retrieval=True)) == 'knowledge'
    assert bot_graph._choose_branch(plain, _decision('overview', needs_retrieval=True)) == 'overview'
    assert bot_graph._choose_branch(plain, _decision('document', needs_retrieval=False)) == 'unsupported'
    # `complex` with retrieval falls back to a knowledge turn instead of the V1 reply.
    assert bot_graph._choose_branch(plain, _decision('complex', needs_retrieval=True)) == 'knowledge'

    n8n = _bot(model={'provider': 'n8n', 'model': 'flow'}, n8n={'webhook_url': 'https://n8n.example/hook'})
    assert bot_graph._choose_branch(n8n, _decision('conversational', needs_retrieval=False)) == 'n8n_smalltalk'
    assert bot_graph._choose_branch(n8n, _decision('document', needs_retrieval=False)) == 'n8n'

    subagent = {'id': 'hr', 'name': 'HR', 'mission': 'HR-Fragen', 'collections': ['hr']}
    single = _bot(agent={'enabled': True, 'subagents': [subagent]})
    assert bot_graph._choose_branch(single, _decision('knowledge', needs_retrieval=True)) == 'agent'
    assert bot_graph._choose_branch(single, _decision('conversational', needs_retrieval=False)) == 'conversational'
    multi = _bot(agent={'enabled': True, 'subagents': [subagent, {**subagent, 'id': 'it', 'name': 'IT'}]})
    assert bot_graph._choose_branch(multi, _decision('action', needs_retrieval=False)) == 'multi_agent'
    # A central-provider override without declared tool support falls back to RAG.
    no_tools = multi.model_copy(update={'model': multi.model.model_copy(update={'provider': 'openai', 'supports_tools': None})})
    assert bot_graph._choose_branch(no_tools, _decision('knowledge', needs_retrieval=True)) == 'knowledge'


def test_topic_block_and_intake_branches_come_first():
    blocked = replace(_decision('knowledge', needs_retrieval=True), blocked_topic='Politik')
    plain = _bot()
    assert bot_graph._choose_branch(plain, blocked, intake_pending=True) == 'blocked'
    knowledge = _decision('knowledge', needs_retrieval=True)
    assert bot_graph._choose_branch(plain, knowledge, intake_pending=True) == 'intake'
    assert bot_graph._choose_branch(plain, _decision('complex', needs_retrieval=True), intake_pending=True) == 'intake'
    # Small talk never triggers the intake question -- except as a re-ask.
    smalltalk = _decision('conversational', needs_retrieval=False)
    assert bot_graph._choose_branch(plain, smalltalk, intake_pending=True) == 'conversational'
    assert bot_graph._choose_branch(plain, smalltalk, intake_pending=True, intake_retry=True) == 'intake'
    # n8n bots get the topic block too.
    n8n = _bot(model={'provider': 'n8n', 'model': 'flow'}, n8n={'webhook_url': 'https://n8n.example/hook'})
    assert bot_graph._choose_branch(n8n, blocked) == 'blocked'


def test_trace_node_names_the_model_only_on_the_direct_path():
    bot = _bot()
    written = []

    class _Runtime:
        context = bot_graph.TurnContext(
            bot=bot, user=ChatUser(id='u'), requested_collections=None, readable_slugs=None, llm_provider=FakeLLM(),
            main_llm_factory=FakeLLM, memory=False, streaming=True, cancel=threading.Event(),
        )
        stream_writer = staticmethod(written.append)

    base = {'timings_ms': {'router_ms': 1.0}, 'decision': _decision('knowledge', needs_retrieval=True)}
    bot_graph._trace({**base, 'branch': 'knowledge'}, _Runtime())
    bot_graph._trace({**base, 'branch': 'knowledge', 'final_answer': 'guard reply'}, _Runtime())
    bot_graph._trace({**base, 'branch': 'agent'}, _Runtime())
    assert [event.trace.model for event in written] == ['fake-chat', None, None]
    assert written[0].trace.timings_ms == {'router_ms': 1.0}


def test_timings_are_merged_never_mutated():
    state = {'timings_ms': {'router_ms': 1.0}, 'total_start': 0.0}
    merged = bot_graph._timing(state, 'llm_ms', 0.0)
    assert set(merged) == {'router_ms', 'llm_ms'}
    assert state['timings_ms'] == {'router_ms': 1.0}
