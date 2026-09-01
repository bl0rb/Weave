"""Request shape for POST /internal/tokens/introspect
(app/api/internal.py). The response is a plain dict, not a pydantic model
here -- see that module's own docstring for why the `{"active": false}`
shape must come back EXACTLY as-is, with no extra null-valued keys a
declared response_model would otherwise add.
"""

from pydantic import BaseModel


class TokenIntrospectionRequest(BaseModel):
    token: str
