"""Admin CRUD and Runtime-only projection for n8n-backed bots."""

import hmac
import httpx
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.models import Collection, ManagedBot, Team, User
from app.schemas.managed_bots import (
    ManagedBotAdminResponse,
    ManagedBotCreate,
    ManagedBotInternalListResponse,
    ManagedBotInternalResponse,
    ManagedBotListResponse,
    ManagedBotUpdate,
)
from app.services.security import (
    decrypt_managed_bot_auth_token,
    encrypt_managed_bot_auth_token,
    enforce_rate_limit,
)


router_admin = APIRouter(
    prefix='/api/v1/auth/admin/bots',
    tags=['managed-bots-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)
router_internal = APIRouter(prefix='/api/v1/internal/bots', tags=['managed-bots-internal'])


def _admin_response(row: ManagedBot) -> ManagedBotAdminResponse:
    return ManagedBotAdminResponse(
        id=row.id,
        kind=row.kind,
        name=row.name,
        description=row.description,
        enabled=row.enabled,
        webhook_url=row.webhook_url,
        system_prompt=row.system_prompt,
        temperature=row.temperature,
        retrieval_enabled=row.retrieval_enabled,
        retrieval_filters=dict(row.retrieval_filters or {}),
        top_k=row.top_k,
        final_k=row.final_k,
        rerank=row.rerank,
        include_uncollected=row.include_uncollected,
        streaming=row.streaming,
        has_auth_token=bool(row.auth_token_encrypted),
        timeout_seconds=row.timeout_seconds,
        teams=list(row.teams or []),
        collections=list(row.collections or []),
        require_sources=row.require_sources,
        no_context_reply=row.no_context_reply,
        created_at=row.created_at,
        updated_at=row.updated_at,
        source='managed',
        editable=True,
    )


def _runtime_bots() -> list[ManagedBotAdminResponse]:
    if not settings.runtime_api_token:
        return []
    try:
        response = httpx.get(
            f'{settings.runtime_bots_base_url.rstrip("/")}/internal/bots',
            headers={'Authorization': f'Bearer {settings.runtime_api_token}'},
            timeout=5,
        )
        response.raise_for_status()
        items = response.json()
    except (httpx.HTTPError, ValueError):
        # Managed n8n bots remain administrable when Runtime is temporarily
        # unavailable; the next refresh exposes local YAML bots again.
        return []
    return [ManagedBotAdminResponse(
        id=item['id'], name=item['name'], description=item.get('description'), enabled=True,
        webhook_url='', streaming=False, has_auth_token=False, timeout_seconds=0,
        teams=list(item.get('teams') or []), collections=list(item.get('collections') or []),
        require_sources=bool(item.get('retrieval', {}).get('enabled')), no_context_reply='',
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        source='runtime', editable=False,
    ) for item in items]


def _require_runtime_token(request: Request) -> None:
    expected = settings.chat_config_service_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='service misconfigured')
    authorization = request.headers.get('authorization', '')
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid service token')


def _validate_references(payload: ManagedBotCreate | ManagedBotUpdate, db: Session) -> None:
    known_teams = set(db.scalars(select(Team.name).where(Team.name.in_(payload.teams))).all())
    unknown_teams = sorted(set(payload.teams) - known_teams)
    if unknown_teams:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Unbekannte Teams: {", ".join(unknown_teams)}',
        )

    known_collections = set(
        db.scalars(select(Collection.slug).where(Collection.slug.in_(payload.collections))).all()
    )
    unknown_collections = sorted(set(payload.collections) - known_collections)
    if unknown_collections:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Unbekannte Wissensbereiche: {", ".join(unknown_collections)}',
        )


def _apply(row: ManagedBot, payload: ManagedBotCreate | ManagedBotUpdate, admin: User) -> None:
    next_url = payload.webhook_url
    url_changed = bool(row.webhook_url and row.webhook_url != next_url)
    row.name = payload.name
    row.kind = payload.kind
    row.description = payload.description
    row.enabled = payload.enabled
    row.webhook_url = next_url
    row.system_prompt = payload.system_prompt
    row.temperature = payload.temperature
    row.retrieval_enabled = payload.retrieval_enabled
    row.retrieval_filters = dict(payload.retrieval_filters)
    row.top_k = payload.top_k
    row.final_k = payload.final_k
    row.rerank = payload.rerank
    row.include_uncollected = payload.include_uncollected
    row.streaming = payload.streaming
    row.timeout_seconds = payload.timeout_seconds
    row.teams = list(payload.teams)
    row.collections = list(payload.collections)
    row.require_sources = payload.require_sources
    row.no_context_reply = payload.no_context_reply
    row.updated_by_id = admin.id
    if payload.auth_token:
        row.auth_token_encrypted = encrypt_managed_bot_auth_token(payload.auth_token.strip())
    elif payload.clear_auth_token or url_changed:
        row.auth_token_encrypted = None


@router_admin.get('', response_model=ManagedBotListResponse)
def list_managed_bots(request: Request, db: Session = Depends(get_db)) -> ManagedBotListResponse:
    enforce_rate_limit(request)
    rows = db.scalars(select(ManagedBot).order_by(ManagedBot.name, ManagedBot.id)).all()
    managed = [_admin_response(row) for row in rows]
    managed_ids = {item.id for item in managed}
    runtime = [item for item in _runtime_bots() if item.id not in managed_ids]
    return ManagedBotListResponse(items=sorted(managed + runtime, key=lambda item: (item.name, item.id)))


@router_admin.post('', response_model=ManagedBotAdminResponse, status_code=status.HTTP_201_CREATED)
def create_managed_bot(
    payload: ManagedBotCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ManagedBotAdminResponse:
    enforce_rate_limit(request)
    if db.get(ManagedBot, payload.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Diese Bot-ID wird bereits verwendet.')
    _validate_references(payload, db)
    row = ManagedBot(id=payload.id, name=payload.name, webhook_url=payload.webhook_url)
    _apply(row, payload, admin)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _admin_response(row)


@router_admin.put('/{bot_id}', response_model=ManagedBotAdminResponse)
def update_managed_bot(
    bot_id: str,
    payload: ManagedBotUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ManagedBotAdminResponse:
    enforce_rate_limit(request)
    row = db.get(ManagedBot, bot_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Bot nicht gefunden.')
    _validate_references(payload, db)
    _apply(row, payload, admin)
    db.commit()
    db.refresh(row)
    return _admin_response(row)


@router_admin.delete('/{bot_id}')
def delete_managed_bot(
    bot_id: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, str]:
    enforce_rate_limit(request)
    row = db.get(ManagedBot, bot_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Bot nicht gefunden.')
    db.delete(row)
    db.commit()
    return {'status': 'deleted'}


@router_internal.get('', response_model=ManagedBotInternalListResponse, dependencies=[Depends(_require_runtime_token)])
def internal_managed_bots(response: Response, db: Session = Depends(get_db)) -> ManagedBotInternalListResponse:
    response.headers['Cache-Control'] = 'no-store'
    rows = db.scalars(select(ManagedBot).where(ManagedBot.enabled.is_(True)).order_by(ManagedBot.id)).all()
    items: list[ManagedBotInternalResponse] = []
    for row in rows:
        try:
            auth_token = decrypt_managed_bot_auth_token(row.auth_token_encrypted) if row.auth_token_encrypted else ''
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f'credential unavailable for bot {row.id}',
            ) from exc
        items.append(ManagedBotInternalResponse(
            id=row.id,
            kind=row.kind,
            name=row.name,
            description=row.description,
            webhook_url=row.webhook_url,
            system_prompt=row.system_prompt,
            temperature=row.temperature,
            retrieval_enabled=row.retrieval_enabled,
            retrieval_filters=dict(row.retrieval_filters or {}),
            top_k=row.top_k,
            final_k=row.final_k,
            rerank=row.rerank,
            include_uncollected=row.include_uncollected,
            streaming=row.streaming,
            auth_token=auth_token,
            timeout_seconds=row.timeout_seconds,
            teams=list(row.teams or []),
            collections=list(row.collections or []),
            require_sources=row.require_sources,
            no_context_reply=row.no_context_reply,
        ))
    return ManagedBotInternalListResponse(items=items)
