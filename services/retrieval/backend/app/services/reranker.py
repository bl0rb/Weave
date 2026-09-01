"""Reranker abstraction for the hybrid-search pipeline's optional last step.

Three providers implement the Reranker protocol below:

- NoopReranker (provider 'none', settings.rerank_provider's default): hands
  candidates back in their input order with score=None -- this is what a
  deployment with no configured reranker gets, so the RRF-fused order is
  passed straight through to `final_k` truncation (see README's "Reranker-
  Integration (Top 20 -> Final 5 Ergebnisse)").
- FakeReranker (provider 'fake'): deterministic, dependency-free
  query/text token-overlap scoring -- no network call, no model, usable in
  tests exactly like FakeEmbeddingProvider is for embeddings.
- HttpReranker (provider 'api'): talks to a Cohere/Jina-compatible
  `POST {base_url}/rerank` endpoint over httpx, with the same "retry
  429/5xx and transport-level failures, give up on the rest" split
  Weave-Knowledge's app/services/embeddings.py:OpenAICompatibleProvider
  uses for outbound embedding calls.

get_reranker() is the seam the search pipeline (app/services/search.py,
built in a parallel stage) is expected to depend on -- mirrors
app/services/embeddings.py's get_provider() in this same codebase: callers
only ever hold a Reranker, never import a concrete provider class
themselves.

RerankError is the one exception every provider failure surfaces as. A
provider error is never fatal to a search request by itself -- the CALLER
(the search pipeline) is expected to catch it and fall back to the
unranked (RRF-fused) order rather than failing the whole request over a
reranker outage; this module only defines the exception; the fallback
decision belongs to that caller, not here.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r'\w+', re.UNICODE)


@dataclass(frozen=True)
class RerankCandidate:
    """One chunk offered up for reranking -- just enough for a reranker to
    score it against the query. `chunk_id` is `Chunk.id` (app/models/models.py),
    carried through unchanged so the caller can map a reranked result back
    onto the full chunk/document row it came from without this module
    needing to know anything about that row's other columns.
    """

    chunk_id: int
    text: str


class RerankError(Exception):
    """Raised for any reranker-provider failure: an unreachable endpoint, a
    non-2xx response that either exhausted its retries or wasn't retryable
    in the first place, or a response body that doesn't parse as expected.
    See this module's docstring for why the caller, not this module, decides
    what to do about it (fall back to unranked order).
    """


class Reranker(Protocol):
    """The seam get_reranker() returns and every caller (the search
    pipeline, tests) depends on instead of a concrete provider class -- see
    this module's docstring.
    """

    def rerank(
        self, query: str, candidates: list[RerankCandidate]
    ) -> list[tuple[RerankCandidate, float | None]]: ...


class NoopReranker:
    """Reranker for settings.rerank_provider == 'none' (the default): hands
    `candidates` back in their input order, each paired with score=None so a
    caller building SearchScores (app/schemas/search.py) can tell "reranking
    didn't run" apart from "reranking ran and this candidate scored 0.0".
    """

    def rerank(
        self, query: str, candidates: list[RerankCandidate]
    ) -> list[tuple[RerankCandidate, float | None]]:
        return [(candidate, None) for candidate in candidates]


class FakeReranker:
    """Deterministic, dependency-free Reranker: scores each candidate by the
    number of distinct lowercased word tokens it shares with the query (a
    plain set intersection, no stemming/IDF/anything model-like), then
    sorts candidates descending by that score.

    Ties keep their input order -- Python's sort is stable and this sorts by
    score alone, so two candidates with equal overlap never swap relative to
    each other. That determinism (same query+candidates in -> same order
    out, every time, no state, no network call) is what makes this usable in
    tests exactly like Weave-Knowledge's FakeEmbeddingProvider is for
    embeddings; no attempt is made at real semantic relevance.
    """

    def rerank(
        self, query: str, candidates: list[RerankCandidate]
    ) -> list[tuple[RerankCandidate, float | None]]:
        query_tokens = set(_TOKEN_RE.findall(query.lower()))
        scored = [
            (candidate, float(len(query_tokens & set(_TOKEN_RE.findall(candidate.text.lower())))))
            for candidate in candidates
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored


# --- HttpReranker ---------------------------------------------------------

_DEFAULT_TIMEOUT_SECONDS = 30.0
# "max 3" per the task spec: the request is attempted up to 3 times total
# (the initial attempt plus up to 2 retries), not 3 retries on top of the
# initial attempt. Same shape as app/services/embeddings.py's
# OpenAICompatibleProvider (which in turn mirrors Weave-Ingest's
# app/workers/webhook_tasks.py:_BACKOFF_SECONDS).
_DEFAULT_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


def _backoff_seconds(attempt: int) -> float:
    index = min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
    return _RETRY_BACKOFF_SECONDS[max(index, 0)]


def _parse_rerank_response(
    response: httpx.Response, *, candidates: list[RerankCandidate]
) -> list[tuple[RerankCandidate, float | None]]:
    """Extract a (candidate, score) pair for every one of `candidates` from a
    200 `/rerank` response shaped like Cohere's or Jina's:
    `{"results": [{"index": int, "relevance_score": float}, ...]}` -- placed
    at their `index` field rather than assumed to already be in request
    order, same reasoning as app/services/embeddings.py's
    _parse_embeddings_response for the analogous `/v1/embeddings` response.

    HttpReranker always requests `top_n=len(candidates)` (see rerank()
    below), so a well-behaved provider returns exactly one result per
    candidate; this raises RerankError rather than silently dropping
    candidates if that invariant doesn't hold, so a caller falling back to
    unranked order on RerankError never has to guess whether it got a
    partial reranking instead.
    """
    try:
        data = response.json()
    except ValueError as exc:
        raise RerankError('rerank response body is not valid JSON') from exc

    results = data.get('results') if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise RerankError("rerank response is missing a 'results' list")

    scored: list[tuple[RerankCandidate, float | None]] = []
    seen_indices: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            raise RerankError('rerank response item has unexpected shape')
        index = item.get('index')
        score = item.get('relevance_score')
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < len(candidates)):
            raise RerankError(f'rerank response item has out-of-range index {index!r}')
        if index in seen_indices:
            raise RerankError(f'rerank response has duplicate index {index!r}')
        seen_indices.add(index)
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise RerankError(f'rerank response item {index} has a non-numeric relevance_score {score!r}')
        scored.append((candidates[index], float(score)))

    if len(scored) != len(candidates):
        raise RerankError(
            f'rerank response returned {len(scored)} result(s) for {len(candidates)} candidate(s)'
        )

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


class HttpReranker:
    """Reranker for settings.rerank_provider == 'api': a Cohere/Jina-
    compatible hosted or self-hosted `/rerank` endpoint.

    rerank() POSTs `{"model": ..., "query": ..., "documents": [text, ...],
    "top_n": len(candidates)}` (`top_n` set to the full candidate count,
    never a smaller value, since the Reranker protocol promises every
    candidate back, just reordered -- unlike a caller hitting the API
    directly to get only its best N) to `{base_url}/rerank` with
    `Authorization: Bearer {api_key}`.

    Retry policy per request: a 429, a 5xx, or a transport-level failure
    (connection refused, timeout, ...) is retried with backoff up to
    `max_attempts` times total; any other 4xx (bad request, bad API key,
    unknown model, ...) raises immediately -- retrying it changes nothing.
    Every failure mode raises RerankError; see this module's docstring for
    why the caller, not this class, decides whether to fall back.
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
        resolved_base_url = base_url if base_url is not None else settings.rerank_base_url
        self._base_url = resolved_base_url.rstrip('/')
        self._api_key = api_key if api_key is not None else settings.rerank_api_key
        self._model = model if model is not None else settings.rerank_model
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)

    def rerank(
        self, query: str, candidates: list[RerankCandidate]
    ) -> list[tuple[RerankCandidate, float | None]]:
        if not candidates:
            return []

        url = f'{self._base_url}/rerank'
        headers = {'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'}
        payload = {
            'model': self._model,
            'query': query,
            'documents': [candidate.text for candidate in candidates],
            'top_n': len(candidates),
        }

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=self._timeout)
            except httpx.HTTPError as exc:
                if attempt >= self._max_attempts:
                    raise RerankError(
                        f'rerank request to {url!r} failed after {attempt} attempt(s): {exc}'
                    ) from exc
                logger.warning('rerank request to %r failed (attempt %d/%d): %s', url, attempt, self._max_attempts, exc)
                time.sleep(_backoff_seconds(attempt))
                continue

            if response.status_code == 200:
                return _parse_rerank_response(response, candidates=candidates)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self._max_attempts:
                    raise RerankError(
                        f'rerank request to {url!r} returned HTTP {response.status_code} '
                        f'after {attempt} attempt(s)'
                    )
                logger.warning(
                    'rerank request to %r returned HTTP %d (attempt %d/%d); retrying',
                    url, response.status_code, attempt, self._max_attempts,
                )
                time.sleep(_backoff_seconds(attempt))
                continue

            # Any other 4xx (400 bad request, 401/403 bad key, 404 unknown
            # model, ...) is not retryable -- retrying an unchanged request
            # against an unchanged endpoint would only get the same 4xx back.
            raise RerankError(
                f'rerank request to {url!r} returned HTTP {response.status_code}: {response.text[:500]}'
            )

        # Unreachable -- the loop above always returns or raises on its
        # final iteration (attempt == self._max_attempts). Kept as a
        # defensive backstop rather than trusting that invariant silently.
        raise RerankError(f'rerank request to {url!r} did not complete')


def get_reranker() -> Reranker:
    """Instantiate the reranker named by settings.rerank_provider.

    Raises ValueError for anything other than 'none'/'fake'/'api' -- fail
    fast at startup time rather than silently falling back to a provider
    nobody asked for (same discipline as app/services/embeddings.py's
    get_provider()).
    """
    provider_name = settings.rerank_provider.strip().lower()
    if provider_name == 'none':
        return NoopReranker()
    if provider_name == 'fake':
        return FakeReranker()
    if provider_name == 'api':
        return HttpReranker()
    raise ValueError(
        f"unknown RERANK_PROVIDER {settings.rerank_provider!r} (expected 'none', 'fake', or 'api')"
    )
