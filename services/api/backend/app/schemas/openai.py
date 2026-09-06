"""OpenAI-compatible request/response shapes for POST /v1/chat/completions
(app/api/openai_compat.py), allowing clients built against OpenAI's Chat
Completions API to talk to Weave-API as
if it were an OpenAI-compatible LLM endpoint, with `model` standing in for a
Weave-Runtime `bot_id` (see that module's own docstring for the full
request/response mapping).

Deliberately NOT `extra='forbid'` anywhere here, unlike
app/schemas/bot.py's (Weave-Runtime's) bot YAML schema: a real OpenAI client
routinely sends fields this shim has no use for (`temperature`, `top_p`,
`max_tokens`, `stream`, `presence_penalty`, ...) -- pydantic's own default
(`extra='ignore'`) is exactly the tolerance a compatibility shim needs, kept
here by simply never overriding it.
"""

from typing import Literal

from pydantic import BaseModel, Field


class OpenAIChatMessage(BaseModel):
    # OpenAI's Chat Completions API allows a 'system' role turn too (Open
    # WebUI commonly sends one) -- accepted here so the request itself
    # validates, even though app/api/openai_compat.py's own _split_messages
    # drops any system-role entries before they ever reach Weave-Runtime
    # (see that function's docstring for why).
    role: Literal['system', 'user', 'assistant']
    content: str


class OpenAIChatCompletionRequest(BaseModel):
    """POST /v1/chat/completions body. `model` is opaque here (exactly like
    POST /v1/chat's own `bot_id`, app/schemas/chat.py) -- an unknown one
    surfaces as whatever status Weave-Runtime's own /internal/bots lookup
    reports for it (404 today), passed straight through by
    app/api/openai_compat.py.

    `stream` is the one field of the real OpenAI request shape this shim
    actually reads (every other extra field -- `temperature`, `top_p`,
    `max_tokens`, ... -- is still silently ignored, see this module's own
    docstring): `False` (the default, matching a client that omits it
    entirely) keeps `chat_completions()`'s existing one-shot JSON response
    byte-for-byte unchanged; `True` switches that same route to a
    `text/event-stream` response of `chat.completion.chunk` events instead
    (see OpenAIChatCompletionChunk below and app/api/openai_compat.py's own
    `_stream_openai_chunks`)."""

    model: str
    messages: list[OpenAIChatMessage] = Field(min_length=1)
    stream: bool = False


class OpenAIChatCompletionChoice(BaseModel):
    index: int = 0
    message: OpenAIChatMessage
    finish_reason: str = 'stop'


class OpenAIUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OpenAIChatCompletionResponse(BaseModel):
    """The one-shot (non-streaming) Chat Completions response shape. `usage`
    is optional on the wire and always omitted here in practice --
    Weave-Runtime's own chat contract (that service's app/schemas/chat.py
    ChatTrace) reports timings and routing metadata, but no token counts, so
    there is nothing authoritative for this shim to report instead of
    fabricating a number no client should trust."""

    id: str
    object: str = 'chat.completion'
    created: int
    model: str
    choices: list[OpenAIChatCompletionChoice]
    usage: OpenAIUsage | None = None


# --- POST /v1/chat/completions with "stream": true -----------------------
#
# OpenAI's own streaming Chat-Completions shape: instead of one
# OpenAIChatCompletionResponse, the wire carries a `text/event-stream` of
# `'data: <chunk json>\n\n'` lines (app/api/openai_compat.py's own
# `_stream_openai_chunks` produces the framing; these three models are only
# the JSON payload of one such line), terminated by a literal
# `'data: [DONE]\n\n'` line that carries no JSON at all -- OpenAI's own
# sentinel, not a fourth model here.


class OpenAIChatCompletionChunkDelta(BaseModel):
    """What changed in this chunk versus the previous one -- never the full
    message-so-far. `role` is set to `'assistant'` on the FIRST chunk of a
    stream only (every OpenAI client keys off exactly that first-chunk
    signal to start a new assistant turn) and omitted (`None`, dropped from
    the wire by `model_dump(exclude_none=True)`) on every later chunk;
    `content` carries one fragment of the answer text, `None` on the
    final, `finish_reason`-carrying chunk which has no more text to add."""

    role: Literal['assistant'] | None = None
    content: str | None = None


class OpenAIChatCompletionChunkChoice(BaseModel):
    index: int = 0
    delta: OpenAIChatCompletionChunkDelta
    # `None` for every chunk except the last, which carries `'stop'` --
    # never fabricated for any OTHER reason (a guard-triggered or
    # V1-unsupported-intent answer from Weave-Runtime still ends in an
    # ordinary `'stop'` here, exactly like the non-streaming response's own
    # `finish_reason='stop'`, since from this shim's perspective the turn
    # still completed successfully, only the fixed text WHICH was streamed
    # differs).
    finish_reason: str | None = None


class OpenAIChatCompletionChunk(BaseModel):
    id: str
    object: str = 'chat.completion.chunk'
    created: int
    model: str
    choices: list[OpenAIChatCompletionChunkChoice]


# --- GET /v1/models --------------------------------------------------------
#
# OpenAI's own model-listing shape, fed from the same bot registry
# GET /v1/bots already proxies (app/api/bots.py's own list_bots(), see
# app/api/openai_compat.py's `list_models`) -- one entry per Weave-Runtime
# bot, `id` standing in for a bot_id exactly like `model` does on
# OpenAIChatCompletionRequest above. Compatible clients can populate a model
# dropdown from this endpoint instead of requiring a bot_id by hand.


class OpenAIModel(BaseModel):
    """One entry of GET /v1/models' `data` list. `created` has no
    authoritative source -- Weave-Runtime's own bot registry
    (GET /internal/bots, that service's app/services/botconfig.py) carries
    no bot creation timestamp at all -- so this is simply "now", recomputed
    on every call; compatible clients use this field
    only for display/sort purposes, never as a cache key or a value they'd
    notice changing between two calls."""

    id: str
    object: str = 'model'
    created: int
    owned_by: str = 'weave'


class OpenAIModelList(BaseModel):
    object: str = 'list'
    data: list[OpenAIModel]
