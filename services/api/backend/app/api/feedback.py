"""PUT /v1/messages/{message_id}/feedback -- rate one assistant answer
(`helpful` | `wrong` | `outdated`, optional comment); upsert, one rating per
user and message. DELETE /v1/messages/{message_id}/feedback -- take the
rating back (idempotent, 204 either way). The caller's current rating is
replayed as `feedback` on each message of GET /v1/conversations/{id}
(app/api/conversations.py).

Same auth+ratelimit dependency and the same IDOR discipline as
GET /v1/conversations/{id}: the message must belong to one of the CALLER's
own conversations and be an assistant message -- anything else (someone
else's message, a user message, an unknown or malformed id) is the same
404, so a caller can never probe which message ids exist.

Every rating change is also queued as an anonymous quality signal for
Weave-Ingest's review tasks (app/services/quality_signals.py), in the same
transaction as the feedback itself, and forwarded after the response via
`BackgroundTasks` -- the user's request never waits for Weave-Ingest or
fails because of it.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import enforce_rate_limit
from app.models.models import Conversation, Message, MessageFeedback, MessageRole, User
from app.schemas.feedback import FeedbackRequest, FeedbackResponse
from app.services import quality_signals

router = APIRouter(prefix='/v1', tags=['feedback'])

# `messages.id` is a 32-bit INTEGER; a larger id can never match and would
# make Postgres reject the query ("integer out of range") instead of 404.
_MAX_MESSAGE_ID = 2**31 - 1


def _own_assistant_message(db: Session, message_id: str, user: User) -> tuple[Message, Conversation]:
    """The assistant message `message_id` in one of `user`'s own
    conversations, with that conversation -- else 404 (see module docstring)."""
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Message not found')
    try:
        parsed_id = int(message_id)
    except ValueError:
        raise not_found from None
    if not 0 < parsed_id <= _MAX_MESSAGE_ID:
        raise not_found
    row = db.execute(
        select(Message, Conversation)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Message.id == parsed_id, Conversation.user_id == user.id)
    ).first()
    if row is None:
        raise not_found
    message, conversation = row
    if message.role != MessageRole.ASSISTANT:
        raise not_found
    return message, conversation


def _own_feedback(db: Session, message: Message, user: User) -> MessageFeedback | None:
    return db.scalar(
        select(MessageFeedback).where(MessageFeedback.message_id == message.id, MessageFeedback.user_id == user.id)
    )


def _upsert(db: Session, message: Message, user: User, body: FeedbackRequest) -> MessageFeedback:
    feedback = _own_feedback(db, message, user)
    if feedback is None:
        feedback = MessageFeedback(id=uuid.uuid4(), message_id=message.id, user_id=user.id)
        db.add(feedback)
    feedback.rating = body.rating
    feedback.comment = body.comment
    db.flush()
    return feedback


@router.put('/messages/{message_id}/feedback', response_model=FeedbackResponse)
def put_feedback(
    message_id: str,
    body: FeedbackRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(enforce_rate_limit),
) -> FeedbackResponse:
    message, conversation = _own_assistant_message(db, message_id, user)
    try:
        feedback = _upsert(db, message, user, body)
    except IntegrityError:
        # A concurrent first rating of the same message won the unique
        # (message_id, user_id) race -- update that row instead.
        db.rollback()
        feedback = _upsert(db, message, user, body)
    # Timestamps are set at flush; refresh once so the signal and the
    # response carry the stored values.
    db.refresh(feedback)
    queued = quality_signals.enqueue_feedback_signal(
        db, feedback=feedback, message=message, conversation=conversation
    )
    db.commit()
    if queued:
        background_tasks.add_task(quality_signals.flush_outbox_in_background)
    return FeedbackResponse(
        message_id=message.id, rating=feedback.rating, comment=feedback.comment, updated_at=feedback.updated_at
    )


@router.delete('/messages/{message_id}/feedback', status_code=status.HTTP_204_NO_CONTENT)
def delete_feedback(
    message_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(enforce_rate_limit),
) -> None:
    message, conversation = _own_assistant_message(db, message_id, user)
    feedback = _own_feedback(db, message, user)
    if feedback is None:
        return
    # Same signal key, now marked retracted -- Weave-Ingest takes the
    # rating out of its task counts (and dismisses a task left with none).
    queued = quality_signals.enqueue_feedback_signal(
        db, feedback=feedback, message=message, conversation=conversation, retracted=True
    )
    db.delete(feedback)
    db.commit()
    if queued:
        background_tasks.add_task(quality_signals.flush_outbox_in_background)
