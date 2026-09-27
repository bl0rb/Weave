"""Request/response bodies for GET /v1/me + PUT /v1/me/locale
(app/api/me.py)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    username: str
    locale: Literal['de', 'en'] | None = None


class LocaleUpdateRequest(BaseModel):
    locale: Literal['de', 'en'] | None


class LocaleResponse(BaseModel):
    locale: Literal['de', 'en'] | None = None


class ApiTokenCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    # None = never expires.
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)


class ApiTokenResponse(BaseModel):
    """A personal API token without its secret -- only its sha256 is stored."""

    id: uuid.UUID
    label: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


class ApiTokenCreateResponse(ApiTokenResponse):
    # The raw value, shown exactly once.
    token: str


class ApiTokenListResponse(BaseModel):
    items: list[ApiTokenResponse]
