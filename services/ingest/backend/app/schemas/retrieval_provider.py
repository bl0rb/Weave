from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalProviderAdminResponse(BaseModel):
    embedding_provider: str
    embedding_base_url: str
    embedding_model: str
    embedding_dimension: int
    embedding_batch_size: int
    embedding_has_api_key: bool
    embedding_key_source: str
    rerank_provider: str
    rerank_base_url: str
    rerank_model: str
    rerank_max_documents: int
    rerank_batch_size: int
    rerank_threads: int
    rerank_has_api_key: bool
    rerank_key_source: str
    semantic_weight: float
    lexical_weight: float
    updated_at: datetime | None
    reindex_started: bool = False


class RetrievalProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    embedding_provider: str = Field(default='fake', max_length=32)
    embedding_base_url: str = Field(default='', max_length=1024)
    embedding_model: str = Field(default='fake-embed', max_length=255)
    embedding_dimension: int = Field(default=1536, ge=1, le=8192)
    embedding_batch_size: int = Field(default=64, ge=1, le=512)
    embedding_api_key: str | None = Field(default=None, max_length=8192)
    clear_embedding_api_key: bool = False
    rerank_provider: str = Field(default='none', max_length=32)
    rerank_base_url: str = Field(default='', max_length=1024)
    rerank_model: str = Field(default='', max_length=255)
    rerank_max_documents: int = Field(default=50, ge=1, le=500)
    rerank_batch_size: int = Field(default=16, ge=1, le=256)
    rerank_threads: int = Field(default=4, ge=1, le=64)
    rerank_api_key: str | None = Field(default=None, max_length=8192)
    clear_rerank_api_key: bool = False
    confirm_reindex: bool = False
    semantic_weight: float = Field(default=0.5, ge=0, le=1)
    lexical_weight: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode='after')
    def validate_weights(self) -> 'RetrievalProviderUpdateRequest':
        if abs((self.semantic_weight + self.lexical_weight) - 1.0) > 0.001:
            raise ValueError('semantic_weight and lexical_weight must add up to 1')
        if self.rerank_provider == 'api' and (not self.rerank_base_url.strip() or not self.rerank_model.strip()):
            raise ValueError('rerank_base_url and rerank_model are required when reranking is enabled')
        return self


class RetrievalProviderInternalResponse(BaseModel):
    embedding_provider: str
    embedding_base_url: str
    embedding_model: str
    embedding_dimension: int
    embedding_batch_size: int
    embedding_api_key: str = ''
    rerank_provider: str
    rerank_base_url: str
    rerank_model: str
    rerank_max_documents: int
    rerank_batch_size: int
    rerank_threads: int
    rerank_api_key: str = ''
    semantic_weight: float
    lexical_weight: float
    updated_at: datetime | None
