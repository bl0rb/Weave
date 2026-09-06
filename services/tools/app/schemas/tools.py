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

    `collection` here is the SINGLE collection the underlying Weave-
    Retrieval call was actually restricted to when the caller named one
    (`SearchToolRequest.collection` / the MCP `search` tool's own
    `collection` argument) -- every hit in that response necessarily
    belongs to it. When no `collection` was named, this is `None`: the
    query ran across the caller's ENTIRE resolved scope, and this service
    does not currently attribute each hit individually. Weave-Retrieval
    DOES return a per-chunk slug (`SearchResult.collection`, see that
    service's app/schemas/search.py) -- echoing it through here instead of
    the request-level value is an open improvement, not a limitation of the
    upstream contract. `document`/`page` remain fully precise either way;
    only this one field is coarser when the search wasn't scoped to a
    single collection to begin with.
    """

    document: str
    page: str | None = None
    collection: str | None = None


class SearchHitOut(BaseModel):
    text: str
    source: SearchSourceOut


class SearchToolResponse(BaseModel):
    query: str
    results: list[SearchHitOut] = []
