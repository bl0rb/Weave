"""Schemas for the admin overview statistics (app/api/admin_statistics.py).

The `Knowledge*` models mirror Weave-Knowledge's GET /api/v1/internal/stats
(KnowledgeStatsResponse there). Unknown fields are ignored and missing
fields inside a section default, so a newer or older Knowledge never breaks
the admin overview. The top-level sections are required: a body without
them (wrong service, a gateway's `{"detail": ...}`) is no statistics and
becomes `knowledge_error`.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Tolerant(BaseModel):
    model_config = ConfigDict(extra='ignore')


class KnowledgeDocumentStatusCounts(_Tolerant):
    pending: int = 0
    indexed: int = 0
    blocked: int = 0
    superseded: int = 0
    failed: int = 0


class KnowledgeDocumentStats(_Tolerant):
    total: int = 0
    by_status: KnowledgeDocumentStatusCounts = Field(default_factory=KnowledgeDocumentStatusCounts)


class KnowledgeChunkStats(_Tolerant):
    total: int = 0
    embedded: int = 0
    avg_chars: float | None = None


class KnowledgeEmbeddingModelStats(_Tolerant):
    model: str
    chunks: int = 0


class KnowledgeCollectionStats(_Tolerant):
    total: int = 0
    with_documents: int = 0


class KnowledgeMemoryStats(_Tolerant):
    documents: int = 0
    collections: int = 0


class KnowledgeStorageStats(_Tolerant):
    documents_bytes: int = 0
    chunks_bytes: int = 0
    memory_bytes: int = 0
    total_bytes: int = 0


class KnowledgeStats(_Tolerant):
    documents: KnowledgeDocumentStats
    chunks: KnowledgeChunkStats
    embedding_models: list[KnowledgeEmbeddingModelStats] = Field(default_factory=list)
    collections: KnowledgeCollectionStats
    memory: KnowledgeMemoryStats
    vector_backend: str
    # PostgreSQL only; None on SQLite.
    storage: KnowledgeStorageStats | None = None
    generated_at: datetime | None = None


class JobStatusCounts(BaseModel):
    PENDING: int = 0
    RUNNING: int = 0
    FINISHED: int = 0
    FAILED: int = 0


class AdminDocumentStatistics(BaseModel):
    # Distinct documents: the newest version of every chain.
    total: int
    # Every version (all jobs).
    versions: int
    # Processing state of every version.
    by_status: JobStatusCounts
    # Released versions delivered to Knowledge (release status 'sent'), not
    # withdrawn and with no later released version anywhere in their chain.
    released: int
    # Versions created in the last 30 days.
    uploaded_last_30_days: int


class StorageBackendStatistics(BaseModel):
    objects: int
    bytes: int


class AdminStorageStatistics(BaseModel):
    objects: int
    bytes: int
    by_backend: dict[str, StorageBackendStatistics]


class AdminCollectionStatistics(BaseModel):
    total: int
    public: int
    restricted: int


class AdminUserStatistics(BaseModel):
    total: int
    active: int
    admins: int


class AdminReviewTaskStatistics(BaseModel):
    open: int
    in_progress: int


class AdminStatisticsResponse(BaseModel):
    documents: AdminDocumentStatistics
    storage: AdminStorageStatistics
    collections: AdminCollectionStatistics
    users: AdminUserStatistics
    teams: int
    review_tasks: AdminReviewTaskStatistics
    subscriptions: int
    # None when Knowledge is not configured or unreachable; knowledge_error
    # then says why (German, shown in the admin overview).
    knowledge: KnowledgeStats | None = None
    knowledge_error: str | None = None
    generated_at: datetime
