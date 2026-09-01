"""POST /rerank -- the one endpoint this service exists to provide. See
README.md for the full field-by-field contract trace back to
Weave-Retrieval's HttpReranker (backend/app/services/reranker.py in that
repo), the one caller this shape is built for.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_service_token
from app.core.config import settings
from app.schemas.rerank import RerankRequest, RerankResponse, RerankResultItem
from app.services.model import reranker_model

logger = logging.getLogger(__name__)

router = APIRouter()

# How long a request waits for a still-loading model before giving up.
# Deliberately about a second, not tens of seconds: a caller that gets a
# 503 here loses nothing but the reranking itself -- Weave-Retrieval
# catches the failure and answers with the untouched RRF order (see its
# reranker.py docstring). Waiting longer only makes that fallback slower.
# It compounds, too: HttpReranker retries a 503 up to three times with
# backoff, so every second spent blocking here is multiplied before the
# search finally gives up. A short wait still covers the one case worth
# covering -- a request arriving in the moment right after startup while
# the model is a heartbeat away from ready. The cold start itself (a
# ~2.3GB download) is the healthcheck's job, not a request's.
_MODEL_WAIT_SECONDS = 1.0


@router.post('/rerank', response_model=RerankResponse)
def rerank(request: RerankRequest, _: None = Depends(require_service_token)) -> RerankResponse:
    # Checked BEFORE touching the model at all -- a cross-encoder scores
    # every document individually (see app/core/config.py's
    # reranker_max_documents docstring), so this is a CPU-latency guardrail,
    # not a memory one; 413 (Payload Too Large) rather than 422, since the
    # payload is well-formed, just too big for this deployment's configured
    # limit.
    if len(request.documents) > settings.reranker_max_documents:
        raise HTTPException(
            status_code=413,
            detail=(
                f'{len(request.documents)} documents exceeds RERANKER_MAX_DOCUMENTS '
                f'({settings.reranker_max_documents})'
            ),
        )

    if request.model is not None and request.model != settings.reranker_model:
        logger.warning(
            "rerank request named model %r but this process only ever loads %r -- scoring with the loaded model anyway",
            request.model, settings.reranker_model,
        )

    if not reranker_model.warm and not reranker_model.wait_until_warm(timeout=_MODEL_WAIT_SECONDS):
        raise HTTPException(status_code=503, detail='reranker model is still loading; retry shortly')

    scores = reranker_model.score(request.query, request.documents)

    top_n = len(request.documents) if request.top_n is None else min(request.top_n, len(request.documents))
    ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)[:top_n]

    return RerankResponse(
        results=[RerankResultItem(index=index, relevance_score=score) for index, score in ranked]
    )
