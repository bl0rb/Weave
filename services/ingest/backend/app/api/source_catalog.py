"""Shared source catalog (Wissensbereiche › Quellenkatalog): curated,
reusable website entries every signed-in user can browse and import from.

Entries are templates (start URL + recommended scope), not content: listing
one grants no read rights anywhere -- an import still needs a knowledge
space the caller controls (checked by POST /import/runs), and the catalog
never imports a domain by itself. Managing entries is admin-only.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.database.session import get_db
from app.models.models import SourceCatalogEntry, User, UserRole
from app.schemas.source_catalog import (
    SourceCatalogEntryCreateRequest,
    SourceCatalogEntryResponse,
    SourceCatalogEntryUpdateRequest,
    SourceCatalogListResponse,
)
from app.services import website
from app.services.security import enforce_rate_limit

router = APIRouter(prefix='/api/v1/source-catalog', tags=['source-catalog'])


def _validated_start_url(raw: str) -> str:
    start_url = website.normalize_page_url(raw)
    if start_url is None or len(start_url) > website.MAX_START_URL_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='start_url must be an http(s) page address of at most 512 characters',
        )
    return start_url


def _to_response(entry: SourceCatalogEntry) -> SourceCatalogEntryResponse:
    response = SourceCatalogEntryResponse.model_validate(entry)
    response.host = website.host_of(entry.start_url)
    return response


def _get_entry(db: Session, entry_id: str) -> SourceCatalogEntry:
    entry = db.get(SourceCatalogEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Source catalog entry not found')
    return entry


@router.get('', response_model=SourceCatalogListResponse)
def list_catalog_entries(
    q: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SourceCatalogListResponse:
    query = select(SourceCatalogEntry).order_by(SourceCatalogEntry.category, SourceCatalogEntry.title)
    if category:
        query = query.where(SourceCatalogEntry.category == category)
    if q and q.strip():
        needle = f'%{q.strip().lower()}%'
        query = query.where(or_(
            SourceCatalogEntry.title.ilike(needle),
            SourceCatalogEntry.description.ilike(needle),
            SourceCatalogEntry.publisher.ilike(needle),
            SourceCatalogEntry.start_url.ilike(needle),
        ))
    entries = db.scalars(query).all()
    return SourceCatalogListResponse(items=[_to_response(entry) for entry in entries], can_manage=user.role == UserRole.ADMIN)


@router.get('/{entry_id}', response_model=SourceCatalogEntryResponse)
def get_catalog_entry(entry_id: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)) -> SourceCatalogEntryResponse:
    return _to_response(_get_entry(db, entry_id))


@router.post('', response_model=SourceCatalogEntryResponse, status_code=status.HTTP_201_CREATED)
def create_catalog_entry(
    payload: SourceCatalogEntryCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> SourceCatalogEntryResponse:
    enforce_rate_limit(request)
    entry = SourceCatalogEntry(
        title=payload.title.strip(),
        description=payload.description.strip(),
        category=payload.category,
        publisher=payload.publisher.strip(),
        kind='website',
        start_url=_validated_start_url(payload.start_url),
        crawl_scope=payload.crawl_scope,
        include_linked_documents=payload.include_linked_documents,
        max_pages=payload.max_pages,
        interval_seconds=payload.interval_seconds,
        legal_source=payload.legal_source,
        created_by=user.id,
    )
    db.add(entry)
    db.commit()
    return _to_response(entry)


@router.patch('/{entry_id}', response_model=SourceCatalogEntryResponse)
def update_catalog_entry(
    entry_id: str,
    payload: SourceCatalogEntryUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> SourceCatalogEntryResponse:
    enforce_rate_limit(request)
    entry = _get_entry(db, entry_id)
    changes = payload.model_dump(exclude_unset=True)
    for key in ('title', 'description', 'category', 'publisher', 'start_url', 'crawl_scope', 'include_linked_documents', 'legal_source'):
        if key in changes and changes[key] is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f'{key} cannot be null')
    if 'start_url' in changes:
        changes['start_url'] = _validated_start_url(changes['start_url'])
    for key in ('title', 'description', 'publisher'):
        if key in changes and changes[key] is not None:
            changes[key] = changes[key].strip()
    if not changes.get('title', entry.title):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='title cannot be empty')
    for key, value in changes.items():
        setattr(entry, key, value)
    db.commit()
    return _to_response(entry)


@router.delete('/{entry_id}')
def delete_catalog_entry(
    entry_id: str, request: Request, db: Session = Depends(get_db), _user: User = Depends(require_admin),
) -> dict[str, str]:
    """Removes the template only: imports created from it keep running
    (ImportSync.options.catalog_entry_id simply no longer resolves)."""
    enforce_rate_limit(request)
    db.delete(_get_entry(db, entry_id))
    db.commit()
    return {'status': 'deleted'}
