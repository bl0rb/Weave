"""POST /api/v1/memory -- condensed knowledge (ADR-0009) for overview
questions. Same permission inputs as SearchRequest, except that
`allowed_collections` is REQUIRED: a collection overview is only equivalent
to its documents' permissions when the collection grant is the boundary
(ADR-0008), so "no restriction" (`None`) is refused rather than honored."""

from datetime import datetime

from pydantic import BaseModel, Field


class MemoryRequest(BaseModel):
    query: str = Field(min_length=1)
    allowed_collections: list[str]
    allowed_teams: list[str] | None = None
    # Refinement within allowed_collections, like SearchFilters.collection.
    collection: str | None = None
    limit: int = Field(default=8, ge=1, le=50)


class MemoryDocumentRef(BaseModel):
    document_id: str
    title: str | None = None
    document_type: str | None = None


class MemoryCollection(BaseModel):
    collection: str
    name: str
    description: str | None = None
    # None when the overview is stale: a document it was built from is no
    # longer indexed here, so only the live document list below is served.
    summary: str | None = None
    topics: list[str] = []
    stale: bool = False
    document_count: int = 0
    documents: list[MemoryDocumentRef] = []
    generator: str | None = None
    built_at: datetime | None = None


class MemoryDocument(BaseModel):
    document_id: str
    collection: str
    title: str | None = None
    document_type: str | None = None
    summary: str | None = None
    key_points: list[str] = []
    original_filename: str | None = None
    document_version: int
    source: str | None = None
    source_kind: str | None = None
    source_url: str | None = None
    uploaded_by: str | None = None
    uploaded_at: str | None = None
    generator: str | None = None
    built_at: datetime | None = None


class MemoryResponse(BaseModel):
    collections: list[MemoryCollection] = []
    documents: list[MemoryDocument] = []
