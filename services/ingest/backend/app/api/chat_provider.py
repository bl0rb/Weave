"""Admin control plane and Runtime-only projection for chat generation."""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.models import ChatProviderConfig, User
from app.schemas.chat_provider import (
    ChatProviderAdminResponse,
    ChatProviderInternalResponse,
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


def _admin_response(row: ChatProviderConfig | None) -> ChatProviderAdminResponse:
    if row is None:
        return ChatProviderAdminResponse(
            configured=False, enabled=False, base_url='', model='', has_api_key=False,
            timeout_seconds=60.0, temperature=None, updated_at=None,
        )
    return ChatProviderAdminResponse(
        configured=True,
        enabled=row.enabled,
        base_url=row.base_url,
        model=row.model,
        has_api_key=bool(row.api_key_encrypted),
        timeout_seconds=row.timeout_seconds,
        temperature=row.temperature,
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


@router_admin.get('', response_model=ChatProviderAdminResponse)
def get_chat_provider(request: Request, db: Session = Depends(get_db)) -> ChatProviderAdminResponse:
    enforce_rate_limit(request)
    return _admin_response(db.get(ChatProviderConfig, 'default'))


@router_admin.put('', response_model=ChatProviderAdminResponse)
def update_chat_provider(
    payload: ChatProviderUpdateRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ChatProviderAdminResponse:
    enforce_rate_limit(request)
    row = db.get(ChatProviderConfig, 'default')
    if row is None:
        row = ChatProviderConfig(id='default')
        db.add(row)

    next_base_url = normalize_base_url(payload.base_url) if payload.base_url.strip() else ''
    base_changed = bool(row.base_url and next_base_url != row.base_url)
    row.enabled = payload.enabled
    row.base_url = next_base_url
    row.model = payload.model.strip()
    row.timeout_seconds = payload.timeout_seconds
    row.temperature = payload.temperature
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
    return _admin_response(row)


@router_admin.post('/test', response_model=ChatProviderTestResponse)
def test_chat_provider(request: Request, db: Session = Depends(get_db)) -> ChatProviderTestResponse:
    enforce_rate_limit(request)
    row = db.get(ChatProviderConfig, 'default')
    if row is None or not row.base_url or not row.model:
        return ChatProviderTestResponse(ok=False, detail='Endpoint und Modell zuerst speichern')
    try:
        api_key = decrypt_chat_provider_api_key(row.api_key_encrypted) if row.api_key_encrypted else ''
    except ValueError:
        return ChatProviderTestResponse(ok=False, detail='Gespeicherter API-Key kann nicht entschlüsselt werden')
    return ChatProviderTestResponse(**test_connection(
        base_url=row.base_url, model=row.model, api_key=api_key, timeout_seconds=row.timeout_seconds,
    ))


@router_internal.get('', response_model=ChatProviderInternalResponse, dependencies=[Depends(_require_runtime_token)])
def internal_chat_provider(response: Response, db: Session = Depends(get_db)) -> ChatProviderInternalResponse:
    response.headers['Cache-Control'] = 'no-store'
    row = db.get(ChatProviderConfig, 'default')
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
        updated_at=row.updated_at,
    )
