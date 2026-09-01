import uuid

from pydantic import BaseModel, Field

from app.core.config import settings


class ChatRequest(BaseModel):
    """POST /v1/chat body. `bot_id` is opaque (Weave-Runtime owns bot
    configuration, see app/models/models.py's Conversation docstring) --
    an unknown one surfaces as a 404 once runtime_client.chat() is actually
    called, not validated here. `conversation_id` omitted (or null) starts
    a fresh conversation; given, it must resolve to one of THIS caller's
    own conversations against THIS SAME bot_id (see
    app/services/conversations.py's get_or_create_conversation)."""

    bot_id: str
    message: str = Field(min_length=1, max_length=settings.chat_message_max_length)
    conversation_id: uuid.UUID | None = None
    # Per-request Collections FILTER, never a grant -- field-for-field
    # Weave-Runtime's own `ChatRequest.collections` (that service's
    # app/schemas/chat.py), forwarded verbatim by runtime_client.chat()/
    # chat_stream() (see those functions' own docstrings). `None` (the
    # default) means "no filter": today's behaviour, byte-for-byte
    # unchanged -- omitted entirely from the outgoing Weave-Runtime request
    # rather than sent as an explicit `null`. This is ALWAYS just a further
    # restriction on top of this caller's own read-authority (bot's
    # configured collections ∩ the caller's team-readable collections,
    # resolved entirely on Weave-Runtime's side) -- it can never widen that
    # scope, and naming a slug outside it is silently dropped there, never
    # surfaced as an error here. Deliberately NOT threaded through
    # app/api/openai_compat.py's OpenAI-compatible shim -- see that
    # module's own comment on `chat_completions` for why.
    collections: list[str] | None = None


class ChatResponse(BaseModel):
    """README's Output contract: `{ answer, sources[], trace,
    conversation_id }` -- `created_at` is left off here since the assistant
    Message row already carries its own (see MessageResponse in
    app/schemas/conversations.py for a caller that wants it, via
    GET /v1/conversations/{id})."""

    conversation_id: uuid.UUID
    answer: str
    sources: list | dict | None = None
    trace: dict | None = None
