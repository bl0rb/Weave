"""Schemas of the quality-signal intake and the 'Prüfaufgaben' portal
surface (app/api/review_tasks.py, app/services/quality_tasks.py)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.services.quality_tasks import MAX_SIGNALS_PER_BATCH, SIGNAL_KEY_MAX_LENGTH

ReviewTaskKindValue = Literal['knowledge_gap', 'answer_feedback', 'review_due', 'contradiction']
ReviewTaskStatusValue = Literal['open', 'in_progress', 'done', 'dismissed']


class QualitySignalIn(BaseModel):
    """One outbox row of Weave-API. `payload` is validated per signal in
    quality_tasks.py (a malformed one is skipped, not the whole batch)."""

    signal_key: str = Field(min_length=1, max_length=SIGNAL_KEY_MAX_LENGTH, pattern=r'^[A-Za-z0-9:_.\-]+$')
    payload: dict[str, Any]


class QualitySignalBatch(BaseModel):
    signals: list[QualitySignalIn] = Field(max_length=MAX_SIGNALS_PER_BATCH)


class QualitySignalBatchResponse(BaseModel):
    accepted: int


class ContradictionReportIn(BaseModel):
    """One possible contradiction found by Weave-Knowledge after indexing
    (app/services/quality_tasks.ingest_contradiction): two chunks of
    different documents of the same space that are very similar but state
    different facts. Texts are trimmed server-side."""

    collection_slug: str = Field(min_length=1, max_length=255)
    source_job_id: str = Field(min_length=1, max_length=36)
    other_source_job_id: str = Field(min_length=1, max_length=36)
    similarity: float | None = None
    heading_path: list[str] | None = Field(default=None, max_length=50)
    other_heading_path: list[str] | None = Field(default=None, max_length=50)
    excerpt: str | None = Field(default=None, max_length=20000)
    other_excerpt: str | None = Field(default=None, max_length=20000)
    facts: list[str] = Field(default_factory=list, max_length=200)
    other_facts: list[str] = Field(default_factory=list, max_length=200)


class ContradictionBatch(BaseModel):
    reports: list[ContradictionReportIn] = Field(max_length=MAX_SIGNALS_PER_BATCH)


class ReviewTaskCollectionRef(BaseModel):
    id: str
    name: str
    slug: str


class ReviewTaskDocumentRef(BaseModel):
    job_id: str
    title: str
    document_version: int


class ReviewTaskUserRef(BaseModel):
    id: str
    username: str


class ReviewTaskOut(BaseModel):
    id: str
    kind: str
    status: str
    title: str
    collection: ReviewTaskCollectionRef
    document: ReviewTaskDocumentRef | None = None
    related_document: ReviewTaskDocumentRef | None = None
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    assignee: ReviewTaskUserRef | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    details: dict[str, Any] = {}


class ReviewTaskListResponse(BaseModel):
    items: list[ReviewTaskOut]
    total: int
    page: int
    page_size: int


class ReviewTaskCountResponse(BaseModel):
    """`open` counts the active tasks (open + in_progress), for the badge."""

    open: int
    by_kind: dict[str, int]


class ReviewTaskUpdate(BaseModel):
    """Only the fields sent are changed; `assignee_id: null` unassigns."""

    status: ReviewTaskStatusValue | None = None
    assignee_id: str | None = None
    resolution_note: str | None = Field(default=None, max_length=2000)

    @field_validator('resolution_note')
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class QualityFeedbackCounts(BaseModel):
    helpful: int = 0
    wrong: int = 0
    outdated: int = 0


class QualityTaskCounts(BaseModel):
    open: int = 0
    in_progress: int = 0


class QualityDocumentStats(BaseModel):
    job_id: str
    title: str
    helpful: int = 0
    wrong: int = 0
    outdated: int = 0


class QualitySummaryResponse(BaseModel):
    """Last 90 days over the caller's visible spaces (tasks: current state)."""

    feedback: QualityFeedbackCounts
    unanswered: int
    tasks: QualityTaskCounts
    documents: list[QualityDocumentStats]
