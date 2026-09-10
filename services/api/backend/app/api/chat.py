"""POST /v1/chat -- the unified chat endpoint (bot_id, conversation_id,
message -> answer + sources + trace, README's Schnittstellen). Sits behind
auth+ratelimit like every other authenticated route.

Turn sequence, in order (see app/services/conversations.py and
app/services/runtime_client.py for why each step is shaped the way it is):

1. Resolve/create the Conversation (`ConversationNotFound` -> 404,
   `ConversationBotMismatch` -> 409 -- see conversations.py's
   get_or_create_conversation).
2. Persist the caller's own message and commit it immediately -- BEFORE
   calling Weave-Runtime, so it survives on disk even if that call then
   fails.
3. Build the `{role, content}` history to send alongside it.
4. Call Weave-Runtime. `RuntimeUnavailable` (unreachable/5xx) -> 502, with
   NO assistant message ever created for that failed turn -- the message
   from step 2 is the only trace of it, ready to retry.
   `RuntimeRejected` (any other 4xx, e.g. unknown bot_id, no permission on
   that bot) -> the exact same upstream status code, passed straight
   through with its `detail`.
5. Persist the assistant's reply (with `sources`/`trace` carried verbatim)
   and return it.

Also POST /v1/chat/stream -- the streaming counterpart of the same turn,
for Weave-API's own UI (not the OpenAI-compatible shim, which has its own
streaming path, `"stream": true` on POST /v1/chat/completions, see
app/api/openai_compat.py) -- see that route's own docstring below for how
persistence and streaming interact.
"""

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import enforce_rate_limit
from app.models.models import Conversation, MessageRole, User
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import conversations as conversations_service
from app.services import runtime_client

router = APIRouter(prefix='/v1', tags=['chat'])


@router.post('/chat', response_model=ChatResponse)
def chat(
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(enforce_rate_limit),
) -> ChatResponse:
    try:
        conversation = conversations_service.get_or_create_conversation(
            db, user=user, conversation_id=body.conversation_id, bot_id=body.bot_id
        )
    except conversations_service.ConversationNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except conversations_service.ConversationBotMismatch as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    user_message = conversations_service.append_message(
        db, conversation, role=MessageRole.USER, content=body.message
    )

    history = conversations_service.build_history(conversation, exclude_message_id=user_message.id)

    try:
        result = runtime_client.chat(
            bot_id=body.bot_id,
            message=body.message,
            history=history,
            user={'id': str(user.id), 'team': user.team, 'teams': user.effective_teams},
            collections=body.collections,
        )
    except runtime_client.RuntimeUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except runtime_client.RuntimeRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail or str(exc)) from exc

    is_first_assistant = len(conversation.messages) == 1
    assistant_message = conversations_service.append_message(
        db,
        conversation,
        role=MessageRole.ASSISTANT,
        content=result.get('answer', ''),
        sources=result.get('sources'),
        trace=result.get('trace'),
    )
    if is_first_assistant:
        try:
            title = runtime_client.conversation_title(question=body.message, answer=assistant_message.content)
            conversations_service.replace_title(db, conversation, title)
        except runtime_client.RuntimeClientError:
            pass

    return ChatResponse(
        conversation_id=conversation.id,
        answer=assistant_message.content,
        sources=assistant_message.sources,
        trace=assistant_message.trace,
    )


def _sse_line(event: dict) -> str:
    """One already-JSON-decoded runtime_client.chat_stream() event,
    re-framed as one outgoing SSE line -- same `'data: <json>\\n\\n'`
    framing contracts/internal-chat.md documents for Weave-Runtime's own
    stream, since this route passes those events through close to
    verbatim (see `_stream_and_persist` below)."""
    return f'data: {json.dumps(event)}\n\n'


def _stream_and_persist(db, conversation: Conversation, events: Iterator[dict]) -> Iterator[str]:
    """The actual StreamingResponse body for POST /v1/chat/stream below --
    forwards every runtime_client.chat_stream() event to OUR OWN caller
    close to verbatim (`trace`/`delta`/`sources`/`done`/`error`, exactly
    the five event types contracts/internal-chat.md defines), while also
    accumulating the pieces of the assistant's turn (`answer` from every
    `delta.text`, `sources`, `trace`) needed to persist it exactly the way
    the non-streaming `chat()` route above does.

    Persistence decision (deliberately asymmetric with step 2 above, worth
    spelling out): the caller's own message was already persisted and
    committed BEFORE this generator was even constructed (same as `chat()`
    above), but the ASSISTANT message is written here ONLY once a `done`
    event is actually seen -- never speculatively, never partially. Three
    ways this stream can end instead of reaching `done`, and every one of
    them leaves NO assistant message behind, only the user's own turn from
    step 2:

    1. Weave-Runtime itself sends a terminal `error` event (a real
       provider's generation failure mid-stream, see
       contracts/internal-chat.md's "Fehlerverhalten" section) -- forwarded
       to our own caller as-is, nothing persisted.
    2. `events` raises `RuntimeClientError` while being iterated (a
       transport failure mid-stream, re-raised by
       runtime_client._iter_chat_stream_events as `RuntimeUnavailable`) --
       our own stream has already committed its `200`/`text/event-stream`
       response by this point, so (exactly like Weave-Runtime's own
       in-band `error` event) the only way left to signal this to OUR
       caller is a synthetic `{"type": "error", ...}` event of our own,
       sent here and nowhere else in this codebase.
    3. The underlying connection to Weave-Runtime is simply cut with no
       terminal event at all (`events` runs out with neither `done` nor
       `error` ever seen, no exception raised either) -- the generator
       just ends here too, silently, exactly mirroring what a genuinely
       dropped connection looks like from either side.

    A half-written assistant message (partial `answer` text, guessed at
    from whatever `delta`s happened to arrive before the drop) would be
    worse than none: it would look like a complete, successful turn to
    anyone reading the conversation back later, with no marker that it was
    actually cut off. Losing the streamed-so-far text on any non-`done`
    ending and leaving the turn retryable (the persisted user message is
    still right there) is the same trade-off `chat()` above already makes
    for `RuntimeUnavailable` on the non-streaming path.
    """
    answer_parts: list[str] = []
    sources: list | dict | None = None
    trace: dict | None = None
    try:
        for event in events:
            event_type = event.get('type')
            if event_type == 'trace':
                trace = event.get('trace')
            elif event_type == 'delta':
                answer_parts.append(event.get('text', ''))
            elif event_type == 'sources':
                sources = event.get('sources')

            if event_type == 'done':
                # Persist BEFORE yielding this event -- if append_message
                # itself somehow fails, our caller must never see a `done`
                # for a turn that was never actually written to disk.
                is_first_assistant = len(conversation.messages) == 1
                conversations_service.append_message(
                    db,
                    conversation,
                    role=MessageRole.ASSISTANT,
                    content=''.join(answer_parts),
                    sources=sources,
                    trace=trace,
                )
                if is_first_assistant:
                    try:
                        title = runtime_client.conversation_title(
                            question=conversation.messages[0].content,
                            answer=''.join(answer_parts),
                        )
                        conversations_service.replace_title(db, conversation, title)
                    except runtime_client.RuntimeClientError:
                        pass

            yield _sse_line(event)

            if event_type in ('done', 'error'):
                return
    except runtime_client.RuntimeClientError as exc:
        # Case 2 above: the stream broke mid-flight. Our own response
        # already committed to text/event-stream -- an HTTP status can no
        # longer change, so the failure goes out as one more in-band event
        # instead, then the generator ends. Nothing is persisted.
        yield _sse_line({'type': 'error', 'detail': str(exc)})
    # Falling off the end of the loop with neither `done` nor `error` seen,
    # and no exception raised either, is case 3 above -- the generator
    # simply ends here too, exactly as silently as the dropped connection
    # that caused it.


@router.post('/chat/stream')
def chat_stream(
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(enforce_rate_limit),
) -> StreamingResponse:
    """The streaming counterpart to POST /v1/chat above, for Weave-API's
    own UI: same request body (`ChatRequest`), same conversation
    resolution and same up-front user-message persistence (steps 1-3 of
    `chat()`'s own docstring, run here identically and just as
    synchronously -- a `ConversationNotFound`/`ConversationBotMismatch`/
    `RuntimeUnavailable`/`RuntimeRejected` all still become a normal
    404/409/502/passed-through-4xx HTTP response here, never an in-band SSE
    event, because every one of them is raised BEFORE this route returns
    its StreamingResponse -- i.e. before any byte of the `200 OK`/
    `text/event-stream` response is committed), but `text/event-stream`
    instead of one JSON object: every event runtime_client.chat_stream()
    yields (`trace`/`delta`/`sources`/`done`/`error`) is forwarded to this
    route's own caller, and the assistant's reply is persisted exactly
    once, at `done` -- see `_stream_and_persist` above for the full
    decision on why persistence and streaming interact that way, and for
    every way this can end WITHOUT persisting anything.

    The response also carries an `X-Conversation-Id` header (not part of
    contracts/internal-chat.md's own five event types, and not one of them
    either) -- the one piece of README's `/v1/chat` output contract
    (`{answer, sources, trace, conversation_id}`) that has nowhere else to
    go here: a caller starting a brand-new conversation (`conversation_id`
    omitted from the request body) has no other way to learn the id it was
    just assigned, needed to continue that same conversation on its next
    turn.
    """
    try:
        conversation = conversations_service.get_or_create_conversation(
            db, user=user, conversation_id=body.conversation_id, bot_id=body.bot_id
        )
    except conversations_service.ConversationNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except conversations_service.ConversationBotMismatch as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    user_message = conversations_service.append_message(
        db, conversation, role=MessageRole.USER, content=body.message
    )
    history = conversations_service.build_history(conversation, exclude_message_id=user_message.id)

    try:
        events = runtime_client.chat_stream(
            bot_id=body.bot_id,
            message=body.message,
            history=history,
            user={'id': str(user.id), 'team': user.team, 'teams': user.effective_teams},
            collections=body.collections,
        )
    except runtime_client.RuntimeUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except runtime_client.RuntimeRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail or str(exc)) from exc

    response = StreamingResponse(
        _stream_and_persist(db, conversation, events),
        media_type='text/event-stream',
    )
    response.headers['X-Conversation-Id'] = str(conversation.id)
    return response
