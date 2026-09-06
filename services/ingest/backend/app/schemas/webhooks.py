from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Fixed event set (exact strings) -- also the payload contract's `event`
# field values. Kept here as the single source of truth: request schemas
# validate against it via the Literal type below (invalid entries 422 for
# free), and app/api/webhook_routes.py imports this tuple for the same
# purpose wherever a plain Python container is more convenient than a type.
#
# 'document.processed' (see contracts/events/document.processed.md) rides
# the exact same connection/delivery/retry machinery as job.finished --
# it is dispatched right alongside job.finished (never on its own, never for
# job.failed) by app/workers/tasks.py's completion hook, through
# app/workers/webhook_tasks.dispatch_job_event with a second event string.
WEBHOOK_EVENTS: tuple[str, ...] = (
    'job.finished', 'job.failed', 'import_run.finished', 'document.processed',
)

WebhookEvent = Literal[
    'job.finished', 'job.failed', 'import_run.finished', 'document.processed',
]


# --- Connections ------------------------------------------------------------

class WebhookConnectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Preserved verbatim after validation because paths and query strings can
    # be meaningful to generic export receivers.
    url: str = Field(min_length=1, max_length=2048)
    # Write-only and optional: a connection may be created without a signing
    # secret, though signed exports are recommended.
    secret: str | None = Field(default=None, max_length=4096)
    events: list[WebhookEvent] = Field(min_length=1, max_length=len(WEBHOOK_EVENTS))
    enabled: bool = True


class WebhookConnectionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    events: list[WebhookEvent] | None = Field(default=None, min_length=1, max_length=len(WEBHOOK_EVENTS))
    enabled: bool | None = None
    # Write-only update, tri-state via model_fields_set (see
    # app/api/webhook_routes.update_webhook_connection):
    #   - key omitted entirely            -> stored secret unchanged
    #   - key present, value None or ''   -> stored secret cleared (secret_encrypted = NULL)
    #   - key present, non-empty value    -> stored secret rotated
    # The secret column is nullable: omitted means "keep", while an explicit
    # empty value clears it.
    secret: str | None = Field(default=None, max_length=4096)


class WebhookConnectionResponse(BaseModel):
    id: str
    name: str
    url: str
    enabled: bool
    events: list[str]
    # Never the secret itself -- just whether one is on file.
    has_secret: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = {'from_attributes': True}


class WebhookConnectionListResponse(BaseModel):
    items: list[WebhookConnectionResponse]


class WebhookConnectionTestResponse(BaseModel):
    ok: bool
    detail: str | None = None
    http_status: int | None = None


# --- Deliveries ---------------------------------------------------------------

class WebhookSendRequest(BaseModel):
    connection_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)


class WebhookDeliveryResponse(BaseModel):
    id: str
    connection_id: str | None = None
    connection_name: str
    event: str
    job_id: str | None = None
    import_run_id: str | None = None
    collection_id: str | None = None
    status: Literal['pending', 'sent', 'failed']
    http_status: int | None = None
    error_message: str | None = None
    attempts: int
    created_at: datetime
    updated_at: datetime

    model_config = {'from_attributes': True}


class WebhookDeliveryListResponse(BaseModel):
    items: list[WebhookDeliveryResponse]
