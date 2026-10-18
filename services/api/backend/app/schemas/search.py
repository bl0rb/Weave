from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    collection: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)


class SearchSource(BaseModel):
    document: str
    document_id: str
    chunk_id: int
    page: str | None = None
    collection: str | None = None
    source_url: str | None = None


class SearchHit(BaseModel):
    text: str
    source: SearchSource


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHit]
