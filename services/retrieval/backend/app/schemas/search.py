"""Request/response shapes for POST /api/v1/search.

The route itself (app/api/search.py) is a 501 placeholder for now -- these
schemas exist so Weave-Runtime can integration-test the request/response
contract and the auth layer (app/core/auth.py) before the actual hybrid-
search pipeline (vector + fulltext + RRF fusion + optional rerank, see
README) is filled in during a later stage.
"""

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    """Metadata filters applied BEFORE vector/fulltext search ever runs --
    ADR-0002's "Metadaten-Filter beim Datenzugriff" (`WHERE team_id IN
    (...)`-style access control lives here, not as a post-hoc result
    filter). Every field is optional; an unset field applies no constraint
    on that column. Matched against the corresponding `chunks.meta` /
    `documents` columns -- see contracts/chunk-store.md.
    """

    team: str | None = None
    department: str | None = None
    source: str | None = None
    language: str | None = None
    document_type: str | None = None
    tags: list[str] | None = None
    # A pure refinement WITHIN whatever SearchRequest.allowed_collections
    # already authorizes -- see that field's own docstring and
    # app/services/search.py's apply_filters(). It can never widen the
    # caller's access: asking for a `collection` outside `allowed_collections`
    # yields an empty result, never an error and never a bypass (Collections
    # contract point 5).
    collection: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    filters: SearchFilters | None = None
    # Teams the calling end user is a member of, propagated down from
    # Weave-API's gateway (ADR-0002: "Sichtbarkeits-Marker setzen, z.B.
    # X-User-Teams Header"). Stage 2's query is expected to enforce
    # `chunks.meta['team'] IN allowed_teams` from THIS field, never from
    # `filters.team` alone -- a caller must not be able to see a document
    # outside its authorized teams merely by omitting the team filter.
    # `None` means the pipeline enforces no team restriction at all (e.g. a
    # service-level caller trusted with full visibility); an empty list
    # means "no teams authorized", i.e. zero results.
    allowed_teams: list[str] | None = None
    # The Collections-contract analogue of allowed_teams above -- a HARD
    # boundary app/services/search.py's apply_filters() enforces in SQL
    # before any ranking runs, exactly like allowed_teams, and just as
    # unconditionally (independent of whether `filters.collection` is also
    # set). `None` means no collection restriction at all -- reserved for
    # service-internal callers trusted with full visibility, same as
    # allowed_teams=None, NEVER something Weave-Runtime should forward
    # unexamined from an end user. An empty list means "no collection
    # authorized" (zero results, `IN ()`), same semantics as
    # allowed_teams=[].
    #
    # A document with NO collection at all (`Document.collection_slug IS
    # NULL` -- pre-Collections legacy content, see that column's own
    # docstring in app/models/models.py) is a special case: it is visible
    # ONLY when `allowed_collections` is `None` (no restriction in force at
    # all) OR the caller explicitly includes the NO_COLLECTION_SENTINEL
    # value ("__none__", app/services/search.py) in this list. Listing real
    # collection slugs alone does NOT implicitly grant access to unscoped
    # legacy documents -- that would silently leak every pre-Collections
    # document to any caller who can read at least one real collection.
    allowed_collections: list[str] | None = None
    # Per-request overrides of settings.search_top_k / settings.search_final_k
    # (see app/core/config.py) -- unset means "use the configured default".
    # Bounded below: 0 or negative limits would turn into silently wrong
    # Python slices on the SQLite fallback and invalid LIMITs on Postgres.
    top_k: int | None = Field(default=None, ge=1, le=200)
    final_k: int | None = Field(default=None, ge=1, le=200)


class SearchScores(BaseModel):
    """Per-signal scores behind one SearchResult's final ranking -- present
    only for the signals that actually ran (e.g. `rerank` is `None` while
    `settings.rerank_provider == 'none'`)."""

    vector: float | None = None
    fulltext: float | None = None
    rrf: float | None = None
    rerank: float | None = None


class SearchResult(BaseModel):
    chunk_id: int
    document_id: str
    text: str
    heading_path: list[str] = []
    page_start: int | None = None
    page_end: int | None = None
    document_version: int
    source: str | None = None
    original_filename: str | None = None
    team: str | None = None
    department: str | None = None
    # `Document.collection_slug`, echoed back verbatim (`None` for a
    # pre-Collections legacy document with no collection at all -- see that
    # column's own docstring in app/models/models.py). This is the basis for
    # a downstream caller to check a reported source against its OWN
    # authorized scope after the fact -- without it, a caller receiving a
    # SearchResult has no way to confirm the collection it came from was
    # actually one the caller was allowed to see, only that
    # SearchRequest.allowed_collections was enforced somewhere upstream.
    collection: str | None = None
    scores: SearchScores
    embedding_model: str | None = None


class SearchTrace(BaseModel):
    """Diagnostics for one search call -- how many candidates each leg of
    the pipeline produced and where the time went, independent of the
    results themselves (README's "Monitoring von Query-Performance").

    `reranked` is the number of candidates actually handed to (and, on
    success, scored by) the reranker -- i.e. `len(kept)` right after RRF
    fusion's own top_k trim, NOT the final number of results returned (that
    is `final_k`-trimmed afterwards, see app/services/search.py's search()).
    `rerank_error` is True exactly when the configured reranker
    (settings.rerank_provider) raised app.services.reranker.RerankError for
    this request -- search() always falls back to the untouched RRF order
    in that case (a reranker outage must never fail the whole search
    request, see that module's own docstring), so a caller can tell "no
    reranker configured" (`rerank_error=False`, every `scores.rerank is
    None`) apart from "a reranker IS configured but just failed this one
    call" (`rerank_error=True`, same all-None scores) purely from the trace.
    """

    vector_candidates: int = 0
    fulltext_candidates: int = 0
    fused: int = 0
    reranked: int = 0
    rerank_error: bool = False
    timings_ms: dict[str, float] = {}


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult] = []
    trace: SearchTrace
