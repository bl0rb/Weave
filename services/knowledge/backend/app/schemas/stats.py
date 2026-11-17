"""Aggregate corpus statistics for Weave-Ingest's admin overview
(GET /api/v1/internal/stats, app/api/stats.py). Counts and sizes only, never
document contents."""

from datetime import datetime

from pydantic import BaseModel


class DocumentStatusCounts(BaseModel):
    pending: int = 0
    indexed: int = 0
    blocked: int = 0
    superseded: int = 0
    failed: int = 0


class DocumentStats(BaseModel):
    total: int
    by_status: DocumentStatusCounts


class ChunkStats(BaseModel):
    total: int
    embedded: int
    avg_chars: float | None


class EmbeddingModelStats(BaseModel):
    model: str
    chunks: int


class CollectionStats(BaseModel):
    total: int
    with_documents: int


class MemoryStats(BaseModel):
    documents: int
    collections: int


class StorageStats(BaseModel):
    documents_bytes: int
    chunks_bytes: int
    memory_bytes: int
    total_bytes: int


class KnowledgeStatsResponse(BaseModel):
    documents: DocumentStats
    chunks: ChunkStats
    embedding_models: list[EmbeddingModelStats]
    collections: CollectionStats
    memory: MemoryStats
    vector_backend: str
    storage: StorageStats | None
    generated_at: datetime
