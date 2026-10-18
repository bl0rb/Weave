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
  served without its generated text (fail closed until the rebuild).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Collection, Document, KnowledgeMemory
from app.schemas.memory import (
    MemoryCollection,
    MemoryDocument,
    MemoryDocumentRef,
    MemoryRequest,
    MemoryResponse,
)
from app.schemas.search import SearchFilters, SearchRequest
from app.services.search import NO_COLLECTION_SENTINEL, apply_filters, search

logger = logging.getLogger(__name__)

KIND_DOCUMENT = 'document'
KIND_COLLECTION = 'collection'

_MAX_LISTED_DOCUMENTS = 50
_SEARCH_POOL = 50
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


def _ranked_document_ids(db: Session, request: MemoryRequest) -> list:
    """Document order from the regular hybrid chunk search (same permission
    inputs), grouped by document. A search failure (embedding provider down)
    only loses this ranking, never the request."""
    try:
        results, _ = search(db, SearchRequest(
            query=request.query,
            filters=SearchFilters(collection=request.collection) if request.collection else None,
            allowed_teams=request.allowed_teams,
            allowed_collections=request.allowed_collections,
            top_k=_SEARCH_POOL,
            final_k=_SEARCH_POOL,
        ))
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning('memory: chunk search for document ranking failed', exc_info=True)
        return []
    return list(dict.fromkeys(result.document_id for result in results))


def _documents(db: Session, request: MemoryRequest, query: str) -> list[MemoryDocument]:
    rows = {str(document.id): (row, document) for row, document in _document_rows(db, request)}
    named = [
        document_id for document_id, (row, document) in rows.items()
        if _named_in(query, row.title) or len(_stem(document.original_filename)) >= _MIN_NAME_CHARS
        and _stem(document.original_filename) in query
    ]
    ordered = list(dict.fromkeys([*named, *_ranked_document_ids(db, request)]))
    return [_to_document(*rows[document_id]) for document_id in ordered if document_id in rows][:request.limit]


def memory_search(db: Session, request: MemoryRequest) -> MemoryResponse:
    query = _normalize(request.query)
    return MemoryResponse(collections=_collections(db, request, query), documents=_documents(db, request, query))
