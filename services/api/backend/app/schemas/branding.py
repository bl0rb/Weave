"""Response body of GET /v1/branding (app/api/branding.py) -- the public
shape of Weave-Ingest's GET /api/v1/branding, re-validated, with asset
URLs pointing at this gateway."""

from typing import Literal

from pydantic import BaseModel


class BrandingTokens(BaseModel):
    accent: str
    accent_hover: str
    accent_ink: str
    accent_soft: str
    on_accent: str


class BrandingPalette(BaseModel):
    light: BrandingTokens
    dark: BrandingTokens


class BrandingAsset(BaseModel):
    content_type: Literal['image/svg+xml', 'image/gif', 'image/png']
    sha256: str
    size_bytes: int
    # /v1/branding/assets/<kind>?v=<first 16 hex of sha256>
    url: str


class BrandingResponse(BaseModel):
    # All null = no branding configured (or Weave-Ingest unreachable with no
    # earlier answer): clients keep their built-in look.
    primary_color: str | None = None
    accent_color: str | None = None
    palette: BrandingPalette | None = None
    logo: BrandingAsset | None = None
    busy: BrandingAsset | None = None
    updated_at: str | None = None
