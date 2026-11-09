from pydantic import BaseModel, Field, field_validator, model_validator


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    collection: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    # How many hits survive reranking; must not exceed top_k (candidate
    # count) when both are given. Unset -> Weave-Retrieval's own default,
    # capped at the effective top_k.
    final_k: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode='after')
    def _final_k_within_top_k(self) -> 'SearchRequest':
        if self.top_k is not None and self.final_k is not None and self.final_k > self.top_k:
            raise ValueError('final_k must be <= top_k')
        return self


class SearchSource(BaseModel):
    document: str
    document_id: str
    chunk_id: int
    page: str | None = None
    collection: str | None = None
    source_url: str | None = None
    heading_path: list[str] = []
    document_version: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    chunk_index: int | None = None
    title: str | None = None
    language: str | None = None
    document_type: str | None = None
    tags: list[str] = []
    source_kind: str | None = None
    uploaded_at: str | None = None
    fetched_at: str | None = None
    legal_status: str | None = None

    @field_validator('source_url', mode='before')
    @classmethod
    def _web_link_only(cls, value: object) -> str | None:
        # Clients may render this as a link: nothing but http(s), same rule
        # as Weave-Runtime's chat Source.source_url.
        return value if isinstance(value, str) and value.startswith(('https://', 'http://')) else None


class SearchScores(BaseModel):
    """Per-stage scores as reported by Weave-Retrieval; `None` where that
    stage did not score the hit (e.g. no fulltext match, rerank skipped)."""

    vector: float | None = None
    fulltext: float | None = None
    rrf: float | None = None
    rerank: float | None = None


class SearchHit(BaseModel):
    rank: int
    text: str
    scores: SearchScores = SearchScores()
    source: SearchSource


class SearchTrace(BaseModel):
    """Subset of Weave-Retrieval's search trace (counts, effective k values,
    timings) -- no query embedding, no chunk text."""

    top_k: int | None = None
    final_k: int | None = None
    vector_candidates: int = 0
    fulltext_candidates: int = 0
    fused: int = 0
    reranked: int = 0
    returned: int = 0
    rerank_error: bool = False
    timings_ms: dict[str, float] = {}


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHit]
    trace: SearchTrace | None = None
