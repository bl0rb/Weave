from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    # Number of bot YAML files under BOTS_DIR that parsed and validated
    # successfully -- 0 whenever `status` is 'degraded' via an exception
    # (see app/main.py's healthcheck: BOTS_DIR unreadable or the FIRST
    # invalid file both abort list_bots() before any count is known).
    bots: int = 0
    detail: str | None = None
