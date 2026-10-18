"""Administration › KI › Wissensgedächtnis (ADR-0009): the knowledge-memory
settings (mode, LLM endpoint, rebuild limits) and its metrics.

Same split as app/api/retrieval_provider.py: an admin resource, an internal
projection for Weave-Knowledge (Bearer CHAT_CONFIG_SERVICE_TOKEN), and
maintenance actions proxied to Knowledge with the shared webhook secret, so
the browser never talks to Knowledge directly. Runtime receives the mode
with every chat-provider snapshot (app/api/chat_provider.py).
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.api.retrieval_provider import _require_internal
from app.core.config import settings
from app.database.session import get_db
from app.models.models import ChatProviderConfig, KnowledgeMemoryConfig, User
from app.schemas.knowledge_memory import (
    KnowledgeMemoryAdminResponse,
    KnowledgeMemoryInternalResponse,
    KnowledgeMemoryRebuildRequest,
    KnowledgeMemoryRebuildResponse,
    KnowledgeMemoryUpdateRequest,
)
from app.services.security import enforce_rate_limit

router_admin = APIRouter(prefix='/api/v1/auth/admin/knowledge-memory', dependencies=[Depends(require_admin), Depends(origin_guard)])
router_internal = APIRouter(prefix='/api/v1/internal/knowledge-memory')
router_maintenance = APIRouter(prefix='/api/v1/admin/knowledge-memory', dependencies=[Depends(require_admin), Depends(origin_guard)])

_FIELDS = ('mode', 'llm_endpoint', 'debounce_seconds', 'max_documents_per_run', 'max_input_chars')


def _row(db: Session) -> KnowledgeMemoryConfig:
    row = db.get(KnowledgeMemoryConfig, 'default')
    if row is None:
        row = KnowledgeMemoryConfig(id='default')
        db.add(row)
        db.flush()
    return row


def _values(row: KnowledgeMemoryConfig) -> dict:
    return {field: getattr(row, field) for field in _FIELDS}


def _defaults() -> dict:
    """The environment-era values shown while nothing is saved here."""
    return {field: KnowledgeMemoryConfig.__table__.c[field].default.arg for field in _FIELDS}


def _knowledge(method: str, path: str, error_detail: str, **kwargs) -> dict:
    if not settings.portal_knowledge_base_url:
        raise HTTPException(status_code=503, detail=error_detail)
    try:
        result = httpx.request(
            method, f'{settings.portal_knowledge_base_url.rstrip("/")}/api/v1/internal/memory/{path}',
            headers={'X-Weave-Reindex-Token': settings.portal_knowledge_webhook_secret}, timeout=5, **kwargs,
        )
        result.raise_for_status()
        return result.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=error_detail) from exc


@router_admin.get('', response_model=KnowledgeMemoryAdminResponse)
def get_knowledge_memory(request: Request, db: Session = Depends(get_db)) -> KnowledgeMemoryAdminResponse:
    enforce_rate_limit(request)
    # The row exists only once an admin saved: `managed` is its existence,
    # not `updated_by_id`, which goes NULL when that admin's account is
    # deleted and must not switch the feature back to the environment.
    row = db.get(KnowledgeMemoryConfig, 'default')
    if row is None:
        return KnowledgeMemoryAdminResponse(**_defaults(), managed=False)
    return KnowledgeMemoryAdminResponse(**_values(row), managed=True, updated_at=row.updated_at)


@router_admin.put('', response_model=KnowledgeMemoryAdminResponse)
def update_knowledge_memory(
    payload: KnowledgeMemoryUpdateRequest, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
) -> KnowledgeMemoryAdminResponse:
    enforce_rate_limit(request)
    if payload.llm_endpoint != 'default' and db.get(ChatProviderConfig, payload.llm_endpoint) is None:
        raise HTTPException(status_code=422, detail='Unbekannter LLM-Endpunkt.')
    row = _row(db)
    for field in _FIELDS:
        setattr(row, field, getattr(payload, field))
    row.updated_by_id = admin.id
    db.commit()
    db.refresh(row)
    return KnowledgeMemoryAdminResponse(**_values(row), managed=True, updated_at=row.updated_at)


@router_maintenance.get('/status')
def knowledge_memory_status(request: Request) -> dict:
    """Knowledge's memory metrics (coverage, pending work, model usage),
    proxied for the admin UI."""
    enforce_rate_limit(request)
    return _knowledge('GET', 'status', 'Die Kennzahlen des Wissensgedächtnisses konnten nicht abgerufen werden.')


@router_maintenance.post('/rebuild', response_model=KnowledgeMemoryRebuildResponse)
def knowledge_memory_rebuild(payload: KnowledgeMemoryRebuildRequest, request: Request) -> KnowledgeMemoryRebuildResponse:
    enforce_rate_limit(request)
    data = _knowledge(
        'POST', 'rebuild', 'Der Neuaufbau konnte nicht gestartet werden. Der Wissensdienst ist nicht erreichbar.',
        json={'collection': payload.collection},
    )
    return KnowledgeMemoryRebuildResponse(queued=int(data.get('queued', 0)))


@router_internal.get('', response_model=KnowledgeMemoryInternalResponse, dependencies=[Depends(_require_internal)])
def internal_knowledge_memory(response: Response, db: Session = Depends(get_db)) -> KnowledgeMemoryInternalResponse:
    response.headers['Cache-Control'] = 'no-store'
    row = db.get(KnowledgeMemoryConfig, 'default')
    if row is None:
        return KnowledgeMemoryInternalResponse(managed=False)
    return KnowledgeMemoryInternalResponse(**_values(row), managed=True)
