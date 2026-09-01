"""Service-to-service authentication for this service's read API.

`GET /api/v1/documents`, `/documents/{id}` and `/collections` sit behind
`require_service_token` (applied at the router level in app/api/routes.py).
They hand out the indexed corpus itself, past the team/`read_teams` scoping
Weave-Retrieval enforces on every search -- an open port here would be a way
around the whole permission model, not merely a debug convenience.

Deliberately NOT applied to two other surfaces:

- `/health` (app/main.py) -- a readiness probe has no token to present.
- The webhook ingress (app/api/events.py) -- it authenticates differently
  and better, by HMAC-SHA256 over the raw body, because it accepts *writes*
  from Weave-Ingest and a bearer token would not tell it whether the body
  it is about to index was tampered with in transit.

A single shared static token is the right weight here, exactly as in
Weave-Retrieval's own app/core/auth.py this module mirrors (ADR-0002's
machine-caller pattern): these routes have no end users to tell apart.
"""

import hmac
import logging

from fastapi import Header, HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

_BEARER_PREFIX = 'Bearer '


def require_service_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency enforcing `Authorization: Bearer <KNOWLEDGE_API_TOKEN>`.

    Checked in this order, same as Weave-Retrieval's version:

    1. An unconfigured (empty) `settings.knowledge_api_token` is refused
       with 503, never treated as "auth disabled" -- falling through would
       let `hmac.compare_digest('', '')` succeed for a caller that also
       sends an empty bearer, turning a forgotten variable into an open
       corpus. 503 tells the operator "fix your deployment", never 200.
    2. A missing or non-Bearer `Authorization` header -> 401.
    3. A token that does not match, compared in constant time so response
       latency cannot be used to guess it byte by byte -> 401.
    """
    if not settings.knowledge_api_token:
        logger.error('KNOWLEDGE_API_TOKEN is not configured; refusing all read-API requests')
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='service token not configured',
        )

    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='missing or malformed bearer token')

    provided = authorization[len(_BEARER_PREFIX):]
    if not hmac.compare_digest(provided, settings.knowledge_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid service token')
