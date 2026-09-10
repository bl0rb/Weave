import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    sources: list | dict | None = None
    trace: dict | None = None
    created_at: datetime


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bot_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse]


class ConversationSummary(BaseModel):
    """One row of GET /v1/conversations -- the history list. No messages:
    a caller wanting the full transcript follows up with
    GET /v1/conversations/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bot_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
