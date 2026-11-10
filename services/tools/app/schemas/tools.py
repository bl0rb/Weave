"""Request/response shapes for both Werkzeuge (list_collections, search) --
shared by the REST mirror (app/api/tools.py) and, for the shapes that also
double as the MCP tools' own return values, app/services/tools.py, which
`.model_dump()`s them into the plain dicts app/mcp_server.py hands back over
MCP.
"""

from pydantic import BaseModel, Field, field_validator, model_validator


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
    # How many hits survive reranking; must not exceed top_k (the candidate
    # count) when both are given. Unset -> Weave-Retrieval's own default,
    # capped at the effective top_k.
    final_k: int | None = Field(default=None, ge=1, le=200)

    @model_validator(mode='after')
    def _final_k_within_top_k(self) -> 'SearchToolRequest':
        check_final_k(self.top_k, self.final_k)
        return self


def check_final_k(top_k: int | None, final_k: int | None) -> None:
    """Shared by the REST request model above and the MCP `search` tool."""
    if top_k is not None and final_k is not None and final_k > top_k:
        raise ValueError('final_k must be <= top_k')


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
    # Further per-hit metadata, copied verbatim from Weave-Retrieval's
    # SearchResult when present.
    heading_path: list[str] = []
    document_version: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    chunk_index: int | None = None
    title: str | None = None
    language: str | None = None
    document_type: str | None = None
    tags: list[str] = []
    source_kind: str | None = None
    source_url: str | None = None
    uploaded_at: str | None = None
    fetched_at: str | None = None
    legal_status: str | None = None

    @field_validator('source_url', mode='before')
    @classmethod
    def _web_link_only(cls, value: object) -> str | None:
        # Clients may render this as a link: nothing but http(s), same rule
        # as Weave-Runtime's chat Source.source_url.
        return value if isinstance(value, str) and value.startswith(('https://', 'http://')) else None


class SearchScoresOut(BaseModel):
    """Per-stage scores as reported by Weave-Retrieval; `None` where that
    stage did not score the hit (e.g. no fulltext match, rerank skipped)."""

    vector: float | None = None
    fulltext: float | None = None
    rrf: float | None = None
    rerank: float | None = None


class SearchHitOut(BaseModel):
    rank: int
    text: str
    scores: SearchScoresOut = SearchScoresOut()
    source: SearchSourceOut


class SearchTraceOut(BaseModel):
    """Subset of Weave-Retrieval's search trace (counts, effective k values,
    timings) -- no query embedding, no chunk text."""

    top_k: int | None = None
    final_k: int | None = None
    vector_candidates: int = 0
    fulltext_candidates: int = 0
    fused: int = 0
    reranked: int = 0
    returned: int = 0
    rerank_error: bool = False
    timings_ms: dict[str, float] = {}


class SearchToolResponse(BaseModel):
    query: str
    results: list[SearchHitOut] = []
    trace: SearchTraceOut | None = None
