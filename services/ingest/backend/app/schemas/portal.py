from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ReleaseStatus = Literal['pending', 'sent', 'failed']


class PortalReleaseSummary(BaseModel):
    id: str
    created_at: datetime
    status: ReleaseStatus
    error_message: str | None = None


class PortalDocumentItem(BaseModel):
    id: str
    original_filename: str
    status: str
    collection_id: str
    collection_name: str
    created_at: datetime
    quality_grade: str | None = None
    quality_recommendation: str | None = None
    can_release: bool
    release: PortalReleaseSummary | None = None


class PortalDocumentListResponse(BaseModel):
    items: list[PortalDocumentItem]
    total: int


class PortalDocumentDetail(PortalDocumentItem):
    markdown: str
    markdown_sha256: str
    profile_id: str | None = None
    can_reprocess: bool = False


class PortalReleaseRequest(BaseModel):
    markdown_sha256: str = Field(pattern=r'^[0-9a-fA-F]{64}$')
    accept_quality_warning: bool = False


class PortalReprocessRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    markdown_sha256: str = Field(pattern=r'^[0-9a-fA-F]{64}$')


class PortalReprocessResponse(BaseModel):
    job_id: str
    status: Literal['queued']
    profile_id: str


class PortalConfigResponse(BaseModel):
    publication_configured: bool
    team_name: str | None
    # Names of every team the caller belongs to (primary team included,
    # deduplicated) -- lets the knowledge-space form offer a real
    # multi-select instead of only the single primary team. Never the
    # system-wide team directory: a non-admin only ever sees their own
    # memberships here.
    team_names: list[str] = Field(default_factory=list)
