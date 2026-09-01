"""OpenAI-compatible POST /v1/chat/completions -- lets Open WebUI (or any
other client speaking OpenAI's own Chat Completions API) use Weave-API as a
drop-in OpenAI endpoint, with a Weave-Runtime `bot_id` standing in for an
OpenAI model name (README's "Open WebUI anbinden"). Behind the same
auth+ratelimit dependency as every other authenticated route
(app/core/ratelimit.py's enforce_rate_limit) -- an OpenAI client
authenticates with a Bearer token exactly like any other caller here, so a
user's existing Personal-API-Token (app/cli.py) is already all that's
needed.

Deliberately stateless, UNLIKE POST /v1/chat (app/api/chat.py): OpenAI's own
Chat Completions contract has no `conversation_id` at all, and this shim
does not invent one -- an OpenAI client already resends the FULL message
history with every request (that's how the real API works: the CLIENT
remembers the conversation, not the server), so this route persists NOTHING
to `conversations`/`messages` (app/models/models.py) and never resolves an
existing Conversation. Every call is independent, exactly as `messages`
alone describes it.

`_split_messages` turns that one OpenAI-shaped list into the two arguments
`runtime_client.chat()` (and, underneath it, Weave-Runtime's own
`/internal/chat`) actually expects: the LAST 'user'-role message becomes
`message` (the turn actually being asked right now); every EARLIER
user/assistant turn becomes `history`, in original order. Any 'system'-role
message is dropped rather than forwarded as history -- Weave-Runtime's own
`ChatMessage` (that service's app/schemas/chat.py) only accepts
`role: Literal['user', 'assistant']`, and a bot's system prompt is already
owned by ITS OWN YAML config (`BotConfig.system_prompt`), not by whatever a
client's OpenAI-shaped request happened to put in a system turn -- forwarding
one would either fail validation outright or silently second-guess the
bot's own configured persona.

Errors map exactly like POST /v1/chat's own turn (see that route's
docstring): `RuntimeUnavailable` -> 502, `RuntimeRejected` -> the exact
upstream status Weave-Runtime returned (e.g. 404 for an unknown `model`/
`bot_id`, 403 for a team not permitted on that bot), passed straight through
with its `detail`.

Also GET /v1/models (OpenAI's own model-listing endpoint) and, on
POST /v1/chat/completions, `"stream": true` -- see `list_models` and
`_stream_openai_chunks` below, respectively.
"""

import time
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.core.ratelimit import enforce_rate_limit
from app.models.models import User
from app.schemas.openai import (
    OpenAIChatCompletionChoice,
    OpenAIChatCompletionChunk,
    OpenAIChatCompletionChunkChoice,
    OpenAIChatCompletionChunkDelta,
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIChatMessage,
    OpenAIModel,
    OpenAIModelList,
)
from app.services import runtime_client
from app.services.runtime_client import RuntimeClientError, list_bots

router = APIRouter(prefix='/v1', tags=['openai-compat'])


def _split_messages(messages: list[OpenAIChatMessage]) -> tuple[str, list[dict]]:
    """`(message, history)` for `runtime_client.chat()` -- see this module's
    own docstring for the exact rule. Raises `ValueError` (mapped to 400 by
    `chat_completions` below) when `messages` contains no 'user'-role entry
    at all -- there is no "current turn" to answer in that case, the same
    way POST /v1/chat's own `ChatRequest.message` can never be empty
    (app/schemas/chat.py's `Field(min_length=1)`)."""
    last_user_index = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].role == 'user'), None)
    if last_user_index is None:
        raise ValueError("messages must contain at least one 'user'-role message")

    history = [
        {'role': m.role, 'content': m.content} for m in messages[:last_user_index] if m.role in ('user', 'assistant')
    ]
    return messages[last_user_index].content, history


def _sse_chunk(chunk: OpenAIChatCompletionChunk) -> str:
    """One OpenAIChatCompletionChunk as one outgoing SSE line -- OpenAI's
    own `'data: <json>\\n\\n'` framing (identical shape to
    contracts/internal-chat.md's, just a different JSON payload).
    `exclude_none=True` drops `delta.role`/`delta.content`/`finish_reason`
    from the wire on every chunk that doesn't set them, rather than sending
    an explicit `null` -- see OpenAIChatCompletionChunkDelta's own
    docstring."""
    return f'data: {chunk.model_dump_json(exclude_none=True)}\n\n'


def _stream_openai_chunks(*, chunk_id: str, created: int, model: str, events: Iterator[dict]) -> Iterator[str]:
    """The StreamingResponse body for `"stream": true` on
    POST /v1/chat/completions below -- translates runtime_client.
    chat_stream()'s five Weave-Runtime event types into OpenAI's own
    `chat.completion.chunk` shape, one `delta` event in, one chunk out:

    - `trace`/`sources`: dropped. Neither has a field in OpenAI's
      Chat-Completions stream shape to carry them (exactly like the
      non-streaming `chat_completions()` below already drops `trace`
      entirely and has no equivalent for `sources` either -- see
      OpenAIChatCompletionResponse's own docstring on `usage` for the same
      "nothing authoritative to map this onto" reasoning).
    - `delta`: becomes one chunk whose `delta.content` carries `text`.
      `delta.role='assistant'` is set on the FIRST such chunk of the
      stream ONLY (`sent_role` below) -- OpenAI clients key off exactly
      that first-chunk signal to start rendering a new assistant turn, see
      OpenAIChatCompletionChunkDelta's own docstring.
    - `done`: becomes one final chunk with `finish_reason='stop'` (and, if
      no `delta` was ever seen -- possible in principle, never in practice
      per contracts/internal-chat.md, which streams even a guard/
      placeholder reply as ordinary `delta` events -- `delta.role`
      is set here instead, so the very first chunk THIS function ever
      emits always carries it, no matter which event triggered it), then
      the literal `'data: [DONE]\\n\\n'` sentinel line, then returns.
    - `error`: Weave-Runtime's generation failed mid-stream, i.e. AFTER
      this shim's own `200`/`text/event-stream` response already committed
      -- there is no HTTP status left to change, and OpenAI's own
      Chat-Completions stream contract has no error-chunk shape to forward
      this as either. The one honest option left is to stop the stream
      right here, with neither a `finish_reason` chunk nor `[DONE]` --
      indistinguishable, from an OpenAI client's point of view, from a real
      OpenAI backend dropping the connection on an internal failure. Same
      handling for `RuntimeClientError` raised mid-iteration (a transport
      failure, see runtime_client._iter_chat_stream_events) -- caught
      below, ending the stream exactly the same way.
    """
    sent_role = False
    try:
        for event in events:
            event_type = event.get('type')
            if event_type == 'delta':
                delta = OpenAIChatCompletionChunkDelta(
                    role=None if sent_role else 'assistant', content=event.get('text', '')
                )
                sent_role = True
                yield _sse_chunk(
                    OpenAIChatCompletionChunk(
                        id=chunk_id,
                        created=created,
                        model=model,
                        choices=[OpenAIChatCompletionChunkChoice(delta=delta, finish_reason=None)],
                    )
                )
            elif event_type == 'done':
                final_delta = OpenAIChatCompletionChunkDelta(role=None if sent_role else 'assistant')
                yield _sse_chunk(
                    OpenAIChatCompletionChunk(
                        id=chunk_id,
                        created=created,
                        model=model,
                        choices=[OpenAIChatCompletionChunkChoice(delta=final_delta, finish_reason='stop')],
                    )
                )
                yield 'data: [DONE]\n\n'
                return
            elif event_type == 'error':
                return
    except RuntimeClientError:
        return


@router.post('/chat/completions', response_model=None)
def chat_completions(
    body: OpenAIChatCompletionRequest,
    user: User = Depends(enforce_rate_limit),
) -> OpenAIChatCompletionResponse | StreamingResponse:
    try:
        message, history = _split_messages(body.messages)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    runtime_user = {'id': str(user.id), 'team': user.team}

    # Deliberately NEVER threads a Collections filter through to
    # runtime_client.chat()/chat_stream() here, unlike POST /v1/chat
    # (app/api/chat.py) -- OpenAI's own Chat-Completions request shape
    # (OpenAIChatCompletionRequest, app/schemas/openai.py) has no field for
    # it, and this shim's whole point is byte-for-byte OpenAI API
    # compatibility (module docstring above); inventing a
    # non-standard extra request field here to carry it would break that
    # compatibility for every other OpenAI-shaped client speaking to this
    # same endpoint. A caller that needs a per-request Collections filter
    # uses POST /v1/chat instead.
    if body.stream:
        try:
            events = runtime_client.chat_stream(bot_id=body.model, message=message, history=history, user=runtime_user)
        except runtime_client.RuntimeUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        except runtime_client.RuntimeRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail or str(exc)) from exc

        return StreamingResponse(
            _stream_openai_chunks(
                chunk_id=f'chatcmpl-{uuid.uuid4().hex}', created=int(time.time()), model=body.model, events=events
            ),
            media_type='text/event-stream',
        )

    try:
        result = runtime_client.chat(bot_id=body.model, message=message, history=history, user=runtime_user)
    except runtime_client.RuntimeUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except runtime_client.RuntimeRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail or str(exc)) from exc

    return OpenAIChatCompletionResponse(
        id=f'chatcmpl-{uuid.uuid4().hex}',
        created=int(time.time()),
        model=body.model,
        choices=[
            OpenAIChatCompletionChoice(
                index=0,
                message=OpenAIChatMessage(role='assistant', content=result.get('answer', '')),
                finish_reason='stop',
            )
        ],
        # See app/schemas/openai.py's OpenAIChatCompletionResponse docstring
        # for why `usage` is always omitted rather than estimated.
        usage=None,
    )


@router.get('/models', response_model=OpenAIModelList, dependencies=[Depends(enforce_rate_limit)])
def list_models() -> OpenAIModelList:
    """OpenAI's own model-listing endpoint, fed from the same bot registry
    GET /v1/bots already proxies (app/api/bots.py) -- see
    app/schemas/openai.py's OpenAIModel/OpenAIModelList docstrings for the
    exact shape and why `created` is simply "now". This is what lets Open
    WebUI populate its own model dropdown by itself (README's "Open WebUI
    anbinden") instead of requiring a bot_id to be typed in by hand.

    Same error mapping as GET /v1/bots: any `RuntimeClientError` (Weave-
    Runtime unreachable, timed out, or rejecting the call outright) becomes
    a blanket 502 -- this proxy makes no finer-grained distinction, exactly
    like that route's own docstring explains for itself.
    """
    try:
        bots = list_bots()
    except RuntimeClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    created = int(time.time())
    return OpenAIModelList(data=[OpenAIModel(id=bot['id'], created=created) for bot in bots])
