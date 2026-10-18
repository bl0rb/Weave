from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryMode = Literal['off', 'extractive', 'llm']


class KnowledgeMemorySettings(BaseModel):
    mode: MemoryMode = 'off'
    llm_endpoint: str = Field(default='default', min_length=1, max_length=64)
    debounce_seconds: int = Field(default=120, ge=10, le=86400)
    max_documents_per_run: int = Field(default=25, ge=1, le=500)
    max_input_chars: int = Field(default=12000, ge=1000, le=200000)


class KnowledgeMemoryUpdateRequest(KnowledgeMemorySettings):
    model_config = ConfigDict(extra='forbid')


class KnowledgeMemoryAdminResponse(KnowledgeMemorySettings):
    # False until an admin saved the settings here; the services then still
    # run on their own KNOWLEDGE_MEMORY_MODE environment value.
    managed: bool
    updated_at: datetime | None = None


class KnowledgeMemoryInternalResponse(KnowledgeMemorySettings):
    managed: bool


class KnowledgeMemoryRebuildRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    collection: str | None = Field(default=None, max_length=255)


class KnowledgeMemoryRebuildResponse(BaseModel):
    queued: int
