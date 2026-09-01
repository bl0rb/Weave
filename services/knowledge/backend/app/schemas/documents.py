from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.models import DocumentStatus


class DocumentSummary(BaseModel):
    """One row of GET /api/v1/documents -- deliberately excludes
    markdown_body/frontmatter (can be large, and callers listing documents
    rarely need either); see DocumentDetail for the single-document view.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_job_id: str
    document_version: int
    original_filename: str | None = None
    engine: str
    team: str | None = None
    department: str | None = None
    tags: list[str] = []
    status: DocumentStatus
    chunk_count: int
    processed_at: datetime
    indexed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentSummary):
    """GET /api/v1/documents/{id} -- everything from the summary plus the
    fields only worth paying for on a single-document fetch.
    """

    content_sha256: str
    previous_job_id: str | None = None
    quality_grade: str | None = None
    quality_recommendation: str | None = None
    frontmatter: dict
    embedding_model: str | None = None
    error: str | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int
