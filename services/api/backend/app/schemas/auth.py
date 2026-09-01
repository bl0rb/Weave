"""Request/response bodies for POST /v1/auth/session/exchange
(app/api/auth.py) -- the cross-origin OIDC post-login handoff's one JSON
endpoint. Every other endpoint in that router takes no body at all (plain
query params on a GET), so this is the first schema module that router
needs.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class SessionExchangeRequest(BaseModel):
    code: str = Field(min_length=1)


class SessionExchangeResponse(BaseModel):
    session_token: str
    expires_at: datetime
