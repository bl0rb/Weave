"""Bearer-token auth for POST /rerank -- fail-closed, same discipline as
every other Weave service's static service token; see
app/core/config.py:Settings.reranker_api_token's own docstring for why an
unset token means "answer 503 for everyone" rather than "accept anyone".
GET /health (app/main.py) does not depend on this -- a liveness probe
carries no bearer token to present.
"""

import hmac

from fastapi import Header, HTTPException

from app.core.config import settings


def require_service_token(authorization: str | None = Header(default=None)) -> None:
    if not settings.reranker_api_token:
        raise HTTPException(
            status_code=503,
            detail='reranker service is not configured (RERANKER_API_TOKEN is unset)',
        )
    # Constant-time, like every sibling service: a plain `!=` on ASCII
    # strings short-circuits at the first differing byte, so the response
    # latency leaks how many leading bytes of a guess were right and the
    # token can be recovered byte by byte.
    expected = f'Bearer {settings.reranker_api_token}'
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail='missing or invalid bearer token')
