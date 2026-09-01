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
#
# 'collection.updated' (see contracts/events/collection.updated.md) also
# rides this same connection/delivery/retry machinery, but its dispatch
# shape differs from every event above: it is fired from app/api/routes.py's
# POST /collections and PATCH /collections/{id} (not a job/run completion
# hook) and fans out to every enabled connection subscribed to it, across
# every owner -- there is no per-task webhook_connection_id to opt a
# collection into, since a collection is a global ACL/registry resource (see
# GET /collections/registry), not a job or run one caller configured with a
# single connection. See app/workers/webhook_tasks.dispatch_collection_event.
WEBHOOK_EVENTS: tuple[str, ...] = (
    'job.finished', 'job.failed', 'import_run.finished', 'document.processed', 'collection.updated',
)

WebhookEvent = Literal[
    'job.finished', 'job.failed', 'import_run.finished', 'document.processed', 'collection.updated',
]


# --- Connections ------------------------------------------------------------

class WebhookConnectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Not normalized/reshaped (unlike OpenWebUIConnectionCreateRequest.base_url)
    # -- see app/api/webhook_routes._validate_webhook_url.
    url: str = Field(min_length=1, max_length=2048)
    # Write-only, and genuinely optional (unlike OpenWebUI's api_key): a
    # connection may be created with no signing secret at all.
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
    # Unlike OpenWebUIConnectionUpdateRequest.api_key (omitted/empty both
    # mean "keep"), the secret column is nullable and a webhook connection
    # with no secret is a valid, meaningful state -- so there must be an
    # explicit way to reach it again after one was set.
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
