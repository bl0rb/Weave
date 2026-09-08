"""HTTP client for Weave-Retrieval's `POST /api/v1/search` and
`GET /api/v1/collections` (see README's "Koordination von Weave-Retrieval
... Aufrufen").

The request/response contract mirrored here comes straight from the real
code in that service: `app/schemas/search.py` (`SearchRequest`/
`SearchResult`/`SearchScores`) and `app/api/search.py` (the route itself) --
NOT a copy-pasted duplicate of those pydantic models, since this client only
ever needs to (a) send a request shaped like `SearchRequest` and (b) read
back the caller-relevant subset of `SearchResult`'s fields as `RetrievedChunk`
below (the fields a chat pipeline actually needs to cite a source and hand
context to an LLM -- `team`/`department`/`embedding_model` are internal to
Weave-Retrieval's own access-control/model-compat bookkeeping and have no
use here). `list_collections()` below is the same kind of mirror, but of
that service's `app/schemas/collections.py` (`CollectionOut`) and
`app/api/collections.py` (the Collections read-authority route, itself
documented as "callers ... are expected to call this endpoint to resolve a
team's readable collection slugs, then forward that same list as
`SearchRequest.allowed_collections`" -- exactly what
app/services/chat.py's `resolve_collection_scope` does with this client's
two functions together).

Unlike Weave-Retrieval's own outbound calls (app/services/embeddings.py,
app/services/reranker.py in that service) or this module's sibling
app/services/llm.py, `search()` below never retries. Weave-Retrieval is on
the hot path of an interactive chat turn, and a caller that has already
budgeted `settings.retrieval_timeout_seconds` for it wants a fast, clear
signal ("retrieval is unavailable, answer without sources" -- README's
Response Guard) rather than this client silently spending 1+2+4 more
seconds retrying before that decision can even be made. Whether/how to
degrade on failure is deliberately left to the caller (the future chat
pipeline), exactly like app/services/reranker.py's RerankError leaves the
fallback decision to ITS caller rather than deciding for it here.

Two exceptions, split the same way Weave-API's own
app/services/runtime_client.py splits a single RuntimeClientError by
`status_code` (except here the split is baked into the exception TYPE
itself, since callers are expected to handle the two cases very
differently -- see README's "Response Guard"):

- RetrievalUnavailable: the upstream SERVICE is the problem -- unreachable,
  timed out, or returned a 5xx -- and retrying later (not now, and not by
  this client) might succeed.
- RetrievalError: the REQUEST is the problem -- a 4xx, e.g. an invalid
  `top_k`/`final_k` combination or a filter Weave-Retrieval's own
  SearchRequest validation rejects -- retrying the identical request would
  only get the same 4xx back.
"""

import json
from dataclasses import dataclass

import httpx

from app.core.config import settings


class RetrievalError(Exception):
    """Weave-Retrieval rejected the request with a 4xx -- see this module's
    docstring. `status_code` is the upstream HTTP status; `detail` is that
    response's own `detail` field when one was present (FastAPI's default
    error-body shape), else a short excerpt of the raw body."""

    def __init__(self, message: str, *, status_code: int, detail: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class RetrievalUnavailable(Exception):
    """Weave-Retrieval could not be reached at all (connection failure,
    timeout) or responded with a 5xx, or answered 200 with a body that
    doesn't parse as a SearchResponse -- see this module's docstring.
    `status_code` is the upstream HTTP status when one was actually
    received (None for a network-level failure/malformed body, where there
    is no status -- or no trustworthy status -- to report)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RetrievedChunkScores:
    """Mirrors Weave-Retrieval's own SearchScores (app/schemas/search.py in
    that service) field-for-field -- see this module's docstring for why
    the per-signal breakdown is kept here rather than collapsed to the
    single final `score` app/schemas/chat.py's `Source` exposes: that
    collapse is a later stage's decision (which signal -- rerank if
    present, else rrf -- becomes "the" score on a chat response), not
    something this client should pre-decide for every caller.
    """

    vector: float | None = None
    fulltext: float | None = None
    rrf: float | None = None
    rerank: float | None = None


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved chunk -- the caller-relevant subset of Weave-Retrieval's
    own SearchResult, see this module's docstring.

    `collection` mirrors that service's own `SearchResult.collection`
    verbatim (`None` for a pre-Collections legacy document with no
    collection at all -- see that field's own docstring in Weave-
    Retrieval's app/schemas/search.py) -- unlike `team`/`department`/
    `embedding_model`, which this module's own docstring says have no use
    here, this one IS forwarded on: app/services/chat.py's `_to_source`
    copies it straight onto `Source.collection` so a caller can see which
    collection backed a retrieved chunk, the same provenance an n8n-provider
    bot's own reported sources carry (see that field's own docstring).
    Defaults to `None` (rather than being a required positional field like
    every other field here) purely so every existing direct `RetrievedChunk(
    ...)` construction across this codebase's own tests keeps working
    unchanged -- `_parse_chunk` below always passes it explicitly regardless.
    """

    chunk_id: int
    document_id: str
    text: str
    source: str | None
    original_filename: str | None
    page_start: int | None
    page_end: int | None
    document_version: int
    heading_path: list[str]
    scores: RetrievedChunkScores
    collection: str | None = None


@dataclass(frozen=True)
class Collection:
    """One entry from Weave-Retrieval's `GET /api/v1/collections` -- mirrors
    that service's own `CollectionOut` (app/schemas/collections.py) field
    for field, see this module's docstring. `public` is that service's own
    derived "readable by everyone" flag (`not read_teams`, never the raw
    team-slug list itself -- see `CollectionOut`'s own docstring for why);
    this client has no use for `public` beyond passing it through, since
    `resolve_collection_scope` (app/services/chat.py) only ever needs
    `slug`.
    """

    slug: str
    name: str
    description: str | None
    public: bool


def _error_detail(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500] or None

    if isinstance(body, dict) and 'detail' in body:
        detail = body['detail']
        # FastAPI's own 422 validation-error body puts a LIST of
        # loc/msg/type dicts under `detail`, not a string -- stringify
        # whatever shape it actually is rather than assuming str.
        return detail if isinstance(detail, str) else json.dumps(detail)

    return response.text[:500] or None


def _parse_chunk(item: dict) -> RetrievedChunk:
    scores_raw = item.get('scores') or {}
    return RetrievedChunk(
        chunk_id=item['chunk_id'],
        document_id=item['document_id'],
        text=item['text'],
        source=item.get('source'),
        original_filename=item.get('original_filename'),
        page_start=item.get('page_start'),
        page_end=item.get('page_end'),
        document_version=item['document_version'],
        heading_path=item.get('heading_path') or [],
        collection=item.get('collection'),
        scores=RetrievedChunkScores(
            vector=scores_raw.get('vector'),
            fulltext=scores_raw.get('fulltext'),
            rrf=scores_raw.get('rrf'),
            rerank=scores_raw.get('rerank'),
        ),
    )


def _parse_collection(item: dict) -> Collection:
    return Collection(
        slug=item['slug'],
        name=item['name'],
        description=item.get('description'),
        public=item['public'],
    )


def search(
    query: str,
    filters: dict | None = None,
    allowed_teams: list[str] | None = None,
    allowed_collections: list[str] | None = None,
    top_k: int | None = None,
    final_k: int | None = None,
) -> list[RetrievedChunk]:
    """POST `{settings.retrieval_base_url}/api/v1/search` with
    `Authorization: Bearer {settings.retrieval_api_token}` and a body
    shaped exactly like Weave-Retrieval's own SearchRequest -- `filters`
    passed straight through as a dict (a bot's `RetrievalConfig.filters`,
    app/schemas/bot.py, already has the same field set as that service's
    own SearchFilters) and `allowed_teams` propagated from the caller's own
    access-control decision (README's "Response Guard" note on team scoping
    -- this client makes none itself).

    `allowed_collections` is the Collections-contract analogue of
    `allowed_teams`, propagated from `app/services/chat.py`'s
    `resolve_collection_scope` -- same "this client makes no access-control
    decision itself" split. `None` here means the SAME thing it means on
    Weave-Retrieval's own `SearchRequest.allowed_collections`
    (app/schemas/search.py in that service): no Collections restriction in
    force at all, reserved for a service-internal caller trusted with full
    visibility. A caller acting on behalf of one end user's chat turn is
    expected to always pass a concrete (possibly empty) list instead -- see
    `resolve_collection_scope`'s own docstring for why this client does not
    enforce that itself.

    `top_k`/`final_k` left `None` means "use Weave-Retrieval's own
    configured default for that value" -- same meaning as on that service's
    SearchRequest, not "send no candidates".

    Never retries -- see this module's docstring for why. Raises
    RetrievalUnavailable for a network-level failure/timeout/5xx/malformed
    200 body, RetrievalError for a 4xx.
    """
    base_url = settings.retrieval_base_url.rstrip('/')
    url = f'{base_url}/api/v1/search'
    headers = {'Authorization': f'Bearer {settings.retrieval_api_token}', 'Content-Type': 'application/json'}
    payload = {
        'query': query,
        'filters': filters,
        'allowed_teams': allowed_teams,
        'allowed_collections': allowed_collections,
        'top_k': top_k,
        'final_k': final_k,
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=settings.retrieval_timeout_seconds)
    except httpx.HTTPError as exc:
        raise RetrievalUnavailable(f'Weave-Retrieval unreachable at {url!r}: {exc}') from exc

    if response.status_code >= 500:
        raise RetrievalUnavailable(
            f'Weave-Retrieval returned HTTP {response.status_code} for POST {url}',
            status_code=response.status_code,
        )

    if response.status_code >= 400:
        detail = _error_detail(response)
        raise RetrievalError(
            f'Weave-Retrieval returned HTTP {response.status_code} for POST {url}: {detail}',
            status_code=response.status_code,
            detail=detail,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RetrievalUnavailable(f'Weave-Retrieval returned a non-JSON response for POST {url}') from exc

    results = data.get('results') if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise RetrievalUnavailable(f"Weave-Retrieval response is missing a 'results' list for POST {url}")

    try:
        return [_parse_chunk(item) for item in results]
    except (KeyError, TypeError) as exc:
        raise RetrievalUnavailable(f'Weave-Retrieval returned a malformed result for POST {url}: {exc}') from exc


def list_collections(team: str | list[str] | None = None) -> list[Collection]:
    """GET `{settings.retrieval_base_url}/api/v1/collections` with
    `Authorization: Bearer {settings.retrieval_api_token}` and, when `team`
    is given, `?team=<team>` -- Weave-Retrieval's own Collections
    read-authority endpoint (app/api/collections.py in that service; see
    this module's docstring). Returns every collection `team` may read at
    all, per that service's own `readable_collections()`.

    `team=None` is forwarded as "no `team` query param at all", not
    `?team=`, matching that route's own documented distinction: no team
    context means only PUBLIC collections come back, same as this client's
    caller (`app/services/chat.py`'s `resolve_collection_scope`) passing
    `ChatUser.team` straight through for an anonymous/system-initiated chat
    (`user.team is None`).

    Never retries -- same reasoning as `search()` above: this is on the hot
    path of an interactive chat turn just as much as the search call it
    precedes. Raises RetrievalUnavailable for a network-level
    failure/timeout/5xx/malformed 200 body, RetrievalError for a 4xx --
    identical error classification to `search()`, so a caller (chat.py) does
    not need to distinguish which of this client's two functions failed.
    """
    base_url = settings.retrieval_base_url.rstrip('/')
    url = f'{base_url}/api/v1/collections'
    headers = {'Authorization': f'Bearer {settings.retrieval_api_token}'}
    params = {'teams': team} if isinstance(team, list) else ({'team': team} if team is not None else None)

    try:
        response = httpx.get(url, headers=headers, params=params, timeout=settings.retrieval_timeout_seconds)
    except httpx.HTTPError as exc:
        raise RetrievalUnavailable(f'Weave-Retrieval unreachable at {url!r}: {exc}') from exc

    if response.status_code >= 500:
        raise RetrievalUnavailable(
            f'Weave-Retrieval returned HTTP {response.status_code} for GET {url}',
            status_code=response.status_code,
        )

    if response.status_code >= 400:
        detail = _error_detail(response)
        raise RetrievalError(
            f'Weave-Retrieval returned HTTP {response.status_code} for GET {url}: {detail}',
            status_code=response.status_code,
            detail=detail,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RetrievalUnavailable(f'Weave-Retrieval returned a non-JSON response for GET {url}') from exc

    if not isinstance(data, list):
        raise RetrievalUnavailable(f'Weave-Retrieval response is not a list for GET {url}')

    try:
        return [_parse_collection(item) for item in data]
    except (KeyError, TypeError) as exc:
        raise RetrievalUnavailable(f'Weave-Retrieval returned a malformed collection for GET {url}: {exc}') from exc
