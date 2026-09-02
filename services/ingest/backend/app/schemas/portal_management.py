from datetime import datetime

from pydantic import BaseModel


class PortalManagementOwner(BaseModel):
    id: str
    username: str


class PortalCollectionItem(BaseModel):
    """`review_count` counts finished, non-empty, unprotected jobs without
    a release, with no import or a FINISHED import, and with quality
    recommendation other than `block` and grade other than `c` (case-insensitive).
    """

    collection_id: str
    slug: str
    name: str
    description: str | None = None
    read_teams: list[str]
    owner: PortalManagementOwner | None = None
    document_count: int
    pending_count: int
    running_count: int
    review_count: int
    failed_count: int
    released_count: int
    created_at: datetime
    updated_at: datetime


class PortalCollectionListResponse(BaseModel):
    items: list[PortalCollectionItem]
    total: int


class PortalActivityItem(BaseModel):
    id: str
    original_filename: str
    status: str
    created_at: datetime
    updated_at: datetime
    collection_id: str | None = None
    collection_name: str | None = None
    import_run_id: str | None = None
    import_status: str | None = None
    release_status: str | None = None
    quality_grade: str | None = None
    quality_recommendation: str | None = None


class PortalActivityCounts(BaseModel):
    pending: int
    running: int
    finished: int
    failed: int


class PortalActivityListResponse(BaseModel):
    items: list[PortalActivityItem]
    total: int
    counts: PortalActivityCounts
