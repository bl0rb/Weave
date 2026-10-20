"""The portable bot document (contracts/bot-document.md) -- a field-for-field
mirror of Weave-Runtime's `BotConfig` and friends (services/runtime/backend/
app/schemas/bot.py), with the same class names, defaults, `Field` bounds,
validators and `extra='forbid'`.

The mirror exists so Weave-Ingest can validate an exported/imported bot
document (app/services/bot_document.py, the admin export/import routes in
app/api/managed_bots.py) without importing Runtime code. Its JSON Schema
must equal the committed contracts/bot-document.schema.json
(tests/test_bot_document_schema.py); any change starts in Runtime's
bot.py, is regenerated there into the contract, and is then repeated here.
Keep the classes free of docstrings: Runtime strips every `description`
from the committed schema, so a docstring here would be the one thing
that breaks parity. Rationale for each field lives in Runtime's bot.py.

`ManagedBot` itself keeps its flat columns; only the `agent` column has
always stored this shape verbatim (the `Agent*` names app/schemas/
managed_bots.py re-exports from here are aliases of the mirror classes).
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

SECRET_PLACEHOLDER = '${WEAVE_SECRET}'
SCHEMA_VERSION = 1

_SLUG_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: str = 'fake'
    model: str
    temperature: float | None = None
    supports_tools: bool | None = None
    endpoint: str | None = None
    endpoints: list[str] = Field(default_factory=list)


class N8nConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    webhook_url: str
    auth_token: SecretStr | None = None
    streaming: bool = False
    timeout_seconds: int = 120


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra='forbid')

    team: str | None = None
    department: str | None = None
    tags: list[str] | None = None
    source: str | None = None
    language: str | None = None
    document_type: str | None = None


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    top_k: int = 20
    final_k: int = 5
    rerank: bool = True
    query_rewrite: bool | None = None
    collections: list[str] = Field(default_factory=list)
    include_uncollected: bool = True


class SubagentLimits(BaseModel):
    model_config = ConfigDict(extra='forbid')

    max_searches: int = 3
    max_results: int = 5
    timeout_seconds: int = 60


class SubagentConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    name: str
    description: str | None = None
    mission: str
    collections: list[str] = Field(default_factory=list)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    include_uncollected: bool = False
    tools: list[str] = Field(default_factory=lambda: ['search_knowledge'])
    model: ModelConfig | None = None
    endpoint: str | None = None
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
    model_config = ConfigDict(extra='forbid')

    max_parallel: int = 3
    max_followups: int = 1
    budget_searches: int = 9
    timeout_seconds: int = 120


class AgentConfig(BaseModel):
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

    # Runtime checks this on BotConfig (`_agent_mode_is_valid`, rule 2); a
    # managed bot's own model is always Runtime's tool-capable 'fake', so
    # the subagent half is the part Ingest can and must check at save time.
    @model_validator(mode='after')
    def validate_subagent_models_support_tools(self) -> 'AgentConfig':
        if not self.enabled:
            return self
        for subagent in self.subagents:
            model = subagent.model
            if model is not None and model.provider != 'fake' and not model.supports_tools:
                raise ValueError(
                    f"subagent {subagent.id!r}'s own model {model.provider!r}/{model.model!r} does not declare "
                    'tool-call support -- set supports_tools: true once the model actually supports tool calling'
                )
        return self


class PermissionsConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    teams: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    public: bool | None = None

    @property
    def is_public(self) -> bool:
        return self.public if self.public is not None else not (self.teams or self.users)


DEFAULT_NO_CONTEXT_REPLY = 'Ich habe dazu keine belegten Informationen gefunden.'
DEFAULT_CLARIFY_QUESTION = (
    'Ich bin mir nicht sicher, worauf sich deine Frage bezieht. Meinst du eines dieser Dokumente?'
)
DEFAULT_CLARIFY_AMBIGUOUS_QUESTION = (
    'Deine Frage passt zu mehreren Dokumenten: {documents}. Welches meinst du?'
)


class GuardConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    require_sources: bool = True
    no_context_reply: str = DEFAULT_NO_CONTEXT_REPLY
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    clarify_below: float | None = Field(default=None, ge=0.0, le=1.0)
    clarify_max_options: int = Field(default=3, ge=1, le=5)
    clarify_question: str = DEFAULT_CLARIFY_QUESTION
    clarify_ambiguous_margin: float | None = Field(default=0.05, ge=0.0, le=1.0)
    clarify_ambiguous_question: str = DEFAULT_CLARIFY_AMBIGUOUS_QUESTION
    uncertainty_hint: bool = True
    weak_context_below: float | None = Field(default=None, ge=0.0, le=1.0)


class BotConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    schema_version: Literal[1] = 1
    id: str
    name: str
    description: str | None = None
    model: ModelConfig
    system_prompt: str
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    n8n: N8nConfig | None = None
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
        return self


BotDocument = BotConfig
