import logging

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth import router as auth_router
from app.api.bots import router as bots_router
from app.api.chat import router as chat_router
from app.api.collections import router as collections_router
from app.api.conversations import router as conversations_router
from app.api.internal import router as internal_router
from app.api.openai_compat import router as openai_compat_router
from app.core.config import settings
from app.core.db import get_db
from app.schemas.health import HealthResponse

app = FastAPI(title=settings.app_name)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


# Unversioned and unauthenticated on purpose -- a liveness/readiness probe
# has no bearer token to present and shouldn't have to care about API
# versioning either; contrast with the versioned, auth+ratelimit-guarded
# /v1 surface registered below.
@app.get('/health', response_model=HealthResponse)
def healthcheck(db: Session = Depends(get_db)) -> HealthResponse:
    # Actually round-trips to the database -- a fresh SELECT 1 catches a
    # missing/unreachable weave_api database, not just "the process is up".
    db.execute(text('SELECT 1'))
    return HealthResponse(status='healthy')


app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(bots_router)
app.include_router(chat_router)
app.include_router(openai_compat_router)
app.include_router(collections_router)
app.include_router(internal_router)
