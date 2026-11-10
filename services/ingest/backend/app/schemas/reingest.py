"""Schemas for the admin re-ingest (app/api/reingest.py, app/services/reingest.py)."""

from datetime import datetime

from pydantic import BaseModel, Field


class ReingestPreviewDocuments(BaseModel):
    rerelease: int
    reprocess: int
    skipped: dict[str, int]
    # Chain heads that came from a connection -- re-ingested through it.
    imported: int


class ReingestPreviewConnections(BaseModel):
    ready: int
    blocked: dict[str, int]


class ReingestPreviewResponse(BaseModel):
    documents: ReingestPreviewDocuments
    connections: ReingestPreviewConnections
    publication_configured: bool
    # Knowledge's inventory could be read (needed to re-release only what is live).
    knowledge_available: bool
    active_run_id: str | None = None


class ReingestStartRequest(BaseModel):
    """The admin's decision. `rerelease` has no default on purpose: re-releasing
    what was released before must be chosen explicitly. The expected counts
    are the preview the admin confirmed; a changed inventory is refused."""

    include_documents: bool = True
    include_connections: bool = True
    rerelease: bool
    accept_quality_warnings: bool = False
    include_edited: bool = False
    expected_documents: int = Field(ge=0)
    expected_connections: int = Field(ge=0)
    # Re-releases of the confirmed preview; when given, only these must match.
    expected_rereleases: int | None = Field(default=None, ge=0)


class ReingestRunResponse(BaseModel):
    id: str
    status: str
    options: dict
    counts: dict
    summary: dict | None = None
    error_message: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class ReingestRunListResponse(BaseModel):
    items: list[ReingestRunResponse]


class ReingestItemResponse(BaseModel):
    id: int
    kind: str
    action: str
    status: str
    result: str | None = None
    message: str | None = None
    label: str | None = None
    job_id: str | None = None
    new_job_id: str | None = None
    sync_id: str | None = None
    import_run_id: str | None = None
    updated_at: datetime


class ReingestItemListResponse(BaseModel):
    items: list[ReingestItemResponse]
    total: int
