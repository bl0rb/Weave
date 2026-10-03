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

import re
from collections.abc import Callable, Iterator
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.auth import require_service_token
from app.schemas.bot import BotConfig, BotRetrievalSummary, BotSummary, LlmEndpointSummary
from app.schemas.chat import ChatRequest, ChatResponse, ChatStreamEvent, ChatUser
from app.services import chat as chat_service
from app.services.botconfig import BotNotFoundError, list_bots
from app.services.chat_config_client import ChatConfigUnavailable
from app.services.chat_config_client import DEFAULT_ENDPOINT, fetch_chat_endpoints, fetch_chat_provider
from app.services.llm import LLMError, OpenAICompatibleLLM
from app.services.n8n_client import N8nUnavailable
from app.services.retrieval_client import RetrievalUnavailable

router = APIRouter(prefix='/internal', dependencies=[Depends(require_service_token)])

_T = TypeVar('_T')


class ConversationTitleRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=4000)


class ConversationTitleResponse(BaseModel):
    title: str


# The title names what the USER asked about -- never what the answer said.
# The answer is only short context (a failed "no data found" answer used to
# turn into a title about the missing data).
_TITLE_SYSTEM_PROMPT = (
    'Du benennst Gespräche. Nenne das THEMA der Nutzerfrage als kurzen Titel mit höchstens sechs Wörtern, '
    'in der Sprache der Frage. Kein Markdown, keine Anführungszeichen, kein Präfix (kein "Titel:", kein Name) '
    'und keine Aussage darüber, ob Informationen gefunden wurden oder fehlen. '
    'Die Antwort dient nur als Kontext; beschreibe sie nicht. Antworte ausschließlich mit dem Titel.'
)
_TITLE_ANSWER_CONTEXT_CHARS = 300
_TITLE_MAX_CHARS = 80
_TITLE_PREFIX = re.compile(r'^(?:titel|title|thema|topic|claude|assistent|assistant|antwort|answer)\s*:\s*', re.IGNORECASE)
_TITLE_QUOTES = ' "\'`„“”‚‘’«»'


def _title_messages(question: str, answer: str) -> list[dict[str, str]]:
    context = ' '.join(answer.split())[:_TITLE_ANSWER_CONTEXT_CHARS]
    return [
        {'role': 'system', 'content': _TITLE_SYSTEM_PROMPT},
        {'role': 'user', 'content': f'Frage: {question}\nAntwort (nur Kontext): {context}'},
    ]


def _clean_title(raw: str) -> str:
    """Model output -> a plain one-line title: no markdown emphasis/heading/
    code characters, no "Titel:"/"Claude:"-style prefix, no surrounding
    quotes, whitespace collapsed, length capped. Empty if nothing is left."""
    text = _strip_quotes(re.sub(r'[*`]+|(?<!\w)#+(?!\w)|(?<!\w)_+|_+(?!\w)', '', raw).strip())
    lines = _TITLE_PREFIX.sub('', text).strip().splitlines()
    title = _strip_quotes(' '.join(lines[0].split())) if lines else ''
    return title[:_TITLE_MAX_CHARS].rstrip()


def _strip_quotes(text: str) -> str:
    """Drop quotes that wrap the whole title, never a quote that belongs to
    it (`Bedeutung von "Onboarding"` keeps its closing quote)."""
    while len(text) >= 2 and text[0] in _TITLE_QUOTES and text[-1] in _TITLE_QUOTES:
        text = text[1:-1].strip()
    return text


def _endpoint_catalog(bots: list[BotConfig]) -> list[dict]:
    """Enabled central LLM endpoints, fetched only when a bot offers a
    choice. A failing catalog hides the picker instead of the roster."""
    if not any(bot.model.endpoints for bot in bots):
        return []
    try:
        return fetch_chat_endpoints() or []
    except ChatConfigUnavailable:
        return []


def _llm_endpoints(bot: BotConfig, catalog: list[dict]) -> list[LlmEndpointSummary]:
    """The endpoints a user may pick for `bot`, its own first; empty
    unless there is an actual choice."""
    if not bot.model.endpoints or bot.model.provider == 'n8n':
        return []
    by_id = {item['id']: item for item in catalog}
    offered = list(by_id) if '*' in bot.model.endpoints else bot.model.endpoints
    ids = dict.fromkeys([bot.model.endpoint or DEFAULT_ENDPOINT, *offered])
    choices = [LlmEndpointSummary.model_validate(by_id[endpoint_id]) for endpoint_id in ids if endpoint_id in by_id]
    return choices if len(choices) > 1 else []


def _summary(bot: BotConfig, catalog: list[dict] | None = None) -> BotSummary:
    return BotSummary(
        id=bot.id,
        name=bot.name,
        description=bot.description,
        retrieval=BotRetrievalSummary(enabled=bot.retrieval.enabled),
        kind='n8n' if bot.model.provider == 'n8n' else 'llm',
        teams=list(bot.permissions.teams),
        public=bot.permissions.is_public,
        collections=list(bot.retrieval.collections),
        llm_endpoints=_llm_endpoints(bot, catalog or []),
    )


def _bots_or_503() -> list[BotConfig]:
    try:
        return list_bots()
    except ChatConfigUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get('/bots', response_model=list[BotSummary])
def list_bots_endpoint() -> list[BotSummary]:
    bots = _bots_or_503()
    catalog = _endpoint_catalog(bots)
    return [_summary(bot, catalog) for bot in bots]


@router.post('/bots/visible', response_model=list[BotSummary])
def list_visible_bots_endpoint(user: ChatUser) -> list[BotSummary]:
    """The bots ``user`` may chat with (ADR 0008) -- the same check a chat
    turn runs, so a gateway never lists a bot that would answer 403."""
    visible = []
    for bot in _bots_or_503():
        try:
            chat_service._check_permissions(bot, user)
        except chat_service.BotPermissionDenied:
            continue
        visible.append(bot)
    catalog = _endpoint_catalog(visible)
    return [_summary(bot, catalog) for bot in visible]


@router.get('/bot-configs', response_model=list[BotConfig])
def list_bot_configs_endpoint() -> list[BotConfig]:
    """One authenticated snapshot for admin editing, independent of roster size.

    BotConfig's SecretStr fields retain the existing detail endpoint's
    masked serialization; this route never exports credential plaintext.
    """
    try:
        return list_bots()
    except ChatConfigUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get('/bots/{bot_id}', response_model=BotConfig)
def get_bot_endpoint(bot_id: str) -> BotConfig:
    try:
        return next(bot for bot in list_bots() if bot.id == bot_id)
    except StopIteration as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='bot not found') from exc
    except ChatConfigUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post('/conversation-title', response_model=ConversationTitleResponse)
def conversation_title(request: ConversationTitleRequest) -> ConversationTitleResponse:
    try:
        config = fetch_chat_provider()
        if config is None or not config.enabled:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Chat provider is disabled')
        result = OpenAICompatibleLLM(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout=min(config.timeout_seconds, 8.0),
            max_attempts=1,
        ).chat(_title_messages(request.question, request.answer), temperature=0.2)
    except (ChatConfigUnavailable, LLMError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Titel konnte nicht erzeugt werden') from exc
    title = _clean_title(result.content)
    if not title:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Titel konnte nicht erzeugt werden')
    return ConversationTitleResponse(title=title)


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
    except ChatConfigUnavailable as exc:
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


def _iter_sse(events: Iterator[ChatStreamEvent | chat_service.Keepalive]) -> Iterator[str]:
    """One SSE line per item from `events` -- `_sse_event` for an ordinary
    `ChatStreamEvent`, or the literal comment line `': keepalive\\n\\n'` for
    `chat_service.KEEPALIVE` (see that sentinel's own docstring for why it
    is never one of the five JSON event shapes `_sse_event` itself handles,
    and app/services/chat.py's `_stream_deferred_n8n` for the one caller
    that ever actually yields it). Compared with `is`, not `==` --
    `Keepalive` carries no data of its own to equal-compare, and `is`
    matches this module's own singleton-sentinel style elsewhere.
    """
    for event in events:
        if event is chat_service.KEEPALIVE:
            yield ': keepalive\n\n'
        else:
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
