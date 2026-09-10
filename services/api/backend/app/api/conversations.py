"""GET /v1/conversations -- list the caller's own chat history.
GET /v1/conversations/{id} -- fetch one conversation, scoped to its
owner, including every message (with sources/trace).
DELETE /v1/conversations/{id} -- permanently remove one conversation and
its messages (cascade, see Conversation.messages relationship).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import enforce_rate_limit
from app.models.models import Conversation, User
from app.schemas.conversations import ConversationListResponse, ConversationResponse

router = APIRouter(prefix='/v1', tags=['conversations'])


@router.get('/conversations', response_model=ConversationListResponse)
def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(enforce_rate_limit),
) -> ConversationListResponse:
    # Most recently active thread first -- the natural order for a history
    # sidebar. No messages loaded here (see ConversationSummary's own
    # docstring): a caller wanting the transcript follows up with the
    # single-conversation route below.
    conversations = db.scalars(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc())
    ).all()
    return ConversationListResponse(items=list(conversations))


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


@router.delete('/conversations/{conversation_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(enforce_rate_limit),
) -> None:
    try:
        parsed_id = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Conversation not found')

    # Same ownership-scoped lookup (and same 404-not-403 IDOR discipline)
    # as GET above -- a caller can never tell "not found" apart from
    # "belongs to someone else" by the response they get.
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == parsed_id, Conversation.user_id == user.id)
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Conversation not found')

    # Messages cascade-delete with the conversation (Conversation.messages'
    # own `cascade='all, delete-orphan'`, app/models/models.py).
    db.delete(conversation)
    db.commit()
