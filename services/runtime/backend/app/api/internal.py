"""Internal, service-authenticated routes -- Weave-API's gateway (or any
other Weave service) is the only intended caller of everything below (see
app/core/auth.py's require_service_token, applied once at the router level,
exactly like Weave-Retrieval's own app/api/search.py does for its single
router).

Prefixed `/internal` rather than Weave-Retrieval's versioned `/api/v1`: this
is service-to-service plumbing (bot roster introspection, the chat
entrypoint), never a public/versioned API surface an outside client
integrates against directly.
"""

from collections.abc import Callable, Iterator
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.core.auth import require_service_token
from app.schemas.bot import BotRetrievalSummary, BotSummary
from app.schemas.chat import ChatRequest, ChatResponse, ChatStreamEvent
from app.services import chat as chat_service
from app.services.botconfig import BotNotFoundError, list_bots
from app.services.n8n_client import N8nUnavailable
from app.services.retrieval_client import RetrievalUnavailable

router = APIRouter(prefix='/internal', dependencies=[Depends(require_service_token)])

_T = TypeVar('_T')


@router.get('/bots', response_model=list[BotSummary])
def list_bots_endpoint() -> list[BotSummary]:
    return [
        BotSummary(
            id=bot.id,
            name=bot.name,
            description=bot.description,
            retrieval=BotRetrievalSummary(enabled=bot.retrieval.enabled),
        )
        for bot in list_bots()
    ]


def _run_pipeline_step(step: Callable[[], _T]) -> _T:
    """Call `step()` and translate the four app/services/chat.py exceptions
    BOTH `/internal/chat` and `/internal/chat/stream` map to a status code,
    identically for either route -- shared here so that mapping is written
    exactly once. Anything else (a RetrievalError from a misconfigured bot,
    an LLMError from a real provider, an n8n_client.N8nError/delegation.
    DelegationConfigError from a misconfigured n8n-provider bot's flow) is
    left to propagate as this service's default 500 -- see chat.py's own
    docstring for why those specifically are not given a dedicated status
    code.

    For `/internal/chat/stream` specifically, `step` is
    `chat_service.handle_chat_stream(request)` -- deliberately NOT a
    generator function itself (see that function's own docstring), so
    calling it here runs its entire bot-load/permission/router/retrieval/
    guard pipeline to completion, and any of these three exceptions, right
    here, before this dependency's caller has produced a StreamingResponse
    (and the `200 OK` it commits to) at all.
    """
    try:
        return step()
    except BotNotFoundError as exc:
        # BotNotFoundError subclasses KeyError (see botconfig.py) -- str()
        # on a KeyError already renders its one argument (the bot_id) in
        # quotes, so no extra !r is needed here.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'unknown bot_id: {exc}') from exc
    except chat_service.BotPermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except RetrievalUnavailable as exc:
        # "the upstream SERVICE is the problem, retrying later might
        # succeed" (see retrieval_client.py's own docstring) -- 503, never a
        # silent answer without sources for a bot that requires them
        # (README's Response Guard). Weave-API's own runtime_client
        # classifies any 5xx here as RuntimeUnavailable and surfaces IT as
        # 502 to ITS caller (app/api/chat.py in that service) -- this
        # service's own 503 is deliberately more specific than that
        # downstream 502 ever is.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except N8nUnavailable as exc:
        # The n8n-provider analogue of RetrievalUnavailable immediately
        # above -- same "upstream SERVICE is the problem" 503, same
        # Response Guard reasoning, see app/services/n8n_client.py's own
        # docstring for exactly which failures land here (n8n unreachable,
        # timed out, or answered 5xx) versus N8nError (left uncaught,
        # default 500 -- see app/services/chat.py's own docstring).
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post('/chat', response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """One turn of a conversation -- see app/services/chat.py's own
    docstring for the full pipeline (intent routing, retrieval, LLM
    generation, response guard) this delegates to. See `_run_pipeline_step`
    above for the three exceptions mapped to a status code here.
    """
    return _run_pipeline_step(lambda: chat_service.handle_chat(request))


def _sse_event(event: ChatStreamEvent) -> str:
    """One `ChatStreamEvent` (app/schemas/chat.py) as one SSE line -- the
    exact `'data: <json>\\n\\n'` framing contracts/internal-chat.md's stream
    section promises, one such line per event, no other SSE fields (`id:`,
    `event:`, ...) needed since every event already self-describes its own
    kind via its own `type` field."""
    return f'data: {event.model_dump_json()}\n\n'


def _iter_sse(events: Iterator[ChatStreamEvent]) -> Iterator[str]:
    for event in events:
        yield _sse_event(event)


@router.post('/chat/stream')
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """The streaming counterpart to `POST /internal/chat` above -- one SSE
    event per pipeline event (`trace` once, then zero or more `delta`, then
    either `sources`+`done` or a single terminal `error`; see
    contracts/internal-chat.md's stream section for the full ordering
    guarantee and app/services/chat.py's `handle_chat_stream` for how it is
    produced).

    Error handling splits at exactly the same point `handle_chat_stream`'s
    own docstring describes: `_run_pipeline_step` below calls
    `handle_chat_stream(request)` itself (not yet any of its events) through
    the SAME three-exception mapping `/internal/chat` uses, so a bot lookup/
    permission/retrieval failure BEFORE the stream starts still answers with
    a normal 401/403/404/503 -- no StreamingResponse has been constructed
    yet at that point, since `handle_chat_stream` runs that entire
    preparation synchronously before returning its (still-unstarted)
    generator. A failure once actual streaming is under way (a real
    provider's LLMError raised mid-generation) can no longer become an HTTP
    status -- `200 OK`/`text/event-stream` is already committed to the
    client by then -- so it surfaces in-band instead, as this stream's own
    single terminal `{"type": "error"}` event; the HTTP status for that
    response is, and stays, `200`. This asymmetry is deliberate and
    documented in contracts/internal-chat.md, not an oversight.
    """
    events = _run_pipeline_step(lambda: chat_service.handle_chat_stream(request))
    return StreamingResponse(_iter_sse(events), media_type='text/event-stream')
