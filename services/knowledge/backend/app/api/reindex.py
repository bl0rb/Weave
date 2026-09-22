import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.models import Document
from app.workers.tasks import REINDEX_TASK_NAME, reindex_all

router = APIRouter(prefix='/api/v1/internal/reindex', tags=['internal-reindex'])


def _require_reindex_token(x_weave_reindex_token: str | None) -> None:
    expected = settings.weave_ingest_webhook_secret
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='service misconfigured')
    if not x_weave_reindex_token or not hmac.compare_digest(x_weave_reindex_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid reindex token')


@router.post('')
def start_reindex(x_weave_reindex_token: str | None = Header(default=None)) -> dict[str, str]:
    _require_reindex_token(x_weave_reindex_token)
    task = reindex_all.delay()
    return {'status': 'started', 'task_id': task.id, 'task_name': REINDEX_TASK_NAME}


@router.get('/status')
def reindex_status(
    x_weave_reindex_token: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict[str, object]:
    """Progress projection for Weave-Ingest's 'Vektoren neu berechnen' admin
    action (see its app/api/retrieval_provider.py reindex-status proxy):
    document counts by status, plus the newest `updated_at` across all
    documents as a cheap "is it still moving" signal -- reindex_all itself
    has no separate task-progress row to read from."""
    _require_reindex_token(x_weave_reindex_token)
    rows = db.execute(select(Document.status, func.count()).group_by(Document.status)).all()
    by_status = {(value.value if hasattr(value, 'value') else value): count for value, count in rows}
    total = sum(by_status.values())
    newest_updated_at = db.scalar(select(func.max(Document.updated_at)))
    return {
        'total': total,
        'by_status': by_status,
        'newest_updated_at': newest_updated_at.isoformat() if newest_updated_at else None,
    }