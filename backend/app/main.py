"""The REST-facing ASGI app: `/health` plus the REST mirror of the two tools
(app/api/tools.py). The MCP server (app/mcp_server.py) is a SEPARATE ASGI
app -- see that module's own docstring for why it is not mounted in here.
"""

import logging

from fastapi import FastAPI

from app.api.tools import router as tools_router
from app.core.config import settings
from app.schemas.health import HealthResponse
from app.services.scope import warn_if_delegation_secret_unconfigured

app = FastAPI(title=settings.app_name)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')

# Logged once, at process start (this module is its own standalone
# uvicorn-run ASGI process -- see app/mcp_server.py's module docstring for
# why the two entry points are separate processes in the first place), so a
# misconfigured WEAVE_DELEGATION_SECRET shows up immediately in the startup
# log rather than only after the first Delegations-Token verification
# attempt -- see warn_if_delegation_secret_unconfigured's own docstring
# (app/services/scope.py).
warn_if_delegation_secret_unconfigured()


# Unversioned and unauthenticated on purpose -- a liveness/readiness probe
# has no service token to present and shouldn't have to care about API
# versioning either; contrast with the versioned, auth-guarded /api/v1/tools
# surface registered below. No downstream round-trip here (unlike e.g.
# Weave-Retrieval's own /health, which pings its database): this service
# owns no database of its own to check -- every fact it serves is fetched
# fresh from Weave-API/Weave-Retrieval on every call (see
# app/services/scope.py's module docstring for why that is deliberate), so
# "the process answers HTTP at all" is the entire liveness question this
# service itself can meaningfully answer; whether ITS OWN upstreams are
# reachable is a separate check for the deployment's own monitoring.
@app.get('/health', response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    return HealthResponse(status='healthy')


app.include_router(tools_router)
