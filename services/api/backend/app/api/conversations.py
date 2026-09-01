"""GET /v1/conversations/{id} -- fetch one conversation, scoped to its
owner, including every message (with sources/trace).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import enforce_rate_limit
from app.models.models import Conversation, User
from app.schemas.conversations import ConversationResponse

router = APIRouter(prefix='/v1', tags=['conversations'])


@router.get('/conversations/{conversation_id}', response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(enforce_rate_limit),
) -> Conversation:
    # A malformed id can never match a real row anyway; treating it the
    # same as "not found" (rather than a 422) avoids leaking id-format
    # validation as a signal distinct from ownership/existence.
    try:
        parsed_id = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Conversation not found')

    # (id AND user_id) binding lives in the query itself: another user's
    # conversation_id must 404, never be readable by guessing/enumerating
    # ids (IDOR) -- same discipline as Weave-Ingest's job-authz surface.
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == parsed_id, Conversation.user_id == user.id)
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Conversation not found')

    # conversation.messages is ordered ASC by Message.id (see the
    # relationship's order_by in app/models/models.py) -- oldest first, the
    # natural reading order for a chat transcript.
    return conversation
