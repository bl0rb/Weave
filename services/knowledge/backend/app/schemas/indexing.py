"""Narrow publication-status contract; never returns document contents."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReleaseReference(BaseModel):
    model_config = ConfigDict(extra='forbid')

    job_id: UUID
    release_id: UUID
    markdown_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class IndexingStatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    items: list[ReleaseReference] = Field(min_length=1, max_length=50)


class IndexingStatusItem(ReleaseReference):
    state: Literal[
        'not_received', 'mismatch', 'pending', 'indexed', 'empty',
        'incomplete', 'failed', 'blocked', 'superseded',
    ]
    indexed_at: datetime | None = None
    chunk_count: int = 0


class IndexingStatusResponse(BaseModel):
    items: list[IndexingStatusItem]
