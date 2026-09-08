"""Schemas for the admin-managed n8n bot control plane."""

import re
from datetime import datetime
from urllib.parse import urlsplit

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_BOT_ID_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_UNSAFE_URL_CHARS = re.compile(r'[\\\x00-\x1f\x7f]')


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
    include_uncollected: bool = True
    streaming: bool = False
    auth_token: str | None = Field(default=None, max_length=8192)
    clear_auth_token: bool = False
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    teams: list[str] = Field(default_factory=list, max_length=100)
    collections: list[str] = Field(default_factory=list, max_length=500)
    require_sources: bool = True
    no_context_reply: str = Field(
        default='Ich habe dazu keine belegten Informationen gefunden.', min_length=1, max_length=2000
    )

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
    def validate_webhook_url(cls, value: str) -> str:
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

    @field_validator('teams', 'collections')
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
    teams: list[str]
    collections: list[str]
    require_sources: bool
    no_context_reply: str
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
    teams: list[str]
    collections: list[str]
    require_sources: bool
    no_context_reply: str


class ManagedBotInternalListResponse(BaseModel):
    items: list[ManagedBotInternalResponse] = Field(default_factory=list)
