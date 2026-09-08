"""Pydantic shapes of inbound `document.processed` and `document.released`
webhook bodies (see the event contracts). Used by app/api/events.py to
validate signature-verified payloads before handling them.

Deliberately tolerant: `extra='ignore'` on every model here, since the
contract's own versioning section promises new optional top-level or
`signals` fields can be added without breaking consumers ("sie ignorieren
unbekannte Felder"). `frontmatter`/`quality.signals` are kept as plain
dicts rather than fully modeled -- this service stores frontmatter
verbatim (see app/models/models.py's Document.frontmatter docstring) and
only ever reads a handful of well-known keys out of it, so a strict nested
schema here would just be a second, driftable copy of
contracts/frontmatter.schema.json.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# job_id is always a Weave-Ingest Job UUID string (see
# contracts/events/document.processed.md's `job_id` field); content_sha256
# is always a lowercase-hex SHA256 digest. Both are pattern-validated here
# (not just `str`) because app/api/events.py builds its idempotency
# `event_key` as f'{job_id}:{content_sha256}' -- an unvalidated job_id
# containing its own ':' would let two DIFFERENT (job_id, content_sha256)
# pairs collide on the same event_key string (job_id='X:Y', sha='Z' ==
# job_id='X', sha='Y:Z'), silently merging two distinct documents'
# idempotency ledger entries. Requiring job_id to be UUID-shaped and
# content_sha256 to be exactly 64 hex chars makes every operand of that
# collision structurally invalid, so both variants are rejected by pydantic
# before an event_key is ever built.
_UUID_PATTERN = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
_SHA256_HEX_PATTERN = r'^[a-f0-9]{64}$'


class DocumentQuality(BaseModel):
    model_config = ConfigDict(extra='ignore')

    # None for both is a real, defensive wire value (see the contract's
    # `quality.grade`/`quality.recommendation` sections) -- consumers are
    # told to treat a null recommendation like 'warn'; app/api/events.py
    # applies that fallback itself rather than baking it into this schema,
    # so the stored `Document.quality_recommendation` stays the raw wire
    # value (including a genuine None) for anyone auditing it later.
    grade: Literal['A', 'B', 'C'] | None = None
    recommendation: Literal['allow', 'warn', 'block'] | None = None
    signals: dict = Field(default_factory=dict)


class _DocumentEventBase(BaseModel):
    model_config = ConfigDict(extra='ignore')

    event: str
    job_id: str = Field(pattern=_UUID_PATTERN)
    document_version: int
    previous_job_id: str | None = None
    content_sha256: str = Field(pattern=_SHA256_HEX_PATTERN)
    original_filename: str
    markdown_url: str
    frontmatter: dict = Field(default_factory=dict)
    quality: DocumentQuality
    engine: str
    processed_at: str


class DocumentProcessedEvent(_DocumentEventBase):
    event: Literal['document.processed']


class DocumentReleasedEvent(_DocumentEventBase):
    """Signed, immutable release notification.

    The release identifiers are deliberately separate from the processed
    event's content hash.  The release snapshot is what Knowledge may
    publish, and its hash is checked again after the authenticated download.
    """

    event: Literal['document.released']
    release_id: UUID
    markdown_sha256: str = Field(pattern=_SHA256_HEX_PATTERN)
    quality_override: bool = Field(default=False, strict=True)
