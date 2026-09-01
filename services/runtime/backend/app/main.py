import logging

from fastapi import FastAPI

from app.api.internal import router as internal_router
from app.core.config import settings
from app.schemas.health import HealthResponse
from app.services.botconfig import BotConfigError, list_bots

app = FastAPI(title=settings.app_name)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger(__name__)


# Unversioned and unauthenticated on purpose -- a liveness/readiness probe
# has no service token to present and shouldn't have to care about API
# versioning either, same reasoning as Weave-Retrieval's own /health.
#
# Actually parses every bot YAML under BOTS_DIR (via list_bots(), see
# app/services/botconfig.py) rather than merely checking the directory
# exists -- a broken bot file is exactly the kind of misconfiguration a
# health probe exists to catch, not something that should only surface the
# next time GET /internal/bots happens to be called. Returns 200 either way:
# a bad bot file degrades the bot roster, but the process itself is still up
# and every other route keeps working, so this is reported via the `status`
# field for an operator/dashboard, not by refusing the health check itself.
@app.get('/health', response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    try:
        bots = list_bots()
    except OSError as exc:
        logger.error('BOTS_DIR (%s) is not readable: %s', settings.bots_dir, exc)
        return HealthResponse(status='degraded', bots=0, detail=f'BOTS_DIR not readable: {exc}')
    except BotConfigError as exc:
        logger.error('invalid bot configuration: %s', exc)
        return HealthResponse(status='degraded', bots=0, detail=str(exc))
    return HealthResponse(status='healthy', bots=len(bots))


app.include_router(internal_router)
