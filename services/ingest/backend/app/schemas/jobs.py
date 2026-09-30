from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.models import CollectionRole, CollectionVisibility, JobStatus


class UploadResponse(BaseModel):
    job_id: str
    status: JobStatus


class CollectionGrantInput(BaseModel):
    """One entry of a knowledge space's access list (ADR 0008): exactly one
    of `user_id` (a local or SSO account) or `team_id`, with a role. Teams
    can be members or readers, never owners."""

    user_id: str | None = None
    team_id: str | None = None
    role: CollectionRole

    @model_validator(mode='after')
    def _one_subject(self) -> 'CollectionGrantInput':
        if (self.user_id is None) == (self.team_id is None):
            raise ValueError('a grant names exactly one of user_id or team_id')
        if self.team_id is not None and self.role == CollectionRole.OWNER:
            raise ValueError('owners are persons, a team can only be member or reader')
        return self


class CollectionCreateRequest(BaseModel):
    name: str = ''
    # Lowercase-hyphen identifier, unique across all collections -- see
    # app/models/models.py's Collection docstring. Left unset (or blank),
    # the server derives one from `name` (see routes._unique_collection_slug).
    slug: str | None = None
    # The space's purpose ("Zweck"); required, see routes.create_collection.
    description: str | None = None
    # Left unset, create_collection fails closed: RESTRICTED. PUBLIC only
    # when sent explicitly.
    visibility: CollectionVisibility | None = None
    responsible_team_id: str | None = None
    # Additional grants; the creator always becomes an owner.
    grants: list[CollectionGrantInput] = Field(default_factory=list)
    email: str = ''
    department: str = ''
    folder: str = ''
    subfolder: str = ''
    password: str = ''


class CollectionUpdateRequest(BaseModel):
    """PATCH payload: every field optional and only applied when present
    (None = leave unchanged). `slug` is deliberately not patchable here --
    it is the collection's stable cross-service identity (see Collection's
    docstring) and already-processed documents carry it in their frontmatter.

    `grants` replaces the whole access list and must keep at least one
    owner. `responsible_team_id` is cleared with an empty string.
    """

    name: str | None = None
    description: str | None = None
    visibility: CollectionVisibility | None = None
    responsible_team_id: str | None = None
    grants: list[CollectionGrantInput] | None = None


class CollectionGrantResponse(BaseModel):
    """A resolved grant. `name` is the username or team name; `team` a
    person's primary team, for display. Never email or any other personal
    field, same discipline as the directory endpoints."""

    user_id: str | None = None
    team_id: str | None = None
    role: CollectionRole
    name: str
    team: str | None = None
    is_active: bool = True


class CollectionTeamRef(BaseModel):
    id: str
    name: str


class CollectionResponse(BaseModel):
    # `can_manage`: owner rights (settings, sharing, delete);
    # `can_upload`: member rights (documents). `role` is the caller's
    # effective role, see app/services/collection_access.py.
    can_manage: bool = False
    can_upload: bool = False
    role: CollectionRole | None = None
    collection_id: str
    slug: str
    name: str
    description: str | None = None
    visibility: CollectionVisibility
    grants: list[CollectionGrantResponse] = Field(default_factory=list)
    created_by: 'JobOwner | None' = None
    responsible_team: CollectionTeamRef | None = None
    email: str
    department: str
    folder: str = ''
    subfolder: str = ''
    job_ids: list[str] = Field(default_factory=list)


class CollectionListResponse(BaseModel):
    items: list[CollectionResponse] = Field(default_factory=list)


class CollectionRegistryEntry(BaseModel):
    """One row of the Weave-Knowledge sync payload (GET
    /collections/registry) -- ACL/identity metadata only, deliberately
    nothing document-shaped (no job_ids, no folder/subfolder/email/
    department)."""

    slug: str
    name: str
    description: str | None = None
    visibility: CollectionVisibility
    read_teams: list[str] = Field(default_factory=list)
    read_users: list[str] = Field(default_factory=list)


class CollectionRegistryResponse(BaseModel):
    items: list[CollectionRegistryEntry] = Field(default_factory=list)


class DirectoryUserEntry(BaseModel):
    """One row of GET /directory/users -- the person-picker's search result
    shape. Deliberately never email or any other personal field beyond
    username/team -- see routes.list_directory_users's own docstring."""

    id: str
    username: str
    display_name: str | None = None
    team: str | None = None


class DirectoryUsersResponse(BaseModel):
    items: list[DirectoryUserEntry] = Field(default_factory=list)


class DirectoryTeamEntry(BaseModel):
    id: str
    name: str
    member_count: int


class DirectoryTeamsResponse(BaseModel):
    items: list[DirectoryTeamEntry] = Field(default_factory=list)


class CollectionStartRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    # Delivery target for every job started in this batch (job.finished /
    # job.failed); None = no webhook. Validated against the caller's own
    # enabled connections once, up front, in start_collection_processing --
    # see app/api/routes.py's _validated_webhook_connection.
    webhook_connection_id: str | None = Field(default=None, min_length=1)


class CollectionStartResponse(BaseModel):
    collection_id: str
    started_jobs: int
    profile_id: str
    skipped_import_jobs: int = 0
    skipped_released_jobs: int = 0


class JobSaveRequest(BaseModel):
    markdown: str = Field(min_length=1)


class JobRestartRequest(BaseModel):
    profile_id: str | None = None


class JobSaveResponse(BaseModel):
    job_id: str
    version: int
    # Editor versions are now stored as job_markdown_versions rows rather
    # than on-disk '.v{n}.md' files (no shared volume between backend and
    # worker), so there is no longer a filesystem path to report here.
    path: str | None = None
    updated_at: datetime


class JobOwner(BaseModel):
    id: str
    username: str

    model_config = {'from_attributes': True}


class JobResponse(BaseModel):
    id: str
    original_filename: str
    status: JobStatus
    tags: list[str] = Field(default_factory=list)
    error_message: str | None = None
    processing_info: dict | None = None
    content_sha256: str | None = None
    document_version: int = 1
    previous_job_id: str | None = None
    benchmark_run_id: str | None = None
    created_at: datetime
    updated_at: datetime
    # None for legacy jobs (owner_id IS NULL) -- see Job.owner_id / _owner_visible.
    owner: JobOwner | None = None


class JobListResponse(BaseModel):
    items: list[JobResponse]


class JobSearchResponse(JobListResponse):
    total: int


class JobVersionEntry(BaseModel):
    job_id: str
    document_version: int
    content_sha256: str | None = None
    status: JobStatus
    created_at: datetime
    uploaded_by: str | None = None
    is_current: bool


class JobVersionsResponse(BaseModel):
    items: list[JobVersionEntry]


class DashboardStatsResponse(BaseModel):
    processed_documents: int
    processed_pages: int
    errors: int
    database_size_bytes: int | None = None


class HealthResponse(BaseModel):
    status: str


class RuntimeCapabilityInfo(BaseModel):
    torch_available: bool
    cuda_available: bool
    selected_device: Literal['gpu', 'cuda', 'cpu']
    platform: str
    no_cuda_reason: str | None = None


class ContainerState(BaseModel):
    name: str
    state: Literal['running', 'stopped', 'degraded', 'unknown']
    detail: str | None = None


class PaddleStatusResponse(BaseModel):
    status: Literal['running', 'failed', 'stopped']
    detail: str | None = None
    runtime: RuntimeCapabilityInfo | None = None
    pending_jobs: int = 0
    running_jobs: int = 0
    queue_total: int = 0
    running_workers: int = 0
    worker_nodes: list[str] = Field(default_factory=list)
    containers: list[ContainerState] = Field(default_factory=list)


class PaddleSettingsResponse(BaseModel):
    default_profile: str
    timeout_seconds: int


class PaddleSettingsUpdate(BaseModel):
    default_profile: str = Field(min_length=1)
    timeout_seconds: int = Field(ge=1)


class PaddleOption(BaseModel):
    value: str
    label: str
    description: str
    # 'ocr' for the static presets, 'vl' for a dynamic 'vl:<connection_id>'
    # entry (one per enabled VlConnection) -- see
    # paddle_service.get_paddle_capabilities. Defaulted for forward
    # compatibility, though the service always sets it explicitly now.
    kind: str = 'ocr'
    text_detection_model_name: str | None = None
    text_recognition_model_name: str | None = None


class PaddleCapabilitiesResponse(BaseModel):
    profiles: list[PaddleOption]


class MarkdownFileEntry(BaseModel):
    path: str
    filename: str
    folder: str
    size_bytes: int
    updated_at: datetime


class MarkdownBrowserResponse(BaseModel):
    items: list[MarkdownFileEntry]


class FolderActionRequest(BaseModel):
    folder: str = ''
    subfolder: str = ''


class FolderActionResponse(BaseModel):
    path: str
    deleted_jobs: int = 0


class PasswordVerificationRequest(BaseModel):
    password: str = Field(min_length=1)


CollectionResponse.model_rebuild()
