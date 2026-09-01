"""Thin HTTP client for Weave-Runtime's internal surfaces: the bot registry
(GET /v1/bots, GET /v1/bots/{id} in app/api/bots.py), the one-shot chat
surface (POST /v1/chat in app/api/chat.py, POST /v1/chat/completions in
app/api/openai_compat.py), and its streaming counterpart (POST
/v1/chat/stream in app/api/chat.py, POST /v1/chat/completions with
`"stream": true` in app/api/openai_compat.py -- see `chat_stream()` below).

Weave-API is a trusted internal caller here (ADR-0002's service-to-service
auth), not attacker-influenced-URL territory -- `RUNTIME_BASE_URL` is a
fixed deployment constant, not user input -- so this is a plain httpx
client with a bearer token and a timeout, unlike e.g. Weave-Ingest's
app/services/safe_fetch.py (SSRF hardening for admin-configured, externally
reachable endpoints).

`_get`/`_post` are the ONE place that actually talks HTTP to Weave-Runtime
and classifies the outcome (`_raise_for_status` below) -- list_bots(),
get_bot(), and chat() are all thin wrappers over these two, so a network
failure, timeout, non-2xx response, or malformed body is handled exactly
the same way regardless of which of the three a caller uses. Two distinct
exceptions come out of that classification, both subclasses of the
pre-existing `RuntimeClientError` so app/api/bots.py's `except
RuntimeClientError` keeps catching either without change:

- `RuntimeUnavailable`: Weave-Runtime itself is the problem (unreachable,
  timed out, 5xx, or a non-JSON body) -- retryable, mapped to 502 by both
  app/api/bots.py and app/api/chat.py.
- `RuntimeRejected`: Weave-Runtime answered but declined this specific
  request (any other 4xx, e.g. an unknown bot_id or a caller not permitted
  on that bot) -- not retryable by resending the same body. app/api/bots.py
  still maps this to a blanket 502 (its proxy contract makes no distinction
  today), but app/api/chat.py passes the upstream status straight through.

`_client()` is factored out (rather than inlined into `_get`/`_post`)
purely so tests can monkeypatch it to return a fake client/response pair
with no real network I/O and no extra mocking dependency -- see
tests/test_bots_api.py and tests/test_chat_api.py.
"""

import json
from collections.abc import Iterator

import httpx

from app.core.config import settings


class RuntimeClientError(Exception):
    """Base class for any failure talking to Weave-Runtime. `status_code`
    carries the upstream HTTP status when one was actually received (None
    for a network-level failure, where there is no status to report)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RuntimeUnavailable(RuntimeClientError):
    """Weave-Runtime itself is the problem: unreachable, timed out,
    responded 5xx, or returned a body that isn't valid JSON. Retrying later
    against an unchanged request might succeed."""


class RuntimeRejected(RuntimeClientError):
    """Weave-Runtime is reachable and answered, but declined this specific
    request (any other 4xx) -- e.g. an unknown bot_id (404) or a caller not
    permitted on that bot (403). `detail` carries whatever Weave-Runtime's
    own JSON error body said in its `detail` field (None if the body had no
    such field, or wasn't JSON at all) for a caller to pass through
    verbatim. Never retryable by just resending the same request."""

    def __init__(self, message: str, *, status_code: int, detail: str | None = None) -> None:
        super().__init__(message, status_code=status_code)
        self.detail = detail


def _client() -> httpx.Client:
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout_seconds,
        read=settings.http_read_timeout_seconds,
        write=settings.http_read_timeout_seconds,
        pool=settings.http_read_timeout_seconds,
    )
    headers = {'Authorization': f'Bearer {settings.runtime_api_token}'}
    return httpx.Client(base_url=settings.runtime_base_url, headers=headers, timeout=timeout)


def _error_detail(response: httpx.Response) -> str | None:
    """Best-effort extraction of Weave-Runtime's own `{"detail": "..."}`
    error body shape (the same one this service's own HTTPException
    responses use) -- None for anything else, never raises."""
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict) and isinstance(body.get('detail'), str):
        return body['detail']
    return None


def _raise_for_status(response: httpx.Response, *, description: str) -> None:
    """Classify a completed HTTP response into RuntimeUnavailable (5xx) /
    RuntimeRejected (any other 4xx), or return normally for a 2xx/3xx.
    Shared by `_get` and `_post` -- see the module docstring."""
    if response.status_code < 400:
        return
    if response.status_code >= 500:
        raise RuntimeUnavailable(
            f'Weave-Runtime returned HTTP {response.status_code} for {description}',
            status_code=response.status_code,
        )
    raise RuntimeRejected(
        f'Weave-Runtime rejected {description} with HTTP {response.status_code}',
        status_code=response.status_code,
        detail=_error_detail(response),
    )


def _parse_json(response: httpx.Response, *, description: str) -> dict | list:
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeUnavailable(f'Weave-Runtime returned a non-JSON response for {description}') from exc


def _get(path: str) -> dict | list:
    description = f'GET {path}'
    try:
        with _client() as client:
            response = client.get(path)
    except httpx.RequestError as exc:
        raise RuntimeUnavailable(f'Weave-Runtime unreachable: {exc}') from exc

    _raise_for_status(response, description=description)
    return _parse_json(response, description=description)


def _post(path: str, json_body: dict) -> dict | list:
    description = f'POST {path}'
    try:
        with _client() as client:
            response = client.post(path, json=json_body)
    except httpx.RequestError as exc:
        raise RuntimeUnavailable(f'Weave-Runtime unreachable: {exc}') from exc

    _raise_for_status(response, description=description)
    return _parse_json(response, description=description)


def list_bots() -> list:
    """GET /internal/bots -- the full bot registry Weave-Runtime exposes,
    passed straight through by GET /v1/bots."""
    return _get('/internal/bots')


def get_bot(bot_id: str) -> dict:
    """GET /internal/bots/{bot_id}, passed straight through by
    GET /v1/bots/{bot_id}."""
    return _get(f'/internal/bots/{bot_id}')


def _chat_payload(*, bot_id: str, message: str, history: list[dict], user: dict, collections: list[str] | None) -> dict:
    """Shared request-body assembly for chat() and chat_stream() below.
    `collections` is only added to the payload when it isn't `None` -- the
    key is omitted entirely rather than sent as an explicit `null`, so a
    caller that never mentions collections at all produces the EXACT same
    outgoing bytes as before this field existed (see ChatRequest.collections'
    own docstring, app/schemas/chat.py: "byte-for-byte unchanged"). Weave-
    Runtime's own `ChatRequest.collections` already defaults to `None`
    either way, so an omitted key and an explicit `null` are equivalent to
    it -- this is purely about not perturbing existing wire-shape
    assertions for the no-filter case, not a behaviour difference on
    Weave-Runtime's side."""
    payload = {'bot_id': bot_id, 'message': message, 'history': history, 'user': user}
    if collections is not None:
        payload['collections'] = collections
    return payload


def chat(
    *, bot_id: str, message: str, history: list[dict], user: dict, collections: list[str] | None = None
) -> dict:
    """POST /internal/chat -- one turn of a conversation, called by both
    POST /v1/chat (app/api/chat.py, stateful/persisted) and
    POST /v1/chat/completions (app/api/openai_compat.py, the stateless
    OpenAI-compatible shim) once each has assembled `history` its own way.

    The request body sent here is field-for-field Weave-Runtime's own
    `ChatRequest` (that service's app/schemas/chat.py, verified directly
    against that module, not just against README prose): `bot_id: str`,
    `message: str` (non-empty), `history: list[{role, content}]` with
    `role` one of `'user'`/`'assistant'` (Weave-Runtime's `ChatMessage` has
    no `'system'` role -- a bot's system prompt lives in ITS OWN YAML
    config, `BotConfig.system_prompt`, never propagated per-request), and
    `user: {id, team}` (Weave-Runtime's `ChatUser`, both fields optional).

    `history` is the conversation's prior turns ONLY -- oldest first --
    NEVER including `message` itself; Weave-Runtime appends `message` as the
    newest turn on its own side. `user` is the propagated identity/team-
    membership context ADR-0002 describes the gateway attaching to a
    request, so Weave-Runtime can apply team-scoped authorization/retrieval
    filtering (that service's own `permissions.teams` check plus its
    retrieval `allowed_teams` scoping) without a round-trip back here.

    Returns Weave-Runtime's response body verbatim: `{answer, sources,
    trace}` (that service's own `ChatResponse`; README's Output contract
    here is the same shape minus the `conversation_id`/`created_at` fields
    THIS gateway itself owns and adds back for POST /v1/chat -- the
    stateless shim has neither). A `RetrievalUnavailable` on Weave-Runtime's
    side surfaces here as an ordinary HTTP 503, which `_raise_for_status`
    classifies as `RuntimeUnavailable` exactly like any other 5xx -- no
    special-casing needed in this function for that specific upstream
    failure mode.

    `collections`: this caller's own per-request Collections filter
    (ChatRequest.collections, app/schemas/chat.py) -- `None` (the default)
    omits the key entirely from the outgoing body, see `_chat_payload`'s own
    docstring for exactly why that distinction matters.
    """
    payload = _chat_payload(bot_id=bot_id, message=message, history=history, user=user, collections=collections)
    return _post('/internal/chat', payload)


def chat_stream(
    *, bot_id: str, message: str, history: list[dict], user: dict, collections: list[str] | None = None
) -> Iterator[dict]:
    """POST /internal/chat/stream -- the SSE counterpart to chat() above.
    Same request body/field mapping as chat() (see that function's own
    docstring); called by both POST /v1/chat/stream (app/api/chat.py,
    persisted) and POST /v1/chat/completions with `"stream": true"`
    (app/api/openai_compat.py, stateless) once each has assembled `history`
    exactly as it does for the non-streaming call.

    Returns an iterator of already-JSON-decoded event dicts, one per SSE
    `data: ...` line Weave-Runtime sends (contracts/internal-chat.md's
    stream section, and Weave-Runtime's own app/schemas/chat.py
    ChatStreamEvent union): `{"type": "trace", "trace": {...}}` exactly
    once first, then zero or more `{"type": "delta", "text": ...}`, then
    either `{"type": "sources", "sources": [...]}` + `{"type": "done"}` or
    a single terminal `{"type": "error", "detail": ...}` in their place.
    Deliberately returned as plain dicts, not typed pydantic models of our
    own mirroring Weave-Runtime's ChatStreamEvent -- both of this
    function's callers only ever re-serialize each event (either passed
    through close to verbatim, for POST /v1/chat/stream, or translated into
    an OpenAI-shaped chunk, for the completions shim) or read a couple of
    well-known keys off it; adding a second, separately-maintained copy of
    Weave-Runtime's own event schema here would be pure duplication with no
    behaviour it actually needs.

    Deliberately NOT a generator function itself (no `yield` anywhere in
    THIS function's own body) -- mirrors Weave-Runtime's own
    `handle_chat_stream` (that service's app/services/chat.py, see its own
    docstring) being exactly the same shape for exactly the same reason:
    the HTTP call itself -- and Weave-Runtime's full bot-load/permission/
    routing/retrieval/guard preparation behind it -- must run to
    completion, and either of the same two exceptions chat() above can
    raise (`RuntimeUnavailable`/`RuntimeRejected`) must already have been
    raised, BEFORE a caller's own StreamingResponse (and the `200 OK` it
    commits to) exists at all. Only the SSE body itself -- this function's
    *return value*, produced by the separate generator function
    `_iter_chat_stream_events` below -- is lazy.
    """
    payload = _chat_payload(bot_id=bot_id, message=message, history=history, user=user, collections=collections)
    client = _client()
    request = client.build_request('POST', '/internal/chat/stream', json=payload)
    try:
        response = client.send(request, stream=True)
    except httpx.RequestError as exc:
        client.close()
        raise RuntimeUnavailable(f'Weave-Runtime unreachable: {exc}') from exc

    if response.status_code >= 400:
        try:
            response.read()
            _raise_for_status(response, description='POST /internal/chat/stream')
        finally:
            response.close()
            client.close()

    return _iter_chat_stream_events(client, response)


def _iter_chat_stream_events(client: httpx.Client, response: httpx.Response) -> Iterator[dict]:
    """The actual lazy SSE body `chat_stream()` above returns -- one parsed
    JSON event per `data: ...` line (Weave-Runtime's own framing: one
    `data:` field per event, no other SSE fields, since every event already
    names its own kind via its own `type` key, see
    contracts/internal-chat.md). Owns closing both `response` and `client`
    exactly once, however this generator ends: run to completion by its
    caller, abandoned early (Python raises `GeneratorExit` right at the
    suspended `yield` when an unexhausted generator is garbage-collected or
    explicitly `.close()`d, which the `finally` below still catches), or
    ended by a transport failure mid-stream -- re-raised here as
    `RuntimeUnavailable` rather than the raw httpx exception, so every
    caller of `chat_stream()` has exactly one exception hierarchy to catch
    for "talking to Weave-Runtime went wrong", whether that happened before
    the stream even started or midway through it. Callers that need to
    distinguish "the stream ended cleanly" from "it broke off partway" do
    so by the EVENTS they actually saw (a `done`/`error` event, or neither),
    not by whether iterating this generator raised -- see app/api/chat.py's
    own `_stream_and_persist` for why that distinction matters there.
    """
    try:
        for line in response.iter_lines():
            if not line.startswith('data: '):
                continue
            yield json.loads(line[len('data: '):])
    except httpx.RequestError as exc:
        raise RuntimeUnavailable(f'Weave-Runtime stream aborted: {exc}') from exc
    finally:
        response.close()
        client.close()
