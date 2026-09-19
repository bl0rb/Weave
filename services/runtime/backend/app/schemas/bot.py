"""Pydantic schema for a bot's YAML configuration file (see bots/*.yaml,
app/services/botconfig.py) plus the trimmed shape exposed by
GET /internal/bots (BotSummary/BotRetrievalSummary at the bottom).

`extra='forbid'` on every model here is deliberate: a bot's YAML is
hand-authored by an operator, not machine-generated, so a stray/misspelled
key (e.g. `desciption:`) is far more likely to be a typo silently doing
nothing than an intentional forward-compatible extension -- better to fail
BotConfig validation loudly (see app/services/botconfig.py's
`BotConfigError`, which names the offending file) than to load a bot that
quietly ignores half its author's intent.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

# Lowercase letters/digits, hyphen-separated -- the same shape a URL path
# segment or a Kubernetes resource name would require, since `id` doubles as
# both this bot's public identifier (ChatRequest.bot_id, see
# app/schemas/chat.py) and, by convention, its YAML filename's stem (e.g.
# 'legal-support' -> bots/legal-support.yaml, though app/services/botconfig.py
# matches on this field, never the filename itself).
_SLUG_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    # 'fake' mirrors settings.llm_provider's own default (app/core/config.py)
    # so a bot YAML with no `model:` section at all -- or one that only sets
    # `model.model` -- still loads and runs against the dependency-free fake
    # provider in dev/tests, never requiring a real LLM API key just to
    # validate the bot roster.
    provider: str = 'fake'
    model: str
    temperature: float | None = None

    # Whether THIS model actually supports OpenAI-compatible tool calling
    # (`app/services/llm.py`'s `chat_with_tools`) -- required, hand-declared
    # configuration, not something this service probes at runtime (a
    # provider's error mode for an unsupported `tools` field on the request
    # varies -- some ignore it silently, some 400 -- so probing it cheaply/
    # safely before every agent-mode turn is not possible; see
    # `BotConfig`'s own `_agent_requires_tool_support` validator for where
    # this is actually enforced). `None` (the default) means "unknown",
    # deliberately treated as "does NOT support tool calls" wherever it
    # matters (that same validator) -- an operator must explicitly opt a
    # model INTO agent mode, never have it silently assumed. The 'fake'
    # provider is the one deliberate exception: `FakeLLM` always implements
    # `chat_with_tools` (app/services/llm.py), so this field is never even
    # consulted for it -- see that validator's own docstring.
    supports_tools: bool | None = None


class N8nConfig(BaseModel):
    """Configuration for the 'n8n' bot provider (see app/services/chat.py's
    `_run_n8n_turn`, app/services/n8n_client.py, contracts/n8n-flow.md) --
    required exactly when `ModelConfig.provider == 'n8n'`, and forbidden
    otherwise (`BotConfig`'s own `_n8n_block_matches_provider` validator
    below enforces both directions).

    `webhook_url` is hand-authored YAML content -- configuration, not
    end-user input -- but it is still checked against
    `settings.n8n_allowed_base_urls` (an exact scheme/host/port plus path-boundary allowlist,
    see that setting's own docstring in app/core/config.py) once, at BOT
    LOAD time (app/services/botconfig.py), not deferred to the first actual
    webhook call: the URL living in a YAML file is what makes it
    configuration in the first place, but that alone must never be enough
    to let this service call an arbitrary network address just because some
    operator's bot file names one.
    """

    model_config = ConfigDict(extra='forbid')

    webhook_url: str
    # Optional second factor for n8n installations protected by a bearer
    # token.  SecretStr keeps accidental repr/log output redacted; the
    # control plane stores the value encrypted and only Runtime receives it.
    auth_token: SecretStr | None = None
    # When enabled, n8n responds with the SSE event contract documented in
    # contracts/n8n-flow.md. Source-required bots are buffered until their
    # evidence is known so streaming can never bypass the response guard.
    streaming: bool = False
    # Passed as this bot's own httpx timeout for the (never-retried, see
    # app/services/n8n_client.py's own docstring) webhook call -- an n8n
    # agent flow doing real tool/search work is expected to take
    # meaningfully longer than a single LLM completion, hence the much
    # longer default than settings.llm_timeout_seconds' own 60s.
    timeout_seconds: int = 120


class RetrievalFilters(BaseModel):
    """Metadata filters applied to a bot's retrieval calls -- the same
    filter surface Weave-Retrieval's own SearchFilters exposes
    (app/schemas/search.py in that service), reused here so a bot's YAML can
    pin retrieval to e.g. one department without a caller having to specify
    filters on every ChatRequest itself."""

    model_config = ConfigDict(extra='forbid')

    team: str | None = None
    department: str | None = None
    tags: list[str] | None = None
    source: str | None = None
    language: str | None = None
    document_type: str | None = None


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    # Off by default: a bot only gets Weave-Retrieval wired into its chat
    # pipeline if its YAML explicitly opts in, e.g. general-assistant.yaml
    # deliberately leaves this False (see bots/general-assistant.yaml).
    enabled: bool = False
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    # Candidates pulled from Weave-Retrieval before reranking; results
    # actually handed to the LLM/returned as sources afterwards. Same
    # defaults and same relationship as Weave-Retrieval's own
    # SEARCH_TOP_K/SEARCH_FINAL_K (app/core/config.py in that service).
    top_k: int = 20
    final_k: int = 5
    rerank: bool = True

    # Which Collections (the cross-service Collections contract -- see
    # Weave-Knowledge's `collections` registry table, synced from
    # Weave-Ingest, and Weave-Retrieval's own GET /api/v1/collections) this
    # bot may search at all. app/services/chat.py's `resolve_collection_scope`
    # intersects this list against whatever the CALLING USER may actually
    # read (`retrieval_client.list_collections`) before ever calling
    # `retrieval_client.search()` -- see that function's own docstring.
    #
    # Empty (the default) means "every collection the calling user is
    # allowed to read" -- deliberately NOT "no Collections restriction at
    # all" the way an empty `PermissionsConfig.teams` means "every team may
    # use this bot": Weave-Retrieval's own `SearchRequest.allowed_collections
    # = None` (genuinely unrestricted, bypassing the Collections boundary
    # entirely) is reserved for service-internal callers, never something
    # Weave-Runtime forwards on behalf of one end user's chat turn.
    collections: list[str] = Field(default_factory=list)

    # Whether documents that predate the Collections feature altogether
    # (`Document.collection_slug IS NULL` -- Weave-Retrieval's own
    # NO_COLLECTION_SENTINEL / apply_filters() docstring,
    # app/services/search.py in that service) stay visible to this bot's
    # retrieval calls, ON TOP OF whatever `collections` above resolves to.
    #
    # True (the default): legacy/uncollected documents remain visible --
    # "Altbestand bleibt sichtbar" -- preserving exactly the retrieval
    # behavior this pipeline had BEFORE Collections existed, when every
    # document was necessarily uncollected. This default is deliberate, not
    # merely convenient: the moment an operator creates the very FIRST
    # collection and tags a handful of documents into it, every OTHER
    # already-indexed document (the entire pre-Collections corpus) would
    # otherwise silently stop being findable by ANY retrieval-backed bot --
    # a real, contract-breaking data-loss regression no bot's YAML ever
    # opted into just by a collection existing somewhere else in the
    # system. See app/services/chat.py's `resolve_collection_scope` for how
    # this flag is actually applied (appending Weave-Retrieval's
    # NO_COLLECTION_SENTINEL, `'__none__'`, to the resolved scope it sends
    # on as `SearchRequest.allowed_collections`).
    #
    # False: strict Collections-only visibility -- only documents explicitly
    # tagged into a collection this bot may search are ever returned. Set
    # this once an operator has deliberately finished migrating a bot's
    # entire corpus into Collections and wants pre-migration stragglers to
    # stay hidden rather than silently included.
    include_uncollected: bool = True


class SubagentLimits(BaseModel):
    """Per-subagent research budget (rollout plan step 1/3) -- enforced by
    `app/services/agents.py`'s `run_subagent`, never here: this model only
    carries the numbers, it has no access to a running turn to bound."""

    model_config = ConfigDict(extra='forbid')

    # How many `search_knowledge` tool calls this ONE subagent may actually
    # execute in a single turn -- a call received after this many have
    # already run gets a budget-exhausted error result instead of a real
    # search (see `run_subagent`'s own docstring).
    max_searches: int = 3
    # Caps `top_k`/`final_k` for every one of this subagent's own
    # `search_knowledge` calls -- a model-requested `top_k` above this is
    # clamped, never rejected as an argument-shape error.
    max_results: int = 5
    # Wall-clock budget for this ONE subagent's entire research loop
    # (checked between loop iterations, never mid-call) -- exceeding it
    # ends the loop with whatever facts/hits were already gathered
    # (`SubagentResult.status = 'partial'`), never an exception.
    timeout_seconds: int = 60


class SubagentConfig(BaseModel):
    """One research profile inside an agent-mode bot's roster (rollout
    plan's "Subagents are research profiles managed inside the main bot") --
    NOT a separate bot, NOT a separate identity: a subagent inherits the
    asking user's own rights entirely (see app/services/scope.py's
    `effective_scope`), it only ever NARROWS what the main bot itself may
    already search, both by `collections` and, optionally, by its own
    `filters`/`model`/`limits`.
    """

    model_config = ConfigDict(extra='forbid')

    # Unique within one bot's own `agent.subagents` list (see AgentConfig's
    # own `_unique_subagent_ids` validator) -- addressed by the main
    # agent's own `research_area(agent_id, question)` tool call (rollout
    # plan step 3), never exposed to the model as anything richer than this
    # slug plus `description`.
    id: str
    name: str
    description: str | None = None
    # Fachlicher Auftrag -- the German-language mission statement handed to
    # this subagent's own system prompt (`app/services/agents.py`'s
    # `run_subagent`) so it stays inside its intended research scope even
    # though nothing on the wire could ever ENFORCE that beyond the
    # Collections boundary below (a model instruction is guidance, not a
    # permission boundary -- `collections`/`filters` are the only things
    # here that actually are one).
    mission: str
    # Explicitly allowed Collections for THIS subagent -- narrows, never
    # widens, whatever the main bot's own `RetrievalConfig.collections`
    # already resolved to for the asking user (see app/services/scope.py's
    # `effective_scope`). Empty is legitimate ONLY alongside
    # `include_uncollected=True` (see `_collections_or_uncollected` below)
    # -- unlike `RetrievalConfig.collections`'s own "empty = no restriction"
    # convention, a subagent's empty `collections` never means "inherit
    # everything": the whole point of "explicitly allowed collections" per
    # the rollout plan is that a subagent's own reach is always named, not
    # defaulted.
    collections: list[str] = Field(default_factory=list)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    # Whether documents with NO collection at all (pre-Collections legacy
    # content, `NO_COLLECTION_SENTINEL`) stay visible to THIS subagent, on
    # top of `collections` -- combined with the main bot's own
    # `RetrievalConfig.include_uncollected` by AND (see
    # app/services/chat.py's `_resolve_agent_scope`): both must allow it.
    # Defaults to `False`, unlike `RetrievalConfig.include_uncollected`'s
    # own default of `True` -- a subagent's reach must be named explicitly,
    # including whether Altbestand is part of that reach at all.
    include_uncollected: bool = False
    # Which tools this subagent may call -- a plain allow-list of tool
    # NAMES, not JSON schemas (those live in app/services/agents.py, next
    # to the one function that actually builds/executes them). Only
    # 'search_knowledge' exists in this rollout round; the field is a list
    # already so a later rollout round can add more without a schema
    # migration.
    tools: list[str] = Field(default_factory=lambda: ['search_knowledge'])
    # Falls back to the main bot's own `BotConfig.model` when unset (see
    # app/services/chat.py's own `subagent.model or bot.model` pattern) --
    # a subagent researching with a smaller/cheaper model than the main
    # bot's own answer-writing model is a common, deliberate choice this
    # leaves open without requiring every subagent to repeat the main
    # bot's model configuration verbatim.
    model: ModelConfig | None = None
    limits: SubagentLimits = Field(default_factory=SubagentLimits)

    @field_validator('id')
    @classmethod
    def _id_must_be_a_slug(cls, value: str) -> str:
        if not _SLUG_PATTERN.match(value):
            raise ValueError(
                f"'{value}' is not a valid subagent id: expected a slug of lowercase letters, digits and "
                "hyphens (e.g. 'it-support')"
            )
        return value

    @model_validator(mode='after')
    def _collections_or_uncollected(self) -> 'SubagentConfig':
        if not self.collections and not self.include_uncollected:
            raise ValueError(
                f"subagent {self.id!r} must set at least one collection, or include_uncollected: true for an "
                "Altbestand-only research profile -- a subagent's reach is never defaulted to 'everything'"
            )
        return self


class AgentLimits(BaseModel):
    """Turn-wide agent-mode budget, on top of each subagent's own
    `SubagentLimits` -- rollout plan step 4's "Start values" (this rollout
    round only actually enforces `timeout_seconds`/`max_searches`-shaped
    numbers via ONE subagent, see app/services/chat.py's own agent-turn
    docstring; `max_parallel`/`max_followups`/`budget_searches` are carried
    here now so a bot's YAML already declares its full intended budget
    ahead of the later rollout step -- LangGraph fan-out -- that actually
    spends `max_parallel`/`max_followups`, and so `budget_searches` is
    already available as this turn's own trace/reporting figure)."""

    model_config = ConfigDict(extra='forbid')

    max_parallel: int = 3
    max_followups: int = 1
    budget_searches: int = 9
    timeout_seconds: int = 120


class AgentConfig(BaseModel):
    """`BotConfig.agent` -- opts one LLM bot into agent mode (rollout plan's
    "Aktivierung ist pro Bot"). `enabled=False` (the default) keeps the
    existing direct-RAG turn byte-for-byte unchanged for every bot that
    doesn't set this block at all, or sets it with `enabled: false` --
    exactly like `N8nConfig`'s own presence never changes anything for a
    bot whose provider isn't 'n8n'.
    """

    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    subagents: list[SubagentConfig] = Field(default_factory=list)
    limits: AgentLimits = Field(default_factory=AgentLimits)

    @model_validator(mode='after')
    def _enabled_requires_subagents(self) -> 'AgentConfig':
        if self.enabled and not self.subagents:
            raise ValueError('agent.enabled is true but agent.subagents is empty -- at least one subagent is required')
        return self

    @model_validator(mode='after')
    def _unique_subagent_ids(self) -> 'AgentConfig':
        ids = [subagent.id for subagent in self.subagents]
        duplicates = sorted({sid for sid in ids if ids.count(sid) > 1})
        if duplicates:
            raise ValueError(f'agent.subagents ids must be unique -- duplicated: {duplicates}')
        return self


class PermissionsConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    # Teams whose members may use this bot (matched against ChatRequest.user.team,
    # see app/schemas/chat.py) -- an empty list means every team may, NOT
    # "no team may"; that's why general-assistant.yaml leaves this empty
    # while legal-support.yaml restricts it to ['legal', 'management'].
    teams: list[str] = Field(default_factory=list)


# German because it is a user-facing string, unlike everything else on this
# page -- see the module docstrings in app/main.py/app/core/auth.py for why
# code and comments stay English while user-visible copy does not.
_DEFAULT_NO_CONTEXT_REPLY = 'Ich habe dazu keine belegten Informationen gefunden.'


class GuardConfig(BaseModel):
    """The response guard (see README's "Zweck") that keeps a
    retrieval-backed bot from answering as if it had sources when it
    actually found none."""

    model_config = ConfigDict(extra='forbid')

    # When True (the default) and retrieval.enabled found nothing usable,
    # the chat pipeline must return `no_context_reply` verbatim instead of
    # letting the LLM improvise an unsourced answer -- see ChatTrace.guard
    # (app/schemas/chat.py) for how a caller can tell this happened.
    require_sources: bool = True
    no_context_reply: str = _DEFAULT_NO_CONTEXT_REPLY


class BotConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    name: str
    description: str | None = None
    model: ModelConfig
    system_prompt: str
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    # None (the default) for every ordinary bot -- required exactly when
    # `model.provider == 'n8n'`, see `_n8n_block_matches_provider` below and
    # N8nConfig's own docstring.
    n8n: N8nConfig | None = None
    # None (the default) for every bot without agent mode -- see
    # AgentConfig's own docstring, and `_agent_requires_tool_support`/
    # `_agent_forbidden_for_n8n_provider` below for the two structural
    # rules this field's presence is checked against.
    agent: AgentConfig | None = None

    @field_validator('id')
    @classmethod
    def _id_must_be_a_slug(cls, value: str) -> str:
        if not _SLUG_PATTERN.match(value):
            raise ValueError(
                f"'{value}' is not a valid bot id: expected a slug of lowercase letters, digits and "
                "hyphens (e.g. 'legal-support')"
            )
        return value

    @model_validator(mode='after')
    def _n8n_block_matches_provider(self) -> 'BotConfig':
        """`model.provider == 'n8n'` and the presence of an `n8n:` block are
        two spellings of the same fact -- either both hold or neither does.
        Checked here (a cross-field, settings-independent structural rule)
        rather than in app/services/botconfig.py, unlike the SSRF allowlist
        check for `n8n.webhook_url` itself (that one genuinely needs
        `settings.n8n_allowed_base_urls`, which this schema module
        deliberately never imports -- see app/services/botconfig.py's own
        webhook-allowlist check for that half). Both directions are user
        errors worth naming plainly: `provider: n8n` with no `n8n:` block
        has no webhook to call at all, and an `n8n:` block on a bot whose
        provider ISN'T `n8n` is dead configuration nobody's pipeline will
        ever read (app/services/chat.py's `_run_n8n_turn` is only ever
        reached via `bot.model.provider == 'n8n'`).
        """
        is_n8n_provider = self.model.provider == 'n8n'
        has_n8n_block = self.n8n is not None
        if is_n8n_provider and not has_n8n_block:
            raise ValueError(
                "model.provider is 'n8n' but no 'n8n:' block is configured (an 'n8n:' block with at least "
                "'webhook_url' is required whenever model.provider is 'n8n')"
            )
        if has_n8n_block and not is_n8n_provider:
            raise ValueError(
                f"an 'n8n:' block is configured but model.provider is {self.model.provider!r}, not 'n8n' -- "
                "either set model.provider to 'n8n' or remove the 'n8n:' block"
            )
        return self

    @model_validator(mode='after')
    def _agent_mode_is_valid(self) -> 'BotConfig':
        """Two structural rules for `agent.enabled` (rollout plan step 3's
        "Aktivierung ist pro Bot", "nur fuer provider != 'n8n'", "vor
        Aktivierung pruefen, dass das konfigurierte Modell Tool-Calls
        unterstuetzt"):

        1. Agent mode is never valid for an n8n-provider bot: n8n runs its
           OWN agent flow (contracts/n8n-flow.md, `_run_n8n_turn`) entirely
           outside this pipeline's own LLM-tool-calling loop -- the two are
           mutually exclusive ways for one bot to do agentic work, never
           combined.
        2. `self.model` must actually declare tool-call support -- `'fake'`
           unconditionally does (see `ModelConfig.supports_tools`'s own
           docstring: `FakeLLM` always implements `chat_with_tools`,
           app/services/llm.py), any other provider only when
           `model.supports_tools` is explicitly `True` (`None`/`False`
           both mean "unsupported" here, exactly `supports_tools`'s own
           documented default). This is a LOAD-TIME check against the
           bot's own hand-authored configuration -- see that field's own
           docstring for why this service never probes a live provider for
           this fact instead. The same rule applies to every subagent's own
           optional `model` override (`SubagentConfig.model`, actually used
           via `subagent.model or bot.model` in app/services/chat.py's
           `_run_subagent`) -- a subagent left with no override inherits
           the main bot's already-checked model, but one configured with
           its own model needs the identical check, or it silently answers
           with zero tool calls at runtime instead of failing at load time.
        """
        if self.agent is None or not self.agent.enabled:
            return self
        if self.model.provider == 'n8n':
            raise ValueError("agent.enabled is true but model.provider is 'n8n' -- n8n bots run their own agent flow")
        provider_supports_tools = self.model.provider == 'fake' or bool(self.model.supports_tools)
        if not provider_supports_tools:
            raise ValueError(
                f"agent.enabled is true but model {self.model.provider!r}/{self.model.model!r} does not declare "
                "tool-call support -- set model.supports_tools: true once the configured model/deployment actually "
                "supports OpenAI-compatible tool calling"
            )
        for subagent in self.agent.subagents:
            if subagent.model is None:
                continue
            subagent_supports_tools = subagent.model.provider == 'fake' or bool(subagent.model.supports_tools)
            if not subagent_supports_tools:
                raise ValueError(
                    f"agent.enabled is true but subagent {subagent.id!r}'s own model "
                    f"{subagent.model.provider!r}/{subagent.model.model!r} does not declare tool-call support -- "
                    "set model.supports_tools: true once the configured model/deployment actually supports "
                    "OpenAI-compatible tool calling"
                )
        return self


class BotRetrievalSummary(BaseModel):
    """Just the one retrieval fact GET /internal/bots exposes -- whether the
    bot is retrieval-backed at all. Everything else on RetrievalConfig
    (filters, top_k, rerank, ...) is internal tuning a caller listing the
    bot roster (Weave-API's gateway, deciding which bot a user may pick) has
    no need to see."""

    enabled: bool


class BotSummary(BaseModel):
    """The response shape for GET /internal/bots (see
    app/api/internal.py) -- deliberately NOT the full BotConfig: a bot's
    `system_prompt` and retrieval `filters` are implementation detail no
    caller listing the roster needs, and leaking them over an internal
    listing endpoint serves no purpose."""

    id: str
    name: str
    description: str | None = None
    retrieval: BotRetrievalSummary
    kind: str = 'llm'
    teams: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
