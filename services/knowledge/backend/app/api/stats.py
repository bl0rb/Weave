"""Aggregate corpus statistics for Weave-Ingest's admin overview, which
proxies this route (the browser never talks to this service directly).

Same internal credential as app/api/reindex.py. Every figure is an
aggregate query (COUNT/AVG/GROUP BY); no row is ever loaded, so the cost
does not grow with the size of the corpus beyond what the database itself
needs to count.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from sqlalchemy import and_, distinct, func, select, text
from sqlalchemy.orm import Session

from app.api.reindex import _require_reindex_token
from app.core.config import settings
from app.core.db import get_db
from app.models.models import Chunk, Collection, Document, DocumentStatus, KnowledgeMemory
from app.schemas.stats import (
    ChunkStats,
    CollectionStats,
    DocumentStats,
    DocumentStatusCounts,
    EmbeddingModelStats,
    KnowledgeStatsResponse,
    MemoryStats,
    StorageStats,
)
from app.services import memory

# Below Weave-Ingest's 5s proxy timeout (app/api/admin_statistics.py).
STATEMENT_TIMEOUT_MS = 4000

router = APIRouter(prefix='/api/v1/internal/stats', tags=['internal-stats'])


def _storage(db: Session) -> StorageStats | None:
    """On-disk size of the corpus tables incl. indexes and TOAST. PostgreSQL
    only; any other dialect (the SQLite test database) reports none."""
    if db.get_bind().dialect.name != 'postgresql':
        return None
    sizes = {
        table: int(db.scalar(
            text('SELECT COALESCE(pg_total_relation_size(to_regclass(:table)), 0)'), {'table': table}
        ) or 0)
        for table in ('documents', 'chunks', 'knowledge_memory')
    }
    return StorageStats(
        documents_bytes=sizes['documents'],
        chunks_bytes=sizes['chunks'],
        memory_bytes=sizes['knowledge_memory'],
        total_bytes=sum(sizes.values()),
    )


@router.get('', response_model=KnowledgeStatsResponse)
def knowledge_stats(
    x_weave_reindex_token: str | None = Header(default=None), db: Session = Depends(get_db)
) -> KnowledgeStatsResponse:
    _require_reindex_token(x_weave_reindex_token)

    if db.get_bind().dialect.name == 'postgresql':
        # Weave-Ingest gives up after 5s; cancel a slow scan instead of
        # leaving it running after the caller is gone. SET LOCAL ends with
        # this request's transaction.
        db.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))

    by_status = {
        getattr(value, 'value', value): count
        for value, count in db.execute(select(Document.status, func.count()).group_by(Document.status)).all()
    }
    embedding_present = Chunk.embedding.is_not(None)
    if db.get_bind().dialect.name == 'sqlite':
        # Same as app/api/indexing.py: SQLite's JSON fallback stores Python
        # None as JSON null, which is not SQL NULL.
        embedding_present = and_(func.json_type(Chunk.embedding) == 'array', func.json_array_length(Chunk.embedding) > 0)
    # One scan of the chunks of INDEXED documents (the search index's base,
    # cf. vector_index._eligible_conditions); the totals are derived from the
    # per-model rows, so chunks.* and embedding_models share one base.
    per_model = db.execute(
        select(
            Chunk.embedding_model, func.count(), func.count().filter(embedding_present),
            func.coalesce(func.sum(Chunk.char_count), 0),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.status == DocumentStatus.INDEXED)
        .group_by(Chunk.embedding_model)
    ).all()
    chunk_total = sum(row[1] for row in per_model)
    chunk_embedded = sum(row[2] for row in per_model)
    char_sum = sum(int(row[3] or 0) for row in per_model)
    models = sorted(
        ((model, count) for model, count, _embedded, _chars in per_model if model is not None),
        key=lambda item: (-item[1], item[0]),
    )
    with_documents = db.scalar(
        select(func.count(distinct(Document.collection_slug)))
        .join(Collection, Collection.slug == Document.collection_slug)
        .where(Document.status == DocumentStatus.INDEXED)
    )

    # Built summaries only, on the same base as memory.memory_status: document
    # summaries of indexed documents, collection overviews with a build (a
    # row holding only dirty_since is a pending rebuild, not a summary).
    memory_documents = db.scalar(
        select(func.count()).select_from(KnowledgeMemory)
        .join(Document, Document.id == KnowledgeMemory.document_id)
        .where(KnowledgeMemory.kind == memory.KIND_DOCUMENT, Document.status == DocumentStatus.INDEXED)
    ) or 0
    memory_collections = db.scalar(
        select(func.count()).select_from(KnowledgeMemory)
        .where(KnowledgeMemory.kind == memory.KIND_COLLECTION, KnowledgeMemory.built_at.is_not(None))
    ) or 0
    if memory_documents or memory_collections:
        # The centrally saved mode decides whether the feature is on; only
        # ask for it (a callback to Ingest) when there is something to report.
        memory.refresh_settings()
        if not memory.enabled():
            memory_documents = memory_collections = 0

    return KnowledgeStatsResponse(
        documents=DocumentStats(total=sum(by_status.values()), by_status=DocumentStatusCounts(**by_status)),
        chunks=ChunkStats(
            total=chunk_total,
            embedded=chunk_embedded,
            avg_chars=round(char_sum / chunk_total, 1) if chunk_total else None,
        ),
        embedding_models=[EmbeddingModelStats(model=model, chunks=count) for model, count in models],
        collections=CollectionStats(
            total=db.scalar(select(func.count()).select_from(Collection)) or 0,
            with_documents=with_documents or 0,
        ),
        memory=MemoryStats(
            documents=memory_documents,
            collections=memory_collections,
        ),
        vector_backend=settings.vector_backend,
        storage=_storage(db),
        generated_at=datetime.now(timezone.utc),
    )
