"""Service-to-service authentication for this API.

Weave-Runtime sits on both sides of the Weave service graph: it is CALLED by
Weave-API's gateway, and it CALLS OUT to Weave-Retrieval (and, later, an LLM
provider) itself. This module guards only the callee side -- every route
except `/health` sits behind `require_service_token` (see app/main.py,
app/api/internal.py) -- checking `RUNTIME_API_TOKEN`, the Bearer token a
caller of THIS service must present. It never touches
`settings.retrieval_api_token`, which is the opposite direction: the token
THIS service presents when it calls Weave-Retrieval's own
require_service_token (see app/core/config.py).

This is line-for-line the same dependency as Weave-Retrieval's own
app/core/auth.py, just guarding a different token -- a single shared static
Bearer token is enough here (ADR-0002's "Bearer-Token (wie in PaddleDoc)"
pattern for machine callers -- see docs/adr/0002-auth-strategie.md), not a
per-caller PAT registry with its own storage/rotation/audit-logging
concerns, since Weave-Runtime is only ever called by another Weave service
behind Weave-API's gateway, never directly by an end user.

Weave-API's gateway is still the one enforcing end-user authn/authz
(ADR-0002: "Nicht bei: Einzelnen Services nicht replizieren") -- the `user`
field on ChatRequest (app/schemas/chat.py) is how that gateway's per-user
identity/team membership is propagated down to this service, not a second
authentication layer.
"""

import hmac
import logging

from fastapi import Header, HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

_BEARER_PREFIX = 'Bearer '


def require_service_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency enforcing `Authorization: Bearer <RUNTIME_API_TOKEN>`.

    Checked in this order:

    1. An unconfigured (empty) `settings.runtime_api_token` is refused
       with 503, never treated as "auth disabled". Falling through to the
       comparison below would let `hmac.compare_digest('', '')` succeed for
       a caller that also sends an empty bearer token, silently turning a
       deployment misconfiguration into an open endpoint -- 503 tells the
       operator "fix your deployment", never 200.
    2. A missing or non-Bearer `Authorization` header -> 401.
    3. A Bearer token that doesn't match, compared with `hmac.compare_digest`
       (constant-time, so response latency can't be used to brute-force the
       token byte-by-byte) -> 401.
    """
    if not settings.runtime_api_token:
        logger.error('RUNTIME_API_TOKEN is not configured; refusing all service-authenticated requests')
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='service token not configured',
        )

    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='missing or malformed bearer token')

    provided = authorization[len(_BEARER_PREFIX):]
    if not hmac.compare_digest(provided, settings.runtime_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid service token')
