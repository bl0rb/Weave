from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatProviderAdminResponse(BaseModel):
    id: str = 'default'
    name: str = ''
    configured: bool
    enabled: bool
    base_url: str
    model: str
    has_api_key: bool
    timeout_seconds: float
    temperature: float | None
    supports_tools: bool
    updated_at: datetime | None


class ChatProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str = Field(default='', max_length=255)
    enabled: bool = False
    base_url: str = Field(default='', max_length=1024)
    model: str = Field(default='', max_length=255)
    api_key: str | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    supports_tools: bool = False

    @model_validator(mode='after')
    def validate_enabled_config(self) -> 'ChatProviderUpdateRequest':
        if self.clear_api_key and self.api_key:
            raise ValueError('api_key and clear_api_key cannot be used together')
        if self.enabled and (not self.base_url.strip() or not self.model.strip()):
            raise ValueError('base_url and model are required when chat generation is enabled')
        return self


class ChatProviderListResponse(BaseModel):
    items: list[ChatProviderAdminResponse] = Field(default_factory=list)


class ChatProviderTestResponse(BaseModel):
    ok: bool
    detail: str
    latency_ms: int | None = None


class ChatProviderInternalResponse(BaseModel):
    configured: bool
    enabled: bool
    base_url: str = ''
    model: str = ''
    api_key: str = ''
    timeout_seconds: float = 60.0
    temperature: float | None = None
    supports_tools: bool = False
    updated_at: datetime | None = None


class ChatEndpointSummary(BaseModel):
    """Key-free catalog entry Runtime shows to chat users."""

    id: str
    name: str
    model: str
    supports_tools: bool = False


class ChatEndpointCatalogResponse(BaseModel):
    items: list[ChatEndpointSummary] = Field(default_factory=list)
