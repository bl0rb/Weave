import hmac
import httpx

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.models import RetrievalProviderConfig, User
from app.schemas.retrieval_provider import (
    RetrievalProviderAdminResponse,
    RetrievalProviderInternalResponse,
    RetrievalProviderUpdateRequest,
)
from app.services.security import (
    decrypt_retrieval_provider_api_key,
    encrypt_retrieval_provider_api_key,
    enforce_rate_limit,
)

router_admin = APIRouter(prefix='/api/v1/auth/admin/retrieval-provider', dependencies=[Depends(require_admin), Depends(origin_guard)])
router_internal = APIRouter(prefix='/api/v1/internal/retrieval-provider')


def _row(db: Session) -> RetrievalProviderConfig:
    row = db.get(RetrievalProviderConfig, 'default')
    if row is None:
        row = RetrievalProviderConfig(id='default')
        db.add(row)
        db.flush()
    return row


def _effective(row: RetrievalProviderConfig) -> dict[str, object]:
    override = row.updated_by_id is not None
    values = {
        'embedding_provider': row.embedding_provider if override else settings.embedding_provider,
        'embedding_base_url': row.embedding_base_url if override else settings.embedding_base_url,
        'embedding_model': row.embedding_model if override else settings.embedding_model,
        'embedding_dimension': row.embedding_dimension if override else settings.embedding_dimension,
        'embedding_batch_size': row.embedding_batch_size if override else settings.embedding_batch_size,
        'rerank_provider': row.rerank_provider if override else settings.rerank_provider,
        'rerank_base_url': row.rerank_base_url if override else settings.rerank_base_url,
        'rerank_model': row.rerank_model if override else settings.rerank_model,
        'rerank_max_documents': row.rerank_max_documents if override else settings.rerank_max_documents,
        'rerank_batch_size': row.rerank_batch_size if override else settings.rerank_batch_size,
        'rerank_threads': row.rerank_threads if override else settings.rerank_threads,
        'semantic_weight': row.semantic_weight,
        'lexical_weight': row.lexical_weight,
    }
    embedding_key = decrypt_retrieval_provider_api_key(row.embedding_api_key_encrypted) if row.embedding_api_key_encrypted else settings.embedding_api_key
    rerank_key = decrypt_retrieval_provider_api_key(row.rerank_api_key_encrypted) if row.rerank_api_key_encrypted else settings.rerank_api_key
    values['embedding_key'] = embedding_key
    values['rerank_key'] = rerank_key
    values['embedding_key_source'] = 'admin' if row.embedding_api_key_encrypted else ('env/vault' if embedding_key else 'none')
    values['rerank_key_source'] = 'admin' if row.rerank_api_key_encrypted else ('env/vault' if rerank_key else 'none')
    return values


def _admin(row: RetrievalProviderConfig) -> RetrievalProviderAdminResponse:
    values = _effective(row)
    return RetrievalProviderAdminResponse(
        embedding_provider=values['embedding_provider'], embedding_base_url=values['embedding_base_url'], embedding_model=values['embedding_model'], embedding_dimension=values['embedding_dimension'], embedding_batch_size=values['embedding_batch_size'], embedding_has_api_key=bool(values['embedding_key']), embedding_key_source=values['embedding_key_source'],
        rerank_provider=values['rerank_provider'], rerank_base_url=values['rerank_base_url'], rerank_model=values['rerank_model'], rerank_max_documents=values['rerank_max_documents'], rerank_batch_size=values['rerank_batch_size'], rerank_threads=values['rerank_threads'], rerank_has_api_key=bool(values['rerank_key']), rerank_key_source=values['rerank_key_source'], semantic_weight=values['semantic_weight'], lexical_weight=values['lexical_weight'], updated_at=row.updated_at,
    )


def _require_internal(request: Request) -> None:
    expected = settings.chat_config_service_token
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if not expected:
        raise HTTPException(status_code=503, detail='service misconfigured')
    if scheme.lower() != 'bearer' or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail='invalid service token')


@router_admin.get('', response_model=RetrievalProviderAdminResponse)
def get_retrieval_provider(request: Request, db: Session = Depends(get_db)) -> RetrievalProviderAdminResponse:
    enforce_rate_limit(request)
    return _admin(_row(db))


@router_admin.put('', response_model=RetrievalProviderAdminResponse)
def update_retrieval_provider(payload: RetrievalProviderUpdateRequest, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> RetrievalProviderAdminResponse:
    enforce_rate_limit(request)
    row = _row(db)
    if row.embedding_dimension != payload.embedding_dimension:
        raise HTTPException(
            status_code=409,
            detail='Eine Änderung der Embedding-Dimension benötigt eine separate Datenbankmigration und ist derzeit nicht über die UI möglich.',
        )
    embedding_changed = any(getattr(row, field) != getattr(payload, field) for field in ('embedding_provider', 'embedding_base_url', 'embedding_model', 'embedding_dimension', 'embedding_batch_size'))
    if embedding_changed and not payload.confirm_reindex:
        raise HTTPException(status_code=409, detail='Embedding-Änderungen benötigen eine bestätigte Voll-Reindizierung.')
    for field in ('embedding_provider', 'embedding_base_url', 'embedding_model', 'embedding_dimension', 'embedding_batch_size', 'rerank_provider', 'rerank_base_url', 'rerank_model', 'rerank_max_documents', 'rerank_batch_size', 'rerank_threads', 'semantic_weight', 'lexical_weight'):
        setattr(row, field, getattr(payload, field))
    row.updated_by_id = admin.id
    if payload.embedding_api_key and payload.embedding_api_key.strip():
        row.embedding_api_key_encrypted = encrypt_retrieval_provider_api_key(payload.embedding_api_key.strip())
    elif payload.clear_embedding_api_key:
        row.embedding_api_key_encrypted = None
    if payload.rerank_api_key and payload.rerank_api_key.strip():
        row.rerank_api_key_encrypted = encrypt_retrieval_provider_api_key(payload.rerank_api_key.strip())
    elif payload.clear_rerank_api_key:
        row.rerank_api_key_encrypted = None
    db.commit()
    db.refresh(row)
    response = _admin(row)
    if embedding_changed:
        try:
            result = httpx.post(
                f'{settings.portal_knowledge_base_url.rstrip("/")}/api/v1/internal/reindex',
                headers={'X-Weave-Reindex-Token': settings.portal_knowledge_webhook_secret}, timeout=5,
            )
            result.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=503, detail='Embedding gespeichert, Reindex konnte nicht gestartet werden.') from exc
        response.reindex_started = True
    return response


@router_internal.get('', response_model=RetrievalProviderInternalResponse, dependencies=[Depends(_require_internal)])
def internal_retrieval_provider(response: Response, db: Session = Depends(get_db)) -> RetrievalProviderInternalResponse:
    response.headers['Cache-Control'] = 'no-store'
    row = _row(db)
    try:
        effective = _effective(row)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail='stored credential unavailable') from exc
    embedding_key = effective['embedding_key']
    rerank_key = effective['rerank_key']
    return RetrievalProviderInternalResponse(
        embedding_provider=effective['embedding_provider'], embedding_base_url=effective['embedding_base_url'], embedding_model=effective['embedding_model'], embedding_dimension=effective['embedding_dimension'], embedding_batch_size=effective['embedding_batch_size'], embedding_api_key=embedding_key,
        rerank_provider=effective['rerank_provider'], rerank_base_url=effective['rerank_base_url'], rerank_model=effective['rerank_model'], rerank_max_documents=effective['rerank_max_documents'], rerank_batch_size=effective['rerank_batch_size'], rerank_threads=effective['rerank_threads'],
        rerank_api_key=rerank_key, semantic_weight=row.semantic_weight, lexical_weight=row.lexical_weight, updated_at=row.updated_at,
    )
