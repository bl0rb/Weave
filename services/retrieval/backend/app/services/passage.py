"""Passage lookup behind POST /api/v1/passage (app/api/search.py): one
target chunk of one document plus up to `context` neighbouring chunks on
each side, for the chat UI's "Textstelle öffnen" view of a citation.

Visibility is NOT re-implemented here: the target chunk is looked up through
app/services/search.py's apply_filters() -- the exact same WHERE clauses a
search request with the same `allowed_teams`/`allowed_collections` runs
under (document status `indexed`, the team gate, the collection gate incl.
NO_COLLECTION_SENTINEL for legacy documents). `filters` is always `None`
here: SearchFilters are search refinements, not access rules. Consequently
a passage is returned exactly when a search by the same caller could have
surfaced that chunk -- a superseded, blocked or out-of-scope document is
indistinguishable from a missing one (get_passage() returns `None` for all
of them, the API turns that into one uniform 404).

Neighbours are only ever read from the SAME, already-authorized document
(by `chunk_index`), so they need no second visibility check.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Chunk, Document
from app.schemas.search import PassageChunk, PassageRequest, PassageResponse
from app.services.search import _meta_str, apply_filters


def get_passage(db: Session, request: PassageRequest) -> PassageResponse | None:
    """Return the passage for `request`, or `None` when the document id is
    malformed or unknown, the chunk does not exist or belongs to another
    document, or the document is not visible to the caller (any status but
    `indexed`, outside `allowed_teams`/`allowed_collections`)."""
    try:
        document_uuid = uuid.UUID(str(request.document_id))
    except ValueError:
        return None

    stmt = (
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.id == request.chunk_id)
        .where(Document.id == document_uuid)
    )
    stmt = apply_filters(stmt, None, request.allowed_teams, request.allowed_collections)
    row = db.execute(stmt).first()
    if row is None:
        return None
    target, document = row

    neighbours = db.execute(
        select(Chunk)
        .where(Chunk.document_id == document.id)
        .where(Chunk.chunk_index >= target.chunk_index - request.context)
        .where(Chunk.chunk_index <= target.chunk_index + request.context)
        .order_by(Chunk.chunk_index.asc())
    ).scalars().all()

    meta = target.meta if isinstance(target.meta, dict) else {}
    frontmatter = document.frontmatter if isinstance(document.frontmatter, dict) else {}
    return PassageResponse(
        document_id=str(document.id),
        source_job_id=document.source_job_id,
        title=_meta_str(frontmatter, 'title'),
        original_filename=document.original_filename,
        document_version=document.document_version,
        source_kind=_meta_str(meta, 'source_kind'),
        source_url=_meta_str(meta, 'source_url'),
        collection=document.collection_slug,
        target_chunk_id=target.id,
        chunks=[
            PassageChunk(
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                heading_path=chunk.heading_path or [],
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                text=chunk.text,
                is_target=chunk.id == target.id,
            )
            for chunk in neighbours
        ],
    )
