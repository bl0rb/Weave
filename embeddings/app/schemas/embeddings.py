"""Request/response shapes for /v1/embeddings and /v1/models.

The /v1/embeddings shapes are copied field-for-field from
Weave-Knowledge's own OpenAI-compatible CLIENT (backend/app/services/
embeddings.py:OpenAICompatibleProvider and _parse_embeddings_response), NOT
guessed from OpenAI's public docs -- that client is this service's actual
contract:

    request:  POST {base_url}/v1/embeddings, body {"model": ..., "input": ...}
    response: {"object": "list", "data": [{"object": "embedding", "index": int,
               "embedding": [float, ...]}, ...], "model": ..., "usage": {...}}

`_parse_embeddings_response` explicitly places each returned embedding at
its OWN `index` field rather than assuming response order matches request
order ("the OpenAI API docs themselves only promise the index field, not
response order") -- app/main.py's handler always emits `data` already
sorted by index (0..N-1, matching the input list), which satisfies that
without requiring the client to re-sort, but the field is still mandatory
here because that client -- and the real OpenAI API -- treat it as the
authoritative position.

`input_type` is this service's own addition on top of that contract (not
part of the OpenAI API): see app/services/encoder.py's module docstring for
why a second field, not a second model name, carries the
query-vs-passage distinction.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

InputType = Literal['query', 'passage']


class EmbeddingsRequest(BaseModel):
    model: str
    input: str | list[str]
    # Cohere-style optional field (see this service's README): absent or
    # explicit "passage" behave identically, so a stock OpenAI client that
    # has never heard of this field keeps working unchanged.
    input_type: InputType | None = Field(default=None)

    @field_validator('input')
    @classmethod
    def _reject_empty_list(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list) and len(value) == 0:
            raise ValueError('input must not be an empty list')
        return value


class EmbeddingItem(BaseModel):
    object: Literal['embedding'] = 'embedding'
    index: int
    embedding: list[float]


class Usage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingsResponse(BaseModel):
    object: Literal['list'] = 'list'
    data: list[EmbeddingItem]
    model: str
    usage: Usage


class ModelInfo(BaseModel):
    id: str
    object: Literal['model'] = 'model'
    owned_by: str = 'weave-tools'


class ModelsResponse(BaseModel):
    object: Literal['list'] = 'list'
    data: list[ModelInfo]


class HealthResponse(BaseModel):
    # 'ok': model loaded and serving. 'starting': still loading (first run
    # downloads the model -- see README). 'error': the background load
    # raised; this process will never become warm on its own. The HTTP
    # status code is 200 in all three cases (see app/main.py's healthcheck
    # docstring for why) -- callers that need readiness, not just liveness,
    # check this field or `warm`, not the status code.
    status: Literal['ok', 'starting', 'error']
    model: str
    dimension: int | None
    threads: int
    warm: bool
