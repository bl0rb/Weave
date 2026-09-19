"""Technical identities (Schritt 5 -- "Technische Identitaeten, MCP und
REST"): admin CRUD for standalone-integration credentials, and the internal
introspection endpoint Weave-Tools calls to resolve one of these tokens
into a Scope (see that service's app/services/scope.py). Follows the same
three-router split as app/api/chat_provider.py / app/api/managed_bots.py:
`router_admin` (require_admin + origin_guard, at the router level) and
`router_internal` (this module's own static service-token gate, mirroring
chat_provider.py's `_require_runtime_token`).

Every mutation (create/update/rotate/revoke) writes one row to
technical_identity_audit; a DENIED introspection attempt is audited too --
a successful one is not (that would be one row per tool call; see
TechnicalIdentity.last_used_at for "was this identity used recently").
"""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import aware_utc, origin_guard, require_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.models import Collection, TechnicalIdentity, TechnicalIdentityAudit, User
from app.schemas.technical_identities import (
    TechnicalIdentityAuditListResponse,
    TechnicalIdentityCreateRequest,
    TechnicalIdentityCreateResponse,
    TechnicalIdentityIntrospectionRequest,
    TechnicalIdentityIntrospectionResponse,
    TechnicalIdentityListResponse,
    TechnicalIdentityResponse,
    TechnicalIdentityUpdateRequest,
)
from app.services.security import enforce_rate_limit, hash_session_token

router_admin = APIRouter(
    prefix='/api/v1/auth/admin/technical-identities',
    tags=['technical-identities-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)
router_internal = APIRouter(prefix='/api/v1/internal/technical-identities', tags=['technical-identities-internal'])

# Raw token shape: 'wti_' + 32 bytes of urlsafe randomness. Weave-Tools'
# own scope.py (_TECHNICAL_IDENTITY_PREFIX, see contracts/technical-
# identities.md) dispatches purely on this exact prefix to tell a
# technical-identity token apart from a Personal-Token (an opaque string
# from Weave-API) or a Delegations-Token (always contains exactly one
# '.') -- same "dispatch by shape, never by a client-declared type field"
# discipline as scope.py's own module docstring.
_TOKEN_PREFIX = 'wti_'
_TOKEN_PREFIX_LEN = 9

# Introspection touches last_used_at at most this often -- identical bound
# and reasoning to API_TOKEN_TOUCH_THRESHOLD in app/api/deps.py.
_LAST_USED_TOUCH_THRESHOLD = timedelta(seconds=60)


def _generate_token() -> str:
    return f'{_TOKEN_PREFIX}{secrets.token_urlsafe(32)}'


def _audit(db: Session, *, identity_id: str | None, event: str, actor: str | None, details: dict | None = None) -> None:
    db.add(TechnicalIdentityAudit(identity_id=identity_id, event=event, actor=actor, details=details or {}))


def _response(identity: TechnicalIdentity) -> TechnicalIdentityResponse:
    return TechnicalIdentityResponse(
        id=identity.id,
        name=identity.name,
        description=identity.description,
        allowed_collections=list(identity.allowed_collections or []),
        enabled=identity.enabled,
        token_prefix=identity.token_prefix,
        created_at=identity.created_at,
        updated_at=identity.updated_at,
        last_used_at=identity.last_used_at,
        expires_at=identity.expires_at,
        revoked_at=identity.revoked_at,
        created_by=identity.created_by,
    )


def _expires_at_from_days(days: int | None) -> datetime | None:
    return datetime.now(timezone.utc) + timedelta(days=days) if days is not None else None


def _validate_collections(collections: list[str], db: Session) -> None:
    """Same known-collection check as managed_bots.py's `_validate_references`:
    an unknown slug here would silently start granting access the moment a
    collection with that exact slug is later created, with no renewed admin
    review of this specific grant."""
    known = set(db.scalars(select(Collection.slug).where(Collection.slug.in_(collections))).all())
    unknown = sorted(set(collections) - known)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Unbekannte Wissensbereiche: {", ".join(unknown)}',
        )


def _get_or_404(db: Session, identity_id: str) -> TechnicalIdentity:
    identity = db.get(TechnicalIdentity, identity_id)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Technical identity not found')
    return identity


@router_admin.get('', response_model=TechnicalIdentityListResponse)
def list_technical_identities(request: Request, db: Session = Depends(get_db)) -> TechnicalIdentityListResponse:
    enforce_rate_limit(request)
    items = db.scalars(select(TechnicalIdentity).order_by(TechnicalIdentity.created_at.desc())).all()
    return TechnicalIdentityListResponse(items=[_response(item) for item in items])


@router_admin.post('', response_model=TechnicalIdentityCreateResponse, status_code=status.HTTP_201_CREATED)
def create_technical_identity(
    payload: TechnicalIdentityCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> TechnicalIdentityCreateResponse:
    enforce_rate_limit(request)
    _validate_collections(payload.allowed_collections, db)
    raw_token = _generate_token()
    identity = TechnicalIdentity(
        name=payload.name.strip(),
        description=(payload.description or '').strip() or None,
        allowed_collections=list(dict.fromkeys(payload.allowed_collections)),
        token_hash=hash_session_token(raw_token),
        token_prefix=raw_token[:_TOKEN_PREFIX_LEN],
        expires_at=_expires_at_from_days(payload.expires_in_days),
        created_by=user.id,
    )
    db.add(identity)
    db.flush()
    _audit(db, identity_id=identity.id, event='created', actor=user.username, details={'name': identity.name})
    db.commit()
    return TechnicalIdentityCreateResponse(**_response(identity).model_dump(), token=raw_token)


@router_admin.put('/{identity_id}', response_model=TechnicalIdentityResponse)
def update_technical_identity(
    identity_id: str,
    payload: TechnicalIdentityUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> TechnicalIdentityResponse:
    enforce_rate_limit(request)
    identity = _get_or_404(db, identity_id)

    changed: dict = {}
    if payload.name is not None and payload.name.strip() != identity.name:
        identity.name = payload.name.strip()
        changed['name'] = identity.name
    if payload.description is not None and payload.description.strip() != (identity.description or ''):
        identity.description = payload.description.strip() or None
        changed['description'] = True
    if payload.allowed_collections is not None:
        _validate_collections(payload.allowed_collections, db)
        new_collections = list(dict.fromkeys(payload.allowed_collections))
        if new_collections != list(identity.allowed_collections or []):
            identity.allowed_collections = new_collections
            changed['allowed_collections'] = new_collections
    if payload.enabled is not None and payload.enabled != identity.enabled:
        identity.enabled = payload.enabled
        changed['enabled'] = identity.enabled
    if payload.clear_expiry:
        identity.expires_at = None
        changed['expires_at'] = None
    elif payload.expires_in_days is not None:
        identity.expires_at = _expires_at_from_days(payload.expires_in_days)
        changed['expires_at'] = 'set'

    if changed:
        _audit(db, identity_id=identity.id, event='updated', actor=user.username, details=changed)
        db.commit()
    return _response(identity)


@router_admin.post('/{identity_id}/rotate', response_model=TechnicalIdentityCreateResponse)
def rotate_technical_identity(
    identity_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
) -> TechnicalIdentityCreateResponse:
    """Mint a new raw token for an existing identity, in place: same id and
    grants, new token_hash/token_prefix, last_used_at reset (the old token
    is gone, so its usage history no longer describes what's live)."""
    enforce_rate_limit(request)
    identity = _get_or_404(db, identity_id)

    raw_token = _generate_token()
    identity.token_hash = hash_session_token(raw_token)
    identity.token_prefix = raw_token[:_TOKEN_PREFIX_LEN]
    identity.last_used_at = None
    _audit(db, identity_id=identity.id, event='rotated', actor=user.username)
    db.commit()
    return TechnicalIdentityCreateResponse(**_response(identity).model_dump(), token=raw_token)


@router_admin.post('/{identity_id}/revoke', status_code=status.HTTP_204_NO_CONTENT)
def revoke_technical_identity(
    identity_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
) -> Response:
    """Sets revoked_at (irreversible -- see the model's own docstring).
    Idempotent: revoking an already-revoked identity is a no-op, not an
    error, since the caller's desired end state ("this identity must not
    work") is already true."""
    enforce_rate_limit(request)
    identity = _get_or_404(db, identity_id)
    if identity.revoked_at is None:
        identity.revoked_at = datetime.now(timezone.utc)
        _audit(db, identity_id=identity.id, event='revoked', actor=user.username)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_admin.get('/{identity_id}/audit', response_model=TechnicalIdentityAuditListResponse)
def list_technical_identity_audit(
    identity_id: str, request: Request, db: Session = Depends(get_db),
) -> TechnicalIdentityAuditListResponse:
    enforce_rate_limit(request)
    _get_or_404(db, identity_id)
    rows = db.scalars(
        select(TechnicalIdentityAudit)
        .where(TechnicalIdentityAudit.identity_id == identity_id)
        .order_by(TechnicalIdentityAudit.created_at.desc())
    ).all()
    return TechnicalIdentityAuditListResponse(items=[
        {'id': r.id, 'event': r.event, 'actor': r.actor, 'details': r.details, 'created_at': r.created_at}
        for r in rows
    ])


# --- internal: Weave-Tools' introspection call ------------------------------

def _require_tools_introspection_token(request: Request) -> None:
    """Mirrors chat_provider.py's `_require_runtime_token` exactly: a
    static, shared Bearer secret gating who may call this endpoint at all
    -- deliberately NOT the technical identity's own token (unlike the
    Weave-API-authority pattern this endpoint otherwise mirrors), because
    the caller here is always Weave-Tools itself, resolving SOMEONE ELSE's
    token on their behalf, never presenting its own."""
    expected = settings.tools_introspection_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='service misconfigured')
    authorization = request.headers.get('authorization', '')
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid service token')


@router_internal.post(
    '/introspect',
    response_model=TechnicalIdentityIntrospectionResponse,
    dependencies=[Depends(_require_tools_introspection_token)],
)
def introspect_technical_identity(
    payload: TechnicalIdentityIntrospectionRequest, db: Session = Depends(get_db),
) -> TechnicalIdentityIntrospectionResponse:
    """Non-enumerating, mirrors Weave-API's own POST /internal/tokens/
    introspect: unknown/disabled/revoked/expired token all collapse into
    the exact same `{"active": false}`, HTTP 200 -- see the response
    schema's own docstring. A denied lookup (found but inactive) is
    audited; an unknown token is not (there is no identity_id to attach
    the audit row to, and auditing "someone probed a token that never
    existed" would need its own, un-keyed audit shape this table doesn't
    have)."""
    token_hash = hash_session_token(payload.token)
    identity = db.scalar(select(TechnicalIdentity).where(TechnicalIdentity.token_hash == token_hash))
    if identity is None:
        return TechnicalIdentityIntrospectionResponse(active=False)

    now = datetime.now(timezone.utc)
    expired = identity.expires_at is not None and aware_utc(identity.expires_at) <= now
    inactive = identity.revoked_at is not None or not identity.enabled or expired
    if inactive:
        _audit(db, identity_id=identity.id, event='introspected_denied', actor=None, details={
            'revoked': identity.revoked_at is not None, 'disabled': not identity.enabled, 'expired': expired,
        })
        db.commit()
        return TechnicalIdentityIntrospectionResponse(active=False)

    if identity.last_used_at is None or now - aware_utc(identity.last_used_at) > _LAST_USED_TOUCH_THRESHOLD:
        identity.last_used_at = now
        db.commit()

    return TechnicalIdentityIntrospectionResponse(
        active=True,
        identity_id=identity.id,
        name=identity.name,
        allowed_collections=list(identity.allowed_collections or []),
        expires_at=identity.expires_at,
    )
