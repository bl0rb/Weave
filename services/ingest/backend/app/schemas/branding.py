from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

BrandingContentType = Literal['image/svg+xml', 'image/gif', 'image/png']


class BrandingPaletteMode(BaseModel):
    accent: str
    accent_hover: str
    accent_ink: str
    accent_soft: str
    on_accent: str


class BrandingPalette(BaseModel):
    light: BrandingPaletteMode
    dark: BrandingPaletteMode


class BrandingAssetInfo(BaseModel):
    content_type: BrandingContentType
    sha256: str
    size_bytes: int
    # Relative to the API base; `v` changes with the content, so a client
    # may cache the URL for good.
    url: str


class BrandingPublic(BaseModel):
    # Lowercase #rrggbb; null = the built-in colour.
    primary_color: str | None = None
    accent_color: str | None = None
    # Derived from the two colours; null when both are null.
    palette: BrandingPalette | None = None
    logo: BrandingAssetInfo | None = None
    busy: BrandingAssetInfo | None = None
    # The latest change of the colours or an asset.
    updated_at: datetime | None = None


class BrandingAdmin(BrandingPublic):
    # True once a colour row or any asset exists.
    managed: bool


class BrandingUpdateRequest(BaseModel):
    """Both keys optional: a key that is present replaces the stored value
    (null = back to the built-in colour), an absent key keeps it. The hex
    format is checked by the endpoint so the 422 carries one German
    message (app/services/branding.normalize_color)."""

    model_config = ConfigDict(extra='forbid')

    primary_color: str | None = None
    accent_color: str | None = None
