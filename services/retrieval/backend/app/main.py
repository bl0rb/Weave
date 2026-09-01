import logging

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.collections import router as collections_router
from app.api.search import router as search_router
from app.core.config import settings
from app.core.db import get_db
from app.schemas.health import HealthResponse

app = FastAPI(title=settings.app_name)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


# Unversioned and unauthenticated on purpose -- a liveness/readiness probe
# has no service token to present and shouldn't have to care about API
# versioning either; contrast with the versioned, auth-guarded /api/v1
# surface registered below.
@app.get('/health', response_model=HealthResponse)
def healthcheck(db: Session = Depends(get_db)) -> HealthResponse:
    # Actually round-trips to the database -- a fresh SELECT 1 catches a
    # missing/unreachable weave_knowledge database (or a misconfigured
    # read-only role with no CONNECT grant), not just "the process is up".
    db.execute(text('SELECT 1'))
    return HealthResponse(status='healthy')


app.include_router(search_router)
app.include_router(collections_router)
