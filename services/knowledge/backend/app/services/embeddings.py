"""Embedding provider abstraction for the index pipeline.

Two providers implement the EmbeddingProvider protocol below:

- FakeEmbeddingProvider: deterministic and dependency-free (the vector for a
  given text is seeded from that text's own sha256 digest, so the same input
  always produces the same output, with no network call and no API key).
  This is settings.embedding_provider's default so local dev and the pytest
  suite never need a real embedding endpoint.
- OpenAICompatibleProvider: talks to any OpenAI-compatible /v1/embeddings
  endpoint (vLLM, Ollama's OpenAI-compat mode, LiteLLM, the real OpenAI API)
  over httpx, batched at settings.embedding_batch_size, with retry/backoff
  on 429/5xx and transport-level failures -- same "retry transport-level and
  5xx, give up on the rest" split Weave-Ingest's
  app/workers/webhook_tasks.py uses for outbound delivery.

get_provider() is the seam app/cli.py's `reindex` command and the future
index pipeline (app/workers/tasks.py's index_document) are expected to
depend on -- mirrors the PageSource Protocol in Weave-Ingest's
app/services/confluence.py: callers only ever hold an EmbeddingProvider,
never import a concrete provider class themselves.

embed_chunks() is the shared "embed and persist" step both call sites use.
"""

import hashlib
import logging
import math
import random
import time
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Chunk, Document

logger = logging.getLogger(__name__)

# services/enrichment.py (structure-aware chunking + YAML-frontmatter
# metadata enrichment, built in a parallel stage) landed with an
# `embedding_input(chunk, frontmatter) -> str` function during this change,
# so the import below succeeds and _embedding_input is simply that function.
# The except branch is a defensive fallback for a checkout where it's
# missing (e.g. a partial worktree) with the exact same signature, so
# embed_chunks() below never needs to know which one it got -- kept rather
# than deleted per this task's own instructions; whoever next touches this
# file can drop it once app.services.enrichment is a given.
try:
    from app.services.enrichment import embedding_input as _embedding_input
except ImportError:  # pragma: no cover -- app.services.enrichment exists in this checkout

    def _embedding_input(chunk: Chunk, frontmatter: dict) -> str:
        """Fallback for app.services.enrichment.embedding_input.

        Prefixes the chunk's heading breadcrumb (if any) ahead of its raw
        text, since embedding a bare fragment with no section context is a
        known way to hurt retrieval quality -- but does none of the
        Confluence-breadcrumb-over-heading-path preference the real
        function's docstring describes. `frontmatter` is accepted and
        unused here for exactly that reason: it's the parameter the real
        implementation needs and this stand-in does not.
        """
        if chunk.heading_path:
            return ' > '.join(chunk.heading_path) + '\n\n' + chunk.text
        return chunk.text


class EmbeddingProviderError(Exception):
    """Raised for any embedding-provider failure: an unreachable endpoint, a
    non-2xx response that either exhausted its retries or wasn't retryable
    in the first place, or a response body that doesn't parse as expected.
    """


class EmbeddingProvider(Protocol):
    """The seam get_provider() returns and every caller (embed_chunks(),
    app/cli.py, the future index pipeline) depends on instead of a concrete
    provider class -- see this module's docstring."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbeddingProvider:
    """Deterministic, dependency-free EmbeddingProvider: the vector for a
    given text is drawn from a `random.Random` seeded with that text's own
    sha256 digest, then L2-normalized. Same text in -> same unit vector out,
    every time, in this process or any other -- no state, no network call.

    Deterministic-but-fake output (rather than e.g. all-zero vectors) is
    what makes this usable in tests that assert two different chunks embed
    to two different vectors, or that re-embedding the same text twice is a
    no-op -- a real embedding model's actual semantics are not attempted.
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

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
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


# --- OpenAICompatibleProvider -------------------------------------------------

_DEFAULT_TIMEOUT_SECONDS = 30.0
# "max 3" per the task spec: the request is attempted up to 3 times total
# (the initial attempt plus up to 2 retries), not 3 retries on top of the
# initial attempt.
_DEFAULT_MAX_ATTEMPTS = 3
# Backoff before each retry, indexed by the attempt number that just failed
# (attempt 1 failed -> wait _RETRY_BACKOFF_SECONDS[0] before attempt 2, ...).
# Same shape as Weave-Ingest's app/workers/webhook_tasks.py:_BACKOFF_SECONDS.
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


def _backoff_seconds(attempt: int) -> float:
    index = min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
    return _RETRY_BACKOFF_SECONDS[max(index, 0)]


def _parse_embeddings_response(response: httpx.Response, *, expected_count: int) -> list[list[float]]:
    """Extract `expected_count` embeddings from a 200 /v1/embeddings
    response, placed at their `index` field rather than assumed to already
    be in request order -- the task spec calls this out explicitly, and the
    OpenAI API docs themselves only promise the index field, not response
    order, so a server is free to return entries out of order (or a
    misbehaving one might)."""
    try:
        data = response.json()
    except ValueError as exc:
        raise EmbeddingProviderError('embedding response body is not valid JSON') from exc

    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise EmbeddingProviderError("embedding response is missing a 'data' list")

    ordered: list[list[float] | None] = [None] * expected_count
    for item in items:
        if not isinstance(item, dict):
            raise EmbeddingProviderError('embedding response item has unexpected shape')
        index = item.get('index')
        embedding = item.get('embedding')
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < expected_count):
            raise EmbeddingProviderError(f'embedding response item has out-of-range index {index!r}')
        if not isinstance(embedding, list) or not embedding:
            raise EmbeddingProviderError(f'embedding response item {index} has no embedding vector')
        ordered[index] = [float(value) for value in embedding]

    if any(vector is None for vector in ordered):
        raise EmbeddingProviderError('embedding response is missing one or more expected vectors')
    return ordered  # type: ignore[return-value]


class OpenAICompatibleProvider:
    """EmbeddingProvider for any OpenAI-compatible /v1/embeddings endpoint.

    embed_batch() splits its input into groups of at most `batch_size`
    (default settings.embedding_batch_size) and POSTs each group separately
    to `{base_url}/v1/embeddings` with `Authorization: Bearer {api_key}` and
    `{"model": ..., "input": [...], "input_type": "passage"}`.

    Retry policy per request: a 429, a 5xx, or a transport-level failure
    (connection refused, timeout, ...) is retried with backoff up to
    `max_attempts` times total; any other 4xx (bad request, bad API key,
    unknown model, ...) raises immediately -- retrying it changes nothing.

    Why `input_type` is a request field, not a second model name: this
    class only ever embeds chunk text meant for the index (never a search
    query -- see embed_batch()/_embed_one_batch() below), so it always
    sends `input_type="passage"`. Some embedding families (e.g. the e5
    family the Weave-Tools embeddings service serves, `intfloat/
    multilingual-e5-small`) are trained with a fixed text prefix that
    differs for a query ("query: ") versus an indexed passage ("passage:
    "), and the usual way an API exposes that distinction is two separate
    model names -- one someone would configure for indexing, a different
    one for querying. That is unusable here: this service persists the
    producing model's name on every chunk (`chunk.embedding_model`, see
    embed_chunks() below), and Weave-Retrieval's search filters candidate
    chunks down to `Chunk.embedding_model == settings.embedding_model` --
    an exact string match (its own app/services/search.py). Two model
    names for the same underlying model would mean a query embedded under
    one name could never match a chunk indexed under the other, no matter
    how similar the actual vectors are. `input_type` carries the
    query/passage distinction instead, as an additional field the two
    endpoints agree on out of band, while the `model` field -- the value
    both services key their compatibility check on -- stays identical
    across every request this class or Weave-Retrieval's sibling provider
    sends. It is optional and additive: the Weave-Tools embeddings service
    treats a request with no `input_type` exactly like `"passage"`, so any
    ordinary OpenAI-compatible client that has never heard of this field
    keeps working unchanged. The name itself ("query"/"passage") is
    borrowed from Cohere's own `input_type` field, not an OpenAI-native
    concept -- see Weave-Tools' `embeddings/README.md` for the request/
    response contract this was copied from.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        batch_size: int | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        resolved_base_url = base_url if base_url is not None else settings.embedding_base_url
        self._base_url = resolved_base_url.rstrip('/')
        self._api_key = api_key if api_key is not None else settings.embedding_api_key
        self._model = model if model is not None else settings.embedding_model
        self._batch_size = max(1, batch_size if batch_size is not None else settings.embedding_batch_size)
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        # Not reported by the /v1/embeddings response in any standard way
        # (the OpenAI API doesn't echo dimensionality back), so this trusts
        # the same settings.embedding_dimension the pgvector column was
        # created with rather than inspecting a response.
        return settings.embedding_dimension

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(self._embed_one_batch(batch))
        return vectors

    def _embed_one_batch(self, batch: list[str]) -> list[list[float]]:
        url = f'{self._base_url}/v1/embeddings'
        headers = {'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'}
        # input_type is always "passage" here -- see this class's own
        # docstring for why that rides along as its own field rather than
        # a second model name.
        payload = {'model': self._model, 'input': batch, 'input_type': 'passage'}

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
                return _parse_embeddings_response(response, expected_count=len(batch))

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
    at startup/CLI-invocation time rather than silently falling back to a
    provider nobody asked for.
    """
    provider_name = settings.embedding_provider.strip().lower()
    if provider_name == 'fake':
        return FakeEmbeddingProvider()
    if provider_name == 'openai':
        return OpenAICompatibleProvider()
    raise ValueError(
        f"unknown EMBEDDING_PROVIDER {settings.embedding_provider!r} (expected 'fake' or 'openai')"
    )


def embed_chunks(db: Session, document: Document, chunks_orm: list[Chunk], provider: EmbeddingProvider) -> None:
    """Embed every chunk in `chunks_orm` with `provider` and write the
    result onto the ORM objects: each chunk gets `embedding` +
    `embedding_model`, and the owning `document.embedding_model` is updated
    to match. `chunks_orm` is never re-derived from `document.markdown_body`
    here -- both call sites (the index pipeline on first run, app/cli.py's
    `reindex` on a later provider/model change) are responsible for deciding
    which chunks to pass in; this function only embeds and persists them.

    Batches provider.embed_batch() calls at settings.embedding_batch_size --
    OpenAICompatibleProvider already batches internally at the same size, so
    this loop's real purpose is bounding how many `_embedding_input(...)`
    strings are held in memory at once for a very large document, and
    keeping FakeEmbeddingProvider's behavior consistent with the real
    provider's batching shape.

    Does not commit: the caller owns the transaction boundary, same
    discipline as every other services/* function here that takes a
    `db: Session` (see e.g. Weave-Ingest's app/services/webhooks.py).
    """
    if not chunks_orm:
        return

    db.add_all(chunks_orm)
    batch_size = max(1, settings.embedding_batch_size)
    for start in range(0, len(chunks_orm), batch_size):
        batch = chunks_orm[start : start + batch_size]
        inputs = [_embedding_input(chunk, document.frontmatter) for chunk in batch]
        vectors = provider.embed_batch(inputs)
        if len(vectors) != len(batch):
            raise EmbeddingProviderError(
                f'provider {provider.model_name!r} returned {len(vectors)} vector(s) for {len(batch)} input(s)'
            )
        for chunk, vector in zip(batch, vectors):
            chunk.embedding = vector
            chunk.embedding_model = provider.model_name

    document.embedding_model = provider.model_name
