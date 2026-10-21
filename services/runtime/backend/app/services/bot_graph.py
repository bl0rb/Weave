"""The ONE compiled LangGraph `StateGraph` behind every chat turn
(`POST /internal/chat` and `POST /internal/chat/stream` alike) -- the
successor to chat.py's former three hand-rolled paths (direct RAG, n8n,
agent mode single/multi) and their three separate thread+queue streaming
loops. chat.py still owns every pipeline step as a plain helper function
(permissions, scope resolution, retrieval, guard, n8n client calls,
subagent research); this module only decides WHICH helper runs WHEN, as
graph nodes and edges, and where stream events are written.

Topology (`_build_graph`):

    START -> route -> {n8n_smalltalk | n8n_scope | agent_prepare | unsupported
                       | overview | knowledge | conversational}
          -> trace -> {n8n_call | agent_single | agent_multi | generate}
          -> generate -> finish -> END

Everything BEFORE `trace` is work that may still fail with a pre-stream HTTP
status (`retrieval_client.RetrievalUnavailable` from scope resolution or
retrieval, see contracts/internal-chat.md): on the streaming route
app/services/stream_bridge.py blocks until `trace` has emitted its event and
re-raises anything that arrived first, so those failures still become
404/403/503 before any `200` SSE stream exists. Everything AFTER `trace` can
only ever fail in-band (`ChatStreamErrorEvent`).

Per-turn objects that are not turn DATA (the resolved `BotConfig`, the
caller, LLM providers, the cancel Event, whether this is a streaming turn)
travel in `TurnContext` through LangGraph's run-scoped `Runtime.context`
(`StateGraph(..., context_schema=TurnContext)`); `TurnState` carries only
what the turn itself produces. The graph is compiled once at import time
(`COMPILED`), without a checkpointer -- Weave-Runtime is stateless.

`timings_ms` is never mutated in place by a node (LangGraph state updates
are merged, not shared): every node that adds a timing returns a fresh
`{**state['timings_ms'], key: value}`.

Stream events: a node writes already-complete `ChatStreamEvent`s through
`runtime.stream_writer` (LangGraph's `custom` stream mode); on the blocking
route that writer is a no-op, so the very same node code serves both
routes. `streamed=True` in the state means the answer text already went out
as `delta` events (n8n live/buffered deltas, the agent paths' final-answer
streaming) and `generate` must not emit it again.
"""

import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.core.config import settings
from app.schemas.bot import BotConfig, SubagentConfig
from app.schemas.chat import (
    AgentTrace,
    ChatStreamDeltaEvent,
    ChatStreamDoneEvent,
    ChatStreamErrorEvent,
    ChatStreamSourcesEvent,
    ChatStreamStatusEvent,
    ChatStreamTraceEvent,
    ChatTrace,
    ChatUser,
    Clarification,
    GuardTrace,
    N8nTrace,
    RetrievalTrace,
    Source,
)
from app.services import agent_graph as agent_graph_service
from app.services import agents as agents_service
from app.services import chat as chat_service
from app.services import llm as llm_service
from app.services import n8n_client
from app.services import router as router_service
from app.services.chat_common import _elapsed_ms


@dataclass(frozen=True)
class TurnContext:
    """See this module's docstring. `bot` is the EFFECTIVE bot for this turn
    (central-provider override and knowledge overview already applied by
    chat.py's `prepare_turn`); `llm_provider` is None exactly for an
    n8n-provider bot; `main_llm_factory` yields a fresh instance of the
    main bot's provider for a subagent inheriting it."""

    bot: BotConfig
    user: ChatUser
    requested_collections: list[str] | None
    readable_slugs: list[str] | None
    llm_provider: llm_service.LLMProvider | None
    main_llm_factory: Callable[[], llm_service.LLMProvider]
    memory: bool
    streaming: bool
    cancel: threading.Event


class TurnState(TypedDict, total=False):
    message: str
    history: list[dict[str, str]]
    timings_ms: dict[str, float]
    total_start: float
    decision: router_service.RouterDecision
    branch: str
    # Direct path: the LLM-ready transcript for `generate`.
    messages: list[dict[str, str]]
    sources: list[Source]
    retrieval_trace: RetrievalTrace | None
    guard: GuardTrace | None
    clarification: Clarification | None
    # Set whenever the answer text is already decided without an LLM call
    # by `generate` (guard reply, V1-unsupported reply, n8n answer, agent
    # paths' own outcome).
    final_answer: str | None
    n8n_single_delta: bool
    n8n_trace: N8nTrace | None
    n8n_scope: list[str]
    agent_trace: AgentTrace | None
    # Single-subagent preparation (`agent_prepare`).
    subagent: SubagentConfig
    agent_scope: list[str]
    allowed_teams: list[str] | None
    subagent_llm_provider: llm_service.LLMProvider
    # Multi-subagent preparation (`agent_prepare`).
    available: list[agent_graph_service.AvailableAgent]
    subagents_by_id: dict[str, SubagentConfig]
    subagent_llm_providers: dict[str, llm_service.LLMProvider]
    # `generate`'s own LLM result on the direct path.
    answer: str
    answer_model: str | None
    streamed: bool
    errored: bool


_AGENT_BRANCHES = frozenset({'agent', 'multi_agent'})
# Branches whose trace event may name the model about to be requested --
# every other branch's answer is either already decided (no LLM call) or
# produced later by n8n/agent work the trace event cannot know about yet.
_DIRECT_BRANCHES = frozenset({'overview', 'knowledge', 'conversational'})


def _timing(state: TurnState, key: str, start: float) -> dict[str, float]:
    return {**state['timings_ms'], key: _elapsed_ms(start)}


# --- route ------------------------------------------------------------------


def _choose_branch(bot: BotConfig, decision: router_service.RouterDecision) -> str:
    if bot.model.provider == 'n8n':
        return 'n8n_smalltalk' if decision.intent == 'conversational' else 'n8n'
    # `BotConfig`'s own `_agent_mode_is_valid` validator guarantees tool
    # support for the YAML-time model, but the central-provider override
    # (chat.py's `prepare_turn`) may have swapped `bot.model` since -- the
    # override sets its own `supports_tools`, so re-checking here is right
    # whether or not an override happened.
    agent_mode_supported = bot.model.provider == 'fake' or bool(bot.model.supports_tools)
    if (
        bot.agent is not None
        and bot.agent.enabled
        and agent_mode_supported
        and decision.intent in chat_service._AGENT_ELIGIBLE_INTENTS
    ):
        return 'multi_agent' if len(bot.agent.subagents) > 1 else 'agent'
    complex_knowledge_fallback = decision.intent == 'complex' and decision.needs_retrieval and bot.retrieval.enabled
    if decision.intent in chat_service._V1_UNSUPPORTED_INTENTS and not complex_knowledge_fallback:
        return 'unsupported'
    if decision.intent == 'overview':
        return 'overview'
    if decision.needs_retrieval and bot.retrieval.enabled:
        return 'knowledge'
    return 'conversational'


def _route(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    bot = ctx.bot
    router_start = time.perf_counter()
    # `llm_call` stays None without a real provider (an n8n bot) even in
    # 'llm' router mode -- router.route() then falls back to RULES with
    # `router_fallback=True`, exactly the behaviour an n8n bot needs.
    llm_call = (
        chat_service._router_llm_call(ctx.llm_provider)
        if settings.router_mode == 'llm' and ctx.llm_provider is not None
        else None
    )
    decision = router_service.route(state['message'], bot, settings.router_mode, llm_call, memory=ctx.memory)
    return {
        'decision': decision,
        'branch': _choose_branch(bot, decision),
        'timings_ms': _timing(state, 'router_ms', router_start),
    }


def _after_route(state: TurnState) -> str:
    branch = state['branch']
    if branch in _AGENT_BRANCHES:
        return 'agent_prepare'
    if branch == 'n8n':
        return 'n8n_scope'
    return branch


# --- pre-trace branches -------------------------------------------------------


def _n8n_smalltalk(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    # No n8n call, no scope resolution, no delegation token for small talk
    # -- see chat.py's `_N8N_SMALLTALK_REPLY`.
    return {'final_answer': chat_service._N8N_SMALLTALK_REPLY, 'sources': []}


def _n8n_scope(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    # Runs BEFORE `trace` on purpose: `resolve_collection_scope` can raise
    # RetrievalUnavailable, which must still surface as a pre-stream 503.
    ctx = runtime.context
    return {'n8n_scope': chat_service.resolve_collection_scope(ctx.bot, ctx.user, ctx.requested_collections)}


def _unsupported(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    return {'final_answer': chat_service._V1_UNSUPPORTED_REPLY, 'sources': []}


def _base_messages(bot: BotConfig, history: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{'role': 'system', 'content': bot.system_prompt}, *history]


def _knowledge_step(state: TurnState, ctx: TurnContext, messages: list[dict[str, str]], *, memory: bool) -> dict:
    """Shared by `knowledge` and an `overview` turn that found nothing in
    the memory -- chat.py's `_run_knowledge_turn` plus the guard-reply
    mapping that used to live in `_prepare_turn`."""
    bot = ctx.bot
    retrieval_start = time.perf_counter()
    result = chat_service._run_knowledge_turn(
        bot, ctx.user, state['message'], messages, ctx.requested_collections,
        history=messages[1:], llm_provider=ctx.llm_provider, readable=ctx.readable_slugs, memory=memory,
    )
    timings = _timing(state, 'retrieval_ms', retrieval_start)
    if result.memory_ms is not None:
        timings['memory_ms'] = result.memory_ms

    if result.guard_triggered:
        # `'filter_excluded_all'` gets its OWN fixed reply, never
        # `bot.guard.no_context_reply` -- see that constant's docstring.
        if result.clarification is not None:
            guard_reply = result.clarification.question
        elif result.guard_reason == 'filter_excluded_all':
            guard_reply = chat_service._FILTER_EXCLUDED_ALL_REPLY
        else:
            guard_reply = bot.guard.no_context_reply
        return {
            'timings_ms': timings,
            'sources': [],
            'retrieval_trace': result.retrieval_trace,
            'guard': GuardTrace(triggered=True, reason=result.guard_reason),
            'final_answer': guard_reply,
            'clarification': result.clarification,
        }
    return {
        'timings_ms': timings,
        'messages': [*result.messages, {'role': 'user', 'content': state['message']}],
        'sources': result.sources,
        'retrieval_trace': result.retrieval_trace,
    }


def _knowledge(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    return _knowledge_step(state, ctx, _base_messages(ctx.bot, state['history']), memory=ctx.memory)


def _overview(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    """ADR-0009: an overview question is answered from the knowledge memory
    first; with nothing there it continues as a regular knowledge turn, or
    as a plain conversational turn when the bot's own overview block (already
    in the system prompt) answers it -- never a chunk search that ends in
    the guard ("never worse than today")."""
    ctx = runtime.context
    bot = ctx.bot
    decision = state['decision']
    messages = _base_messages(bot, state['history'])
    timings = state['timings_ms']
    overview = None
    if bot.retrieval.enabled:
        memory_start = time.perf_counter()
        overview = chat_service._run_overview_turn(
            bot, ctx.user, state['message'], messages, ctx.requested_collections, ctx.readable_slugs,
        )
        timings = _timing(state, 'memory_ms', memory_start)
    if overview is not None:
        messages, sources, retrieval_trace = overview
        return {
            'timings_ms': timings,
            'messages': [*messages, {'role': 'user', 'content': state['message']}],
            'sources': sources,
            'retrieval_trace': retrieval_trace,
        }
    if router_service.answered_from_bot_overview(state['message']):
        return {
            'timings_ms': timings,
            'decision': replace(decision, intent='conversational', needs_retrieval=False),
            'messages': [*messages, {'role': 'user', 'content': state['message']}],
            'sources': [],
        }
    if decision.needs_retrieval and bot.retrieval.enabled:
        # The memory was already asked for this question -- no second
        # orientation call.
        return {**_knowledge_step({**state, 'timings_ms': timings}, ctx, messages, memory=False)}
    return {
        'timings_ms': timings,
        'messages': [*messages, {'role': 'user', 'content': state['message']}],
        'sources': [],
    }


def _conversational(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    return {
        'messages': [*_base_messages(ctx.bot, state['history']), {'role': 'user', 'content': state['message']}],
        'sources': [],
    }


def _agent_prepare(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    """Collections-scope resolution for the agent paths (can raise
    RetrievalUnavailable -> must run before `trace`) plus per-subagent LLM
    provider resolution. Provider order is an observable contract (tests
    script `get_llm` by call order): the main bot's provider was already
    created by `prepare_turn`; subagents follow here, in YAML order."""
    ctx = runtime.context
    bot = ctx.bot
    assert ctx.llm_provider is not None  # never an n8n bot here
    allowed_teams = chat_service._allowed_teams(bot, ctx.user)

    if state['branch'] == 'multi_agent':
        available = chat_service._available_agents_for_graph(
            bot, ctx.user, ctx.requested_collections, allowed_teams, user_readable=ctx.readable_slugs,
        )
        available_ids = {agent['agent_id'] for agent in available}
        resolved = {
            subagent.id: chat_service._subagent_endpoint_llm(subagent, bot)
            for subagent in bot.agent.subagents
            if subagent.id in available_ids
        }
        # Every subagent gets its OWN provider instance, even one inheriting
        # the main model: research workers run concurrently and must never
        # share one mutable FakeLLM queue/connection pool.
        providers = {
            subagent_id: (
                endpoint_llm
                or (llm_service.get_llm(subagent.model.provider) if subagent.model is not None else ctx.main_llm_factory())
            )
            for subagent_id, (subagent, endpoint_llm) in resolved.items()
        }
        return {
            'available': available,
            'subagents_by_id': {subagent_id: subagent for subagent_id, (subagent, _) in resolved.items()},
            'subagent_llm_providers': providers,
            'allowed_teams': allowed_teams,
        }

    subagent, endpoint_llm = chat_service._subagent_endpoint_llm(chat_service._select_subagent(bot, ctx.user), bot)
    agent_scope = chat_service._resolve_agent_scope(
        bot, subagent, ctx.user, ctx.requested_collections, user_readable=ctx.readable_slugs,
    )
    subagent_model = subagent.model
    subagent_llm_provider = endpoint_llm or (
        ctx.llm_provider
        if subagent_model is None or subagent_model.provider == bot.model.provider
        else llm_service.get_llm(subagent_model.provider)
    )
    return {
        'subagent': subagent,
        'agent_scope': agent_scope,
        'allowed_teams': allowed_teams,
        'subagent_llm_provider': subagent_llm_provider,
    }


# --- trace --------------------------------------------------------------------


def _build_trace(state: TurnState, *, model: str | None) -> ChatTrace:
    decision = state['decision']
    return ChatTrace(
        intent=decision.intent,
        confidence=decision.confidence,
        needs_retrieval=decision.needs_retrieval,
        needs_tool=decision.needs_tool,
        retrieval=state.get('retrieval_trace'),
        clarification=state.get('clarification'),
        model=model,
        router_mode=settings.router_mode,
        timings_ms=state['timings_ms'],
        guard=state.get('guard'),
        n8n=state.get('n8n_trace'),
        agent=state.get('agent_trace'),
    )


def _trace(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    """Emits the stream's first event. `model` is the model ABOUT to be
    requested on the direct path and None whenever no LLM call will happen
    (answer already decided) or the answer comes from work the trace event
    cannot know about yet (n8n flow, agent research)."""
    if runtime.context.streaming:
        model = (
            runtime.context.bot.model.model
            if state.get('final_answer') is None and state['branch'] in _DIRECT_BRANCHES
            else None
        )
        runtime.stream_writer(ChatStreamTraceEvent(trace=_build_trace(state, model=model)))
    return {}


def _after_trace(state: TurnState) -> str:
    branch = state['branch']
    if branch == 'n8n':
        return 'n8n_call'
    if branch == 'agent':
        return 'agent_single'
    if branch == 'multi_agent':
        return 'agent_multi'
    return 'generate'


# --- post-trace work ------------------------------------------------------------


def _n8n_call(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    bot = ctx.bot
    scope = state['n8n_scope']
    if not ctx.streaming:
        n8n_start = time.perf_counter()
        result = chat_service._run_n8n_flow_blocking(bot, state['message'], state['history'], ctx.user, scope)
        timings = _timing(state, 'n8n_ms', n8n_start)
        kept_sources, dropped_count = chat_service._filter_n8n_sources_by_scope(result.sources, scope, bot_id=bot.id)
        n8n_trace = N8nTrace(dropped_sources=dropped_count)
        if bot.guard.require_sources and not kept_sources:
            return {
                'timings_ms': timings, 'sources': [], 'guard': GuardTrace(triggered=True, reason='no_context'),
                'final_answer': bot.guard.no_context_reply, 'n8n_single_delta': False, 'n8n_trace': n8n_trace,
            }
        return {
            'timings_ms': timings, 'sources': kept_sources, 'final_answer': result.answer,
            'n8n_single_delta': True, 'n8n_trace': n8n_trace,
        }

    # Streaming: `bot.guard.require_sources` BUFFERS every delta until the
    # flow reported its sources, THEN filters by scope and applies the guard
    # (a triggered guard discards the buffered text for the fixed reply;
    # otherwise the whole answer goes out as ONE delta). Without
    # `require_sources` every delta is forwarded the instant it arrives.
    writer = runtime.stream_writer
    require_sources = bot.guard.require_sources
    buffered: list[str] = []
    sources: list[Source] = []
    error_detail: str | None = None
    try:
        if bot.n8n.streaming:
            for stream_event in n8n_client.run_flow_stream(
                bot, state['message'], state['history'], ctx.user, scope, cancel=ctx.cancel,
            ):
                if isinstance(stream_event, n8n_client.N8nStreamDelta):
                    if stream_event.text:
                        if require_sources:
                            buffered.append(stream_event.text)
                        else:
                            writer(ChatStreamDeltaEvent(text=stream_event.text))
                elif isinstance(stream_event, n8n_client.N8nStreamSources):
                    sources = stream_event.sources
                elif isinstance(stream_event, n8n_client.N8nStreamError):
                    # Authored by the flow itself (like its answer text), so
                    # it may be shown -- capped, and never empty.
                    error_detail = (stream_event.detail or '').strip()[:500] or 'Der n8n-Agentenflow hat einen Fehler gemeldet.'
                    break
                elif isinstance(stream_event, n8n_client.N8nStreamDone):
                    break
                if ctx.cancel.is_set():
                    break
        else:
            result = n8n_client.run_flow(bot, state['message'], state['history'], ctx.user, scope)
            if result.answer:
                if require_sources:
                    buffered.append(result.answer)
                else:
                    writer(ChatStreamDeltaEvent(text=result.answer))
            sources = result.sources
    except Exception as exc:  # noqa: BLE001 -- 200/text-event-stream is already committed; in-band error only
        # The exception text names the webhook_url and may quote raw n8n
        # response fragments -- fine for the server log, never for a browser.
        chat_service.logger.warning('n8n stream turn failed for bot %s: %s', bot.id, exc)
        if isinstance(exc, n8n_client.N8nUnavailable):
            error_detail = 'Der n8n-Agentenflow ist derzeit nicht erreichbar oder hat nicht rechtzeitig geantwortet.'
        elif isinstance(exc, n8n_client.N8nError):
            error_detail = 'Der n8n-Agentenflow hat eine ungültige Antwort geliefert.'
        else:
            error_detail = 'Der n8n-Agentenflow ist unerwartet fehlgeschlagen.'

    if error_detail is not None:
        writer(ChatStreamErrorEvent(detail=error_detail))
        return {'errored': True, 'streamed': True}

    kept_sources, dropped_count = chat_service._filter_n8n_sources_by_scope(sources, scope, bot_id=bot.id)
    if require_sources and not kept_sources:
        for delta in llm_service.iter_text_deltas(bot.guard.no_context_reply):
            writer(ChatStreamDeltaEvent(text=delta))
        return {
            'sources': [], 'streamed': True, 'guard': GuardTrace(triggered=True, reason='no_context'),
            'n8n_trace': N8nTrace(dropped_sources=dropped_count),
        }
    if require_sources:
        full_text = ''.join(buffered)
        if full_text:
            writer(ChatStreamDeltaEvent(text=full_text))
    return {'sources': kept_sources, 'streamed': True, 'n8n_trace': N8nTrace(dropped_sources=dropped_count)}


def _agent_single(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    """Rollout plan "Schritt 2": ONE subagent's bounded `search_knowledge`
    loop, then the guard on its hits, then the main bot's final answer from
    the structured facts (`generate`, via `messages`). Kept as its own node
    rather than a one-entry fan-out through the multi-subagent graph: its
    status events, prompt and budget semantics are an observable contract
    and differ from the graph's (no planning/merging stages, no turn-wide
    search budget, `_facts_context_block` prompt)."""
    ctx = runtime.context
    bot = ctx.bot
    writer = runtime.stream_writer
    subagent = state['subagent']
    writer(ChatStreamStatusEvent(
        stage='researching', agent_id=subagent.id, agent_name=subagent.name, message=f'{subagent.name} wird durchsucht',
    ))
    agent_start = time.perf_counter()
    subagent_model = subagent.model or bot.model
    result = agents_service.run_subagent(
        bot, subagent, state['message'], state['agent_scope'], state['subagent_llm_provider'],
        model=subagent_model.model, temperature=subagent_model.temperature, allowed_teams=state['allowed_teams'],
        memory=ctx.memory,
    )
    timings = _timing(state, 'agent_ms', agent_start)
    writer(ChatStreamStatusEvent(
        stage='researching', agent_id=subagent.id, agent_name=subagent.name, state=result.status,
        message=f'{subagent.name} abgeschlossen',
    ))
    sources = chat_service._sources_from_result(result)
    agent_trace = chat_service._agent_trace_for(bot, result)

    if bot.guard.require_sources and not sources:
        return {
            'timings_ms': timings, 'sources': [], 'guard': GuardTrace(triggered=True, reason='no_context'),
            'final_answer': bot.guard.no_context_reply, 'agent_trace': agent_trace,
        }

    writer(ChatStreamStatusEvent(stage='answering', message='Antwort wird formuliert'))
    messages = [
        {'role': 'system', 'content': bot.system_prompt},
        *state['history'],
        {'role': 'system', 'content': chat_service._facts_context_block(subagent, result)},
        {'role': 'user', 'content': state['message']},
    ]
    return {'timings_ms': timings, 'sources': sources, 'messages': messages, 'agent_trace': agent_trace}


def _agent_multi(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    """Rollout plan "Schritt 3": the LangGraph orchestrator-worker flow
    (app/services/agent_graph.py) as a nested graph. Its `answer` node
    streams the final answer itself (via `progress_cb`), so `streamed=True`
    unless the guard fired -- then `final_answer` is the fixed reply for
    `generate` to chunk out, exactly like every other guard outcome."""
    ctx = runtime.context
    bot = ctx.bot
    assert ctx.llm_provider is not None
    writer = runtime.stream_writer

    def _progress_cb(kind: str, payload: Any) -> None:
        if kind == 'status':
            writer(payload)
        elif kind == 'delta':
            writer(ChatStreamDeltaEvent(text=payload))

    agent_start = time.perf_counter()
    outcome = agent_graph_service.run_graph(
        bot, state['message'], state['history'], state['available'], state['subagents_by_id'],
        state['subagent_llm_providers'], ctx.llm_provider,
        cancel=ctx.cancel, progress_cb=_progress_cb if ctx.streaming else None, memory=ctx.memory,
    )
    timings = _timing(state, 'agent_ms', agent_start)
    return {
        'timings_ms': timings,
        'sources': outcome.sources,
        'guard': GuardTrace(triggered=True, reason='no_context') if outcome.guard_triggered else None,
        'final_answer': outcome.final_answer,
        'agent_trace': outcome.agent_trace,
        # A non-guard outcome's full answer already went out as deltas.
        'streamed': ctx.streaming and not outcome.guard_triggered,
    }


def _cited(state: TurnState, answer: str) -> dict:
    """A knowledge/overview turn numbered its sources for the LLM (chat.py's
    `_context_block`/`_memory_sources`): report only the ones the answer
    cites. Agent turns number facts, not sources, and keep theirs as is."""
    if state['branch'] not in ('knowledge', 'overview') or not state.get('sources'):
        return {}
    return {'sources': chat_service.cited_sources(answer, state['sources'])}


def _generate(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    bot = ctx.bot
    writer = runtime.stream_writer
    if state.get('streamed'):
        return {}

    final_answer = state.get('final_answer')
    if final_answer is not None:
        if ctx.streaming:
            if state.get('n8n_single_delta'):
                # A finished n8n reply has no smaller meaningful unit --
                # exactly ONE delta (none for an empty answer).
                deltas = iter([final_answer] if final_answer else [])
            else:
                deltas = llm_service.iter_text_deltas(final_answer)
            for delta in deltas:
                writer(ChatStreamDeltaEvent(text=delta))
        return {'streamed': True}

    messages = state['messages']
    assert ctx.llm_provider is not None  # never an n8n bot on this path
    llm_start = time.perf_counter()
    if ctx.streaming:
        parts: list[str] = []
        try:
            for delta in llm_service.iter_chat_stream(
                ctx.llm_provider, messages, model=bot.model.model, temperature=bot.model.temperature,
            ):
                parts.append(delta)
                writer(ChatStreamDeltaEvent(text=delta))
        except llm_service.LLMError as exc:
            if state['branch'] in _AGENT_BRANCHES:
                # The agent paths report any failure with their own fixed
                # text (see stream_bridge.py) -- never the provider's own.
                raise
            writer(ChatStreamErrorEvent(detail=str(exc)))
            return {'errored': True, 'streamed': True}
        return {
            'timings_ms': _timing(state, 'llm_ms', llm_start), 'streamed': True,
            **_cited(state, ''.join(parts)),
        }

    llm_result = ctx.llm_provider.chat(messages, model=bot.model.model, temperature=bot.model.temperature)
    return {
        **_cited(state, llm_result.content),
        'timings_ms': _timing(state, 'llm_ms', llm_start),
        'answer': llm_result.content,
        # The blocking agent path never reported the answering model
        # (`trace.model` is None for it) -- only the direct path does.
        'answer_model': llm_result.model if state['branch'] in _DIRECT_BRANCHES else None,
    }


def _finish(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    timings = _timing(state, 'total_ms', state['total_start'])
    if runtime.context.streaming and not state.get('errored'):
        runtime.stream_writer(ChatStreamSourcesEvent(sources=state.get('sources') or []))
        runtime.stream_writer(ChatStreamDoneEvent())
    return {'timings_ms': timings}


# --- graph ----------------------------------------------------------------------

_PRE_TRACE_NODES = ('n8n_smalltalk', 'n8n_scope', 'agent_prepare', 'unsupported', 'overview', 'knowledge', 'conversational')
_POST_TRACE_NODES = ('n8n_call', 'agent_single', 'agent_multi', 'generate')


def _build_graph():
    graph = StateGraph(TurnState, context_schema=TurnContext)
    graph.add_node('route', _route)
    graph.add_node('n8n_smalltalk', _n8n_smalltalk)
    graph.add_node('n8n_scope', _n8n_scope)
    graph.add_node('agent_prepare', _agent_prepare)
    graph.add_node('unsupported', _unsupported)
    graph.add_node('overview', _overview)
    graph.add_node('knowledge', _knowledge)
    graph.add_node('conversational', _conversational)
    graph.add_node('trace', _trace)
    graph.add_node('n8n_call', _n8n_call)
    graph.add_node('agent_single', _agent_single)
    graph.add_node('agent_multi', _agent_multi)
    graph.add_node('generate', _generate)
    graph.add_node('finish', _finish)

    graph.add_edge(START, 'route')
    graph.add_conditional_edges('route', _after_route, list(_PRE_TRACE_NODES))
    for node in _PRE_TRACE_NODES:
        graph.add_edge(node, 'trace')
    graph.add_conditional_edges('trace', _after_trace, list(_POST_TRACE_NODES))
    for node in ('n8n_call', 'agent_single', 'agent_multi'):
        graph.add_edge(node, 'generate')
    graph.add_edge('generate', 'finish')
    graph.add_edge('finish', END)
    return graph.compile()


COMPILED = _build_graph()


def initial_state(message: str, history: list[dict[str, str]], total_start: float) -> TurnState:
    return {'message': message, 'history': history, 'timings_ms': {}, 'total_start': total_start}


def run_config() -> RunnableConfig:
    """The turn graph runs its nodes strictly one at a time, so it needs no
    `max_concurrency` of its own -- and must NOT set one: LangGraph sizes
    the run's worker pool from it, and the streaming run keeps one worker
    busy waiting for stream events, so `max_concurrency=1` would starve
    every node. The research fan-out's own bound (`agent.limits.max_parallel`)
    is applied by agent_graph.run_graph on its nested run."""
    return RunnableConfig()
