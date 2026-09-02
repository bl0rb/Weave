from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatProviderAdminResponse(BaseModel):
    configured: bool
    enabled: bool
    base_url: str
    model: str
    has_api_key: bool
    timeout_seconds: float
    temperature: float | None
    updated_at: datetime | None


class ChatProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    base_url: str = Field(default='', max_length=1024)
    model: str = Field(default='', max_length=255)
    api_key: str | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    @model_validator(mode='after')
    def validate_enabled_config(self) -> 'ChatProviderUpdateRequest':
        if self.clear_api_key and self.api_key:
            raise ValueError('api_key and clear_api_key cannot be used together')
        if self.enabled and (not self.base_url.strip() or not self.model.strip()):
            raise ValueError('base_url and model are required when chat generation is enabled')
        return self


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
    updated_at: datetime | None = None
