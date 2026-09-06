"""Schemas for the admin-managed n8n bot control plane."""

import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_BOT_ID_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_UNSAFE_URL_CHARS = re.compile(r'[\\\x00-\x1f\x7f]')


class ManagedBotWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool = True
    webhook_url: str = Field(min_length=1, max_length=2048)
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

    @field_validator('name', 'webhook_url', 'no_context_reply')
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
    name: str
    description: str | None
    enabled: bool
    webhook_url: str
    streaming: bool
    has_auth_token: bool
    timeout_seconds: int
    teams: list[str]
    collections: list[str]
    require_sources: bool
    no_context_reply: str
    created_at: datetime
    updated_at: datetime


class ManagedBotListResponse(BaseModel):
    items: list[ManagedBotAdminResponse] = Field(default_factory=list)


class ManagedBotInternalResponse(BaseModel):
    id: str
    name: str
    description: str | None
    webhook_url: str
    streaming: bool
    auth_token: str = ''
    timeout_seconds: int
    teams: list[str]
    collections: list[str]
    require_sources: bool
    no_context_reply: str


class ManagedBotInternalListResponse(BaseModel):
    items: list[ManagedBotInternalResponse] = Field(default_factory=list)
