"""Request/response shapes for both Werkzeuge (list_collections, search) --
shared by the REST mirror (app/api/tools.py) and, for the shapes that also
double as the MCP tools' own return values, app/services/tools.py, which
`.model_dump()`s them into the plain dicts app/mcp_server.py hands back over
MCP.
"""

from pydantic import BaseModel, Field


class CollectionOut(BaseModel):
    """One collection THIS caller may read -- never a fremde one; see
    app/services/tools.py:list_collections_for_scope.
    """

    slug: str
    name: str
    description: str | None = None


class SearchToolRequest(BaseModel):
    """Body of POST /api/v1/tools/search. Deliberately carries no
    user/team/allowed_collections field of any kind -- see
    app/services/scope.py's Grundregel Rechte reasoning and
    app/api/tools.py's own docstring. Any extra key a caller sends anyway
    (a stray `team`, `user_id`, ...) is silently dropped by pydantic's
    default `extra='ignore'` before this handler ever sees it; it is never
    consulted for scoping regardless.
    """

    query: str = Field(min_length=1)
    # A pure refinement WITHIN the caller's own resolved Scope -- see
    # app/services/tools.py:search_for_scope's own docstring for the
    # intersection rule (a collection outside the Scope yields an empty
    # result, never an error).
    collection: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=200)


class SearchSourceOut(BaseModel):
    """Where one search hit's text came from.

    `collection`, `document_id` and `chunk_id` are the hit's OWN, actual
    values, taken verbatim from Weave-Retrieval's per-chunk response
    (`SearchResult.collection`/`document_id`/`chunk_id`, see that service's
    app/schemas/search.py) -- never inferred from the request. `collection`
    is `None` only when Weave-Retrieval itself reports no collection for
    that chunk (a legacy, uncollected document), regardless of whether the
    caller's own request/MCP-argument named a single collection or left the
    search unscoped across their whole resolved scope. `document_id` and
    `chunk_id` are stable identifiers a caller (e.g. an orchestrating agent
    merging hits from several searches) can use to deduplicate or attribute
    hits by identity; `document`/`page` remain the human-readable label.
    """

    document: str
    document_id: str
    chunk_id: int
    page: str | None = None
    collection: str | None = None


class SearchHitOut(BaseModel):
    text: str
    source: SearchSourceOut


class SearchToolResponse(BaseModel):
    query: str
    results: list[SearchHitOut] = []
