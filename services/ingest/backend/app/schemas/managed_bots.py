"""Schemas for the admin-managed n8n bot control plane."""

import re
from datetime import datetime
from urllib.parse import urlsplit

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.models import BotRole


_BOT_ID_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_UNSAFE_URL_CHARS = re.compile(r'[\\\x00-\x1f\x7f]')
_SLUG_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


class AgentSubagentFilters(BaseModel):
    """Mirrors Weave-Runtime's `RetrievalFilters` (app/schemas/bot.py) --
    field names and defaults kept identical on purpose so a saved payload
    here is byte-for-byte what Runtime's own `AgentConfig` will accept when
    it next loads this bot."""

    model_config = ConfigDict(extra='forbid')

    team: str | None = None
    department: str | None = None
    tags: list[str] | None = None
    source: str | None = None
    language: str | None = None
    document_type: str | None = None


class AgentSubagentModel(BaseModel):
    """Mirrors Weave-Runtime's `ModelConfig` (app/schemas/bot.py) -- a
    subagent's own model override. `supports_tools` only matters for a
    non-'fake' provider; 'unknown' (`None`) means 'assume no tool support',
    exactly like Runtime -- see AgentConfig.validate_subagent_models_support_tools."""

    model_config = ConfigDict(extra='forbid')

    provider: str = 'fake'
    model: str = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    supports_tools: bool | None = None


class AgentSubagentLimits(BaseModel):
    """Mirrors Weave-Runtime's `SubagentLimits` (app/schemas/bot.py),
    including its defaults -- rollout plan "Schritt 4" start values."""

    model_config = ConfigDict(extra='forbid')

    max_searches: int = Field(default=3, ge=1, le=50)
    max_results: int = Field(default=5, ge=1, le=50)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)


class AgentSubagent(BaseModel):
    """Mirrors Weave-Runtime's `SubagentConfig` (app/schemas/bot.py) --
    field names, defaults and the two cross-field rules below (unique id
    shape, collections-or-uncollected) are kept identical to that model on
    purpose, so a shape this validator accepts is guaranteed to be one
    Runtime's own loader will accept too, and a shape it rejects gives a
    readable 422 here instead of only surfacing as a Runtime-side bot-load
    failure the next time this bot's turn runs."""

    model_config = ConfigDict(extra='forbid')

    id: str
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    mission: str = Field(min_length=1, max_length=4000)
    collections: list[str] = Field(default_factory=list, max_length=500)
    filters: AgentSubagentFilters = Field(default_factory=AgentSubagentFilters)
    include_uncollected: bool = False
    tools: list[str] = Field(default_factory=lambda: ['search_knowledge'])
    model: AgentSubagentModel | None = None
    # Central LLM endpoint for this subagent (Runtime's `SubagentConfig.endpoint`);
    # existence and tool support are checked against the database on save.
    endpoint: str | None = Field(default=None, max_length=36)
    limits: AgentSubagentLimits = Field(default_factory=AgentSubagentLimits)

    @field_validator('id')
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(
                f"'{value}' is not a valid subagent id: expected lowercase letters, digits and hyphens "
                "(e.g. 'it-support')"
            )
        return value

    @model_validator(mode='after')
    def validate_collections_or_uncollected(self) -> 'AgentSubagent':
        if not self.collections and not self.include_uncollected:
            raise ValueError(
                f"subagent {self.id!r} must set at least one collection, or include_uncollected: true"
            )
        return self


class AgentLimits(BaseModel):
    """Mirrors Weave-Runtime's `AgentLimits` (app/schemas/bot.py),
    including its defaults -- rollout plan "Schritt 4" start values
    (3/1/9/120)."""

    model_config = ConfigDict(extra='forbid')

    max_parallel: int = Field(default=3, ge=1, le=20)
    max_followups: int = Field(default=1, ge=0, le=10)
    budget_searches: int = Field(default=9, ge=0, le=500)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)


class AgentConfig(BaseModel):
    """Mirrors Weave-Runtime's `AgentConfig` (app/schemas/bot.py) --
    `BotConfig.agent`'s exact shape, so an admin-saved agent-mode
    configuration is validated here, with a readable 422 detail on a
    malformed shape, instead of only failing silently as an opaque dict
    that Runtime rejects at its own next bot-load. Field names/defaults
    are kept identical to Runtime's own model on purpose (see that
    module's own docstring for the reasoning behind each one)."""

    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    subagents: list[AgentSubagent] = Field(default_factory=list, max_length=20)
    limits: AgentLimits = Field(default_factory=AgentLimits)

    @model_validator(mode='after')
    def validate_enabled_requires_subagents(self) -> 'AgentConfig':
        if self.enabled and not self.subagents:
            raise ValueError('agent.enabled is true but agent.subagents is empty -- at least one subagent is required')
        return self

    @model_validator(mode='after')
    def validate_unique_subagent_ids(self) -> 'AgentConfig':
        ids = [subagent.id for subagent in self.subagents]
        duplicates = sorted({sid for sid in ids if ids.count(sid) > 1})
        if duplicates:
            raise ValueError(f'agent.subagents ids must be unique -- duplicated: {duplicates}')
        return self

    @model_validator(mode='after')
    def validate_subagent_models_support_tools(self) -> 'AgentConfig':
        """Runtime's `BotConfig._agent_mode_is_valid`, for the part Ingest
        can see: a managed LLM bot's own model is Runtime's tool-capable
        'fake' default, but a subagent's own model override must declare
        tool support ('fake', or `supports_tools: true`). Runtime drops a
        bot it rejects, so this has to fail here, at save time."""
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


class BotGrantInput(BaseModel):
    """One entry of a bot's access list (ADR 0008): exactly one of
    `user_id` (a local or SSO account) or `team_id`. Teams can only be
    users, never owners."""

    model_config = ConfigDict(extra='forbid')

    user_id: str | None = None
    team_id: str | None = None
    role: BotRole

    @model_validator(mode='after')
    def validate_subject(self) -> 'BotGrantInput':
        if (self.user_id is None) == (self.team_id is None):
            raise ValueError('a grant names exactly one of user_id or team_id')
        if self.team_id is not None and self.role == BotRole.OWNER:
            raise ValueError('owners are persons, a team can only be a user')
        return self


class BotGrantResponse(BaseModel):
    """A resolved bot grant; `name` is the username or team name."""

    user_id: str | None = None
    team_id: str | None = None
    role: BotRole
    name: str
    team: str | None = None
    is_active: bool = True


class ManagedBotWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')

    kind: Literal['n8n', 'llm'] = 'n8n'
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool = True
    webhook_url: str | None = Field(default=None, max_length=2048)
    system_prompt: str | None = Field(default=None, max_length=12000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    retrieval_enabled: bool = False
    retrieval_filters: dict = Field(default_factory=dict)
    top_k: int = Field(default=20, ge=1, le=100)
    final_k: int = Field(default=5, ge=1, le=100)
    rerank: bool = True
    # Fail closed for legacy documents without a space (ADR 0008 addendum).
    include_uncollected: bool = False
    streaming: bool = False
    auth_token: str | None = Field(default=None, max_length=8192)
    clear_auth_token: bool = False
    timeout_seconds: int = Field(default=120, ge=1, le=14400)
    # Who may use the bot (ADR 0008): everyone, or only the grants.
    public: bool = False
    grants: list[BotGrantInput] = Field(default_factory=list, max_length=500)
    collections: list[str] = Field(default_factory=list, max_length=500)
    require_sources: bool = True
    no_context_reply: str = Field(
        default='Ich habe dazu keine belegten Informationen gefunden.', min_length=1, max_length=2000
    )
    # Agent-mode configuration (Weave-Runtime's `BotConfig.agent`, rollout
    # plan "Schritt 4 -- Administration und Streaming") -- validated here
    # via `AgentConfig` (this module, a field-for-field mirror of Runtime's
    # own model) so a malformed shape gives a readable 422 at save time
    # rather than only surfacing as a Runtime-side bot-load failure the
    # next time this bot's turn runs.
    agent: AgentConfig | None = Field(default=None)
    # LLM endpoint (Admin > Chat-Provider) this bot answers with; None = the
    # central 'default'. `llm_endpoints` are further endpoints users may
    # pick per chat ('*' = all enabled ones).
    llm_endpoint: str | None = Field(default=None, max_length=36)
    llm_endpoints: list[str] = Field(default_factory=list, max_length=50)

    @field_validator('llm_endpoint')
    @classmethod
    def normalize_llm_endpoint(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value else ''
        return cleaned or None

    @field_validator('llm_endpoints')
    @classmethod
    def normalize_llm_endpoints(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator('name', 'no_context_reply')
    @classmethod
    def strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('value cannot be empty')
        return cleaned

    @field_validator('description')
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value else ''
        return cleaned or None

    @field_validator('webhook_url')
    @classmethod
    def validate_webhook_url(cls, value: str | None) -> str | None:
        # LLM bots carry no webhook at all; `validate_kind` below owns the
        # "required for n8n" rule.
        if not value:
            return None
        if _UNSAFE_URL_CHARS.search(value):
            raise ValueError('webhook_url must be an unambiguous HTTP(S) URL')
        try:
            parsed = urlsplit(value)
            # Accessing port is deliberate: urlsplit accepts malformed text
            # such as ':not-a-port' until this property is evaluated.
            parsed.port
        except ValueError as exc:
            raise ValueError('webhook_url must be an unambiguous HTTP(S) URL') from exc
        if (
            parsed.scheme.lower() not in {'http', 'https'}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError('webhook_url must be an unambiguous HTTP(S) URL')
        # A trailing slash can be route-significant to the n8n deployment;
        # preserve the administrator's exact endpoint after whitespace trim.
        return value

    @field_validator('collections')
    @classmethod
    def normalize_scope(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode='after')
    def validate_token_change(self) -> 'ManagedBotWrite':
        if self.clear_auth_token and self.auth_token:
            raise ValueError('auth_token and clear_auth_token cannot be used together')
        return self

    @model_validator(mode='after')
    def validate_kind(self) -> 'ManagedBotWrite':
        if self.final_k > self.top_k:
            raise ValueError('final_k must be less than or equal to top_k')
        if self.kind == 'n8n' and not self.webhook_url:
            raise ValueError('webhook_url is required for n8n bots')
        if self.kind == 'llm' and (not self.system_prompt or self.webhook_url or self.auth_token or self.streaming or self.clear_auth_token):
            raise ValueError('LLM bots require system_prompt and cannot use n8n fields')
        # Runtime's `BotConfig._agent_mode_is_valid` rule 1: it drops such a
        # bot, and for an override of a YAML bot that would reopen the
        # YAML bot's own access.
        if self.kind == 'n8n' and self.agent is not None and self.agent.enabled:
            raise ValueError('agent mode is not available for n8n bots')
        if self.kind == 'n8n' and (self.llm_endpoint or self.llm_endpoints):
            raise ValueError('n8n bots cannot use LLM endpoints')
        return self


class ManagedBotCreate(ManagedBotWrite):
    id: str = Field(min_length=1, max_length=255)

    @field_validator('id')
    @classmethod
    def validate_id(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not _BOT_ID_RE.fullmatch(cleaned):
            raise ValueError('id must contain lowercase letters, digits and single hyphens')
        return cleaned


class ManagedBotUpdate(ManagedBotWrite):
    pass


class ManagedBotAdminResponse(BaseModel):
    id: str
    kind: str
    name: str
    description: str | None
    enabled: bool
    webhook_url: str | None
    system_prompt: str | None = None
    temperature: float | None = None
    retrieval_enabled: bool = False
    retrieval_filters: dict = Field(default_factory=dict)
    top_k: int = 20
    final_k: int = 5
    rerank: bool = True
    include_uncollected: bool = True
    streaming: bool
    has_auth_token: bool
    timeout_seconds: int
    public: bool = False
    grants: list[BotGrantResponse] = Field(default_factory=list)
    collections: list[str]
    require_sources: bool
    no_context_reply: str
    agent: dict | None = None
    llm_endpoint: str | None = None
    llm_endpoints: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    source: str = 'managed'
    editable: bool = True


class ManagedBotListResponse(BaseModel):
    items: list[ManagedBotAdminResponse] = Field(default_factory=list)


class ManagedBotInternalResponse(BaseModel):
    id: str
    kind: str = 'n8n'
    name: str
    description: str | None
    webhook_url: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    retrieval_enabled: bool = False
    retrieval_filters: dict = Field(default_factory=dict)
    top_k: int = 20
    final_k: int = 5
    rerank: bool = True
    include_uncollected: bool = True
    streaming: bool
    auth_token: str = ''
    timeout_seconds: int
    # Projection of the grants for Runtime's permission check (ADR 0008):
    # team names, Weave-Ingest user ids (owners included) and whether the
    # bot is shared with everyone.
    teams: list[str]
    users: list[str] = Field(default_factory=list)
    public: bool = False
    collections: list[str]
    require_sources: bool
    no_context_reply: str
    agent: dict | None = None
    llm_endpoint: str | None = None
    llm_endpoints: list[str] = Field(default_factory=list)


class ManagedBotInternalListResponse(BaseModel):
    items: list[ManagedBotInternalResponse] = Field(default_factory=list)
    # IDs explicitly removed from the effective Runtime roster.  This lets
    # Runtime suppress bundled YAML bots whose config is read-only here.
    disabled_ids: list[str] = Field(default_factory=list)


class ManagedBotOwnerResponse(BaseModel):
    """What a bot owner sees and maintains (ADR 0008): the content and the
    sharing, never the technical connection (webhook, secret, timeout)."""

    id: str
    kind: str
    name: str
    description: str | None
    enabled: bool
    system_prompt: str | None = None
    retrieval_enabled: bool = False
    collections: list[str]
    require_sources: bool
    no_context_reply: str
    public: bool
    grants: list[BotGrantResponse] = Field(default_factory=list)
    updated_at: datetime


class ManagedBotOwnerListResponse(BaseModel):
    items: list[ManagedBotOwnerResponse] = Field(default_factory=list)


class ManagedBotOwnerUpdate(BaseModel):
    """Owner PATCH: every field optional (None = unchanged). `grants`
    replaces the users; owners stay as the administrators set them."""

    model_config = ConfigDict(extra='forbid')

    description: str | None = Field(default=None, max_length=4000)
    system_prompt: str | None = Field(default=None, max_length=12000)
    collections: list[str] | None = Field(default=None, max_length=500)
    require_sources: bool | None = None
    no_context_reply: str | None = Field(default=None, min_length=1, max_length=2000)
    public: bool | None = None
    grants: list[BotGrantInput] | None = Field(default=None, max_length=500)

    @field_validator('collections')
    @classmethod
    def normalize_collections(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode='after')
    def validate_user_grants_only(self) -> 'ManagedBotOwnerUpdate':
        if self.grants and any(grant.role == BotRole.OWNER for grant in self.grants):
            raise ValueError('owners are set by an administrator')
        return self
