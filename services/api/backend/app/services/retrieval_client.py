"""Thin HTTP client for Weave-Retrieval's Collections read-authority surface
(GET /api/v1/collections, that service's app/api/collections.py) -- the one
call GET /v1/collections (app/api/collections.py, this service) makes to
answer "which collections may THIS caller read".

Same reasoning as app/services/runtime_client.py's own docstring: Weave-API
is a trusted, service-token-authenticated internal caller here (ADR-0002's
service-to-service auth) -- `RETRIEVAL_BASE_URL` is a fixed deployment
constant, not user input -- so this is a plain httpx client with a bearer
token and a timeout, not SSRF-hardened fetch code.

Unlike runtime_client.py there is only ONE call here and ONE failure shape
worth distinguishing to a caller: reachable-and-answered-2xx, or everything
else. There is no request-shaped 4xx this call could ever legitimately
receive to pass through distinctly (`team` is always this caller's OWN
team, resolved server-side from their own User row -- never attacker-
influenced input a 4xx could be blamed on), so a single `RetrievalClientError`
(unlike runtime_client's `RuntimeUnavailable`/`RuntimeRejected` split) is
enough; app/api/collections.py maps every instance of it to 502.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class RetrievalClientError(Exception):
    """Weave-Retrieval is unreachable, timed out, answered with a non-2xx
    status, or returned a body that isn't valid JSON. Covers a misconfigured
    `RETRIEVAL_API_TOKEN` on this side too: Weave-Retrieval's own
    `require_service_token` (that service's app/core/auth.py) turns a
    missing/mismatched/unconfigured service token into its own 401/503,
    which lands here exactly like any other non-2xx response -- see this
    module's own docstring and settings.retrieval_api_token's comment in
    app/core/config.py."""


class RetrievalRequestRejected(RetrievalClientError):
    """Weave-Retrieval rejected the search parameters themselves (HTTP 400),
    e.g. an explicit `final_k` larger than an explicit `top_k` (a `final_k`
    without `top_k` is not rejected: Weave-Retrieval raises its default
    `top_k` to it). A caller error, not a gateway failure."""


def _unreachable(exc: httpx.RequestError, *, description: str) -> RetrievalClientError:
    """The RetrievalClientError for a transport-level failure, same as
    runtime_client._unreachable: the raw httpx error (internal host name,
    port, errno) is logged here and NOT put into the message, because
    app/api/collections.py, app/api/search.py and app/api/portal_artifacts.py
    pass `str(exc)` straight through as the caller-visible 502 `detail`."""
    logger.warning('Weave-Retrieval request failed (%s): %r', description, exc)
    return RetrievalClientError('Weave-Retrieval unreachable')


def _client() -> httpx.Client:
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout_seconds,
        read=settings.http_read_timeout_seconds,
        write=settings.http_read_timeout_seconds,
        pool=settings.http_read_timeout_seconds,
    )
    headers = {'Authorization': f'Bearer {settings.retrieval_api_token}'}
    return httpx.Client(base_url=settings.retrieval_base_url, headers=headers, timeout=timeout)


def list_collections(*, team: str | list[str] | None, user: str | None = None) -> list:
    """GET /api/v1/collections?team=<team>&user=<user> -- the collections
    `team` (and, when given, the individually-shared-with `user`) may read,
    verbatim from Weave-Retrieval (that service's own `CollectionOut`:
    `slug`/`name`/`description`/`public`). `team=None` (a user with no team
    assigned yet) is a legitimate call, not an error -- omitting the query
    param entirely reproduces Weave-Retrieval's own `readable_collections
    (team=None)` semantics on that side ("no team context" -> public
    collections only), the same way a caller there is expected to behave
    (see that service's app/api/collections.py). `user` is the caller's own
    Weave-Ingest user id (app/services/ingest_identity.py's
    `ingest_subject`) and is omitted the same way when unknown -- there is
    no per-person grant to resolve without it, so the call falls back to
    team/public collections only."""
    params = {'teams': team} if isinstance(team, list) else ({'team': team} if team is not None else None)
    if user is not None:
        params = dict(params) if params is not None else {}
        params['user'] = user

    try:
        with _client() as client:
            response = client.get('/api/v1/collections', params=params)
    except httpx.RequestError as exc:
        raise _unreachable(exc, description='GET /api/v1/collections') from exc

    if response.status_code >= 400:
        raise RetrievalClientError(
            f'Weave-Retrieval returned HTTP {response.status_code} for GET /api/v1/collections'
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RetrievalClientError('Weave-Retrieval returned a non-JSON response for GET /api/v1/collections') from exc


def release_collection(release_id: str) -> str | None:
    """GET /api/v1/releases/{release_id}/collection -- the space the
    document released as `release_id` belongs to, per the same index the
    chat's sources come from. `None` for an unknown (or superseded) release
    and for a document without a space; callers deny access on `None`."""
    try:
        with _client() as client:
            response = client.get(f'/api/v1/releases/{release_id}/collection')
    except httpx.RequestError as exc:
        raise _unreachable(exc, description='GET /api/v1/releases/{id}/collection') from exc

    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise RetrievalClientError(
            f'Weave-Retrieval returned HTTP {response.status_code} for GET /api/v1/releases/{{id}}/collection'
        )
    try:
        collection = response.json().get('collection')
    except (ValueError, AttributeError) as exc:
        raise RetrievalClientError('Weave-Retrieval returned an invalid release lookup response') from exc
    return collection if isinstance(collection, str) else None


def search(
    *,
    query: str,
    allowed_collections: list[str],
    allowed_teams: list[str],
    top_k: int | None,
    final_k: int | None = None,
) -> dict:
    """POST /api/v1/search -- hybrid search restricted to exactly
    `allowed_collections` (never `None` from here: that is Weave-Retrieval's
    "no restriction" sentinel for trusted service callers) and, for legacy
    documents without a collection, `allowed_teams`. Returns that service's
    response body verbatim (`results` list plus optional `trace` dict)."""
    body: dict[str, object] = {
        'query': query,
        'allowed_collections': allowed_collections,
        'allowed_teams': allowed_teams,
    }
    if top_k is not None:
        body['top_k'] = top_k
    if final_k is not None:
        body['final_k'] = final_k

    try:
        with _client() as client:
            response = client.post('/api/v1/search', json=body)
    except httpx.RequestError as exc:
        raise _unreachable(exc, description='POST /api/v1/search') from exc

    if response.status_code == 400:
        try:
            detail = response.json().get('detail')
        except (ValueError, AttributeError):
            detail = None
        raise RetrievalRequestRejected(detail if isinstance(detail, str) else 'Search parameters rejected')
    if response.status_code >= 400:
        raise RetrievalClientError(f'Weave-Retrieval returned HTTP {response.status_code} for POST /api/v1/search')

    try:
        payload = response.json()
    except ValueError as exc:
        raise RetrievalClientError('Weave-Retrieval returned a non-JSON response for POST /api/v1/search') from exc
    if not isinstance(payload, dict):
        raise RetrievalClientError('Weave-Retrieval returned a non-JSON response for POST /api/v1/search')
    return payload
