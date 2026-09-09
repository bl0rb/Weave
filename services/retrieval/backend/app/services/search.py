"""Hybrid-search pipeline: metadata filtering, parallel vector + fulltext
retrieval, Reciprocal Rank Fusion, optional reranking, and result assembly.

Reranking (settings.rerank_provider, see app/services/reranker.py) is the
pipeline's last step: the RRF-fused top `top_k` candidates ("kept" below)
are handed to whatever get_reranker() resolves to, and the fused order is
replaced by the reranker's own order (unless it's a NoopReranker, which
hands candidates back unchanged with score=None -- exactly what
settings.rerank_provider == 'none', the default, gets). Only THEN is the
result trimmed down to `final_k` -- a reranker can narrow a candidate set,
it can never be asked to produce MORE final results than it was given
candidates for, which is also why app/api/search.py rejects a request where
`final_k > top_k` outright before search() ever runs.

A reranker-provider failure (RerankError, see app/services/reranker.py's own
docstring for why that module itself never decides this) is never fatal to
the request: search() catches it, falls back to the untouched RRF order
(every `scores.rerank` stays `None`, same as NoopReranker), and records
`trace.rerank_error = True` so a caller/operator can tell a reranker outage
apart from "no reranker configured" -- see SearchTrace's own docstring.

Every function here that runs against the database talks in terms of
`app.models.models.Chunk`/`Document` -- the shared read-model documented in
contracts/chunk-store.md. See that module's own docstring
for why this service never writes to either table.

Postgres vs. SQLite dialect branching, throughout this module, is decided
from `settings.database_url` (the same test Weave-Retrieval's own
app/core/db.py already uses for engine kwargs) rather than by inspecting a
live connection -- this process only ever talks to one database for its
entire lifetime, so the setting alone is a reliable, session-independent
signal, and it lets apply_filters() build a WHERE clause without needing a
bound Session/Connection in hand.
"""

from __future__ import annotations

import logging
import math
import re
import time
import httpx
from dataclasses import dataclass

from sqlalchemy import Float, Select, Text, bindparam, cast, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.orm import Session

from app.core.config import settings


def refresh_control_plane() -> None:
    if not settings.chat_config_base_url or not settings.chat_config_service_token:
        return
    try:
        response = httpx.get(
            f'{settings.chat_config_base_url.rstrip("/")}/api/v1/internal/retrieval-provider',
            headers={'Authorization': f'Bearer {settings.chat_config_service_token}'}, timeout=2,
        )
        if response.status_code != 200:
            return
        values = response.json()
        for name in ('embedding_provider', 'embedding_base_url', 'embedding_model', 'embedding_dimension', 'embedding_batch_size', 'rerank_provider', 'rerank_base_url', 'rerank_model', 'rerank_max_documents', 'rerank_batch_size', 'rerank_threads', 'semantic_weight', 'lexical_weight'):
            if name in values:
                setattr(settings, name, values[name])
        settings.embedding_api_key = values.get('embedding_api_key', settings.embedding_api_key)
        settings.rerank_api_key = values.get('rerank_api_key', settings.rerank_api_key)
    except (httpx.HTTPError, ValueError):
        return
from app.models.models import Chunk, Document, DocumentStatus
from app.schemas.search import SearchFilters, SearchRequest, SearchResult, SearchScores, SearchTrace
from app.services.embeddings import embed_query
from app.services.reranker import RerankCandidate, RerankError, get_reranker

logger = logging.getLogger(__name__)

# --- dialect helper -----------------------------------------------------------


def _is_sqlite() -> bool:
    """True when this process is configured against SQLite (local dev, the
    pytest suite) rather than Postgres (the only dialect a real deployment
    ever points DATABASE_URL at -- see app/core/config.py's database_url
    docstring). Read fresh from settings every call (never cached at import
    time) so tests that monkeypatch settings.database_url observe the
    dialect they asked for.
    """
    return settings.database_url.startswith('sqlite')


# --- request-level defaults ----------------------------------------------------


def resolve_top_k(request: SearchRequest) -> int:
    """`request.top_k` if the caller set one, else settings.search_top_k."""
    return request.top_k if request.top_k is not None else settings.search_top_k


def resolve_final_k(request: SearchRequest) -> int:
    """`request.final_k` if the caller set one, else settings.search_final_k."""
    return request.final_k if request.final_k is not None else settings.search_final_k


# --- metadata filtering ---------------------------------------------------------

# Chunk.meta keys that back SearchFilters.source/language/document_type --
# see this module's own filter-scoping notes below for why these three (plus
# 'tags') are matched against `chunks.meta`, never a `documents` column.
_META_SCALAR_FILTERS = ('source', 'language', 'document_type')

# Magic value a caller may include in SearchRequest.allowed_collections to
# mean "documents with NO collection at all (Document.collection_slug IS
# NULL -- pre-Collections legacy content) are ALSO allowed", on top of
# whichever real collection slugs are listed alongside it -- see
# apply_filters()'s own docstring below and the Collections contract's point
# 5. Never a real collection slug: Weave-Ingest's own Collections contract
# defines a slug as a lowercase identifier a human named a collection with,
# and this exact string is reserved system-wide for this sentinel meaning
# instead (mirrored in SearchRequest.allowed_collections's own docstring).
NO_COLLECTION_SENTINEL = '__none__'


def apply_filters(
    stmt: Select,
    filters: SearchFilters | None,
    allowed_teams: list[str] | None,
    allowed_collections: list[str] | None,
) -> Select:
    """Add every metadata WHERE-clause that is safe to express directly in
    SQL to `stmt`, BEFORE any ranking/limit is applied -- filtering after
    the fact would let an out-of-scope or non-indexed chunk occupy a
    candidate slot that should have gone to an in-scope match. `stmt` must
    already SELECT from a query that has `chunks` JOINed to `documents`
    (see vector_search()/fulltext_search() below); this function only adds
    `.where(...)` predicates, it never introduces the join itself.

    Filter scoping -- verified against app/models/models.py + the
    chunk-store contract, NOT assumed:
    - `documents.status`: always constrained to `indexed`. `blocked`,
      `superseded`, `pending`, and `failed` documents must never surface in
      a search result, independent of anything the caller asked for.
    - `allowed_teams` (from SearchRequest, NOT SearchFilters): the
      access-control boundary. Applied unconditionally whenever it is not
      `None`, regardless of whether `filters.team` is also set -- a caller
      must never be able to see a document outside its authorized teams
      merely by omitting the team filter (see SearchRequest.allowed_teams's
      own docstring). An empty list is "no team authorized" and correctly
      yields zero rows via `IN ()`.
    - `allowed_collections` (from SearchRequest, NOT SearchFilters): the
      Collections-contract analogue of `allowed_teams` above, enforced the
      exact same way -- unconditionally whenever it is not `None`,
      independent of `filters.collection`, directly against the real,
      indexed `Document.collection_slug` column (so, unlike the
      `chunks.meta`-based filters below, this needs no separate SQLite
      fallback path at all -- both dialects get the identical SQL
      predicate, exactly like the `allowed_teams`/`Document.team` clause
      above). A plain `IN (...)` would, however, silently exclude every
      document with `collection_slug IS NULL` (SQL's three-valued logic:
      `NULL IN (...)` is never true) -- correct for a caller who was never
      granted anything beyond specific collections, but NOT what the
      Collections contract's own Altbestand rule (point 5) asks for: an
      unscoped legacy document must additionally stay visible when the
      caller explicitly lists NO_COLLECTION_SENTINEL ("__none__") alongside
      whatever real slugs it was granted, so that rule is applied here as an
      explicit `OR collection_slug IS NULL` rather than relying on however a
      given SQL engine happens to treat NULL inside `IN (...)`.
    - `filters.team` / `filters.department` / `filters.collection`: real,
      indexed columns on `documents` (`Document.team`, `Document.department`,
      `Document.collection_slug`) -- filtered there directly, not via
      `chunks.meta`. `filters.collection` is purely a refinement WITHIN
      `allowed_collections`: both clauses are AND'ed together like every
      other filter here, so asking for a `collection` outside
      `allowed_collections` intersects to zero rows rather than ever
      widening what the caller was actually granted.
    - `filters.source` / `filters.language` / `filters.document_type`: NOT
      `documents` columns at all (`documents` has `original_filename`, not
      `source` -- the raw source filename/URL only ever lives in
      `documents.frontmatter['source']`, denormalized onto every chunk as
      `chunks.meta['source']` by Weave-Knowledge's own
      app/services/enrichment.py; `language`/`document_type` aren't even
      formal frontmatter keys -- contracts/frontmatter.schema.json allows
      arbitrary additional properties, so these two are matched
      best-effort, only when actually present in `chunks.meta`). All three
      are therefore matched against `chunks.meta`, the field the Chunk
      model's own docstring says this service's filtering should read
      directly rather than re-deriving from `documents.frontmatter` on
      every query.
    - `filters.tags`: same reasoning as above -- `chunks.meta['tags']` (a
      list), matched as "at least one requested tag present" (a facet-style
      OR, the usual semantics for a multi-value tag filter).

    On Postgres, the `chunks.meta` clauses above compile to raw `->>`/`?|`
    JSON operator expressions (portable across the plain `json` column type
    this model currently uses and a future `jsonb` migration, since both
    support `->>`). On SQLite there is no reliable, version-independent SQL
    surface for the same operations, so this function deliberately leaves
    them OFF the SQL statement entirely on that dialect -- the SQLite
    fallback branches of vector_search()/fulltext_search() apply the exact
    same semantics as a Python-side post-filter (see meta_matches() below)
    over the rows this function's SQL-safe clauses already narrowed down,
    before any ranking or limiting happens.
    """
    stmt = stmt.where(Document.status == DocumentStatus.INDEXED)

    if allowed_teams is not None:
        stmt = stmt.where(Document.team.in_(allowed_teams))

    if allowed_collections is not None:
        if NO_COLLECTION_SENTINEL in allowed_collections:
            real_slugs = [slug for slug in allowed_collections if slug != NO_COLLECTION_SENTINEL]
            stmt = stmt.where(or_(Document.collection_slug.in_(real_slugs), Document.collection_slug.is_(None)))
        else:
            stmt = stmt.where(Document.collection_slug.in_(allowed_collections))

    if filters is None:
        return stmt

    if filters.team is not None:
        stmt = stmt.where(Document.team == filters.team)
    if filters.department is not None:
        stmt = stmt.where(Document.department == filters.department)
    if filters.collection is not None:
        stmt = stmt.where(Document.collection_slug == filters.collection)

    if not _is_sqlite():
        for key in _META_SCALAR_FILTERS:
            value = getattr(filters, key)
            if value is not None:
                stmt = stmt.where(Chunk.meta.op('->>')(key) == value)
        if filters.tags:
            # jsonb's `?|` ("any of these array elements present") needs an
            # actual jsonb value on the left-hand side -- `meta` is a plain
            # `json` column (see Chunk.meta), so it is cast explicitly
            # rather than assumed. The operator must target the `tags`
            # array inside meta (`meta -> 'tags'`), not the meta object
            # itself -- applied to the object it would match top-level
            # keys instead of tag values. `?|` takes a `text[]` on the
            # right; pg_array(..., type_=Text) builds that as bound
            # parameters (never string-interpolated) rather than raw SQL.
            stmt = stmt.where(
                cast(Chunk.meta, JSONB).op('->')('tags').op('?|')(pg_array(filters.tags, type_=Text))
            )

    return stmt


def meta_matches(meta: dict, filters: SearchFilters | None) -> bool:
    """Python-side equivalent of apply_filters()'s `chunks.meta`-based
    clauses (source/language/document_type/tags) -- used ONLY by the SQLite
    fallback paths of vector_search()/fulltext_search(), since apply_filters()
    itself leaves those four filters off the SQL statement on that dialect
    (see its own docstring). `team`/`department`/`collection`/`allowed_teams`/
    `allowed_collections`/`status` are NOT re-checked here -- those are real
    columns apply_filters() already applied in SQL on every dialect,
    including SQLite.
    """
    if filters is None:
        return True
    for key in _META_SCALAR_FILTERS:
        value = getattr(filters, key)
        if value is not None and meta.get(key) != value:
            return False
    if filters.tags:
        chunk_tags = meta.get('tags') or []
        if not any(tag in chunk_tags for tag in filters.tags):
            return False
    return True


# --- vector search ---------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in plain Python -- the SQLite fallback's equivalent
    of Postgres's `1 - (embedding <=> :vec)`. Guards against a zero-norm
    vector (never produced by FakeEmbeddingProvider, but not something this
    function should assume) by returning 0.0 rather than dividing by zero.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _vector_search_sqlite(
    db: Session,
    query_vec: list[float],
    filters: SearchFilters | None,
    allowed_teams: list[str] | None,
    allowed_collections: list[str] | None,
    limit: int,
) -> list[tuple[Chunk, float]]:
    stmt = (
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.embedding.is_not(None))
        .where(Chunk.embedding_model == settings.embedding_model)
    )
    stmt = apply_filters(stmt, filters, allowed_teams, allowed_collections)
    rows = db.execute(stmt).all()

    scored = [
        (chunk, _cosine_similarity(query_vec, chunk.embedding))
        for chunk, document in rows
        if meta_matches(chunk.meta, filters)
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored[:limit]


def vector_search(
    db: Session,
    query_vec: list[float],
    filters: SearchFilters | None,
    allowed_teams: list[str] | None,
    allowed_collections: list[str] | None,
    limit: int,
) -> list[tuple[Chunk, float]]:
    """The vector leg of hybrid search: nearest chunks to `query_vec` by
    cosine similarity, restricted to chunks embedded with the SAME model
    settings.embedding_model names (`Chunk.embedding_model` is per-chunk,
    not just per-document, since a model change mid-reindex can leave the
    two briefly out of sync -- see the chunk-store contract's `embedding_model`
    field) and to chunks that actually HAVE an embedding at all.

    Returns `(chunk, score)` pairs, `limit` at most, ordered by score
    descending -- `score` is cosine SIMILARITY (1.0 == identical direction),
    not distance, on both dialects: Postgres's `<=>` operator returns cosine
    DISTANCE, so that leg negates it (`1 - distance`) to match the SQLite
    fallback's own direct cosine-similarity computation. This is what lets
    rrf_fuse() below treat both legs' scores uniformly.
    """
    if _is_sqlite():
        return _vector_search_sqlite(db, query_vec, filters, allowed_teams, allowed_collections, limit)

    # Postgres: `chunks.embedding <=> :vec`, using the HNSW index
    # (`ix_chunks_embedding_hnsw`, `vector_cosine_ops` -- see
    # Weave-Knowledge's alembic/versions/0001_init.py) rather than a
    # sequential scan. The query vector is bound as a `:query_vec` parameter
    # typed as `Chunk.embedding`'s own column type (VectorType) rather than
    # passed as a bare Python list -- that is what makes SQLAlchemy apply
    # VectorType's bind_processor (which on Postgres delegates to
    # pgvector.sqlalchemy.Vector's own encoding), the same encoding path
    # every actual INSERT into this column already goes through, instead of
    # leaving the driver to guess how to adapt a raw `list[float]`.
    query_vec_param = bindparam('query_vec', query_vec, type_=Chunk.embedding.type)
    # type_=Float is load-bearing: without it SQLAlchemy infers the
    # expression's type from the left operand (Vector) and runs the returned
    # DISTANCE -- an ordinary float -- through pgvector's own result
    # processor, which tries to parse it as a '[1,2,3]' vector literal and
    # dies with "'float' object is not subscriptable". The SQLite fallback
    # computes cosine in Python and never touches this path, which is why
    # the test suite stayed green while every real Postgres query failed.
    distance = Chunk.embedding.op('<=>', return_type=Float)(query_vec_param)
    stmt = (
        select(Chunk, distance.label('distance'))
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.embedding.is_not(None))
        .where(Chunk.embedding_model == settings.embedding_model)
    )
    stmt = apply_filters(stmt, filters, allowed_teams, allowed_collections)
    # Chunk.id as secondary key keeps ties at the `limit` cutoff
    # deterministic across executions (the SQLite fallback tie-breaks
    # the same way).
    stmt = stmt.order_by(distance.asc(), Chunk.id.asc()).limit(limit)
    rows = db.execute(stmt).all()
    return [(chunk, 1.0 - float(dist)) for chunk, dist in rows]


# --- fulltext search ---------------------------------------------------------------

_WORD_RE = re.compile(r'\w+', re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _fulltext_score(query_tokens: list[str], chunk_tokens: set[str]) -> float:
    """Simple token-overlap scoring for the SQLite fallback: the fraction of
    (deduplicated) query tokens that also appear in the chunk's own token
    set. 0.0 (no overlap at all) is never returned as a "hit" by
    _fulltext_search_sqlite() below -- same as Postgres's `@@` match
    predicate, a fulltext leg with zero term overlap isn't a candidate at
    all, just an absent one.
    """
    if not query_tokens:
        return 0.0
    matches = sum(1 for token in query_tokens if token in chunk_tokens)
    return matches / len(query_tokens)


def _fulltext_search_sqlite(
    db: Session,
    query: str,
    filters: SearchFilters | None,
    allowed_teams: list[str] | None,
    allowed_collections: list[str] | None,
    limit: int,
) -> list[tuple[Chunk, float]]:
    query_tokens = _tokenize(query)
    stmt = select(Chunk, Document).join(Document, Chunk.document_id == Document.id)
    stmt = apply_filters(stmt, filters, allowed_teams, allowed_collections)
    rows = db.execute(stmt).all()

    scored = []
    for chunk, document in rows:
        if not meta_matches(chunk.meta, filters):
            continue
        score = _fulltext_score(query_tokens, set(_tokenize(chunk.text)))
        if score > 0.0:
            scored.append((chunk, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored[:limit]


def fulltext_search(
    db: Session,
    query: str,
    filters: SearchFilters | None,
    allowed_teams: list[str] | None,
    allowed_collections: list[str] | None,
    limit: int,
) -> list[tuple[Chunk, float]]:
    """The fulltext leg of hybrid search. Returns `(chunk, score)` pairs,
    `limit` at most, ordered by score descending; a chunk with zero textual
    overlap with `query` is never included (there is no meaningful "0.0
    fulltext score" candidate, only an absent one, on either dialect).
    """
    if _is_sqlite():
        return _fulltext_search_sqlite(db, query, filters, allowed_teams, allowed_collections, limit)

    # Postgres: `websearch_to_tsquery('simple', :q)` against the GENERATED
    # `chunks.tsv` column (see Weave-Knowledge's alembic/versions/
    # 0001_init.py), ranked with `ts_rank`. `tsv` is deliberately NOT a
    # mapped ORM column (see app/models/models.py's Chunk docstring) -- it's
    # addressed here as a raw column reference instead. `websearch_to_tsquery`
    # (rather than plain `to_tsquery`) tolerates arbitrary user input
    # (quotes, `-`/`OR`, unbalanced operators) without raising a syntax
    # error, the same reason any public search box uses it over the stricter
    # `to_tsquery`.
    tsv_column = literal_column('chunks.tsv')
    ts_query = func.websearch_to_tsquery('simple', bindparam('fts_query', query))
    rank = func.ts_rank(tsv_column, ts_query)
    stmt = (
        select(Chunk, rank.label('rank'))
        .join(Document, Chunk.document_id == Document.id)
        .where(tsv_column.op('@@')(ts_query))
    )
    stmt = apply_filters(stmt, filters, allowed_teams, allowed_collections)
    # Chunk.id as secondary key keeps ties at the `limit` cutoff
    # deterministic across executions (the SQLite fallback tie-breaks
    # the same way).
    stmt = stmt.order_by(rank.desc(), Chunk.id.asc()).limit(limit)
    rows = db.execute(stmt).all()
    return [(chunk, float(rank_value)) for chunk, rank_value in rows]


# --- Reciprocal Rank Fusion ---------------------------------------------------------


@dataclass
class FusedResult:
    chunk: Chunk
    rrf_score: float
    vector_score: float | None
    fulltext_score: float | None


def rrf_fuse(
    vector_results: list[tuple[Chunk, float]],
    fulltext_results: list[tuple[Chunk, float]],
    k: int | None = None,
) -> list[FusedResult]:
    """Reciprocal Rank Fusion: each leg's rank (1-indexed, best match first)
    contributes `1 / (k + rank)` to a chunk's fused score, summed across
    whichever leg(s) that chunk actually appears in -- a chunk found by
    BOTH legs accumulates both contributions, which is precisely what makes
    RRF prefer a result both signals agree on over one only a single signal
    surfaced, without needing either leg's raw score to be on a comparable
    scale to the other's (cosine similarity and ts_rank/token-overlap are
    NOT comparable numbers -- RRF only ever looks at rank, never at score
    magnitude, which is the whole point of using it to fuse them).

    `k` defaults to settings.rrf_k, read at CALL time (not as a mutable
    default argument) so a test that monkeypatches settings.rrf_k is
    observed correctly. Sorted by fused score descending, tie-broken by
    `chunk.id` ascending for a fully deterministic, stable order regardless
    of dict/insertion ordering.
    """
    if k is None:
        k = settings.rrf_k

    contributions: dict[int, FusedResult] = {}

    for rank, (chunk, score) in enumerate(vector_results, start=1):
        entry = contributions.get(chunk.id)
        if entry is None:
            entry = FusedResult(chunk=chunk, rrf_score=0.0, vector_score=None, fulltext_score=None)
            contributions[chunk.id] = entry
        entry.rrf_score += settings.semantic_weight / (k + rank)
        entry.vector_score = score

    for rank, (chunk, score) in enumerate(fulltext_results, start=1):
        entry = contributions.get(chunk.id)
        if entry is None:
            entry = FusedResult(chunk=chunk, rrf_score=0.0, vector_score=None, fulltext_score=None)
            contributions[chunk.id] = entry
        entry.rrf_score += settings.lexical_weight / (k + rank)
        entry.fulltext_score = score

    fused = list(contributions.values())
    fused.sort(key=lambda entry: (-entry.rrf_score, entry.chunk.id))
    return fused


# --- top-level pipeline ---------------------------------------------------------


def _to_search_result(entry: FusedResult, document: Document, rerank_score: float | None) -> SearchResult:
    chunk = entry.chunk
    return SearchResult(
        chunk_id=chunk.id,
        document_id=str(document.id),
        text=chunk.text,
        heading_path=chunk.heading_path or [],
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        document_version=document.document_version,
        # `source` has no `documents` column of its own (only
        # `original_filename` does -- see apply_filters()'s docstring for
        # where this was actually verified); the raw source filename/URL
        # lives in the per-chunk denormalized metadata instead.
        source=chunk.meta.get('source'),
        original_filename=document.original_filename,
        team=document.team,
        department=document.department,
        # `document` is already the JOINed row vector_search()/
        # fulltext_search() fetched alongside its Chunk -- no extra query
        # needed. `None` here means the exact same thing it means on
        # `Document.collection_slug` itself: a pre-Collections legacy
        # document, not "unknown collection" (see that column's docstring).
        collection=document.collection_slug,
        scores=SearchScores(
            vector=entry.vector_score,
            fulltext=entry.fulltext_score,
            rrf=entry.rrf_score,
            rerank=rerank_score,
        ),
        embedding_model=chunk.embedding_model,
    )


def _rerank(
    query: str, kept: list[FusedResult]
) -> tuple[list[tuple[FusedResult, float | None]], bool]:
    """Run `kept` (the RRF-fused top `top_k`, already trimmed by the caller)
    through get_reranker(), and pair each FusedResult back up with its
    rerank score. Returns `(ordered, rerank_error)`: `ordered` is `kept`
    itself, reordered the way the reranker's own output says (NoopReranker's
    output order equals its input order, i.e. the untouched RRF order --
    see that class's docstring), paired with a score that is `None` for
    every entry exactly when reranking didn't run at all (settings.rerank_
    provider == 'none') or just failed (see below).

    A RerankError from the provider (an outage, a malformed response, ...)
    is caught here, not left to propagate -- a search request must never
    fail outright just because the optional reranking step couldn't run
    (see app/services/reranker.py's own module docstring for why that
    decision belongs to this caller). On that path this returns `kept` in
    its original (untouched RRF) order, every score `None`, and
    `rerank_error=True` -- functionally identical to what a NoopReranker
    would have produced, plus the trace flag a caller/operator needs to
    tell "no reranker configured" apart from "reranker outage this call".
    """
    if not kept:
        return [], False

    candidates = [RerankCandidate(chunk_id=entry.chunk.id, text=entry.chunk.text) for entry in kept]
    by_chunk_id = {entry.chunk.id: entry for entry in kept}

    try:
        scored_candidates = get_reranker().rerank(query, candidates)
    except RerankError as exc:
        logger.warning('reranker failed, falling back to RRF order: %s', exc)
        return [(entry, None) for entry in kept], True

    return [(by_chunk_id[candidate.chunk_id], score) for candidate, score in scored_candidates], False


def search(db: Session, request: SearchRequest) -> tuple[list[SearchResult], SearchTrace]:
    """Run the full hybrid-search pipeline for one request: embed the query,
    run the vector and fulltext legs (each capped at `top_k` candidates),
    fuse them with RRF, keep the fused top `top_k`, rerank that pool
    (settings.rerank_provider -- a NoopReranker for the 'none' default
    passes the RRF order straight through), and finally trim down to
    `final_k`. `final_k` also gates request validation (see
    app/api/search.py's `final_k > top_k` check) before this function ever
    runs, since a reranker can narrow a candidate set but never grow it.

    Returns `(results, trace)` rather than a full SearchResponse -- building
    the response envelope (echoing `request.query` back) is the API layer's
    job, not this service's.
    """
    refresh_control_plane()
    timings_ms: dict[str, float] = {}
    top_k = resolve_top_k(request)
    final_k = resolve_final_k(request)
    filters = request.filters
    allowed_teams = request.allowed_teams
    allowed_collections = request.allowed_collections

    start = time.perf_counter()
    query_vec = embed_query(request.query)
    timings_ms['embed'] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    vector_results = vector_search(db, query_vec, filters, allowed_teams, allowed_collections, top_k)
    timings_ms['vector_search'] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    fulltext_results = fulltext_search(db, request.query, filters, allowed_teams, allowed_collections, top_k)
    timings_ms['fulltext_search'] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    fused = rrf_fuse(vector_results, fulltext_results, settings.rrf_k)
    kept = fused[:top_k]
    timings_ms['fuse'] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    reranked, rerank_error = _rerank(request.query, kept)
    timings_ms['rerank'] = (time.perf_counter() - start) * 1000

    final_pairs = reranked[:final_k]

    # One JOIN-free lookup per kept chunk's owning document -- `documents`
    # is already joined inside vector_search()/fulltext_search()'s own
    # statements, but that Document instance isn't threaded back through
    # rrf_fuse() (which only ever sees `(chunk, score)` pairs), so it is
    # fetched again here via the identity map (already loaded this session
    # for every row either leg touched, so this is not a fresh SELECT for
    # any chunk actually present in `final_pairs`).
    results = [
        _to_search_result(entry, db.get(Document, entry.chunk.document_id), rerank_score)
        for entry, rerank_score in final_pairs
    ]

    trace = SearchTrace(
        vector_candidates=len(vector_results),
        fulltext_candidates=len(fulltext_results),
        fused=len(fused),
        reranked=len(kept),
        rerank_error=rerank_error,
        timings_ms=timings_ms,
    )
    return results, trace
