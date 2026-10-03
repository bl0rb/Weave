"""Schemas for the manual 'Index aus Freigaben neu aufbauen' admin action
(app/api/knowledge_maintenance.py) -- the manual counterpart of the
automatic post-import rebuild documented in app/services/backup.py -- and
for withdrawing orphaned Knowledge documents (app/services/knowledge_orphans.py)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class KnowledgeRebuildResponse(BaseModel):
    requeued: int
    worker_required: bool = True


class KnowledgeRebuildStatusResponse(BaseModel):
    total_releases: int
    withdrawn: int
    pending: int
    sent: int
    failed: int
    last_sent_at: datetime | None = None


class KnowledgeOrphanCleanupRequest(BaseModel):
    """`dry_run` defaults to true. Applying requires the orphan count of the
    dry run the admin reviewed, so nothing is withdrawn that was not shown."""

    dry_run: bool = True
    expected_orphans: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def require_reviewed_count(self):
        if not self.dry_run and self.expected_orphans is None:
            raise ValueError('expected_orphans is required when dry_run is false')
        return self


class KnowledgeOrphanItem(BaseModel):
    job_id: str
    collection_slug: str | None = None
    status: str
    reason: Literal['job_missing', 'collection_deleted', 'collection_unknown', 'withdrawn']
    withdrawal: str  # 'none' or the KnowledgeWithdrawal status before this call


class KnowledgeOrphanCleanupResponse(BaseModel):
    dry_run: bool
    scanned: int
    orphans: int
    by_reason: dict[str, int]
    already_pending: int
    queued: int
    items: list[KnowledgeOrphanItem]
    truncated: bool
    worker_required: bool = True
