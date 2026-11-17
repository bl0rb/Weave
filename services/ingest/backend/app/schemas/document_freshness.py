"""Schemas of the document freshness surface (app/api/document_freshness.py):
review dates, version comparison, topic subscriptions and the change feed."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.portal import PortalDocumentReview


class DocumentReviewUpdate(BaseModel):
    """PUT /documents/{id}/review: the next due date (null clears it)."""

    review_due_at: date | None


class DocumentReviewConfirm(BaseModel):
    """POST /documents/{id}/review/confirm (body optional): an explicit next
    due date; omitted = now + the space's review interval (or none)."""

    review_due_at: date | None = None


class DocumentReviewResponse(PortalDocumentReview):
    """`{review_due_at, last_reviewed_at, last_reviewed_by, overdue}`."""


class DocumentDiffVersion(BaseModel):
    job_id: str
    document_version: int
    created_at: datetime | None = None


class DocumentDiffStats(BaseModel):
    added_lines: int
    removed_lines: int
    sections_added: int
    sections_removed: int
    sections_changed: int


class DocumentDiffHunk(BaseModel):
    op: Literal['equal', 'insert', 'delete', 'replace']
    base: list[str]
    target: list[str]


class DocumentDiffSection(BaseModel):
    heading_path: list[str]
    change: Literal['added', 'removed', 'changed']
    added_lines: int
    removed_lines: int
    hunks: list[DocumentDiffHunk]


class DocumentDiff(BaseModel):
    """Section-aware comparison (app/services/document_diff.py); unchanged
    sections are omitted, `summary` is German, human readable."""

    base: DocumentDiffVersion
    target: DocumentDiffVersion
    stats: DocumentDiffStats
    summary: list[str]
    sections: list[DocumentDiffSection]


class ChangeCollectionRef(BaseModel):
    id: str
    name: str
    slug: str


class TopicSubscriptionCreate(BaseModel):
    """At least one of `collection_id` / `query` (keywords, every one must
    occur). `label` defaults to the query or the space's name."""

    collection_id: str | None = None
    query: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=120)

    @field_validator('query', 'label', 'collection_id')
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class TopicSubscriptionOut(BaseModel):
    id: str
    label: str
    query: str | None = None
    # The normalised keywords the matcher uses.
    terms: list[str] = []
    collection: ChangeCollectionRef | None = None
    created_at: datetime


class TopicSubscriptionListResponse(BaseModel):
    items: list[TopicSubscriptionOut]


class ChangeSummary(BaseModel):
    """Diff stats (null for a new document) and up to 10 summary lines."""

    stats: DocumentDiffStats | None = None
    lines: list[str] = []


class ChangeSubscriptionRef(BaseModel):
    id: str
    label: str


class ChangeNotificationOut(BaseModel):
    id: str
    subscription: ChangeSubscriptionRef | None = None
    job_id: str
    previous_job_id: str | None = None
    document_version: int | None = None
    collection: ChangeCollectionRef
    change_type: Literal['new', 'updated']
    title: str
    summary: ChangeSummary
    matched_terms: list[str] = []
    read_at: datetime | None = None
    created_at: datetime


class ChangeNotificationListResponse(BaseModel):
    items: list[ChangeNotificationOut]
    total: int
    page: int
    page_size: int


class ChangeReadRequest(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=500)
    all: bool = False


class ChangeReadResponse(BaseModel):
    updated: int


class ChangeCountResponse(BaseModel):
    unread: int


class ChangeSearchItem(BaseModel):
    job_id: str
    previous_job_id: str | None = None
    document_version: int
    collection: ChangeCollectionRef
    change_type: Literal['new', 'updated']
    title: str
    released_at: datetime
    summary: ChangeSummary
    matched_terms: list[str] = []


class ChangeSearchResponse(BaseModel):
    items: list[ChangeSearchItem]
    terms: list[str] = []
    # True when the per-request scan cap stopped the search before older
    # releases were checked (app/services/change_feed.search_changes).
    incomplete: bool = False

