import hmac

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import settings
from app.workers.tasks import REINDEX_TASK_NAME, reindex_all

router = APIRouter(prefix='/api/v1/internal/reindex', tags=['internal-reindex'])


@router.post('')
def start_reindex(x_weave_reindex_token: str | None = Header(default=None)) -> dict[str, str]:
    expected = settings.weave_ingest_webhook_secret
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='service misconfigured')
    if not x_weave_reindex_token or not hmac.compare_digest(x_weave_reindex_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid reindex token')
    task = reindex_all.delay()
    return {'status': 'started', 'task_id': task.id, 'task_name': REINDEX_TASK_NAME}