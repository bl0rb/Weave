"""User-facing, read-only schemas for VL connections. Admin VL connection
CRUD schemas live in app/schemas/auth.py next to the OIDC Provider* schemas
they're modeled on; this module is scoped to what app/api/vl_connections.py
returns.
"""

from pydantic import BaseModel


class VlConnectionPublic(BaseModel):
    id: str
    name: str
    model: str

    model_config = {'from_attributes': True}


class VlConnectionPublicListResponse(BaseModel):
    items: list[VlConnectionPublic]
