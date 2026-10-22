"""Schemas for the admin-managed n8n bot control plane."""

import re
from datetime import datetime
from urllib.parse import urlsplit

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.models import BotRole
from app.schemas.bot_document import (  # noqa: F401 -- AgentLimits is re-exported for the admin API's wording
    AgentConfig,
    AgentLimits,
    BlockedTopic,
    IntakeConfig,
    ModelConfig,
    RetrievalFilters,
    SubagentConfig,
    SubagentLimits,
)


_BOT_ID_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_UNSAFE_URL_CHARS = re.compile(r'[\\\x00-\x1f\x7f]')


# The agent-mode shape (Runtime's `BotConfig.agent`) is validated with the
# portable bot document's mirror classes (app/schemas/bot_document.py);
# the `Agent*` names stay as aliases for the admin API's own wording.
AgentSubagentFilters = RetrievalFilters
AgentSubagentModel = ModelConfig
AgentSubagentLimits = SubagentLimits
AgentSubagent = SubagentConfig


class BotConfigExtras(BaseModel):
    """Guard fields of Runtime's `GuardConfig` that have no column of their
    own (`ManagedBot.config_extras`): every one optional, an omitted key
    means "Runtime default" and is not projected at all."""

    model_config = ConfigDict(extra='forbid')

    clarify_max_options: int | None = Field(default=None, ge=1, le=5)
    clarify_question: str | None = Field(default=None, min_length=1, max_length=2000)
    clarify_ambiguous_margin: float | None = Field(default=None, ge=0, le=1)
    clarify_ambiguous_question: str | None = Field(default=None, min_length=1, max_length=2000)


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
    # Retrieval-guard tuning (Weave-Runtime BotConfig.guard/retrieval):
    # rerank-score thresholds 0..1, None = runtime default.
    min_score: float | None = Field(default=None, ge=0, le=1)
    clarify_below: float | None = Field(default=None, ge=0, le=1)
    weak_context_below: float | None = Field(default=None, ge=0, le=1)
    query_rewrite: bool | None = None
    uncertainty_hint: bool = True
    # Agent-mode configuration (Weave-Runtime's `BotConfig.agent`, rollout
    # plan "Schritt 4 -- Administration und Streaming") -- validated here
    # via `AgentConfig` (this module, a field-for-field mirror of Runtime's
    # own model) so a malformed shape gives a readable 422 at save time
    # rather than only surfacing as a Runtime-side bot-load failure the
    # next time this bot's turn runs.
    agent: AgentConfig | None = Field(default=None)
    # Guard tuning without its own column (Runtime's clarify_* fields);
    # None = every one at its Runtime default.
    config_extras: BotConfigExtras | None = None
    # Chat presentation: hide the sources panel; a note next to the composer.
    show_sources: bool = True
    disclaimer: str | None = Field(default=None, max_length=500)
    # Intake question at conversation start (LLM bots only) and the topic
    # block (Runtime's BotConfig.intake / guard.blocked_topics); a None
    # reply = Runtime's default refusal text.
    intake: IntakeConfig | None = None
    blocked_topics: list[BlockedTopic] = Field(default_factory=list, max_length=20)
    blocked_topic_reply: str | None = Field(default=None, max_length=2000)
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

    @field_validator('description', 'disclaimer', 'blocked_topic_reply')
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
        if self.kind == 'n8n' and self.intake is not None:
            raise ValueError('the intake question is not available for n8n bots')
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
    min_score: float | None = None
    clarify_below: float | None = None
    weak_context_below: float | None = None
    query_rewrite: bool | None = None
    uncertainty_hint: bool = True
    agent: dict | None = None
    # Only the keys that are set (see BotConfigExtras).
    config_extras: dict | None = None
    show_sources: bool = True
    disclaimer: str | None = None
    intake: dict | None = None
    blocked_topics: list[dict] = Field(default_factory=list)
    blocked_topic_reply: str | None = None
    llm_endpoint: str | None = None
    llm_endpoints: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    source: str = 'managed'
    editable: bool = True
    # True when Runtime's roster lacks this active managed bot (list only).
    runtime_rejected: bool = False


class ManagedBotListResponse(BaseModel):
    items: list[ManagedBotAdminResponse] = Field(default_factory=list)


class BotImportWarningItem(BaseModel):
    code: Literal['missing_secret', 'dropped_field', 'permissions_reset']
    detail: str


class BotImportReport(BaseModel):
    """Result of `POST /api/v1/auth/admin/bots/import` (dry run or applied,
    see contracts/bot-document.md): what the file is, what would happen to
    it, and why not."""

    schema_version: int | None = None
    bot_id: str | None = None
    name: str | None = None
    kind: str | None = None
    # An existing bot of the same id: a managed row, a Runtime/YAML bot
    # (overwrite = managed override), or a deleted bot's tombstone.
    conflict: Literal['managed', 'runtime', 'tombstoned'] | None = None
    action: Literal['create', 'overwrite', 'blocked']
    warnings: list[BotImportWarningItem] = Field(default_factory=list)
    # Parse/schema/validation problems; any one of them blocks the import.
    errors: list[str] = Field(default_factory=list)
    applied: bool = False
    bot: ManagedBotAdminResponse | None = None


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
    min_score: float | None = None
    clarify_below: float | None = None
    weak_context_below: float | None = None
    query_rewrite: bool | None = None
    uncertainty_hint: bool = True
    agent: dict | None = None
    # From `config_extras`; serialised only when set (see the route's
    # `response_model_exclude_unset`) so Runtime keeps its own default otherwise.
    clarify_max_options: int | None = None
    clarify_question: str | None = None
    clarify_ambiguous_margin: float | None = None
    clarify_ambiguous_question: str | None = None
    show_sources: bool = True
    disclaimer: str | None = None
    intake: dict | None = None
    blocked_topics: list[dict] = Field(default_factory=list)
    blocked_topic_reply: str | None = None
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
    min_score: float | None = None
    clarify_below: float | None = None
    weak_context_below: float | None = None
    query_rewrite: bool | None = None
    uncertainty_hint: bool = True
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
    # Retrieval-guard tuning: an explicit null on a threshold / query_rewrite
    # resets it to the runtime default; an omitted key leaves it unchanged.
    min_score: float | None = Field(default=None, ge=0, le=1)
    clarify_below: float | None = Field(default=None, ge=0, le=1)
    weak_context_below: float | None = Field(default=None, ge=0, le=1)
    query_rewrite: bool | None = None
    uncertainty_hint: bool | None = None
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
