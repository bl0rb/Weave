"""Schemas for the disaster-recovery admin surface (app/api/backup.py).

The import request itself is NOT a schema here: `POST .../imports` is a
multipart upload (file + passphrase + force), read via FastAPI's
`File`/`Form` params directly in the route, the same way every other
multipart endpoint in this codebase (e.g. `save_upload` callers in
app/api/routes.py) has no dedicated Pydantic request model either.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class BackupExportRequest(BaseModel):
    # Matches the target-side PBKDF2 key derivation's own minimum-effort
    # assumption (app/services/backup.py's PBKDF2_ITERATIONS) -- a short
    # passphrase would make that iteration count moot. Kept in lockstep with
    # the admin UI's own client-side 12-character minimum (backup-tab.tsx)
    # so the guarantee holds regardless of entry point (UI, CLI, direct API
    # call).
    passphrase: str = Field(min_length=12, max_length=256)


class BackupRunResponse(BaseModel):
    id: str
    kind: str
    status: str
    file_name: str | None
    size_bytes: int | None
    progress: dict
    report: dict | None
    error_message: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {'from_attributes': True}


class BackupRunListResponse(BaseModel):
    runs: list[BackupRunResponse]


class TargetStateResponse(BaseModel):
    fresh: bool
    reasons: list[str]
