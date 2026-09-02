"""LLM-provider abstraction for the chat pipeline (see README's "LLM-
Provider-Abstraktion", app/schemas/bot.py's ModelConfig).

Two providers implement the LLMProvider protocol below:

- FakeLLM (provider 'fake', settings.llm_provider's default and every bot
  YAML's ModelConfig.provider default): deterministic, dependency-free --
  no network call, no model, usable in tests and local dev exactly like
  Weave-Retrieval's FakeEmbeddingProvider/FakeReranker are for their own
  pipeline stages (app/services/embeddings.py, app/services/reranker.py in
  that service).
- OpenAICompatibleLLM (provider 'openai'): talks to any OpenAI-compatible
  `POST {base_url}/v1/chat/completions` endpoint over httpx, with the same
  "retry 429/5xx/transport failures, give up on the rest" split
  Weave-Retrieval's OpenAICompatibleProvider/HttpReranker use for their own
  outbound calls.

get_llm() is the seam a caller (the future chat pipeline, tests) is
expected to depend on -- mirrors those same modules' get_provider()/
get_reranker() factories: callers only ever hold an LLMProvider, never
import a concrete provider class themselves.

LLMError is the one exception OpenAICompatibleLLM raises for any provider
failure -- FakeLLM never raises at all, by construction (see its own
docstring). `transient` tells a caller whether retrying the SAME request
later might succeed (an exhausted-retries 429/5xx/transport failure) versus
never (a 4xx -- the request itself is the problem); `status_code` carries
the upstream HTTP status when one was actually received (None for a
network-level failure, where there is no status to report).

Streaming (see app/services/chat.py's `handle_chat_stream`): both providers
also implement `chat_stream(messages, model, temperature) -> Iterator[str]`,
yielding the answer as a sequence of text deltas whose concatenation is
byte-for-byte the same string `chat()` would have returned for the identical
call. `iter_chat_stream()` at the bottom of this module is the seam a caller
is actually expected to use instead of `provider.chat_stream(...)` directly
-- see its own docstring for why: it is what lets a caller treat literally
any LLMProvider-shaped object as streaming-capable, even one that only ever
implements the required `chat()` method, by falling back to that provider's
whole answer as a single delta.
"""

import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class LLMResult:
    content: str
    model: str
    usage: LLMUsage | None = None


class LLMError(Exception):
    """Raised for any OpenAICompatibleLLM failure: an unreachable endpoint, a
    non-2xx response that either exhausted its retries or wasn't retryable
    in the first place, or a response body that doesn't parse as expected.
    See this module's docstring for `transient`/`status_code`.
    """

    def __init__(self, message: str, *, transient: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status_code = status_code


class LLMProvider(Protocol):
    """The seam get_llm() returns and every caller (the future chat
    pipeline, tests) depends on instead of a concrete provider class -- see
    this module's docstring."""

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
    ) -> Iterator[str]: ...


# --- FakeLLM ---------------------------------------------------------------

# A "context block" is any message with role 'system' whose content embeds
# one or more lines starting with `[source N]` (case-insensitive, N a
# positive integer) -- the convention a retrieval-aware caller is expected
# to use when it prepends retrieved chunks to the system prompt, one such
# marker line per chunk, before that chunk's own text. FakeLLM sums these
# markers across every system-role message in `messages` to get the total
# source count N, and appends ' [context:N]' to its reply whenever N > 0 --
# this lets a test assert "retrieval actually reached the LLM call, with
# exactly this many sources" without FakeLLM understanding anything about
# what a source's text actually says. A real provider (OpenAICompatibleLLM)
# never looks for this marker at all; it is purely a FakeLLM/test-and-dev
# convention.
_CONTEXT_ROLE = 'system'
_SOURCE_MARKER_RE = re.compile(r'^\[source\s+\d+\]', re.IGNORECASE | re.MULTILINE)

_FAKE_DEFAULT_MODEL = 'fake-chat'


def _count_context_sources(messages: list[dict[str, str]]) -> int:
    return sum(
        len(_SOURCE_MARKER_RE.findall(message.get('content') or ''))
        for message in messages
        if message.get('role') == _CONTEXT_ROLE
    )


_TEXT_DELTA_RE = re.compile(r'\S+\s*')


def iter_text_deltas(text: str) -> Iterator[str]:
    """Split `text` into a sequence of small chunks -- each a run of
    non-whitespace plus whatever whitespace immediately follows it -- whose
    concatenation, in order, reconstructs `text` exactly. This is FakeLLM's
    own `chat_stream` behaviour below, but it is deliberately a public,
    standalone function rather than a private method on that class: any
    plain string this pipeline needs to stream out as multiple normal
    'delta' events -- not only an LLM-generated answer, but also
    app/services/chat.py's own static guard/V1-unsupported-intent replies --
    goes through this exact same chunker, so a streamed turn never special-
    cases "this text came from an LLM" vs. "this text is fixed copy" in how
    it is broken into deltas. An empty `text` yields nothing at all (zero
    deltas, not one empty delta).
    """
    return (match.group() for match in _TEXT_DELTA_RE.finditer(text))


def _last_user_message(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get('role') == 'user':
            return message.get('content') or ''
    # No user-role entry at all is not expected in practice (a chat always
    # has at least one user turn) but is not this function's job to reject
    # -- an empty string just means there is nothing to echo back.
    return ''


class FakeLLM:
    """Deterministic, dependency-free LLMProvider: no network call, no
    model, no state. `chat()` always succeeds and never raises.

    The reply is `'[fake-llm] ' + <the last user-role message's content>`,
    optionally suffixed with `' [context:N]'` when a system-role context
    block carrying N `[source ...]` markers was present -- see this
    module's own comment above for that convention. Same input `messages`
    (and `model`) always produces the exact same LLMResult, every time, in
    this process or any other.

    `usage` is filled in deterministically too, purely by whitespace word
    count (`prompt_tokens` = total words across every message's content,
    `completion_tokens` = words in the reply) -- not real token counts, but
    stable and cheap enough to exercise callers that expect `usage` to be
    present, exactly like a real provider's would be.

    `temperature` is accepted (for LLMProvider conformance) and ignored --
    there is nothing stochastic here to temper.
    """

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        last_user = _last_user_message(messages)
        reply = f'[fake-llm] {last_user}'

        source_count = _count_context_sources(messages)
        if source_count:
            reply += f' [context:{source_count}]'

        prompt_tokens = sum(len((message.get('content') or '').split()) for message in messages)
        completion_tokens = len(reply.split())

        return LLMResult(
            content=reply,
            model=model or _FAKE_DEFAULT_MODEL,
            usage=LLMUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        )

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
    ) -> Iterator[str]:
        """The exact same deterministic reply `chat()` would return for this
        `messages`/`model`, split into several deltas via `iter_text_deltas`
        -- purely so a caller (and its own tests) can observe genuine
        multi-delta streaming without a real network stream anywhere in the
        loop. `''.join(FakeLLM().chat_stream(messages, model=model)) ==
        FakeLLM().chat(messages, model=model).content` always holds.
        """
        yield from iter_text_deltas(self.chat(messages, model=model, temperature=temperature).content)


# --- OpenAICompatibleLLM ----------------------------------------------------

# "max 3" per the task spec: the request is attempted up to 3 times total
# (the initial attempt plus up to 2 retries), not 3 retries on top of the
# initial attempt. Same shape and same values as Weave-Retrieval's
# app/services/embeddings.py/app/services/reranker.py.
_DEFAULT_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


def _backoff_seconds(attempt: int) -> float:
    index = min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
    return _RETRY_BACKOFF_SECONDS[max(index, 0)]


def _parse_chat_response(response: httpx.Response, *, fallback_model: str) -> LLMResult:
    """Extract an LLMResult from a 200 `/v1/chat/completions` response
    shaped like OpenAI's: `{"choices": [{"message": {"content": ...}}, ...],
    "model": ..., "usage": {"prompt_tokens": ..., "completion_tokens":
    ...}}`. `usage` is optional on the wire (some OpenAI-compatible servers
    omit it) and therefore optional on LLMResult too; `model` falls back to
    the model actually requested (`fallback_model`) when the response
    doesn't echo one back, same defensive shape-checking as
    Weave-Retrieval's _parse_embeddings_response/_parse_rerank_response for
    their own externally-controlled response bodies.
    """
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError('LLM response body is not valid JSON') from exc

    choices = data.get('choices') if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise LLMError("LLM response is missing a non-empty 'choices' list")

    first_choice = choices[0]
    message = first_choice.get('message') if isinstance(first_choice, dict) else None
    content = message.get('content') if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LLMError('LLM response choice has no message.content string')

    usage = None
    usage_raw = data.get('usage') if isinstance(data, dict) else None
    if isinstance(usage_raw, dict):
        prompt_tokens = usage_raw.get('prompt_tokens')
        completion_tokens = usage_raw.get('completion_tokens')
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            usage = LLMUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

    model_name = data.get('model') if isinstance(data, dict) else None
    if not isinstance(model_name, str) or not model_name:
        model_name = fallback_model

    return LLMResult(content=content, model=model_name, usage=usage)


def _iter_sse_deltas(response: httpx.Response, *, url: str) -> Iterator[str]:
    """Parse `response` (already confirmed `status_code == 200`, still open
    for streaming reads) as an OpenAI-compatible `/v1/chat/completions` SSE
    stream: each event is one line `'data: <json>'`; blank lines and any
    other SSE fields (e.g. `'event: ...'`) are silently ignored -- an
    OpenAI-compatible server never sends them, but a conforming SSE consumer
    tolerates them anyway. Each parsed JSON object's
    `choices[0].delta.content` is yielded whenever it is present and a
    non-empty string; the literal line `'data: [DONE]'` ends the stream (the
    generator simply returns, it does not raise) -- the same terminal
    marker every OpenAI-compatible streaming endpoint sends, per that API's
    own convention.

    Raises LLMError for a data line that isn't valid JSON -- there is no
    retry-worthy classification for a malformed body, exactly like
    `_parse_chat_response`'s identical JSON failure above; any other
    exception (in practice, an httpx transport error while reading the next
    line) propagates unchanged, for `OpenAICompatibleLLM.chat_stream` itself
    to classify.
    """
    for raw_line in response.iter_lines():
        line = raw_line.strip()
        if not line or not line.startswith('data:'):
            continue
        data = line[len('data:'):].strip()
        if data == '[DONE]':
            return
        try:
            event = json.loads(data)
        except ValueError as exc:
            raise LLMError(f'LLM stream from {url!r} sent a non-JSON data line: {data[:200]!r}') from exc

        choices = event.get('choices') if isinstance(event, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        first_choice = choices[0]
        delta = first_choice.get('delta') if isinstance(first_choice, dict) else None
        content = delta.get('content') if isinstance(delta, dict) else None
        if isinstance(content, str) and content:
            yield content


def _chat_completions_url(base_url: str) -> str:
    """Accept a provider root as well as the common base ending in /v1."""
    normalized = base_url.rstrip('/')
    return f'{normalized}/chat/completions' if normalized.endswith('/v1') else f'{normalized}/v1/chat/completions'


class OpenAICompatibleLLM:
    """LLMProvider for any OpenAI-compatible `/v1/chat/completions`
    endpoint.

    chat() POSTs `{"model": ..., "messages": [...], "temperature": ...}`
    (temperature omitted entirely when None, so a server-side default
    applies rather than an explicit `null`) to
    `{base_url}/v1/chat/completions` with `Authorization: Bearer {api_key}`.

    Retry policy per request: a 429, a 5xx, or a transport-level failure
    (connection refused, timeout, ...) is retried with backoff up to
    `max_attempts` times total, raising LLMError(transient=True) if every
    attempt fails; any other 4xx (bad request, bad API key, unknown model,
    ...) raises LLMError(transient=False) immediately -- retrying it
    changes nothing. Identical policy to Weave-Retrieval's
    OpenAICompatibleProvider/HttpReranker.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        resolved_base_url = base_url if base_url is not None else settings.llm_base_url
        self._base_url = resolved_base_url.rstrip('/')
        self._api_key = api_key if api_key is not None else settings.llm_api_key
        self._default_model = model if model is not None else settings.llm_default_model
        self._timeout = timeout if timeout is not None else settings.llm_timeout_seconds
        self._max_attempts = max(1, max_attempts)

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        url = _chat_completions_url(self._base_url)
        headers = {'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'}
        resolved_model = model or self._default_model
        payload: dict = {'model': resolved_model, 'messages': messages}
        if temperature is not None:
            payload['temperature'] = temperature

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=self._timeout)
            except httpx.HTTPError as exc:
                if attempt >= self._max_attempts:
                    raise LLMError(
                        f'LLM request to {url!r} failed after {attempt} attempt(s): {exc}',
                        transient=True,
                    ) from exc
                logger.warning('LLM request to %r failed (attempt %d/%d): %s', url, attempt, self._max_attempts, exc)
                time.sleep(_backoff_seconds(attempt))
                continue

            if response.status_code == 200:
                return _parse_chat_response(response, fallback_model=resolved_model)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self._max_attempts:
                    raise LLMError(
                        f'LLM request to {url!r} returned HTTP {response.status_code} after {attempt} attempt(s)',
                        transient=True,
                        status_code=response.status_code,
                    )
                logger.warning(
                    'LLM request to %r returned HTTP %d (attempt %d/%d); retrying',
                    url, response.status_code, attempt, self._max_attempts,
                )
                time.sleep(_backoff_seconds(attempt))
                continue

            # Any other 4xx (400 bad request, 401/403 bad key, 404 unknown
            # model, ...) is not retryable -- retrying an unchanged request
            # against an unchanged endpoint would only get the same 4xx back.
            raise LLMError(
                f'LLM request to {url!r} returned HTTP {response.status_code}: {response.text[:500]}',
                transient=False,
                status_code=response.status_code,
            )

        # Unreachable -- the loop above always returns or raises on its
        # final iteration (attempt == self._max_attempts). Kept as a
        # defensive backstop rather than trusting that invariant silently.
        raise LLMError(f'LLM request to {url!r} did not complete', transient=True)

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
    ) -> Iterator[str]:
        """`chat()`'s streaming counterpart: the identical request, plus
        `"stream": true`, read incrementally via `_iter_sse_deltas` instead
        of parsed as one JSON body. Same retry policy as `chat()` for
        everything up to and including the response headers (a 429/5xx/
        transport failure retries with backoff up to `max_attempts` times;
        any other 4xx raises immediately, never retried) -- with one
        necessary addition `chat()` has no equivalent of: once at least one
        delta has already been handed to THIS method's own caller for the
        current attempt, a failure while reading the rest of that same
        stream (a transport error mid-read) is never retried either, even
        if it would otherwise qualify -- retrying from scratch at that point
        would silently restart the answer on top of text the caller already
        received, corrupting the transcript instead of merely delaying it.
        That case raises LLMError(transient=True) immediately instead; see
        app/services/chat.py's `_stream_prepared_turn` for how a caller
        turns this into a single terminal `{"type": "error"}` SSE event.
        """
        url = _chat_completions_url(self._base_url)
        headers = {'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'}
        resolved_model = model or self._default_model
        payload: dict = {'model': resolved_model, 'messages': messages, 'stream': True}
        if temperature is not None:
            payload['temperature'] = temperature

        for attempt in range(1, self._max_attempts + 1):
            started = False
            try:
                with httpx.stream('POST', url, headers=headers, json=payload, timeout=self._timeout) as response:
                    if response.status_code == 200:
                        for delta in _iter_sse_deltas(response, url=url):
                            started = True
                            yield delta
                        return

                    response.read()
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt >= self._max_attempts:
                            raise LLMError(
                                f'LLM streaming request to {url!r} returned HTTP {response.status_code} '
                                f'after {attempt} attempt(s)',
                                transient=True,
                                status_code=response.status_code,
                            )
                        logger.warning(
                            'LLM streaming request to %r returned HTTP %d (attempt %d/%d); retrying',
                            url, response.status_code, attempt, self._max_attempts,
                        )
                        time.sleep(_backoff_seconds(attempt))
                        continue

                    # Any other 4xx -- not retryable, same reasoning as chat().
                    raise LLMError(
                        f'LLM streaming request to {url!r} returned HTTP {response.status_code}: '
                        f'{response.text[:500]}',
                        transient=False,
                        status_code=response.status_code,
                    )
            except httpx.HTTPError as exc:
                if started:
                    raise LLMError(f'LLM stream from {url!r} was interrupted: {exc}', transient=True) from exc
                if attempt >= self._max_attempts:
                    raise LLMError(
                        f'LLM streaming request to {url!r} failed after {attempt} attempt(s): {exc}',
                        transient=True,
                    ) from exc
                logger.warning(
                    'LLM streaming request to %r failed (attempt %d/%d): %s',
                    url, attempt, self._max_attempts, exc,
                )
                time.sleep(_backoff_seconds(attempt))
                continue

        # Unreachable -- see the identical backstop at the end of chat().
        raise LLMError(f'LLM streaming request to {url!r} did not complete', transient=True)


def get_llm(provider_name: str | None = None) -> LLMProvider:
    """Instantiate the LLMProvider named by `provider_name` -- normally a
    specific bot's own `BotConfig.model.provider` (app/schemas/bot.py) --
    falling back to `settings.llm_provider` when `provider_name` is falsy
    (None or ''). A bot YAML with no `model.provider` key already resolves
    through ModelConfig's own 'fake' default before this function ever sees
    it; the fallback here matters for a programmatic caller that has no
    BotConfig at hand and passes None directly.

    'fake' -> FakeLLM(), always available, no configuration required.
    'openai' -> OpenAICompatibleLLM(), reads settings.llm_base_url/
    llm_api_key/llm_default_model/llm_timeout_seconds.
    Anything else raises ValueError -- fail fast rather than silently
    falling back to a provider nobody asked for (same discipline as
    Weave-Retrieval's app/services/embeddings.py:get_provider()/
    app/services/reranker.py:get_reranker()).
    """
    resolved = (provider_name or settings.llm_provider).strip().lower()
    if resolved == 'fake':
        return FakeLLM()
    if resolved == 'openai':
        return OpenAICompatibleLLM()
    raise ValueError(f"unknown LLM provider {resolved!r} (expected 'fake' or 'openai')")


def iter_chat_stream(
    provider: LLMProvider,
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float | None = None,
) -> Iterator[str]:
    """The seam app/services/chat.py's `handle_chat_stream` actually calls
    for every streaming turn, instead of `provider.chat_stream(...)`
    directly -- this is what lets that pipeline treat ANY LLMProvider as
    streaming-capable, including a hypothetical one that implements only the
    required `chat()` method. `LLMProvider` is a plain `Protocol` (see this
    module's docstring), never enforced at runtime the way an ABC would be,
    so `chat_stream` being part of its declared shape does not by itself
    guarantee every object handed in here actually has one.

    A provider with no `chat_stream` of its own falls back to its complete
    `chat()` answer, yielded as a single delta -- exactly the "Provider ohne
    Streaming-Faehigkeit" fallback the streaming pipeline needs so IT never
    has to special-case a non-streaming provider itself; the caller can
    always just iterate this function's return value. FakeLLM and
    OpenAICompatibleLLM -- every provider this module actually ships -- both
    implement real, multi-delta `chat_stream`, so this fallback is never
    exercised by either of them in practice; it exists so `LLMProvider` can
    keep growing without forcing a simultaneous update to every provider
    that already implements it.
    """
    stream = getattr(provider, 'chat_stream', None)
    if stream is not None:
        yield from stream(messages, model=model, temperature=temperature)
        return
    yield provider.chat(messages, model=model, temperature=temperature).content
