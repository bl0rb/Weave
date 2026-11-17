"""Request/response bodies of the answer-feedback endpoints
(PUT/DELETE /v1/messages/{message_id}/feedback, app/api/feedback.py) and the
`feedback` state GET /v1/conversations/{id} replays on each message
(app/schemas/conversations.py's MessageResponse)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

FEEDBACK_COMMENT_MAX_LENGTH = 2000

Rating = Literal['helpful', 'wrong', 'outdated']


class FeedbackRequest(BaseModel):
    """PUT body. `comment` is optional free text ("Was ist falsch oder
    veraltet?"), stripped before the length check; blank becomes `None`."""

    rating: Rating
    comment: str | None = Field(default=None, max_length=FEEDBACK_COMMENT_MAX_LENGTH)

    @field_validator('comment', mode='before')
    @classmethod
    def _strip_comment(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class FeedbackState(BaseModel):
    """The caller's current rating of one assistant message."""

    rating: Rating
    comment: str | None = None
    updated_at: datetime


class FeedbackResponse(FeedbackState):
    message_id: int
