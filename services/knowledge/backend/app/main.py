import logging

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.events import router as events_router
from app.api.indexing import router as indexing_router
from app.api.reindex import router as reindex_router
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
# AV-03: no I/O here -- this is what Helm now uses for liveness only, so an
# ongoing DB outage no longer restart-loops the process. /ready below is the
# dependency-aware endpoint for readiness.
@app.get('/health', response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    return HealthResponse(status='healthy')


# AV-03: the actual DB round-trip Weave-Ingest's own /health never did --
# a fresh SELECT 1 catches a missing/unreachable weave_knowledge database,
# which is exactly what an index-pipeline readiness probe needs, without
# restarting a process that a DB outage can't fix anyway.
@app.get('/ready')
def readiness(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        db.execute(text('SELECT 1'))
    except SQLAlchemyError:
        logging.getLogger(__name__).warning('readiness: database unavailable', exc_info=True)
        return JSONResponse(status_code=503, content={'status': 'unavailable', 'reason': 'database'})
    return JSONResponse(status_code=200, content={'status': 'ready'})


app.include_router(router)
app.include_router(events_router)
app.include_router(indexing_router)
app.include_router(reindex_router)
