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

import httpx

from app.core.config import settings


class RetrievalClientError(Exception):
    """Weave-Retrieval is unreachable, timed out, answered with a non-2xx
    status, or returned a body that isn't valid JSON. Covers a misconfigured
    `RETRIEVAL_API_TOKEN` on this side too: Weave-Retrieval's own
    `require_service_token` (that service's app/core/auth.py) turns a
    missing/mismatched/unconfigured service token into its own 401/503,
    which lands here exactly like any other non-2xx response -- see this
    module's own docstring and settings.retrieval_api_token's comment in
    app/core/config.py."""


def _client() -> httpx.Client:
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout_seconds,
        read=settings.http_read_timeout_seconds,
        write=settings.http_read_timeout_seconds,
        pool=settings.http_read_timeout_seconds,
    )
    headers = {'Authorization': f'Bearer {settings.retrieval_api_token}'}
    return httpx.Client(base_url=settings.retrieval_base_url, headers=headers, timeout=timeout)


def list_collections(*, team: str | None) -> list:
    """GET /api/v1/collections?team=<team> -- the collections `team` may
    read, verbatim from Weave-Retrieval (that service's own `CollectionOut`:
    `slug`/`name`/`description`/`public`). `team=None` (a user with no team
    assigned yet) is a legitimate call, not an error -- omitting the query
    param entirely reproduces Weave-Retrieval's own `readable_collections
    (team=None)` semantics on that side ("no team context" -> public
    collections only), the same way a caller there is expected to behave
    (see that service's app/api/collections.py)."""
    params = {'team': team} if team is not None else None

    try:
        with _client() as client:
            response = client.get('/api/v1/collections', params=params)
    except httpx.RequestError as exc:
        raise RetrievalClientError(f'Weave-Retrieval unreachable: {exc}') from exc

    if response.status_code >= 400:
        raise RetrievalClientError(
            f'Weave-Retrieval returned HTTP {response.status_code} for GET /api/v1/collections'
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RetrievalClientError('Weave-Retrieval returned a non-JSON response for GET /api/v1/collections') from exc
