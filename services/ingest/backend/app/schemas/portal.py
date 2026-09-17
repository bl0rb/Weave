from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ReleaseStatus = Literal['pending', 'sent', 'failed']


class PortalReleaseSummary(BaseModel):
    id: str
    created_at: datetime
    status: ReleaseStatus
    error_message: str | None = None
    # Username of the acting user at release time (DocumentRelease.owner_id);
    # None when unknown (no release, or the owning user was later deleted).
    released_by: str | None = None


SourceKind = Literal['upload', 'confluence', 'mail', 'unknown']


class PortalDocumentSource(BaseModel):
    kind: SourceKind
    label: str
    path: str | None = None
    url: str | None = None


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
    source: PortalDocumentSource
    # 'skipped' when a reviewer decided not to release this document (see
    # app/api/portal.py's _review_decision()); None otherwise.
    review_decision: str | None = None


class PortalDocumentListResponse(BaseModel):
    items: list[PortalDocumentItem]
    total: int


class PortalQualitySignals(BaseModel):
    ocr_confidence: float | None = None
    confidence_sample_size: int = 0
    structure_quality: float = 0.0
    noise_penalty: float = 0.0
    text_quality: float = 0.0
    field_validation: dict = Field(default_factory=dict)


class PortalQualityThresholds(BaseModel):
    A: float
    B: float


class PortalQualityDetail(BaseModel):
    grade: str | None = None
    score: float | None = None
    recommendation: str | None = None
    thresholds: PortalQualityThresholds
    signals: PortalQualitySignals
    issues: list = Field(default_factory=list)


# Machine keys explaining an absent quality grade, mapped to German text by
# the frontend -- see app/api/portal.py's _quality_detail().
QualityMissingReason = Literal['not_finished', 'failed', 'legacy', 'import_without_gate', 'unknown']


class PortalDocumentDetail(PortalDocumentItem):
    markdown: str
    markdown_sha256: str
    profile_id: str | None = None
    can_reprocess: bool = False
    quality: PortalQualityDetail | None = None
    quality_missing_reason: QualityMissingReason | None = None


class PortalReleaseRequest(BaseModel):
    markdown_sha256: str = Field(pattern=r'^[0-9a-fA-F]{64}$')
    accept_quality_warning: bool = False


class PortalCollectionReleaseRequest(BaseModel):
    accept_quality_warnings: bool = False


class PortalCollectionReleaseResponse(BaseModel):
    released: int
    skipped: int


class PortalReprocessRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    markdown_sha256: str = Field(pattern=r'^[0-9a-fA-F]{64}$')


class PortalReprocessResponse(BaseModel):
    job_id: str
    status: Literal['queued']
    profile_id: str


class PortalBulkActionRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal['release', 'skip', 'unskip', 'delete']
    accept_quality_warnings: bool = False


class PortalBulkErrorItem(BaseModel):
    job_id: str
    reason: str


class PortalBulkActionResponse(BaseModel):
    """Result of a bulk portal action. Rejected items (already released,
    quality-C without acceptance, no permission, not found, ...) are all
    reported via `errors`, never counted separately -- there is no partial
    'skipped but not an error' outcome for a bulk action."""

    done: int
    errors: list[PortalBulkErrorItem] = Field(default_factory=list)


class PortalConfigResponse(BaseModel):
    publication_configured: bool
    team_name: str | None
    # Names of every team the caller belongs to (primary team included,
    # deduplicated) -- lets the knowledge-space form offer a real
    # multi-select instead of only the single primary team. Never the
    # system-wide team directory: a non-admin only ever sees their own
    # memberships here.
    team_names: list[str] = Field(default_factory=list)
