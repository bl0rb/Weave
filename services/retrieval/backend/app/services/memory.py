"""Knowledge memory read path (ADR-0009): collection overviews and document
summaries for overview questions ("Was ist in Bereich X?", "Welche Dokumente
gibt es zu Y?", "Worum geht es in Dokument Z?").

Permissions are part of every query here, never a filter afterwards:
- document summaries are read only JOINed to `documents` through
  app/services/search.py's apply_filters() -- the exact predicate the chunk
  search uses (status indexed, allowed_collections, legacy team gate);
- collection overviews are read only for slugs in `allowed_collections`, the
  predicate apply_filters() applies to every document inside a collection;
- an overview whose source documents are no longer all indexed there is
  served without its generated text (fail closed until the rebuild);
- knowledge cards (include_cards) are read only JOINed to `documents` on
  their primary source through apply_filters(), only from spaces whose
  `memory_cards_enabled` is on, and dropped unless EVERY source document is
  visible under the same apply_filters() (fail closed).

include_chunks (the one-call path) embeds the query once and runs the chunk
search focused on the documents/card sources found here; a focused miss or
no focus at all runs the plain search the caller would have sent itself.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import Float, bindparam, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Collection, Document, KnowledgeCard, KnowledgeMemory
from app.schemas.memory import (
    MemoryCard,
    MemoryCollection,
    MemoryDocument,
    MemoryDocumentRef,
    MemoryRequest,
    MemoryResponse,
)
from app.schemas.search import SearchFilters, SearchRequest, SearchResponse
from app.services.embeddings import embed_query
from app.services.search import (
    NO_COLLECTION_SENTINEL,
    _cosine_similarity,
    _document_uuids,
    _is_sqlite,
    apply_filters,
    search,
)

logger = logging.getLogger(__name__)

KIND_DOCUMENT = 'document'
KIND_COLLECTION = 'collection'

_MAX_LISTED_DOCUMENTS = 50
# Ranking pass only orders the summaries: a small pool without reranking.
_SEARCH_POOL = 20
# Postgres card candidates per returned card, before the source check.
_CARD_OVERFETCH = 4
_MIN_NAME_CHARS = 4


def _normalize(text: str | None) -> str:
    return ' '.join(re.sub(r'[_\-.]+', ' ', (text or '').casefold()).split())


def _stem(filename: str | None) -> str:
    return _normalize((filename or '').rsplit('.', 1)[0])


def _named_in(query: str, *names: str | None) -> bool:
    return any(len(name) >= _MIN_NAME_CHARS and name in query for name in map(_normalize, names))


def _document_rows(db: Session, request: MemoryRequest, *conditions):
    """(summary, document) pairs the caller may read -- apply_filters() is
    the boundary, exactly as for a chunk search with the same request."""
    filters = SearchFilters(collection=request.collection) if request.collection else None
    stmt = (
        select(KnowledgeMemory, Document)
        .join(Document, Document.id == KnowledgeMemory.document_id)
        .where(KnowledgeMemory.kind == KIND_DOCUMENT, *conditions)
    )
    stmt = apply_filters(stmt, filters, request.allowed_teams, request.allowed_collections)
    return db.execute(stmt.order_by(KnowledgeMemory.title, Document.id)).all()


def _frontmatter_str(document: Document, key: str) -> str | None:
    value = (document.frontmatter or {}).get(key)
    return value if isinstance(value, str) and value else None


def _to_document(row: KnowledgeMemory, document: Document) -> MemoryDocument:
    return MemoryDocument(
        document_id=str(document.id),
        collection=document.collection_slug,
        title=row.title,
        document_type=row.document_type,
        summary=row.summary,
        key_points=row.key_points or [],
        original_filename=document.original_filename,
        document_version=document.document_version,
        source=_frontmatter_str(document, 'source'),
        source_kind=_frontmatter_str(document, 'source_kind'),
        source_url=_frontmatter_str(document, 'source_url'),
        uploaded_by=_frontmatter_str(document, 'uploaded_by'),
        uploaded_at=_frontmatter_str(document, 'uploaded_at'),
        generator=row.generator,
        built_at=row.built_at,
    )


def _collections(db: Session, request: MemoryRequest, query: str) -> list[MemoryCollection]:
    slugs = [slug for slug in request.allowed_collections if slug != NO_COLLECTION_SENTINEL]
    if request.collection is not None:
        slugs = [slug for slug in slugs if slug == request.collection]
    if not slugs:
        return []
    registry = db.scalars(select(Collection).where(Collection.slug.in_(slugs)).order_by(Collection.name)).all()
    named = [collection for collection in registry if _named_in(query, collection.name, collection.slug)]
    chosen = {collection.slug: collection for collection in (named or registry)}
    if not chosen:
        return []
    overviews = {
        row.collection_slug: row
        for row in db.scalars(
            select(KnowledgeMemory).where(
                KnowledgeMemory.kind == KIND_COLLECTION,
                KnowledgeMemory.collection_slug.in_(list(chosen)),
                KnowledgeMemory.built_at.is_not(None),
            )
        ).all()
    }
    if not overviews:
        return []
    live: dict[str, list[tuple[KnowledgeMemory, Document]]] = {}
    for row, document in _document_rows(db, request, Document.collection_slug.in_(list(overviews))):
        live.setdefault(document.collection_slug, []).append((row, document))

    result = []
    for slug, collection in chosen.items():
        overview = overviews.get(slug)
        if overview is None:
            continue
        documents = live.get(slug, [])
        if not documents:
            continue
        current = {str(document.id) for _, document in documents}
        stale = not set(overview.source_document_ids or []) <= current
        result.append(MemoryCollection(
            collection=slug,
            name=collection.name,
            description=collection.description,
            summary=None if stale else overview.summary,
            topics=[] if stale else (overview.topics or []),
            stale=stale,
            document_count=len(documents),
            documents=[
                MemoryDocumentRef(document_id=str(document.id), title=row.title, document_type=row.document_type)
                for row, document in documents[:_MAX_LISTED_DOCUMENTS]
            ],
            generator=overview.generator,
            built_at=overview.built_at,
        ))
    return result[:request.limit]


def _search_request(request: MemoryRequest, **overrides) -> SearchRequest:
    """A SearchRequest with the memory request's query and permission inputs
    (and its collection refinement as the only filter)."""
    values = dict(
        query=request.query,
        filters=SearchFilters(collection=request.collection) if request.collection else None,
        allowed_teams=request.allowed_teams,
        allowed_collections=request.allowed_collections,
    )
    values.update(overrides)
    return SearchRequest(**values)


def _ranked_document_ids(db: Session, request: MemoryRequest, query_vec: list[float] | None = None) -> list:
    """Document order from the regular hybrid chunk search (same permission
    inputs), grouped by document -- a small pool without reranking, and no
    control-plane refresh of its own. A search failure (embedding provider
    down) only loses this ranking, never the request."""
    try:
        results, _ = search(
            db,
            _search_request(request, top_k=_SEARCH_POOL, final_k=_SEARCH_POOL, rerank=False),
            refresh=False,
            query_vec=query_vec,
        )
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning('memory: chunk search for document ranking failed', exc_info=True)
        return []
    return list(dict.fromkeys(result.document_id for result in results))


def _documents(
    db: Session, request: MemoryRequest, query: str, query_vec: list[float] | None = None,
) -> list[MemoryDocument]:
    rows = {str(document.id): (row, document) for row, document in _document_rows(db, request)}
    if not rows:
        return []  # nothing built yet: no embedding and no search, Runtime runs the chunk search itself
    named = [
        document_id for document_id, (row, document) in rows.items()
        if _named_in(query, row.title) or len(_stem(document.original_filename)) >= _MIN_NAME_CHARS
        and _stem(document.original_filename) in query
    ]
    ordered = list(dict.fromkeys([*named, *_ranked_document_ids(db, request, query_vec)]))
    return [_to_document(*rows[document_id]) for document_id in ordered if document_id in rows][:request.limit]


def _card_candidates(db: Session, request: MemoryRequest, query_vec: list[float]) -> list[tuple[KnowledgeCard, float]]:
    """(card, cosine similarity) of cards whose primary document the caller
    may read (apply_filters()) in a space with cards switched on."""
    filters = SearchFilters(collection=request.collection) if request.collection else None
    conditions = (
        Collection.memory_cards_enabled.is_(True),
        # A card follows its space: a document moved elsewhere no longer
        # carries the old space's cards.
        Document.collection_slug == KnowledgeCard.collection_slug,
        KnowledgeCard.embedding.is_not(None),
        KnowledgeCard.embedding_model == settings.embedding_model,
    )
    if _is_sqlite():
        stmt = (
            select(KnowledgeCard)
            .join(Document, Document.id == KnowledgeCard.document_id)
            .join(Collection, Collection.slug == KnowledgeCard.collection_slug)
            .where(*conditions)
        )
        stmt = apply_filters(stmt, filters, request.allowed_teams, request.allowed_collections)
        return [(card, _cosine_similarity(query_vec, card.embedding)) for card in db.scalars(stmt).all()]
    # Postgres: same `<=>` shape as search.py's _vector_search_db() (see
    # there for why the parameter type and return_type=Float are needed).
    distance = KnowledgeCard.embedding.op('<=>', return_type=Float)(
        bindparam('card_query_vec', query_vec, type_=KnowledgeCard.embedding.type)
    )
    stmt = (
        select(KnowledgeCard, distance.label('distance'))
        .join(Document, Document.id == KnowledgeCard.document_id)
        .join(Collection, Collection.slug == KnowledgeCard.collection_slug)
        .where(*conditions)
    )
    stmt = apply_filters(stmt, filters, request.allowed_teams, request.allowed_collections)
    stmt = stmt.order_by(distance.asc(), KnowledgeCard.id.asc()).limit(request.card_limit * _CARD_OVERFETCH)
    return [(card, 1.0 - float(dist)) for card, dist in db.execute(stmt).all()]


def _cards(db: Session, request: MemoryRequest, query_vec: list[float]) -> list[MemoryCard]:
    candidates = [
        (card, score) for card, score in _card_candidates(db, request, query_vec)
        if score >= settings.memory_card_min_similarity
    ]
    if not candidates:
        return []
    # Fail closed: every source document must be visible and indexed under
    # the same apply_filters(), not only the primary one.
    wanted = {str(value) for card, _ in candidates for value in card.source_document_ids or []}
    filters = SearchFilters(collection=request.collection) if request.collection else None
    stmt = apply_filters(
        select(Document.id).where(Document.id.in_(_document_uuids(sorted(wanted)))),
        filters, request.allowed_teams, request.allowed_collections,
    )
    visible = {str(document_id) for document_id in db.scalars(stmt).all()}
    cards = []
    for card, score in candidates:
        sources = list(dict.fromkeys(str(value) for value in card.source_document_ids or []))
        if not all(source in visible for source in sources):
            continue
        cards.append(MemoryCard(
            card_id=card.id,
            collection=card.collection_slug,
            question=card.question,
            answer=card.answer,
            keywords=[k for k in card.keywords or [] if isinstance(k, str)],
            document_id=str(card.document_id),
            source_document_ids=sources,
            score=score,
        ))
    cards.sort(key=lambda card: (-card.score, card.card_id))
    return cards[:request.card_limit]


def _chunk_search(
    db: Session, request: MemoryRequest, documents: list[MemoryDocument], cards: list[MemoryCard],
    query_vec: list[float],
) -> tuple[SearchResponse, list[str] | None]:
    """The chunk search focused on the memory's documents and card sources;
    a focused miss, or no focus at all, runs the plain search -- the very
    request the caller would otherwise send to /search -- so the one-call
    path never finds less than the plain search."""
    sizes = dict(top_k=request.top_k, final_k=request.final_k, rerank=request.rerank)
    cap = request.limit + (request.card_limit if request.include_cards else 0)
    focus = list(dict.fromkeys(
        [document.document_id for document in documents]
        + [source for card in cards for source in [card.document_id, *card.source_document_ids]]
    ))[:cap]
    if focus:
        focused = _search_request(
            request, filters=SearchFilters(collection=request.collection, document_ids=focus), **sizes,
        )
        results, trace = search(db, focused, refresh=False, query_vec=query_vec)
        if results:
            return SearchResponse(query=request.query, results=results, trace=trace), focus
    results, trace = search(db, _search_request(request, **sizes), refresh=False, query_vec=query_vec)
    return SearchResponse(query=request.query, results=results, trace=trace), None


# pgvector applies the permission/space filters after the HNSW scan: with
# the default scan only the ~40 globally nearest cards are considered, so a
# caller of a small space could get none. Iterative scans (pgvector >= 0.8)
# keep scanning until enough rows pass the filters; older versions only get
# a wider ef_search. Each is tried on its own, an unknown setting is skipped.
_CARD_SCAN_SETTINGS = ('SET LOCAL hnsw.iterative_scan = relaxed_order', 'SET LOCAL hnsw.ef_search = 200')


def _widen_card_scan(db: Session) -> None:
    for statement in _CARD_SCAN_SETTINGS:
        try:
            with db.begin_nested():
                db.execute(text(statement))
        except SQLAlchemyError:
            logger.debug('memory: %r not supported here', statement)


def _safe_cards(db: Session, request: MemoryRequest, query_vec: list[float]) -> list[MemoryCard]:
    """Cards are an optional orientation layer: a database without
    Knowledge migration 0008 (rollout order), a failing card query or a bad
    card row must not cost the request its summaries and chunk search; the
    failure serves no cards. Everything runs in a savepoint that is rolled
    back afterwards (the card search only reads): that keeps the Postgres
    transaction usable after an error and reverts the scan settings, so the
    chunk searches that follow run unchanged."""
    try:
        savepoint = db.begin_nested()
        try:
            if not _is_sqlite():
                _widen_card_scan(db)
            return _cards(db, request, query_vec)
        finally:
            savepoint.rollback()
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning('memory: knowledge card search failed, serving no cards', exc_info=True)
        return []


def memory_search(db: Session, request: MemoryRequest) -> MemoryResponse:
    query = _normalize(request.query)
    collections = _collections(db, request, query)
    if not (request.include_chunks or request.include_cards):
        return MemoryResponse(collections=collections, documents=_documents(db, request, query))
    query_vec = embed_query(request.query)  # once, for every search below
    documents = _documents(db, request, query, query_vec)
    cards = _safe_cards(db, request, query_vec) if request.include_cards else []
    response = MemoryResponse(collections=collections, documents=documents, cards=cards)
    if request.include_chunks:
        response.search, response.focus_documents = _chunk_search(db, request, documents, cards, query_vec)
    return response
