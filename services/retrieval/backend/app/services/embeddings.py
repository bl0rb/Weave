"""Query-embedding provider abstraction for the hybrid-search pipeline.

CRITICAL: whatever provider/model/dimension this module resolves to at query
time MUST be the exact same model and dimension Weave-Knowledge indexed
`chunks.embedding` with (see settings.embedding_model/embedding_dimension's
own docstrings in app/core/config.py, and
contracts/chunk-store.md). A query embedded under a different model or dimension than
the stored vectors is not merely less accurate -- pgvector's cosine distance
between two incompatible embedding spaces is a meaningless number, and
vector_search() additionally guards this at the SQL/Python level by only
ever comparing against chunks whose own `embedding_model` matches
settings.embedding_model (see app/services/search.py). This module cannot
enforce the match itself (it has no way to know what Weave-Knowledge
actually indexed with); the operational contract is "both services' env
config is provisioned from the same EMBEDDING_PROVIDER/EMBEDDING_MODEL/
EMBEDDING_DIMENSION values", full stop.

Deliberately a thinned-down copy of Weave-Knowledge's own
app/services/embeddings.py: this service only ever embeds ONE piece of text
per search request (the incoming query), never a batch of chunks meant for
persistence -- so there is no `embed_chunks()` "embed and persist" step and
no dependency on app.services.enrichment's breadcrumb-prefixing at all.
`embed_query(text) -> list[float]` is the one entry point services/search.py
actually calls.

FakeEmbeddingProvider's algorithm is copied byte-for-byte from
Weave-Knowledge's own version: for the SAME input text, both services MUST
produce the IDENTICAL vector, or even the deterministic dev/test provider
would make the vector-search leg meaningless (a chunk embedded by
Weave-Knowledge's fake provider and a query embedded by a differently-seeded
fake provider would never score anywhere near 1.0 for identical text, which
is exactly the invariant this service's own tests rely on -- see
tests/test_search_service.py).
"""

import hashlib
import logging
import math
import random
import time
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0
# "max 3" per Weave-Knowledge's own embeddings.py: the request is attempted
# up to 3 times total (the initial attempt plus up to 2 retries).
_DEFAULT_MAX_ATTEMPTS = 3
# Backoff before each retry, indexed by the attempt number that just failed
# -- same shape and same values as Weave-Knowledge's own
# app/services/embeddings.py:_RETRY_BACKOFF_SECONDS, which in turn mirrors
# Weave-Ingest's app/workers/webhook_tasks.py:_BACKOFF_SECONDS.
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


def _backoff_seconds(attempt: int) -> float:
    index = min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
    return _RETRY_BACKOFF_SECONDS[max(index, 0)]


class EmbeddingProviderError(Exception):
    """Raised for any embedding-provider failure: an unreachable endpoint, a
    non-2xx response that either exhausted its retries or wasn't retryable
    in the first place, or a response body that doesn't parse as expected.
    """


class EmbeddingProvider(Protocol):
    """The seam get_provider() returns and every caller (services/search.py's
    search()) depends on instead of a concrete provider class -- mirrors the
    PageSource-style Protocol pattern used across every Weave backend, see
    Weave-Knowledge's own app/services/embeddings.py for the sibling this was
    copied from."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_query(self, text: str) -> list[float]: ...


class FakeEmbeddingProvider:
    """Deterministic, dependency-free EmbeddingProvider: the vector for a
    given text is drawn from a `random.Random` seeded with that text's own
    sha256 digest, then L2-normalized. Same text in -> same unit vector out,
    every time, in this process or any other -- no state, no network call.

    This is settings.embedding_provider's default so local dev and the
    pytest suite never need a real embedding endpoint. The algorithm below
    is byte-for-byte identical to Weave-Knowledge's own FakeEmbeddingProvider
    (app/services/embeddings.py) -- see this module's own docstring for why
    that identity matters.
    """

    def __init__(self, *, dimension: int | None = None, model_name: str | None = None) -> None:
        self._dimension = dimension if dimension is not None else settings.embedding_dimension
        self._model_name = model_name if model_name is not None else settings.embedding_model

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_query(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        seed = int.from_bytes(digest[:8], 'big')
        rng = random.Random(seed)
        vector = [rng.uniform(-1.0, 1.0) for _ in range(self._dimension)]
        norm = math.sqrt(sum(component * component for component in vector))
        if norm == 0:
            # Every one of `dimension` independent uniform draws landing on
            # exactly 0.0 is astronomically unlikely, but a zero vector
            # can't be normalized -- fall back to a fixed unit vector rather
            # than dividing by zero.
            vector = [0.0] * self._dimension
            vector[0] = 1.0
            norm = 1.0
        return [component / norm for component in vector]


def _parse_embedding_response(response: httpx.Response) -> list[float]:
    """Extract the single embedding vector from a 200 /v1/embeddings
    response for a one-item request. Unlike Weave-Knowledge's batched
    _parse_embeddings_response, there is no `index` field ambiguity to
    resolve here -- a one-item request has exactly one entry to look at --
    but the same defensive shape-checking applies since this is still an
    externally-controlled response body.
    """
    try:
        data = response.json()
    except ValueError as exc:
        raise EmbeddingProviderError('embedding response body is not valid JSON') from exc

    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise EmbeddingProviderError("embedding response is missing a non-empty 'data' list")

    item = items[0]
    if not isinstance(item, dict):
        raise EmbeddingProviderError('embedding response item has unexpected shape')
    embedding = item.get('embedding')
    if not isinstance(embedding, list) or not embedding:
        raise EmbeddingProviderError('embedding response item has no embedding vector')
    return [float(value) for value in embedding]


class OpenAICompatibleProvider:
    """EmbeddingProvider for any OpenAI-compatible /v1/embeddings endpoint.

    embed_query() POSTs a single-item batch to `{base_url}/v1/embeddings`
    with `Authorization: Bearer {api_key}` and `{"model": ..., "input":
    [text], "input_type": "query"}` -- a search request only ever needs to
    embed the one incoming query string, so there is no batching concern
    here the way there is on Weave-Knowledge's chunk-indexing side.

    Retry policy per request: a 429, a 5xx, or a transport-level failure
    (connection refused, timeout, ...) is retried with backoff up to
    `max_attempts` times total; any other 4xx (bad request, bad API key,
    unknown model, ...) raises immediately -- retrying it changes nothing.
    Identical policy to Weave-Knowledge's own OpenAICompatibleProvider.

    Why `input_type` is a request field, not a second model name: this
    class only ever embeds one incoming search string (never chunk text
    meant for the index -- see embed_query() below), so it always sends
    `input_type="query"`. Some embedding families (e.g. the e5 family the
    Weave-Tools embeddings service serves, `intfloat/multilingual-e5-
    small`) are trained with a fixed text prefix that differs for a query
    ("query: ") versus an indexed passage ("passage: "), and the usual way
    an API exposes that distinction is two separate model names -- one for
    indexing, a different one for querying. That is unusable here: this
    module's own docstring above already spells out why -- Weave-Knowledge
    persists the producing model's name on every chunk
    (`chunk.embedding_model`), and this service's own vector_search()
    filters candidate chunks down to `Chunk.embedding_model ==
    settings.embedding_model` -- an exact string match (see
    app/services/search.py). Two model names for the same underlying model
    would mean a query embedded under one name could never match a chunk
    indexed under the other, no matter how similar the actual vectors are.
    `input_type` carries the query/passage distinction instead, as an
    additional field the two endpoints agree on out of band, while the
    `model` field -- the value both services key their compatibility check
    on -- stays identical across every request this class or
    Weave-Knowledge's sibling provider sends. It is optional and additive:
    the Weave-Tools embeddings service treats a request with no
    `input_type` exactly like `"passage"`, so any ordinary
    OpenAI-compatible client that has never heard of this field keeps
    working unchanged. The name itself ("query"/"passage") is borrowed
    from Cohere's own `input_type` field, not an OpenAI-native concept --
    see Weave-Tools' `embeddings/README.md` for the request/response
    contract this was copied from.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        resolved_base_url = base_url if base_url is not None else settings.embedding_base_url
        self._base_url = resolved_base_url.rstrip('/')
        self._api_key = api_key if api_key is not None else settings.embedding_api_key
        self._model = model if model is not None else settings.embedding_model
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        # Not reported by the /v1/embeddings response in any standard way,
        # so this trusts the same settings.embedding_dimension the pgvector
        # column (and Weave-Knowledge's own index) was created with, rather
        # than inspecting a response.
        return settings.embedding_dimension

    def embed_query(self, text: str) -> list[float]:
        url = f'{self._base_url}/v1/embeddings'
        headers = {'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'}
        # input_type is always "query" here -- see this class's own
        # docstring for why that rides along as its own field rather than
        # a second model name.
        payload = {'model': self._model, 'input': [text], 'input_type': 'query'}

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=self._timeout)
            except httpx.HTTPError as exc:
                if attempt >= self._max_attempts:
                    raise EmbeddingProviderError(
                        f'embedding request to {url!r} failed after {attempt} attempt(s): {exc}'
                    ) from exc
                logger.warning('embedding request to %r failed (attempt %d/%d): %s', url, attempt, self._max_attempts, exc)
                time.sleep(_backoff_seconds(attempt))
                continue

            if response.status_code == 200:
                return _parse_embedding_response(response)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self._max_attempts:
                    raise EmbeddingProviderError(
                        f'embedding request to {url!r} returned HTTP {response.status_code} '
                        f'after {attempt} attempt(s)'
                    )
                logger.warning(
                    'embedding request to %r returned HTTP %d (attempt %d/%d); retrying',
                    url, response.status_code, attempt, self._max_attempts,
                )
                time.sleep(_backoff_seconds(attempt))
                continue

            # Any other 4xx (400 bad request, 401/403 bad key, 404 unknown
            # model, ...) is not retryable -- retrying an unchanged request
            # against an unchanged endpoint would only get the same 4xx back.
            raise EmbeddingProviderError(
                f'embedding request to {url!r} returned HTTP {response.status_code}: {response.text[:500]}'
            )

        # Unreachable -- the loop above always returns or raises on its
        # final iteration (attempt == self._max_attempts). Kept as a
        # defensive backstop rather than trusting that invariant silently.
        raise EmbeddingProviderError(f'embedding request to {url!r} did not complete')


def get_provider() -> EmbeddingProvider:
    """Instantiate the embedding provider named by settings.embedding_provider.

    Raises ValueError for anything other than 'fake'/'openai' -- fail fast
    at startup/request time rather than silently falling back to a provider
    nobody asked for.
    """
    provider_name = settings.embedding_provider.strip().lower()
    if provider_name == 'fake':
        return FakeEmbeddingProvider()
    if provider_name == 'openai':
        return OpenAICompatibleProvider()
    raise ValueError(
        f"unknown EMBEDDING_PROVIDER {settings.embedding_provider!r} (expected 'fake' or 'openai')"
    )


def embed_query(text: str) -> list[float]:
    """Embed one search query with whatever provider settings.embedding_provider
    names -- the single entry point app/services/search.py's search() calls.
    A thin convenience wrapper around get_provider().embed_query(text) so
    callers don't need to hold a provider instance across a request at all.
    """
    return get_provider().embed_query(text)
