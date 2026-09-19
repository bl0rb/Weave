"""LangGraph orchestrator-worker flow for a multi-subagent research turn
(rollout plan "Schritt 3 -- LangGraph mit mehreren Subagenten") -- the
successor to Schritt 2's own single-subagent `agents.run_subagent`, used by
app/services/chat.py exactly when a bot's agent mode has MORE than one
configured subagent (see that module's own `_prepare_turn`, the
`len(bot.agent.subagents) > 1` branch); a bot with exactly one subagent keeps
using Schritt 2's own `_run_agent_turn_blocking`/`_stream_deferred_agent`
completely unchanged.

Pipeline (docs.langchain.com/oss/python/langgraph/workflows-agents' own
"orchestrator-worker" pattern): `plan` (the main bot's own LLM, offered ONE
tool -- `research_area(agent_id, question)` -- decides which available
research agent(s) should investigate which subquestion(s); a plain text
reply with no tool call is the "simple path", falling back to the single
best-matching agent) -> fan-out via `langgraph.types.Send` to a `research`
worker node per planned subquestion (each one, in turn, Schritt 2's own
`agents.run_subagent`, bounded by its own `SubagentLimits` AND this turn's
shared `AgentLimits.budget_searches`) -> `merge` (dedupe hits, tag
contradicting facts, decide whether an up-to-`AgentLimits.max_followups`
follow-up round is worth running for any subquestion that came back
`'failed'`/`'partial'`/factless) -> `answer` (the main bot's own LLM
formulates the final answer from every fact gathered so far, or -- via the
SAME `bot.guard.require_sources` this pipeline already enforces for the
direct-RAG and single-subagent turns -- `bot.guard.no_context_reply` when
NOTHING usable came back at all).

`langgraph.graph.StateGraph`/`Send`/`RunnableConfig(max_concurrency=...)`
(see requirements.in's own comment on why LangGraph is a deliberate
dependency here) do the actual scheduling: `RunnableConfig(max_concurrency=
bot.agent.limits.max_parallel)`, passed to `compiled.invoke(...)` in
`run_graph` below, is what keeps at most `max_parallel` `research` workers
running at once, never this module's own bookkeeping.

Failure semantics mirror the rollout plan's own wording exactly: a subagent
that came back `'failed'`/`'partial'` with no facts at all is INCOMPLETE
research, never silently treated as "no information" -- `_answer` appends a
fixed, German, per-agent sentence naming the gap ("Für den Bereich <name>
liegen keine Ergebnisse vor; die Recherche war unvollständig.") whenever the
turn still has SOME usable evidence from other agents; when there is no
evidence at all, the existing `bot.guard.require_sources` guard fires
instead, exactly as it already does for the direct-RAG and single-subagent
paths. Internal planner/subagent reasoning (system prompts, raw tool-calling
transcripts) never reaches `GraphOutcome` -- only the resolved plan (`{
'agent_id', 'subquestion'}` pairs, for `AgentTrace.plan`), each subagent's
own already-public `SubagentResult` fields, and the final answer text do.

`_to_source`/`NO_COLLECTION_SENTINEL` are imported LAZILY from
app.services.chat (inside the functions that need them, never at this
module's own top level) because chat.py imports THIS module at its own top
level (`agent_graph_service`, alongside its existing `agents_service`
import) -- an eager import in both directions would be a genuine cycle; see
chat.py's own top-of-file note on the identical, longer-standing
scope.py<->chat.py relationship (there, the two modules are reversed: scope.py
imports chat.py eagerly, chat.py imports scope.py lazily -- the same
principle, applied here with the eager/lazy roles swapped to match which
module chat.py itself imports first).
"""

import logging
import operator
import re
import threading
import time
from dataclasses import dataclass
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.schemas.bot import BotConfig, SubagentConfig
from app.schemas.chat import AgentTrace, Source, SubagentTrace
from app.services import agents as agents_service
from app.services import llm as llm_service

logger = logging.getLogger(__name__)

RESEARCH_AREA_TOOL = 'research_area'


class AvailableAgent(TypedDict):
    """One subagent this turn's planner may delegate to -- built by
    app/services/chat.py's `_available_agents_for_graph` BEFORE the graph
    ever runs (Collections-scope resolution can itself raise
    RetrievalUnavailable/RetrievalError, which must surface pre-stream, same
    reasoning as `_DeferredAgentTurn`'s own docstring for the single-subagent
    case). `effective_scope` is already the fully resolved rollout-plan
    intersection (user rights ∩ main-bot collections ∩ subagent collections
    ∩ request filter) -- never re-resolved inside this module. Only agents
    with a NON-empty `effective_scope` are ever included here at all (see
    that builder's own docstring); this module never sees, and therefore
    never offers the planner, a subagent this caller could not possibly get
    any real hits from.
    """

    agent_id: str
    effective_scope: list[str]
    allowed_teams: list[str] | None


class PlannedSubquestion(TypedDict):
    agent_id: str
    subquestion: str


class GraphState(TypedDict):
    """This graph's own shared state -- see this module's own docstring for
    the pipeline each field flows through. `results`/`plan_history` both use
    `operator.add` as their reducer: every worker/round APPENDS to them
    (LangGraph's own map-reduce convention for a Send-based fan-out), never
    replaces them -- `results` therefore always holds the CUMULATIVE
    `SubagentResult` list across every round this turn ever ran, including
    superseded pre-follow-up attempts (see `_final_results_per_agent` for
    where that is collapsed back down to one entry per agent).

    `plan` doubles as the trigger for `_dispatch`'s own conditional-edge
    routing: non-empty routes to `Send('research', ...)` once per entry,
    empty routes straight to `answer` -- true both right after `plan` (an
    agent-mode turn with no available agents at all) and after `merge`
    decides no follow-up round is warranted.
    """

    question: str
    history: list[dict[str, str]]
    available: list[AvailableAgent]
    plan: list[PlannedSubquestion]
    plan_history: Annotated[list[PlannedSubquestion], operator.add]
    results: Annotated[list[agents_service.SubagentResult], operator.add]
    followups_used: int
    budget: dict[str, float]
    final_answer: str | None
    guard_triggered: bool


def research_area_tool_schema(available: list[AvailableAgent]) -> dict:
    """The ONE tool the main bot's own planner LLM is offered -- rollout
    plan step 3's `research_area(agent_id, question)`. `agent_id` is
    constrained, via a JSON-schema `enum`, to exactly the ids in
    `available` -- the model can never even SYNTACTICALLY name an agent
    outside its own offered roster (`_plan`'s own validation below still
    checks it again explicitly, since a provider's `enum` enforcement is
    guidance, not a guarantee -- the identical defense-in-depth posture
    app/services/agents.py's own `search_knowledge_tool_schema` docstring
    already documents for that tool). No identity/permission-shaped
    property exists here at all, for the same reason.
    """
    return {
        'type': 'function',
        'function': {
            'name': RESEARCH_AREA_TOOL,
            'description': (
                'Delegate one focused subquestion to a research agent for its own area of expertise. '
                'Call this once per independent subquestion you need researched -- several times for '
                'several areas. If the question is simple enough for a single area, either call this '
                'once or just answer directly without calling it at all.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'agent_id': {
                        'type': 'string',
                        'enum': [agent['agent_id'] for agent in available],
                        'description': 'Which research agent should investigate this subquestion.',
                    },
                    'question': {'type': 'string', 'description': 'The focused subquestion for that agent.'},
                },
                'required': ['agent_id', 'question'],
                'additionalProperties': False,
            },
        },
    }


_PLANNER_SYSTEM_PROMPT = (
    'You are the planning stage of a research assistant with several specialist research agents '
    'available. Decide which agent(s) should investigate which part of the question below, and call '
    'research_area once per independent subquestion -- several times for several areas. If the question '
    'is simple enough for a single area, either call research_area once or answer directly.'
)


def _plan(state: GraphState, *, bot: BotConfig, main_llm_provider: llm_service.LLMProvider) -> dict:
    available = state['available']
    if not available:
        return {'plan': [], 'plan_history': []}

    messages = [
        {'role': 'system', 'content': _PLANNER_SYSTEM_PROMPT},
        *state['history'],
        {'role': 'user', 'content': state['question']},
    ]
    try:
        result = main_llm_provider.chat_with_tools(
            messages, tools=[research_area_tool_schema(available)],
            model=bot.model.model, temperature=bot.model.temperature,
        )
    except llm_service.LLMError as exc:
        logger.warning('agent-graph planner LLM call failed for bot %r: %s', bot.id, exc)
        result = None

    valid_ids = {agent['agent_id'] for agent in available}
    plan: list[PlannedSubquestion] = []
    if result is not None and result.tool_calls:
        for call in result.tool_calls:
            if call.name != RESEARCH_AREA_TOOL or not isinstance(call.arguments, dict):
                continue
            agent_id = call.arguments.get('agent_id')
            question = call.arguments.get('question')
            if agent_id in valid_ids and isinstance(question, str) and question.strip():
                plan.append({'agent_id': agent_id, 'subquestion': question})

    if not plan:
        # Simple path (rollout plan step 3: "a direct text answer without
        # tool calls = simple path -> run the single best-matching
        # subagent"). `available[0]` is always that best match --
        # app/services/chat.py's `_available_agents_for_graph` orders
        # `available` starting with exactly the subagent
        # `_select_subagent` (Schritt 2) would itself have picked -- so
        # falling back to it here reproduces Schritt 2's own single-subagent
        # choice, just researched through this graph's own
        # research/merge/answer machinery instead of a separate code path.
        plan = [{'agent_id': available[0]['agent_id'], 'subquestion': state['question']}]
    return {'plan': plan, 'plan_history': plan}


def _dispatch(state: GraphState) -> list[Send] | str:
    """The conditional-edge routing function shared by BOTH `plan` and
    `merge` (see `GraphState`'s own docstring on why `plan` doubles as this
    decision's own trigger): empty `plan` means nothing left to research --
    route straight to `answer` -- otherwise fan out one `Send('research',
    ...)` per planned subquestion, each carrying everything `_research`
    needs (the fully pre-resolved scope/team axis from `available`, plus
    this round's own shared-budget snapshot) without that worker ever
    having to read the rest of this graph's own state.
    """
    if not state['plan']:
        return 'answer'
    available_by_id = {agent['agent_id']: agent for agent in state['available']}
    sends: list[Send] = []
    for planned in state['plan']:
        agent = available_by_id[planned['agent_id']]
        sends.append(Send('research', {
            'agent_id': planned['agent_id'],
            'subquestion': planned['subquestion'],
            'effective_scope': agent['effective_scope'],
            'allowed_teams': agent['allowed_teams'],
            'budget_cap': state['budget']['searches_left'],
            'deadline': state['budget']['deadline'],
        }))
    return sends


def _research(
    payload: dict,
    *,
    bot: BotConfig,
    subagents_by_id: dict[str, SubagentConfig],
    subagent_llm_providers: dict[str, llm_service.LLMProvider],
    cancel: threading.Event | None,
) -> dict:
    """One `Send('research', ...)` worker invocation -- Schritt 2's own
    `agents.run_subagent`, for exactly one planned subquestion. Runs however
    many of these `RunnableConfig(max_concurrency=...)` (see `run_graph`)
    allows at once; each subagent's OWN `llm_provider` instance
    (`subagent_llm_providers`, resolved once up front by app/services/
    chat.py, never shared across subagents even when two share the identical
    underlying model/provider) is what keeps two concurrently-running
    workers from ever touching the same mutable `FakeLLM.tool_responses`
    queue from two threads at once.
    """
    agent_id = payload['agent_id']
    subagent = subagents_by_id[agent_id]

    if (cancel is not None and cancel.is_set()) or time.monotonic() >= payload['deadline']:
        return {'results': [agents_service.SubagentResult(
            agent_id=agent_id, status='failed', facts=[],
            open_points=['Recherche wurde wegen Zeitlimit oder Verbindungsabbruch nicht durchgeführt.'],
            hits=[], searches_used=0,
        )]}

    # Shared `AgentLimits.budget_searches` (rollout plan step 4's turn-wide
    # budget, on top of each subagent's own `SubagentLimits.max_searches`):
    # capped here, per dispatched worker, to whatever this round's own
    # `budget_cap` snapshot (`_dispatch`) still allowed -- a soft cap, not a
    # perfectly atomic one, since several workers dispatched in the SAME
    # round read the identical snapshot; good enough for a turn-wide budget
    # that is itself just a spending guideline, never a security boundary
    # (Collections scope/`allowed_teams`, enforced inside `run_subagent`
    # itself, are the actual boundary).
    budget_cap = max(0, payload['budget_cap'])
    capped_subagent = subagent
    if budget_cap < subagent.limits.max_searches:
        capped_subagent = subagent.model_copy(
            update={'limits': subagent.limits.model_copy(update={'max_searches': budget_cap})}
        )

    model_cfg = subagent.model or bot.model
    result = agents_service.run_subagent(
        bot, capped_subagent, payload['subquestion'], payload['effective_scope'],
        subagent_llm_providers[agent_id],
        model=model_cfg.model, temperature=model_cfg.temperature,
        allowed_teams=payload['allowed_teams'], cancel=cancel,
    )
    return {'results': [result]}


def _followup_question(question: str, gap: agents_service.SubagentResult) -> str:
    hint = '; '.join(gap.open_points) if gap.open_points else 'weitere Belege für die bisherige Antwort'
    return f'{question} (Folgerecherche zu offenen Punkten: {hint})'


def _merge(
    state: GraphState, *, limits, cancel: threading.Event | None,
) -> dict:
    """Runs once per completed `research` round (`add_edge('research',
    'merge')` -- LangGraph waits for every `Send`-ed worker of a round
    before entering this node). Decrements the shared `budget_searches` by
    what this round actually spent, then decides whether a follow-up round
    is worth it: any subquestion this round came back `'failed'`/
    `'partial'`, or with zero facts at all, up to `limits.max_followups`
    ROUNDS total (never per gap) and only while the shared budget/overall
    deadline/cancellation still allow it -- rollout plan step 4's own
    "detect subquestions with... status failed/partial or empty facts ->
    up to limits.max_followups follow-up research rounds while
    budget_searches remains".

    `state['results'][-len(state['plan']):]` is THIS round's own slice --
    safe because rounds run strictly sequentially (one `merge` call per
    completed `research` fan-out, never overlapping), so the newest
    `len(state['plan'])` entries of the cumulative, `operator.add`-reduced
    `results` list are exactly, and only, this round's own.
    """
    this_round = state['results'][-len(state['plan']):] if state['plan'] else []
    budget = dict(state['budget'])
    budget['searches_left'] = max(0.0, budget['searches_left'] - sum(result.searches_used for result in this_round))

    followups_used = state['followups_used']
    gaps = [result for result in this_round if result.status in ('failed', 'partial') or not result.facts]
    can_follow_up = (
        bool(gaps)
        and followups_used < limits.max_followups
        and budget['searches_left'] > 0
        and not (cancel is not None and cancel.is_set())
        and time.monotonic() < budget['deadline']
    )

    next_plan: list[PlannedSubquestion] = []
    if can_follow_up:
        for gap in gaps:
            next_plan.append({'agent_id': gap.agent_id, 'subquestion': _followup_question(state['question'], gap)})
        followups_used += 1

    return {'plan': next_plan, 'plan_history': next_plan, 'followups_used': followups_used, 'budget': budget}


_GAP_NOTE_TEMPLATE = 'Für den Bereich {name} liegen keine Ergebnisse vor; die Recherche war unvollständig.'

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _final_results_per_agent(
    results: list[agents_service.SubagentResult],
) -> dict[str, agents_service.SubagentResult]:
    """The LAST `SubagentResult` per `agent_id` in `results` (cumulative
    across every round, see `GraphState`'s own docstring) -- a follow-up
    round's own outcome supersedes whatever that same agent came back with
    earlier, so a subquestion that started `'partial'` and later succeeded
    is never still reported as an unresolved gap."""
    latest: dict[str, agents_service.SubagentResult] = {}
    for result in results:
        latest[result.agent_id] = result
    return latest


def _gap_notes(results: list[agents_service.SubagentResult], subagents_by_id: dict[str, SubagentConfig]) -> list[str]:
    notes = []
    for result in _final_results_per_agent(results).values():
        if result.status in ('failed', 'partial') and not result.facts:
            subagent = subagents_by_id.get(result.agent_id)
            name = subagent.name if subagent is not None else result.agent_id
            notes.append(_GAP_NOTE_TEMPLATE.format(name=name))
    return notes


def _subject_key(text: str, *, words: int = 4) -> str:
    return ' '.join(_WORD_RE.findall(text.lower())[:words])


def _detect_contradictions(results: list[agents_service.SubagentResult]) -> list[str]:
    """A cheap, purely lexical heuristic (rollout plan step 4's own "as
    judged by a cheap heuristic or one LLM call"): facts from different
    subagents (or the same one, across rounds) whose first few words match
    (a crude stand-in for "same subject") but whose full text does not are
    flagged for the final prompt -- never silently merged/deduplicated like
    an exact-duplicate fact would be, and never dropped, only surfaced so
    the main bot's own final answer can choose to mention the discrepancy.
    """
    groups: dict[str, set[str]] = {}
    for result in results:
        for fact in result.facts:
            key = _subject_key(fact.text)
            if not key:
                continue
            groups.setdefault(key, set()).add(fact.text.strip())
    return [
        f"Zum Thema '{key}': " + ' | '.join(sorted(texts))
        for key, texts in groups.items()
        if len(texts) > 1
    ]


def _dedup_sources(results: list[agents_service.SubagentResult], to_source) -> list[Source]:
    seen: set[tuple[str, int]] = set()
    sources: list[Source] = []
    for result in results:
        for chunk in result.hits:
            key = (chunk.document_id, chunk.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            sources.append(to_source(chunk))
    return sources


def _combined_context_block(
    results: list[agents_service.SubagentResult],
    subagents_by_id: dict[str, SubagentConfig],
    contradictions: list[str],
) -> str:
    sections = [
        'Answer using only the facts below, gathered by several research agents, each named. '
        'Name any open points explicitly rather than guessing; never invent a fact beyond what is listed.'
    ]
    index = 1
    for result in results:
        subagent = subagents_by_id.get(result.agent_id)
        name = subagent.name if subagent is not None else result.agent_id
        for fact in result.facts:
            sections.append(f'[source {index}] ({name}) {fact.text}')
            index += 1
    if contradictions:
        sections.append(
            'Possible contradictions between agents -- mention them if relevant:\n' + '\n'.join(contradictions)
        )
    return '\n\n'.join(sections)


def _answer(
    state: GraphState, *, bot: BotConfig, subagents_by_id: dict[str, SubagentConfig],
    main_llm_provider: llm_service.LLMProvider,
) -> dict:
    from app.services.chat import _to_source  # lazy import -- see this module's own docstring

    results = state['results']
    sources = _dedup_sources(results, _to_source)

    if bot.guard.require_sources and not sources:
        # No facts/sources at all -- the existing response guard applies
        # exactly as it already does for the direct-RAG and single-subagent
        # paths (rollout plan step 5's own failure semantics).
        return {'final_answer': bot.guard.no_context_reply, 'guard_triggered': True}

    gap_notes = _gap_notes(results, subagents_by_id)
    contradictions = _detect_contradictions(results)
    context = _combined_context_block(results, subagents_by_id, contradictions)
    messages = [
        {'role': 'system', 'content': bot.system_prompt},
        *state['history'],
        {'role': 'system', 'content': context},
        {'role': 'user', 'content': state['question']},
    ]
    answer = main_llm_provider.chat(messages, model=bot.model.model, temperature=bot.model.temperature).content
    if gap_notes:
        # Rollout plan step 5: "mit brauchbaren Teilantworten antwortet der
        # Hauptbot und benennt die Lücke" -- appended as fixed, deterministic
        # German sentences rather than left to the LLM's own compliance with
        # the context block's "name any open points" instruction, exactly
        # like every other fixed guard/gap copy elsewhere in this codebase
        # (app/services/chat.py's own `_V1_UNSUPPORTED_REPLY`/
        # `_FILTER_EXCLUDED_ALL_REPLY`) is a literal string, not a prompt.
        answer = answer + '\n\n' + '\n'.join(gap_notes)
    return {'final_answer': answer, 'guard_triggered': False}


def _build_graph(
    bot: BotConfig,
    subagents_by_id: dict[str, SubagentConfig],
    subagent_llm_providers: dict[str, llm_service.LLMProvider],
    main_llm_provider: llm_service.LLMProvider,
    cancel: threading.Event | None,
):
    graph = StateGraph(GraphState)
    graph.add_node('plan', lambda state: _plan(state, bot=bot, main_llm_provider=main_llm_provider))
    graph.add_node('research', lambda payload: _research(
        payload, bot=bot, subagents_by_id=subagents_by_id,
        subagent_llm_providers=subagent_llm_providers, cancel=cancel,
    ))
    graph.add_node('merge', lambda state: _merge(state, limits=bot.agent.limits, cancel=cancel))
    graph.add_node('answer', lambda state: _answer(
        state, bot=bot, subagents_by_id=subagents_by_id, main_llm_provider=main_llm_provider,
    ))

    graph.add_edge(START, 'plan')
    graph.add_conditional_edges('plan', _dispatch, ['research', 'answer'])
    graph.add_edge('research', 'merge')
    graph.add_conditional_edges('merge', _dispatch, ['research', 'answer'])
    graph.add_edge('answer', END)
    return graph.compile()


@dataclass(frozen=True)
class GraphOutcome:
    """`run_graph`'s own return shape -- everything app/services/chat.py's
    `_run_multi_agent_turn_blocking`/`_stream_deferred_multi_agent` need to
    build a `_PreparedTurn`, and nothing more: no internal prompts, no raw
    tool-calling transcript, no per-round intermediate state (rollout plan's
    own "internal prompts/reasoning are never exposed"). `sources` is
    already `[]` whenever `guard_triggered` is True, mirroring
    `_run_agent_turn_blocking`'s own identical convention."""

    final_answer: str
    sources: list[Source]
    guard_triggered: bool
    agent_trace: AgentTrace


def run_graph(
    bot: BotConfig,
    message: str,
    history: list[dict[str, str]],
    available: list[AvailableAgent],
    subagents_by_id: dict[str, SubagentConfig],
    subagent_llm_providers: dict[str, llm_service.LLMProvider],
    main_llm_provider: llm_service.LLMProvider,
    *,
    cancel: threading.Event | None = None,
) -> GraphOutcome:
    """Build and run this turn's own graph, once, and reduce its final state
    to a `GraphOutcome`. `RunnableConfig(max_concurrency=bot.agent.limits.
    max_parallel)` is what actually bounds how many `research` workers a
    single fan-out round may run at once -- LangGraph's own `Send`
    scheduling honors it directly (see this module's own docstring), this
    function does no concurrency bookkeeping of its own.

    `cancel` (optional): forwarded to every `research` worker AND checked
    by `merge` before starting a follow-up round -- see `_research`'s own
    docstring. `None` (the default, e.g. `handle_chat`'s own blocking path,
    which has no external disconnect signal to forward) means only this
    turn's own `bot.agent.limits.timeout_seconds` deadline can ever stop
    the graph early.
    """
    limits = bot.agent.limits
    deadline = time.monotonic() + max(1, limits.timeout_seconds)
    compiled = _build_graph(bot, subagents_by_id, subagent_llm_providers, main_llm_provider, cancel)

    initial_state: GraphState = {
        'question': message,
        'history': history,
        'available': available,
        'plan': [],
        'plan_history': [],
        'results': [],
        'followups_used': 0,
        'budget': {'searches_left': max(0, limits.budget_searches), 'deadline': deadline},
        'final_answer': None,
        'guard_triggered': False,
    }
    final_state = compiled.invoke(
        initial_state, config=RunnableConfig(max_concurrency=max(1, limits.max_parallel))
    )

    results: list[agents_service.SubagentResult] = final_state['results']
    subagent_traces = [
        SubagentTrace(id=result.agent_id, status=result.status, searches_used=result.searches_used, hits=len(result.hits))
        for result in _final_results_per_agent(results).values()
    ]
    budget_used = max(0, limits.budget_searches - int(final_state['budget']['searches_left']))

    from app.services.chat import _to_source  # lazy import -- see this module's own docstring

    guard_triggered = bool(final_state['guard_triggered'])
    sources = [] if guard_triggered else _dedup_sources(results, _to_source)
    trace = AgentTrace(
        mode='graph',
        subagents=subagent_traces,
        budget=limits.budget_searches,
        plan=[dict(item) for item in final_state['plan_history']] or None,
        followups=final_state['followups_used'],
        budget_used=budget_used,
    )
    return GraphOutcome(
        final_answer=final_state['final_answer'] or bot.guard.no_context_reply,
        sources=sources,
        guard_triggered=guard_triggered,
        agent_trace=trace,
    )
