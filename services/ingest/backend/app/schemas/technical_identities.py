"""Schemas for TechnicalIdentity admin CRUD and the Weave-Tools-facing
internal introspection endpoint (app/api/technical_identities.py). Mirrors
the ApiToken* split in app/schemas/auth.py: exactly one response ever
carries the raw token value, and only at creation/rotation time.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class TechnicalIdentityCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    # Collection slugs to grant. Omitted/empty = no knowledge access at all
    # (Step 5: new integrations get NO access until collections are granted).
    allowed_collections: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class TechnicalIdentityUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    allowed_collections: list[str] | None = None
    enabled: bool | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    # Explicit flag so "clear the expiry" (None) is distinguishable from
    # "the field was simply not sent" -- same reasoning as ManagedBotUpdate's
    # clear_auth_token.
    clear_expiry: bool = False


class TechnicalIdentityResponse(BaseModel):
    """Admin-facing view -- token_prefix only, never the raw token."""

    id: str
    name: str
    description: str | None
    allowed_collections: list[str]
    enabled: bool
    token_prefix: str
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_by: str | None


class TechnicalIdentityCreateResponse(TechnicalIdentityResponse):
    """The only response that ever carries the raw token -- issued once, at
    creation or rotation time (see create_technical_identity /
    rotate_technical_identity)."""

    token: str


class TechnicalIdentityListResponse(BaseModel):
    items: list[TechnicalIdentityResponse]


class TechnicalIdentityAuditEntry(BaseModel):
    id: str
    event: str
    actor: str | None
    details: dict
    created_at: datetime


class TechnicalIdentityAuditListResponse(BaseModel):
    items: list[TechnicalIdentityAuditEntry]


# --- internal: Weave-Tools' own introspection call --------------------------

class TechnicalIdentityIntrospectionRequest(BaseModel):
    token: str = Field(min_length=1)


class TechnicalIdentityIntrospectionResponse(BaseModel):
    """Non-enumerating, mirrors Weave-API's own POST /internal/tokens/
    introspect contract exactly: unknown/disabled/revoked/expired all
    collapse into `{"active": false}` -- never a distinguishable error, so
    a caller of THIS endpoint gets no oracle over whether a given token
    string ever existed."""

    active: bool
    identity_id: str | None = None
    name: str | None = None
    allowed_collections: list[str] | None = None
    expires_at: datetime | None = None
