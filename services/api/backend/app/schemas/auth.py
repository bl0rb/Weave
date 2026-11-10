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
    # RFC 7636 section 4.1: 43-128 characters from the unreserved set.
    # Required when the login was started with a `code_challenge` (native-app
    # login, the chat's SSO handoff); a verifier never redeems an unbound
    # code (app/api/auth.py). The pattern also keeps non-ASCII input a 422
    # instead of a 500 from the ASCII-only S256 hash.
    code_verifier: str | None = Field(default=None, pattern=r'^[A-Za-z0-9._~-]{43,128}$')


class SessionExchangeResponse(BaseModel):
    session_token: str
    expires_at: datetime
