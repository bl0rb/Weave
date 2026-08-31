"""The two Werkzeuge's actual implementation, written and tested exactly
once: app/mcp_server.py's MCP tools and app/api/tools.py's REST mirror are
both thin transports that resolve a Scope (app/services/scope.py) from
their own request's Authorization header and then call straight through to
`list_collections_for_scope`/`search_for_scope` below -- neither transport
adds, removes, or overrides any scoping logic of its own.

Both functions talk to Weave-Retrieval exactly the way this service's own
Grundregel Rechte requires: `scope.allowed_collections` is the ONLY thing
ever trusted as the access boundary, and it is applied fresh, in this
service's own code, on every call -- never delegated to Weave-Retrieval
alone to enforce from a caller-suppliable argument (though Weave-Retrieval's
own `allowed_collections` filter, see that service's app/services/search.py,
is a second, independent hard boundary underneath this one, not a
replacement for it).
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.schemas.tools import CollectionOut, SearchHitOut, SearchSourceOut, SearchToolResponse
from app.services.scope import Scope


def _retrieval_headers() -> dict[str, str]:
    return {'Authorization': f'Bearer {settings.retrieval_api_token}'}


def list_collections_for_scope(scope: Scope) -> list[CollectionOut]:
    """The collections THIS caller (`scope`) may read -- name, slug,
    description. Never a fremde one.

    Fetches every collection readable by `scope.team` from Weave-Retrieval
    (GET /api/v1/collections?team=...) purely for its name/description
    metadata, then keeps only the rows whose slug is also in
    `scope.allowed_collections` -- that intersection, not Weave-Retrieval's
    response, is what actually enforces the boundary: `scope.allowed_
    collections` is already the caller's own fully-resolved scope (see
    scope.py), so for a Personal-Token caller this is a no-op filter over
    an identical set, and for a Delegations-Token caller (whose embedded
    `collections` list can be a narrower loan than everything the
    delegating human could otherwise read) it is a real restriction.
    NO_COLLECTION_SENTINEL ("__none__", see scope.py's module docstring),
    when present in `scope.allowed_collections`, never matches a real
    collection's slug and is simply absent from this listing -- it has no
    name/description to show, only a meaning for search_for_scope below.
    """
    params = {'team': scope.team} if scope.team is not None else {}
    response = httpx.get(
        f'{settings.retrieval_base_url}/api/v1/collections',
        params=params,
        headers=_retrieval_headers(),
        timeout=settings.retrieval_timeout_seconds,
    )
    response.raise_for_status()

    allowed = set(scope.allowed_collections)
    return [
        CollectionOut(slug=row['slug'], name=row['name'], description=row.get('description'))
        for row in response.json()
        if row['slug'] in allowed
    ]


def _format_page(page_start: int | None, page_end: int | None) -> str | None:
    if page_start is None:
        return None
    if page_end is None or page_end == page_start:
        return str(page_start)
    return f'{page_start}-{page_end}'


def search_for_scope(
    scope: Scope,
    *,
    query: str,
    collection: str | None,
    top_k: int | None,
) -> SearchToolResponse:
    """Hybrid search over exactly `scope`'s own readable collections.

    `collection`, if given, is a pure refinement WITHIN `scope.allowed_
    collections` -- never a way to widen it. A `collection` outside the
    caller's scope returns an EMPTY result, not an error and not any hint
    that the collection exists (Grundregel Rechte: "kein Fehler, kein
    Hinweis auf deren Existenz") -- this is checked here, in this service's
    own code, before Weave-Retrieval is ever called; Weave-Retrieval's own
    `allowed_collections` enforcement (app/services/search.py's
    apply_filters(), a second, independent hard boundary) would produce the
    same empty result on its own if this check were skipped, but skipping
    it would mean the boundary is enforced ONLY by the downstream service,
    contrary to this service's own contract.
    """
    if collection is not None:
        if collection not in scope.allowed_collections:
            return SearchToolResponse(query=query, results=[])
        effective_collections = [collection]
    else:
        effective_collections = list(scope.allowed_collections)

    body: dict[str, object] = {
        'query': query,
        'allowed_collections': effective_collections,
        # Weave-Retrieval's own SearchRequest.allowed_teams is `list[str] |
        # None` (see that service's app/schemas/search.py) with `None`
        # meaning "no restriction at all" -- a meaning this service must
        # never forward on a caller's behalf (Grundregel Rechte again).
        # `scope.team` is `None` for a caller with no team at all, so this
        # wraps it into `[]` ("no team authorized", i.e. zero results)
        # rather than `[None]`, which is not even a valid `list[str]` value
        # and would fail Weave-Retrieval's own request validation outright.
        'allowed_teams': [scope.team] if scope.team is not None else [],
    }
    if top_k is not None:
        body['top_k'] = top_k

    response = httpx.post(
        f'{settings.retrieval_base_url}/api/v1/search',
        json=body,
        headers=_retrieval_headers(),
        timeout=settings.retrieval_timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()

    results = [
        SearchHitOut(
            text=hit['text'],
            source=SearchSourceOut(
                document=hit.get('original_filename') or hit['document_id'],
                page=_format_page(hit.get('page_start'), hit.get('page_end')),
                # Only ever known when the caller itself named a single
                # collection -- see SearchSourceOut's own docstring for why
                # an unscoped query can't attribute a hit to one.
                collection=collection,
            ),
        )
        for hit in payload.get('results', [])
    ]
    return SearchToolResponse(query=query, results=results)
