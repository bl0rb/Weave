import logging

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.events import router as events_router
from app.api.routes import router
from app.core.config import settings
from app.core.db import get_db
from app.schemas.health import HealthResponse

app = FastAPI(title=settings.app_name)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


# Unversioned and unauthenticated on purpose -- a liveness/readiness probe
# has no session/token to present and shouldn't have to care about API
# versioning either; contrast with the versioned /api/v1/documents surface
# registered below.
@app.get('/health', response_model=HealthResponse)
def healthcheck(db: Session = Depends(get_db)) -> HealthResponse:
    # Actually round-trips to the database (unlike Weave-Ingest's own
    # /health, which is a pure liveness check) -- a fresh SELECT 1 catches a
    # missing/unreachable weave_knowledge database, not just "the process is
    # up", which is exactly what an index-pipeline readiness probe needs.
    db.execute(text('SELECT 1'))
    return HealthResponse(status='healthy')


app.include_router(router)
app.include_router(events_router)
