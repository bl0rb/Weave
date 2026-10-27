from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.models import ImportAuthType, ImportRunStatus, JobStatus


# --- Sources ------------------------------------------------------------------

ImportSourceKind = Literal['confluence', 'website']


class ImportSourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # 'confluence' (default) or 'website' (generic website / intranet).
    kind: ImportSourceKind = 'confluence'
    base_url: str = Field(min_length=1, max_length=1024)
    # Must match the kind (models.IMPORT_AUTH_TYPES_BY_KIND); 'none' is the
    # only type without a credential.
    auth_type: ImportAuthType
    # Email for cloud_basic, username for http_basic; ignored otherwise.
    auth_username: str = Field(default='', max_length=320)
    # Write-only: no response schema in this module has a credential field.
    # Empty only for auth_type 'none'.
    credential: str = Field(default='', max_length=4096)
    # Website sources: release an intranet (private-address) host for this
    # connection. Admin-only; anyone else gets a 403.
    allow_private_target: bool = False


class ImportSourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, min_length=1, max_length=1024)
    auth_type: ImportAuthType | None = None
    auth_username: str | None = Field(default=None, max_length=320)
    # Write-only update: omitted or empty keeps the stored credential.
    credential: str | None = Field(default=None, max_length=4096)
    allow_private_target: bool | None = None


class ImportSourceResponse(BaseModel):
    id: str
    name: str
    kind: str = 'confluence'
    base_url: str
    server_kind: str = ''
    auth_type: ImportAuthType
    auth_username: str = ''
    allow_private_target: bool = False
    # Never the credential itself -- just whether one is on file.
    has_credential: bool = True
    last_validated_at: datetime | None = None
    created_at: datetime
    # Derived: number of configured imports (ImportSync) on this source.
    sync_count: int = 0

    model_config = {'from_attributes': True}


class ImportSourceListResponse(BaseModel):
    items: list[ImportSourceResponse]


class ImportSourceTestResponse(BaseModel):
    ok: bool
    detail: str | None = None
    server_kind: str | None = None


# --- Runs ---------------------------------------------------------------------

class ImportRunScope(BaseModel):
    # 'space' / 'page' for Confluence sources, 'url' (a start page) for
    # website sources.
    type: Literal['space', 'page', 'url']
    # Space key, numeric page id, or a pasted Confluence page URL (the server
    # extracts the pageId before persisting, so this may exceed the stored
    # scope_value's 512 chars).
    value: str = Field(min_length=1, max_length=2048)


class ImportRunOptions(BaseModel):
    # Collection assignment is selected by id only; slug and display name are
    # resolved server-side and stored as authoritative snapshots.
    collection_id: str | None = Field(default=None, min_length=1, exclude_if=lambda value: value is None)
    # None = server default; the server clamps to import_max_pages /
    # import_max_depth regardless of the requested value.
    max_pages: int | None = Field(default=None, ge=1)
    max_depth: int | None = Field(default=None, ge=0)
    include_attachments: bool = True
    ocr_attachments: bool = False
    ocr_profile_id: str | None = None
    folder: str = ''
    subfolder: str = ''
    tags: list[str] = Field(default_factory=list)
    email: str = ''
    # When set, each imported page's job additionally gets its Confluence
    # ancestor titles (PageContext.ancestor_titles / convert_page's
    # path_titles) attached as tags, on top of `tags` above -- see
    # app/workers/import_tasks.py's _job_tags.
    path_tags: bool = False
    # Delivery target for this run's 'import_run.finished' event; None = no
    # webhook. Validated against the caller's own enabled connections at
    # create time (app/api/import_routes.py's create_import_run) -- see
    # app/workers/webhook_tasks.py's dispatch_run_event for the consumer.
    webhook_connection_id: str | None = None
    # --- Website sources only (ignored for Confluence) ---
    # 'page' = the start page only, 'prefix' = pages under the start page's
    # directory, 'host' = every page on the host (see app/services/website.py).
    crawl_scope: Literal['page', 'prefix', 'host'] = 'prefix'
    # Extract the 'Rechtsstand' line of legal texts into frontmatter.
    legal_source: bool = False
    # The source-catalog entry this import was created from, if any.
    catalog_entry_id: str | None = None


class ImportSyncSchedule(BaseModel):
    """Auto-sync of an import configuration. interval_seconds is
    server-clamped to >= settings.confluence_refresh_min_interval_seconds."""

    enabled: bool = False
    interval_seconds: int | None = Field(default=None, ge=1)


class ImportRunCreateRequest(BaseModel):
    source_id: str = Field(min_length=1)
    scope: ImportRunScope
    options: ImportRunOptions = Field(default_factory=ImportRunOptions)
    # Every run belongs to the import configuration of its (source, scope,
    # space); created on first use. Given here, it also sets that
    # configuration's schedule; omitted, an existing schedule is kept.
    sync: ImportSyncSchedule | None = None


class ImportRunOwner(BaseModel):
    id: str
    username: str

    model_config = {'from_attributes': True}


class ImportRunResponse(BaseModel):
    id: str
    sync_id: str | None = None
    kind: str
    status: ImportRunStatus
    scope_type: str
    scope_value: str
    root_page_title: str = ''
    can_sync: bool = False
    can_edit: bool = False
    missing_page_count: int = 0
    pages_discovered: int = 0
    pages_imported: int = 0
    pages_failed: int = 0
    attachments_saved: int = 0
    artifact_bytes: int = 0
    content_bytes: int = 0
    created_at: datetime
    # Refreshed at least once per imported page by the worker -- doubles as
    # the staleness heartbeat the frontend/API read.
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    owner: ImportRunOwner | None = None

    model_config = {'from_attributes': True}


class ImportRunListResponse(BaseModel):
    items: list[ImportRunResponse]


class ImportRunError(BaseModel):
    page_id: str
    title: str = ''
    error: str


class ImportRunJobSummary(BaseModel):
    id: str
    # From Job.original_filename.
    title: str
    status: JobStatus


class ImportRunDetailResponse(ImportRunResponse):
    missing_pages: list[dict] = Field(default_factory=list)
    can_remove_missing: bool = False
    # Needed to prefill the "edit & run again" wizard; the source may since
    # have been deleted (source_id goes NULL on delete, runs keep history).
    source_id: str | None = None
    # The persisted options snapshot (see ImportRun.options). Extra keys
    # written by other run kinds -- e.g. is_refresh on auto-refresh runs --
    # are ignored by ImportRunOptions.model_validate (Pydantic's default
    # extra='ignore' for dict input).
    options: ImportRunOptions = Field(default_factory=ImportRunOptions)
    current_page_title: str = ''
    error_message: str | None = None
    cancel_requested: bool = False
    errors: list[ImportRunError] = Field(default_factory=list)
    jobs: list[ImportRunJobSummary] = Field(default_factory=list)
    # SH-04: lets a FINISHED run that still has unprocessed attachment-OCR
    # children (backstop send failed / startup recovery hasn't caught up
    # yet) be told apart from one that is genuinely all done.
    pending_attachment_jobs: int = 0


# --- Import configurations (syncs) ------------------------------------------------

class ImportSyncLastRun(BaseModel):
    """The configuration's most recent run of any status."""

    id: str
    status: ImportRunStatus
    is_refresh: bool = False
    created_at: datetime
    finished_at: datetime | None = None
    pages_imported: int = 0
    pages_failed: int = 0


class ImportSyncResponse(BaseModel):
    id: str
    source_id: str
    source_name: str = ''
    # The connection's kind: 'confluence' | 'website'.
    source_kind: str = 'confluence'
    scope_type: str
    scope_value: str
    root_page_title: str = ''
    collection_id: str | None = None
    collection_name: str | None = None
    options: ImportRunOptions = Field(default_factory=ImportRunOptions)
    enabled: bool = False
    interval_seconds: int | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    next_refresh_at: datetime | None = None
    last_run: ImportSyncLastRun | None = None
    created_at: datetime


class ImportSyncListResponse(BaseModel):
    items: list[ImportSyncResponse]


class ImportSyncUpdateRequest(BaseModel):
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=1)
    # Replaces the stored options. Scope and knowledge space are fixed for
    # a configuration; options.collection_id must be omitted or unchanged.
    options: ImportRunOptions | None = None


class ImportRunCancelResponse(BaseModel):
    id: str
    status: ImportRunStatus
    cancel_requested: bool = False


class ImportWithdrawalRequest(BaseModel):
    confirm: bool = False


class ImportRunDeleteResponse(BaseModel):
    id: str
    deleted_jobs: int = 0


# --- Job artifacts ------------------------------------------------------------

class JobArtifactResponse(BaseModel):
    id: str
    kind: str
    filename: str
    content_type: str
    size_bytes: int

    model_config = {'from_attributes': True}


class JobArtifactListResponse(BaseModel):
    items: list[JobArtifactResponse]
