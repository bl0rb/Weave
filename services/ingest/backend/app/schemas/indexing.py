from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.portal import PortalReleaseSummary


class PortalIndexingStatus(BaseModel):
    state: Literal[
        'not_received', 'mismatch', 'pending', 'indexed', 'empty',
        'incomplete', 'failed', 'blocked', 'superseded', 'unavailable',
    ]
    indexed_at: datetime | None = None
    chunk_count: int = Field(default=0, ge=0, strict=True)
    # Last status Knowledge confirmed for exactly this release, served during
    # a short transient outage (app/services/indexing_status.py).
    stale: bool = False
    checked_at: datetime | None = None
    # 'unavailable' only: a transient outage the portal shows as neutral.
    retrying: bool = False


class PortalIndexingItem(BaseModel):
    job_id: str
    release: PortalReleaseSummary | None = None
    indexing: PortalIndexingStatus | None = None


class PortalIndexingResponse(BaseModel):
    items: list[PortalIndexingItem]
