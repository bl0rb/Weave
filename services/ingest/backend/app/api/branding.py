"""Administration › Betrieb › Erscheinungsbild: the instance's colours (as
hex codes), logo and busy animation, applied by the portal and -- through
Weave-API -- the chat, including their login pages.

The read side is public on purpose: the login pages need it before any
session exists, and it carries nothing but colours and images an admin
chose to show. Writes follow the knowledge-memory pattern (admin + CSRF
origin check at the router, rate limit per handler). Validation, palette
and cache live in app/services/branding.py.
"""

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.database.session import get_db
from app.models.models import BrandingAsset, BrandingConfig, User
from app.schemas.branding import BrandingAdmin, BrandingPublic, BrandingUpdateRequest
from app.services.branding import (
    ASSET_EXTENSIONS,
    ASSET_KINDS,
    COLOR_ERROR,
    MAX_ASSET_BYTES,
    admin_branding,
    invalidate_cache,
    normalize_color,
    public_branding,
    validate_asset,
)
from app.services.security import enforce_rate_limit

router_public = APIRouter(prefix='/api/v1/branding')
router_admin = APIRouter(prefix='/api/v1/auth/admin/branding', dependencies=[Depends(require_admin), Depends(origin_guard)])

# Even opened directly, an uploaded SVG can neither run script nor load
# anything; embedded raster images (data:) still render.
ASSET_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"

_ASSET_RESPONSES = {
    200: {'content': {content_type: {} for content_type in ASSET_EXTENSIONS}, 'description': 'The stored image.'},
    304: {'description': 'Unchanged (If-None-Match).'},
    404: {'description': 'Unknown kind or no image stored.'},
}


def _kind(kind: str) -> str:
    if kind not in ASSET_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unbekannte Bildart.')
    return kind


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    if if_none_match.strip() == '*':
        return True
    return any(tag.strip().removeprefix('W/') == etag for tag in if_none_match.split(','))


@router_public.get('', response_model=BrandingPublic)
def get_branding(request: Request, response: Response, db: Session = Depends(get_db)) -> BrandingPublic:
    enforce_rate_limit(request)
    response.headers['Cache-Control'] = 'no-cache'
    return public_branding(db)


@router_public.get('/assets/{kind}', response_class=Response, responses=_ASSET_RESPONSES)
def get_branding_asset(kind: str, request: Request, db: Session = Depends(get_db)) -> Response:
    enforce_rate_limit(request)
    kind = _kind(kind)
    meta = db.execute(
        select(BrandingAsset.content_type, BrandingAsset.sha256).where(BrandingAsset.kind == kind)
    ).first()
    if meta is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Kein Bild hinterlegt.')
    if _etag_matches(request.headers.get('if-none-match'), f'"{meta.sha256}"'):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=_asset_headers(kind, meta.content_type, meta.sha256))
    # Bytes and metadata in one read, so a concurrent replacement cannot
    # pair new bytes with the old type or ETag.
    row = db.execute(
        select(BrandingAsset.data, BrandingAsset.content_type, BrandingAsset.sha256).where(BrandingAsset.kind == kind)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Kein Bild hinterlegt.')
    return Response(content=row.data, media_type=row.content_type, headers=_asset_headers(kind, row.content_type, row.sha256))


def _asset_headers(kind: str, content_type: str, sha256: str) -> dict[str, str]:
    return {
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': ASSET_CSP,
        'Content-Disposition': f'inline; filename="branding-{kind}.{ASSET_EXTENSIONS.get(content_type, "bin")}"',
        'Cache-Control': 'public, max-age=300',
        'ETag': f'"{sha256}"',
    }


@router_admin.get('', response_model=BrandingAdmin)
def get_branding_admin(request: Request, db: Session = Depends(get_db)) -> BrandingAdmin:
    enforce_rate_limit(request)
    return admin_branding(db)


@router_admin.put('', response_model=BrandingAdmin)
def update_branding(
    payload: BrandingUpdateRequest, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
) -> BrandingAdmin:
    enforce_rate_limit(request)
    # Only the keys the admin sent; an absent key keeps its stored value.
    values = {}
    for field in payload.model_fields_set:
        try:
            values[field] = normalize_color(getattr(payload, field))
        except ValueError:
            raise HTTPException(status_code=422, detail=COLOR_ERROR) from None
    row = db.get(BrandingConfig, 'default')
    if row is None:
        row = BrandingConfig(id='default')
        db.add(row)
    for field, value in values.items():
        setattr(row, field, value)
    row.updated_by_id = admin.id
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_cache()
    return admin_branding(db)


@router_admin.put('/assets/{kind}', response_model=BrandingAdmin)
def upload_branding_asset(
    kind: str, request: Request, file: UploadFile = File(...),
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
) -> BrandingAdmin:
    enforce_rate_limit(request)
    kind = _kind(kind)
    # One byte more than allowed tells "too large" apart without reading on.
    data = file.file.read(MAX_ASSET_BYTES + 1)
    content_type = validate_asset(data)
    row = db.get(BrandingAsset, kind)
    if row is None:
        row = BrandingAsset(kind=kind)
        db.add(row)
    row.content_type = content_type
    row.data = data
    row.sha256 = hashlib.sha256(data).hexdigest()
    row.size_bytes = len(data)
    row.filename = (file.filename or '')[:255] or None
    row.updated_by_id = admin.id
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_cache()
    return admin_branding(db)


@router_admin.delete('/assets/{kind}', response_model=BrandingAdmin)
def delete_branding_asset(kind: str, request: Request, db: Session = Depends(get_db)) -> BrandingAdmin:
    enforce_rate_limit(request)
    kind = _kind(kind)
    row = db.get(BrandingAsset, kind)
    if row is not None:
        db.delete(row)
        db.commit()
        invalidate_cache()
    return admin_branding(db)
