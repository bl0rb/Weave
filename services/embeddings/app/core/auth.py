"""Incoming-request auth for every business route (/v1/embeddings,
/v1/models) -- NOT /health, which stays public like every other Weave
service's liveness probe (see app/main.py).

Bit-for-bit the same fail-closed pattern Weave-Tools' own
require_tools_service_token and Weave-API's require_introspection_service_token
use (backend/app/api/deps.py and backend/app/core/auth.py in those repos,
respectively): an unconfigured token answers 503 (misconfiguration), never
the 401 a genuinely wrong credential gets, and the comparison itself is
constant-time so response latency can't be used to brute-force the token
byte-by-byte.

Read on the standard `Authorization: Bearer <token>` header (not a
dedicated `X-...` header like Weave-Tools' own coarse service gate) on
purpose: this service impersonates an OpenAI-compatible /v1/embeddings
endpoint, and every OpenAI client -- including Weave-Knowledge's own
OpenAICompatibleProvider, see that repo's app/services/embeddings.py --
already sends its API key exactly this way.
"""

import hmac
import logging

from fastapi import Header, HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

_BEARER_PREFIX = 'Bearer '


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    if not settings.embeddings_api_token:
        logger.error('EMBEDDINGS_API_TOKEN is not configured; refusing all authenticated requests')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='service token not configured')

    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='missing or malformed bearer token')

    provided = authorization[len(_BEARER_PREFIX):]
    if not hmac.compare_digest(provided, settings.embeddings_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid api token')
