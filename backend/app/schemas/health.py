"""Response shape for GET /health (app/main.py)."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
