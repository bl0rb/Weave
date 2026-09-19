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

import logging

import httpx

from app.core.config import settings
from app.schemas.tools import CollectionOut, SearchHitOut, SearchSourceOut, SearchToolResponse
from app.services.scope import Scope

logger = logging.getLogger(__name__)


def _retrieval_headers() -> dict[str, str]:
    return {'Authorization': f'Bearer {settings.retrieval_api_token}'}


def _audit(*, scope: Scope, tool: str, collection: str | None, result_count: int | None, denied: bool) -> None:
    """One structured log line per tool call (Schritt 5's own audit
    requirement for the technical-identity path, but written for every
    `scope.kind` alike -- an external integration's calls are exactly as
    auditable as a human's own). Deliberately never includes the caller's
    bearer token, any hit's document text, or anything else beyond who
    asked, what they asked for, and how many rows came back -- an operator
    reading this log can answer "did identity X read collection Y" without
    this line itself becoming something worth protecting as hard as the
    documents it might otherwise have echoed.
    """
    logger.info(
        'tools call: kind=%s caller=%s tool=%s collection=%s result_count=%s denied=%s',
        scope.kind, scope.user_id, tool, collection or '*', result_count, denied,
    )


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
    params = {'teams': scope.teams} if scope.teams is not None else ({'team': scope.team} if scope.team is not None else {})
    response = httpx.get(
        f'{settings.retrieval_base_url}/api/v1/collections',
        params=params,
        headers=_retrieval_headers(),
        timeout=settings.retrieval_timeout_seconds,
    )
    response.raise_for_status()

    allowed = set(scope.allowed_collections)
    out = [
        CollectionOut(slug=row['slug'], name=row['name'], description=row.get('description'))
        for row in response.json()
        if row['slug'] in allowed
    ]
    _audit(scope=scope, tool='list_collections', collection=None, result_count=len(out), denied=False)
    return out


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
            _audit(scope=scope, tool='search', collection=collection, result_count=None, denied=True)
            return SearchToolResponse(query=query, results=[])
        effective_collections = [collection]
    else:
        effective_collections = list(scope.allowed_collections)

    # Weave-Retrieval's own SearchRequest.allowed_teams is `list[str] | None`
    # (see that service's app/schemas/search.py), and that service's
    # apply_filters() ANDs allowed_teams with allowed_collections -- an
    # empty list there means "no team authorized", i.e. zero rows, no
    # matter what allowed_collections says. For a caller who has a team at
    # all (`scope.team` or `scope.teams` set), that is exactly the
    # enforcement this service must forward: `scope.effective_teams` wraps
    # a single `team` into `[team]`, never `None`, since `None` is Weave-
    # Retrieval's own sentinel for "no team restriction at all" and no
    # team-scoped caller is trusted with that meaning (Grundregel Rechte).
    # A technical identity (`scope.kind == 'technical'`) has NO team
    # dimension at all by design -- `team` and `teams` are always `None`
    # for it (see contracts/technical-identities.md), and its sole access
    # dimension is `allowed_collections`. Forwarding `[]` for it would
    # silently AND every search to zero results regardless of which
    # collections it was granted, which is not "no team authorized" but
    # "team filtering does not apply to this caller at all" -- so `None`
    # is forwarded instead, leaving `allowed_collections` (checked above,
    # and enforced again by Weave-Retrieval itself) as the only boundary.
    # A personal/delegated caller with a genuinely empty `team` keeps the
    # existing `[]` behaviour -- unlike a technical identity, `team`/`teams`
    # are real, populated access dimensions for those kinds, so an absent
    # value there still means "no team authorized" and must still zero out.
    allowed_teams: list[str] | None
    if scope.kind == 'technical':
        allowed_teams = None
    else:
        allowed_teams = scope.effective_teams

    body: dict[str, object] = {
        'query': query,
        'allowed_collections': effective_collections,
        'allowed_teams': allowed_teams,
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
                document_id=hit['document_id'],
                chunk_id=hit['chunk_id'],
                page=_format_page(hit.get('page_start'), hit.get('page_end')),
                # The hit's own actual collection, not the request-level
                # closure variable -- see SearchSourceOut's docstring.
                collection=hit.get('collection'),
            ),
        )
        for hit in payload.get('results', [])
    ]
    _audit(scope=scope, tool='search', collection=collection, result_count=len(results), denied=False)
    return SearchToolResponse(query=query, results=results)
