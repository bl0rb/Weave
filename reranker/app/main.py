"""The reranker service's ASGI app: GET /health plus POST /rerank
(app/api/rerank.py). See that module and README.md for the full contract
this service implements for Weave-Retrieval's HttpReranker.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.rerank import router as rerank_router
from app.core.config import settings
from app.schemas.health import HealthResponse
from app.services.model import reranker_model

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.reranker_api_token:
        logger.warning(
            'RERANKER_API_TOKEN is unset -- POST /rerank will answer 503 for every request until it is set'
        )
    # Loaded on a background thread, NOT awaited here -- GET /health must
    # answer immediately (see below), including while bge-reranker-v2-m3's
    # ~2.3 GB of weights are still downloading/loading on a cold cache,
    # rather than uvicorn's own startup blocking for however long that
    # takes. POST /rerank (app/api/rerank.py) is the one route that
    # actually waits on `reranker_model.warm`.
    threading.Thread(target=reranker_model.load, name='reranker-model-loader', daemon=True).start()
    yield


app = FastAPI(title='Weave Reranker', lifespan=lifespan)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


@app.get('/health', response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    # Answers immediately no matter what -- including mid-load (see
    # lifespan above) -- so an orchestrator's liveness probe passes as soon
    # as the PROCESS is up. `warm` is the separate, honest signal for "can
    # this thing actually serve a rerank request yet"; see README.md's
    # "Betriebshinweis" for why that distinction matters here specifically.
    return HealthResponse(
        status='healthy',
        model=settings.reranker_model,
        threads=settings.reranker_threads,
        warm=reranker_model.warm,
        max_documents=settings.reranker_max_documents,
    )


app.include_router(rerank_router)
