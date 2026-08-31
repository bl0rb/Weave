"""FastAPI dependencies shared by app/api/tools.py: the REST mirror's own
coarse service gate (require_tools_service_token) and the scope-resolution
dependency (get_scope) both REST endpoints build on top of the exact same
app/services/scope.py:resolve_scope() the MCP server (app/mcp_server.py)
also calls.
"""

import hmac
import logging

from fastapi import Header, HTTPException, status

from app.core.config import settings
from app.services.scope import Scope, ScopeConfigurationError, ScopeError, resolve_scope

logger = logging.getLogger(__name__)


def require_tools_service_token(x_tools_service_token: str | None = Header(default=None)) -> None:
    """Enforce `X-Tools-Service-Token: <TOOLS_API_TOKEN>` on every REST
    tools route. Mirrors Weave-Retrieval's own require_service_token (see
    that service's app/core/auth.py) almost exactly -- same fail-closed 503
    for an unconfigured token, same constant-time comparison -- with one
    deliberate difference: it is checked on a DEDICATED header, never on
    `Authorization`. `Authorization` is reserved, on every route in this
    service, for the caller's own personal-or-delegation token (see
    get_scope below); mixing the two into one header would make it
    impossible for a single request to carry both "is this deployment
    allowed to call Weave-Tools at all" and "whose rights does this
    particular call run under" at the same time.
    """
    if not settings.tools_api_token:
        logger.error('TOOLS_API_TOKEN is not configured; refusing all REST tool calls')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='service token not configured')

    if not x_tools_service_token or not hmac.compare_digest(x_tools_service_token, settings.tools_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid service token')


def get_scope(authorization: str | None = Header(default=None)) -> Scope:
    """Resolve the calling human's (or delegated agent's) Scope from THIS
    request's own `Authorization` header -- freshly, on every call (see
    resolve_scope's own docstring for why there is deliberately no cache).
    The request body is never consulted here, and never could widen what
    this returns even if it were.

    ScopeConfigurationError (settings.weave_delegation_secret unset -- see
    that exception's own docstring) is caught SEPARATELY from ScopeError,
    and BEFORE it, on purpose: same fail-closed 503 as
    require_tools_service_token above for an unconfigured TOOLS_API_TOKEN,
    never the 401 a genuinely invalid/expired caller credential gets. A 401
    here would read, to the caller, like their own token is wrong -- masking
    what is actually this deployment's own misconfiguration.
    """
    try:
        return resolve_scope(authorization)
    except ScopeConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None
    except ScopeError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from None
