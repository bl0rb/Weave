"""Admin control plane and Runtime-only projection for chat generation."""

import hmac
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.models import ChatProviderConfig, ManagedBot, User
from app.schemas.chat_provider import (
    ChatEndpointCatalogResponse,
    ChatEndpointSummary,
    ChatProviderAdminResponse,
    ChatProviderInternalResponse,
    ChatProviderListResponse,
    ChatProviderTestResponse,
    ChatProviderUpdateRequest,
)
from app.services.chat_provider import normalize_base_url, test_connection
from app.services.security import (
    decrypt_chat_provider_api_key,
    encrypt_chat_provider_api_key,
    enforce_rate_limit,
)

router_admin = APIRouter(
    prefix='/api/v1/auth/admin/chat-provider',
    tags=['chat-provider-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)
router_internal = APIRouter(prefix='/api/v1/internal/chat-provider', tags=['chat-provider-internal'])


DEFAULT_ENDPOINT_ID = 'default'
_ENDPOINT_ID_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


def _endpoint_id(raw: str) -> str:
    """Path-supplied endpoint id: a slug, like bot ids, so it is safe in a
    Runtime query parameter and a bot's YAML."""
    if len(raw) > 36 or not _ENDPOINT_ID_RE.fullmatch(raw):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Ungültige Endpoint-ID')
    return raw


def _admin_response(row: ChatProviderConfig | None) -> ChatProviderAdminResponse:
    if row is None:
        return ChatProviderAdminResponse(
            configured=False, enabled=False, base_url='', model='', has_api_key=False,
            timeout_seconds=60.0, temperature=None, supports_tools=False, updated_at=None,
        )
    return ChatProviderAdminResponse(
        id=row.id,
        name=row.name,
        configured=True,
        enabled=row.enabled,
        base_url=row.base_url,
        model=row.model,
        has_api_key=bool(row.api_key_encrypted),
        timeout_seconds=row.timeout_seconds,
        temperature=row.temperature,
        supports_tools=row.supports_tools,
        updated_at=row.updated_at,
    )


def _require_runtime_token(request: Request) -> None:
    expected = settings.chat_config_service_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='service misconfigured')
    authorization = request.headers.get('authorization', '')
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid service token')


def _upsert(db: Session, endpoint_id: str, payload: ChatProviderUpdateRequest, admin: User) -> ChatProviderConfig:
    row = db.get(ChatProviderConfig, endpoint_id)
    if row is None:
        row = ChatProviderConfig(id=endpoint_id)
        db.add(row)

    next_base_url = normalize_base_url(payload.base_url) if payload.base_url.strip() else ''
    base_changed = bool(row.base_url and next_base_url != row.base_url)
    row.name = payload.name.strip()
    row.enabled = payload.enabled
    row.base_url = next_base_url
    row.model = payload.model.strip()
    row.timeout_seconds = payload.timeout_seconds
    row.temperature = payload.temperature
    row.supports_tools = payload.supports_tools
    row.updated_by_id = admin.id

    # A credential is scoped to the endpoint it was entered for. Moving to a
    # different host/path without entering a key clears the old ciphertext,
    # preventing an accidental credential forward to the new endpoint.
    if payload.api_key is not None and payload.api_key.strip():
        row.api_key_encrypted = encrypt_chat_provider_api_key(payload.api_key.strip())
    elif payload.clear_api_key or base_changed:
        row.api_key_encrypted = None

    db.commit()
    db.refresh(row)
    return row


def _test(row: ChatProviderConfig | None) -> ChatProviderTestResponse:
    if row is None or not row.base_url or not row.model:
        return ChatProviderTestResponse(ok=False, detail='Endpoint und Modell zuerst speichern')
    try:
        api_key = decrypt_chat_provider_api_key(row.api_key_encrypted) if row.api_key_encrypted else ''
    except ValueError:
        return ChatProviderTestResponse(ok=False, detail='Gespeicherter API-Key kann nicht entschlüsselt werden')
    return ChatProviderTestResponse(**test_connection(
        base_url=row.base_url, model=row.model, api_key=api_key, timeout_seconds=row.timeout_seconds,
    ))


@router_admin.get('', response_model=ChatProviderAdminResponse)
def get_chat_provider(request: Request, db: Session = Depends(get_db)) -> ChatProviderAdminResponse:
    enforce_rate_limit(request)
    return _admin_response(db.get(ChatProviderConfig, DEFAULT_ENDPOINT_ID))


@router_admin.put('', response_model=ChatProviderAdminResponse)
def update_chat_provider(
    payload: ChatProviderUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ChatProviderAdminResponse:
    enforce_rate_limit(request)
    return _admin_response(_upsert(db, DEFAULT_ENDPOINT_ID, payload, admin))


@router_admin.post('/test', response_model=ChatProviderTestResponse)
def test_chat_provider(request: Request, db: Session = Depends(get_db)) -> ChatProviderTestResponse:
    enforce_rate_limit(request)
    return _test(db.get(ChatProviderConfig, DEFAULT_ENDPOINT_ID))


@router_admin.get('/endpoints', response_model=ChatProviderListResponse)
def list_chat_endpoints(request: Request, db: Session = Depends(get_db)) -> ChatProviderListResponse:
    """Every configured endpoint, the central 'default' first."""
    enforce_rate_limit(request)
    rows = db.scalars(select(ChatProviderConfig)).all()
    rows = sorted(rows, key=lambda row: (row.id != DEFAULT_ENDPOINT_ID, (row.name or row.id).lower()))
    return ChatProviderListResponse(items=[_admin_response(row) for row in rows])


@router_admin.put('/endpoints/{endpoint_id}', response_model=ChatProviderAdminResponse)
def upsert_chat_endpoint(
    endpoint_id: str,
    payload: ChatProviderUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ChatProviderAdminResponse:
    enforce_rate_limit(request)
    return _admin_response(_upsert(db, _endpoint_id(endpoint_id), payload, admin))


@router_admin.delete('/endpoints/{endpoint_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_chat_endpoint(endpoint_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    enforce_rate_limit(request)
    endpoint_id = _endpoint_id(endpoint_id)
    if endpoint_id == DEFAULT_ENDPOINT_ID:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Der zentrale Endpoint kann nur deaktiviert werden')
    in_use = db.scalars(select(ManagedBot.id).where(ManagedBot.llm_endpoint == endpoint_id)).first()
    if in_use is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'Endpoint wird von Bot {in_use!r} verwendet')
    row = db.get(ChatProviderConfig, endpoint_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Endpoint nicht gefunden')
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_admin.post('/endpoints/{endpoint_id}/test', response_model=ChatProviderTestResponse)
def test_chat_endpoint(endpoint_id: str, request: Request, db: Session = Depends(get_db)) -> ChatProviderTestResponse:
    enforce_rate_limit(request)
    return _test(db.get(ChatProviderConfig, _endpoint_id(endpoint_id)))


@router_internal.get(
    '/endpoints', response_model=ChatEndpointCatalogResponse, dependencies=[Depends(_require_runtime_token)]
)
def internal_chat_endpoints(response: Response, db: Session = Depends(get_db)) -> ChatEndpointCatalogResponse:
    """Enabled endpoints without credentials -- what Runtime offers chat
    users to choose from."""
    response.headers['Cache-Control'] = 'no-store'
    rows = db.scalars(select(ChatProviderConfig).where(ChatProviderConfig.enabled.is_(True))).all()
    rows = sorted(rows, key=lambda row: (row.id != DEFAULT_ENDPOINT_ID, (row.name or row.id).lower()))
    return ChatEndpointCatalogResponse(items=[
        ChatEndpointSummary(id=row.id, name=row.name or row.model, model=row.model, supports_tools=row.supports_tools)
        for row in rows if row.base_url and row.model
    ])


@router_internal.get('', response_model=ChatProviderInternalResponse, dependencies=[Depends(_require_runtime_token)])
def internal_chat_provider(
    response: Response, endpoint: str = DEFAULT_ENDPOINT_ID, db: Session = Depends(get_db)
) -> ChatProviderInternalResponse:
    response.headers['Cache-Control'] = 'no-store'
    row = db.get(ChatProviderConfig, endpoint)
    if row is None:
        return ChatProviderInternalResponse(configured=False, enabled=False)
    try:
        api_key = decrypt_chat_provider_api_key(row.api_key_encrypted) if row.api_key_encrypted else ''
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='stored credential unavailable') from exc
    return ChatProviderInternalResponse(
        configured=True,
        enabled=row.enabled,
        base_url=row.base_url,
        model=row.model,
        api_key=api_key,
        timeout_seconds=row.timeout_seconds,
        temperature=row.temperature,
        supports_tools=row.supports_tools,
        updated_at=row.updated_at,
    )
