"""Request/response bodies for GET /v1/me + PUT /v1/me/locale
(app/api/me.py)."""

from typing import Literal

from pydantic import BaseModel


class MeResponse(BaseModel):
    username: str
    locale: Literal['de', 'en'] | None = None


class LocaleUpdateRequest(BaseModel):
    locale: Literal['de', 'en'] | None


class LocaleResponse(BaseModel):
    locale: Literal['de', 'en'] | None = None
