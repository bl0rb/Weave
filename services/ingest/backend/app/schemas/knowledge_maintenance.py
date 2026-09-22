"""Schemas for the manual 'Index aus Freigaben neu aufbauen' admin action
(app/api/knowledge_maintenance.py) -- the manual counterpart of the
automatic post-import rebuild documented in app/services/backup.py."""

from datetime import datetime

from pydantic import BaseModel


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
