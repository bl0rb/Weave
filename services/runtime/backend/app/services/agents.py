"""Single-subagent knowledge-research loop (rollout plan "Schritt 2 --
Tool-Calls und ein Subagent", step D): `run_subagent` drives ONE
subagent's bounded `search_knowledge`-tool-calling loop and returns a
structured `SubagentResult` -- app/services/chat.py's agent-mode turn is
the one caller, and (per this rollout round's own scope) only ever runs
this function once per turn, for a single chosen subagent; multi-subagent
fan-out (LangGraph orchestrator-worker) is a later rollout step.

Deliberately a pure, dependency-injected function rather than something
that resolves its own `LLMProvider`/Collections scope: every input
(`llm_provider`, `effective_scope`) is already fully resolved by the
caller, which is exactly what makes this module unit-testable end to end
with nothing but `app.services.llm.FakeLLM`'s own scripted
`tool_responses` (see that class's own docstring) and a monkeypatched
`app.services.retrieval_client.search` -- no chat pipeline, no HTTP, no
real LLM in the loop at all.

Argument validation (`_validate_search_knowledge_args`) is the one place
this module enforces the rollout plan's "the model never receives or sets
user identity or permissions as a tool argument" rule: `search_knowledge`'s
own JSON schema (`search_knowledge_tool_schema`) already has no such field
at all, and this validator additionally REJECTS a call that names one
anyway (defense in depth -- a real provider is not guaranteed to enforce
`additionalProperties: false` itself). A validation failure never raises
out of `run_subagent`: it becomes a `role: 'tool'` error result the
subagent's own LLM sees, giving it a bounded chance to correct itself,
exactly like the rollout plan's "invalid arguments produce a tool result
error the model sees (bounded retries), never an exception to the user".
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field

from app.schemas.bot import BotConfig, SubagentConfig
from app.services import llm as llm_service
from app.services import retrieval_client

logger = logging.getLogger(__name__)

SEARCH_KNOWLEDGE_TOOL = 'search_knowledge'

# Fields a well-behaved `search_knowledge` call could never legitimately
# need -- see this module's own docstring. Checked in ADDITION to (never
# instead of) `search_knowledge_tool_schema()`'s own `additionalProperties:
# false`/`required` shape.
_FORBIDDEN_ARG_KEYS = frozenset(
    {'user', 'user_id', 'username', 'team', 'teams', 'permissions', 'scope', 'allowed_collections', 'allowed_teams'}
)
_KNOWN_ARG_KEYS = frozenset({'query', 'collection', 'top_k'})


def search_knowledge_tool_schema() -> dict:
    """The one `tools` entry offered to a subagent's own LLM in this
    rollout round (rollout plan: "initially only knowledge search") -- an
    OpenAI-compatible function-tool schema, passed verbatim to
    `LLMProvider.chat_with_tools` (app/services/llm.py). No identity/
    permission-shaped property exists here at all; see this module's own
    docstring for the enforcement this schema alone cannot guarantee by
    itself.
    """
    return {
        'type': 'function',
        'function': {
            'name': SEARCH_KNOWLEDGE_TOOL,
            'description': (
                'Search the knowledge base for passages relevant to your own research mission. '
                'Returns matching passages together with their document/chunk identifiers.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'The search query text.'},
                    'collection': {
                        'type': 'string',
                        'description': 'Optional: restrict this one search to a single collection slug you are allowed to use.',
                    },
                    'top_k': {
                        'type': 'integer',
                        'description': 'Optional: how many results to return (capped by your own search budget).',
                    },
                },
                'required': ['query'],
                'additionalProperties': False,
            },
        },
    }


class ToolArgumentError(Exception):
    """Raised by `_validate_search_knowledge_args` for arguments that don't
    conform to `search_knowledge`'s own schema (or that try to smuggle an
    identity/permission field) -- always caught inside `run_subagent`'s own
    loop, never propagated; see this module's docstring."""


def _validate_search_knowledge_args(raw_arguments: dict) -> tuple[str, str | None, int | None]:
    if not isinstance(raw_arguments, dict):
        raise ToolArgumentError('arguments must be a JSON object')
    forbidden = _FORBIDDEN_ARG_KEYS.intersection(raw_arguments)
    if forbidden:
        raise ToolArgumentError(f'arguments must not set identity/permission fields: {sorted(forbidden)}')
    extra = set(raw_arguments) - _KNOWN_ARG_KEYS
    if extra:
        raise ToolArgumentError(f'unknown argument(s): {sorted(extra)}')

    query = raw_arguments.get('query')
    if not isinstance(query, str) or not query.strip():
        raise ToolArgumentError("'query' must be a non-empty string")

    collection = raw_arguments.get('collection')
    if collection is not None and not isinstance(collection, str):
        raise ToolArgumentError("'collection' must be a string when set")

    top_k = raw_arguments.get('top_k')
    if top_k is not None and (not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0):
        raise ToolArgumentError("'top_k' must be a positive integer when set")

    return query, collection, top_k


@dataclass(frozen=True)
class SourceRef:
    document_id: str
    chunk_id: int
    collection: str | None = None


@dataclass(frozen=True)
class Fact:
    text: str
    source_refs: list[SourceRef] = field(default_factory=list)


@dataclass(frozen=True)
class SubagentResult:
    """`run_subagent`'s own return shape (rollout plan step D). `status`:
    `'complete'` (the subagent produced its own structured final answer
    within budget), `'partial'` (budget/timeout/attempt-cap exhausted, or
    the LLM itself failed mid-loop, but SOME facts or hits were still
    gathered), `'failed'` (nothing usable came back at all -- treated by
    the caller as incomplete research, never as "no information", per the
    rollout plan's own failure semantics)."""

    agent_id: str
    status: str
    facts: list[Fact]
    open_points: list[str]
    hits: list[retrieval_client.RetrievedChunk]
    searches_used: int


def _parse_subagent_answer(
    text: str, hits_by_ref: dict[tuple[str, int], retrieval_client.RetrievedChunk]
) -> tuple[list[Fact], list[str]]:
    """Parse a subagent's final answer as the JSON object its own system
    prompt (`_SUBAGENT_SYSTEM_PROMPT_TEMPLATE`) asks for: `{"facts":
    [{"text": ..., "source_refs": [{"document_id": ..., "chunk_id": ...}]},
    ...], "open_points": [...]}`. Tolerant of a non-JSON/malformed answer
    (e.g. a scriptless `FakeLLM`'s own plain-text reply in a test) -- falls
    back to one fact carrying the raw text verbatim with no source refs,
    rather than raising: some text is still worth surfacing.

    Every `source_ref` not found in `hits_by_ref` (keyed by
    `(document_id, chunk_id)`, built from every hit ACTUALLY retrieved in
    this loop) is silently dropped -- a fact's own citation is never
    trusted on the model's say-so alone, exactly like app/services/chat.py's
    `_filter_n8n_sources_by_scope` never trusts an external agent's own
    source claims unchecked.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None

    if not isinstance(parsed, dict):
        return ([Fact(text=text)] if text.strip() else []), []

    facts: list[Fact] = []
    for raw_fact in parsed.get('facts') or []:
        if not isinstance(raw_fact, dict):
            continue
        fact_text = raw_fact.get('text')
        if not isinstance(fact_text, str) or not fact_text:
            continue
        refs: list[SourceRef] = []
        for raw_ref in raw_fact.get('source_refs') or []:
            if not isinstance(raw_ref, dict):
                continue
            document_id = raw_ref.get('document_id')
            chunk_id = raw_ref.get('chunk_id')
            if not isinstance(document_id, str) or not isinstance(chunk_id, int):
                continue
            hit = hits_by_ref.get((document_id, chunk_id))
            if hit is None:
                continue
            refs.append(SourceRef(document_id=document_id, chunk_id=chunk_id, collection=hit.collection))
        facts.append(Fact(text=fact_text, source_refs=refs))

    open_points = [point for point in (parsed.get('open_points') or []) if isinstance(point, str) and point]
    return facts, open_points


_SUBAGENT_SYSTEM_PROMPT_TEMPLATE = (
    "You are the research subagent '{name}'. Mission: {mission}\n"
    'Use the search_knowledge tool to gather evidence for the question below, within your own search budget. '
    'When you are done researching -- or your budget is exhausted -- reply with ONLY a JSON object shaped exactly '
    '{{"facts": [{{"text": "...", "source_refs": [{{"document_id": "...", "chunk_id": 0}}]}}], '
    '"open_points": ["..."]}}. No prose, no markdown fences -- that JSON object is your entire final reply.'
)

_BUDGET_EXHAUSTED_PROMPT = (
    'Your research budget is exhausted. Answer now with the JSON object described above, using only what you '
    'already found -- name anything still uncertain under open_points.'
)


def run_subagent(
    bot: BotConfig,
    subagent: SubagentConfig,
    question: str,
    effective_scope: list[str],
    llm_provider: llm_service.LLMProvider,
    *,
    model: str,
    temperature: float | None = None,
    allowed_teams: list[str] | None = None,
    cancel: threading.Event | None = None,
) -> SubagentResult:
    """Run `subagent`'s bounded tool-calling research loop for `question`
    and return a `SubagentResult`.

    `effective_scope` is the CALLER's own already-resolved Collections
    scope for this subagent (app/services/scope.py's `effective_scope()`,
    or app/services/chat.py's own Altbestand-only special case) -- an
    empty list simply means every `search_knowledge` call this loop makes
    returns zero hits (`retrieval_client.search` already treats
    `allowed_collections=[]` this way), not that this function refuses to
    run: whether an empty scope is even worth running a subagent over is
    the CALLER's own decision.

    `cancel` (rollout plan "Schritt 3 -- LangGraph mit mehreren Subagenten"):
    an optional, caller-owned `threading.Event` this loop checks alongside
    its own `deadline` at the very same point (top of the loop, between
    iterations, never mid-call) -- set by app/services/agent_graph.py's
    graph runner on a client disconnect or an overall-graph timeout that
    this ONE subagent's own `timeout_seconds` budget would not otherwise
    have caught yet (a multi-subagent turn's shared wall-clock limit is a
    graph-level concern, not something `SubagentLimits` alone expresses).
    Treated exactly like `deadline` exhaustion: the loop stops taking any
    further action and returns whatever facts/hits were already gathered
    (`_finish_on_budget_exhaustion`) rather than raising. `None` (the
    default) preserves this function's original, pre-`Schritt 3` behavior
    byte-for-byte for every caller that never passes one.

    `allowed_teams` is the CALLER's own already-resolved team-rights axis
    (app/services/chat.py's `_allowed_teams(bot, user)`) -- passed through
    to every `retrieval_client.search()` call this loop makes, exactly like
    `_run_knowledge_turn`'s own direct-RAG call. `Document.team` is a
    separate SQL column from `Document.collection_slug` (services/
    retrieval's `apply_filters()`), so Collections scope alone (`effective_
    scope` above) never substitutes for it: leaving this `None` means
    "unrestricted", per `retrieval_client.search()`'s own docstring, so a
    subagent must never search with `allowed_teams=None` on behalf of a
    real caller.

    Budget, from `subagent.limits` (app/schemas/bot.py): at most
    `max_searches` real `search_knowledge` calls are ever executed -- a
    tool call received once that budget is spent gets a `role: 'tool'`
    error result explaining as much (so the model can still answer)
    instead of one more real search. `max_results` caps `top_k`/`final_k`
    for every call (a model-requested `top_k` above it is clamped, never
    rejected as an argument error). `timeout_seconds` bounds this
    function's OWN wall-clock time, checked between loop iterations (never
    mid-call); once exceeded the loop stops with `status='partial'` and
    whatever facts/hits were already gathered.

    Never raises for an ordinary tool-argument/search failure: invalid
    arguments and a failed `retrieval_client.search()` call each become
    one more `role: 'tool'` error result the model sees, still counted
    against the search budget so a persistently misbehaving model or a
    persistently failing search still terminates the loop rather than
    spinning forever. An `llm_service.LLMError` from `chat_with_tools`
    itself ends the loop immediately with `status='partial'`/`'failed'`
    (per this dataclass's own docstring) -- a genuinely broken LLM call is
    not something one more loop iteration could plausibly recover from.
    """
    limits = subagent.limits
    max_searches = max(0, limits.max_searches)
    max_results = max(1, limits.max_results)
    deadline = time.monotonic() + max(1, limits.timeout_seconds)

    # Merge bot-level and subagent-level metadata filters -- the rollout
    # plan's "team limits and extra search filters keep applying" applies
    # to a bot-level pin (e.g. `bot.retrieval.filters.department`) exactly
    # as much as to a subagent's own `filters`, so neither is dropped here.
    # Subagent fields win on overlap (they only ever narrow further, never
    # widen, per `SubagentConfig`'s own docstring), mirroring how Collections
    # scope already combines the bot's and the subagent's own axes.
    filters = {
        **bot.retrieval.filters.model_dump(exclude_none=True),
        **subagent.filters.model_dump(exclude_none=True),
    } or None
    tools = [search_knowledge_tool_schema()]

    messages: list[dict] = [
        {
            'role': 'system',
            'content': _SUBAGENT_SYSTEM_PROMPT_TEMPLATE.format(name=subagent.name, mission=subagent.mission),
        },
        {'role': 'user', 'content': question},
    ]

    hits: list[retrieval_client.RetrievedChunk] = []
    hits_by_ref: dict[tuple[str, int], retrieval_client.RetrievedChunk] = {}
    searches_used = 0
    attempts = 0
    # A generous but finite cap on total loop iterations regardless of the
    # tool-call/error mix -- a model that keeps sending invalid arguments
    # (never consuming the real search budget) must still terminate.
    max_attempts = max(1, max_searches) * 2 + 2

    while True:
        if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
            return _finish_on_budget_exhaustion(
                llm_provider, messages, hits, hits_by_ref, searches_used, subagent.id, model, temperature
            )
        attempts += 1
        if attempts > max_attempts:
            return _finish_on_budget_exhaustion(
                llm_provider, messages, hits, hits_by_ref, searches_used, subagent.id, model, temperature
            )

        try:
            result = llm_provider.chat_with_tools(messages, tools=tools, model=model, temperature=temperature)
        except llm_service.LLMError as exc:
            logger.warning('subagent %r LLM call failed: %s', subagent.id, exc)
            status = 'partial' if (hits or searches_used) else 'failed'
            return SubagentResult(
                agent_id=subagent.id, status=status, facts=[], open_points=[], hits=hits, searches_used=searches_used
            )

        if not result.tool_calls:
            facts, open_points = _parse_subagent_answer(result.content or '', hits_by_ref)
            return SubagentResult(
                agent_id=subagent.id, status='complete', facts=facts, open_points=open_points,
                hits=hits, searches_used=searches_used,
            )

        messages.append({
            'role': 'assistant',
            'content': result.content or '',
            'tool_calls': [
                {
                    'id': call.id,
                    'type': 'function',
                    'function': {'name': call.name, 'arguments': json.dumps(call.arguments)},
                }
                for call in result.tool_calls
            ],
        })

        for index, call in enumerate(result.tool_calls):
            if call.name != SEARCH_KNOWLEDGE_TOOL:
                messages.append({
                    'role': 'tool', 'tool_call_id': call.id,
                    'content': json.dumps({'error': f'unknown tool {call.name!r}'}),
                })
                continue
            if index > 0:
                # Only the first tool call in a single LLM turn is executed
                # this rollout round -- see this module's own docstring on
                # single-tool, single-call-per-turn scope.
                messages.append({
                    'role': 'tool', 'tool_call_id': call.id,
                    'content': json.dumps({'error': 'only one tool call is processed per turn; call again next turn'}),
                })
                continue
            if searches_used >= max_searches:
                messages.append({
                    'role': 'tool', 'tool_call_id': call.id,
                    'content': json.dumps({'error': 'search budget exhausted; answer with what you already have'}),
                })
                continue

            try:
                query, collection, requested_top_k = _validate_search_knowledge_args(call.arguments)
            except ToolArgumentError as exc:
                searches_used += 1  # counts against the budget -- see this function's own docstring
                messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': json.dumps({'error': str(exc)})})
                continue

            call_collections = effective_scope
            if collection is not None:
                call_collections = [slug for slug in effective_scope if slug == collection]

            top_k = min(requested_top_k, max_results) if requested_top_k else max_results
            searches_used += 1
            try:
                chunks = retrieval_client.search(
                    query=query, filters=filters, allowed_teams=allowed_teams,
                    allowed_collections=call_collections, top_k=top_k, final_k=top_k,
                )
            except (retrieval_client.RetrievalUnavailable, retrieval_client.RetrievalError) as exc:
                logger.warning('subagent %r search_knowledge failed: %s', subagent.id, exc)
                messages.append({
                    'role': 'tool', 'tool_call_id': call.id,
                    'content': json.dumps({'error': 'search temporarily unavailable'}),
                })
                continue

            for chunk in chunks:
                key = (chunk.document_id, chunk.chunk_id)
                if key not in hits_by_ref:
                    hits.append(chunk)
                    hits_by_ref[key] = chunk

            tool_payload = {
                'results': [
                    {
                        'text': chunk.text,
                        'document_id': chunk.document_id,
                        'chunk_id': chunk.chunk_id,
                        'collection': chunk.collection,
                    }
                    for chunk in chunks
                ]
            }
            messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': json.dumps(tool_payload)})


def _finish_on_budget_exhaustion(
    llm_provider: llm_service.LLMProvider,
    messages: list[dict],
    hits: list[retrieval_client.RetrievedChunk],
    hits_by_ref: dict[tuple[str, int], retrieval_client.RetrievedChunk],
    searches_used: int,
    agent_id: str,
    model: str,
    temperature: float | None,
) -> SubagentResult:
    """Ask the subagent's own LLM ONE more time -- with no tools offered,
    so it cannot keep researching past its own exhausted budget -- to
    summarize whatever it already found into the same structured shape,
    rather than returning an empty result outright the moment budget/
    timeout/attempt-cap is hit."""
    try:
        final = llm_provider.chat_with_tools(
            messages + [{'role': 'user', 'content': _BUDGET_EXHAUSTED_PROMPT}],
            tools=[], model=model, temperature=temperature,
        )
        facts, open_points = _parse_subagent_answer(final.content or '', hits_by_ref)
    except llm_service.LLMError:
        facts, open_points = [], []

    status = 'failed' if not facts and not hits else 'partial'
    return SubagentResult(
        agent_id=agent_id, status=status, facts=facts, open_points=open_points, hits=hits, searches_used=searches_used
    )
