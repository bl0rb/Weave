"""Conversation-state helpers for POST /v1/chat (app/api/chat.py):
resolving/creating the Conversation a turn belongs to, persisting each side
of the turn, and building the `{role, content}` history Weave-Runtime's
chat contract expects (app/services/runtime_client.py's chat()).

Kept as a plain function module (not a class) over a caller-supplied
`Session` -- same shape as every other Weave service's thin service layer,
easy to call from a route handler or a test without any extra wiring.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Conversation, Message, MessageRole, User


class ConversationError(Exception):
    """Base class for get_or_create_conversation() failures."""


class ConversationNotFound(ConversationError):
    """No conversation exists with this id for THIS user -- covers both "no
    such id at all" and "that id belongs to a different user" identically,
    same 404-not-403 IDOR discipline as GET /v1/conversations/{id}
    (app/api/conversations.py): a caller must never be able to distinguish
    "no such conversation" from "that's someone else's conversation" by the
    response they get back."""


class ConversationBotMismatch(ConversationError):
    """The conversation exists and belongs to this caller, but was started
    against a different bot_id than the one this request names. Continuing
    it under a different bot would silently replay one bot's conversation
    history into another bot's context -- rejected as a conflict rather
    than silently ignored or silently switched."""

    def __init__(self, message: str, *, conversation_bot_id: str, requested_bot_id: str) -> None:
        super().__init__(message)
        self.conversation_bot_id = conversation_bot_id
        self.requested_bot_id = requested_bot_id


def get_or_create_conversation(
    db: Session,
    *,
    user: User,
    conversation_id: uuid.UUID | None,
    bot_id: str,
) -> Conversation:
    """Resolve `conversation_id` (scoped to `user`, per the IDOR discipline
    above) if given, or start a fresh Conversation for `bot_id` otherwise.

    Raises `ConversationNotFound` / `ConversationBotMismatch` rather than
    ever returning None or a conversation that doesn't actually match this
    caller/bot -- app/api/chat.py maps those to 404/409 respectively.
    """
    if conversation_id is None:
        conversation = Conversation(user_id=user.id, bot_id=bot_id)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation

    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id)
    )
    if conversation is None:
        raise ConversationNotFound(f'Conversation {conversation_id} not found')
    if conversation.bot_id != bot_id:
        raise ConversationBotMismatch(
            f'Conversation {conversation_id} belongs to bot {conversation.bot_id!r}, not {bot_id!r}',
            conversation_bot_id=conversation.bot_id,
            requested_bot_id=bot_id,
        )
    return conversation


def append_message(
    db: Session,
    conversation: Conversation,
    *,
    role: MessageRole,
    content: str,
    sources: list | dict | None = None,
    trace: dict | None = None,
) -> Message:
    """Persist one turn (user or assistant) and commit immediately -- NOT
    batched with any later write in the same request. app/api/chat.py
    relies on this: the caller's own message must survive on disk even if
    the following Weave-Runtime call then fails (RuntimeUnavailable -> 502),
    so the turn can be retried without the caller having to re-type it.
    """
    message = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        sources=sources,
        trace=trace,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def build_history(
    conversation: Conversation,
    *,
    limit: int | None = None,
    exclude_message_id: int | None = None,
) -> list[dict]:
    """Return `conversation.messages` as the `[{role, content}]` shape
    Weave-Runtime's chat contract expects, oldest first, capped to the
    `limit` most recent turns (default `settings.history_max_messages`,
    resolved here rather than baked into the signature so a test's
    monkeypatched setting is honoured).

    `exclude_message_id` drops one message (by id) before the limit is
    applied. app/api/chat.py calls this AFTER persisting the turn's own
    user message (see append_message's docstring for why persistence
    happens that early) and passes that message's id here, so it reaches
    Weave-Runtime exactly once -- as the separate `message` argument to
    runtime_client.chat() -- never also duplicated as the newest entry of
    `history`.
    """
    if limit is None:
        limit = settings.history_max_messages

    # conversation.messages is already ordered ASC by Message.id (see the
    # relationship's order_by in app/models/models.py) -- oldest first.
    messages = [m for m in conversation.messages if m.id != exclude_message_id]
    if limit <= 0:
        messages = []
    else:
        messages = messages[-limit:]
    return [{'role': m.role.value, 'content': m.content} for m in messages]
