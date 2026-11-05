import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field

from app.services.notices import answer_notices


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    sources: list | dict | None = None
    trace: dict | None = None
    created_at: datetime

    @computed_field
    @property
    def notices(self) -> list[str]:
        """Same answer metadata as ChatResponse.notices, derived from the
        stored sources -- so a reloaded conversation keeps its flags."""
        return answer_notices(self.sources)


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
