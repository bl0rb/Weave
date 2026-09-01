"""Request/response shapes for POST /rerank -- deliberately Cohere/Jina-
shaped (`{"results": [{"index", "relevance_score"}, ...]}`) because that is
exactly what Weave-Retrieval's HttpReranker (backend/app/services/
reranker.py in that repo) sends and parses; see README.md for the full
field-by-field trace back to that module.
"""

from pydantic import BaseModel, Field


class RerankRequest(BaseModel):
    # Echoed by NO field in the response -- unlike Weave-Knowledge's
    # embeddings contract, which echoes its model name back on every
    # response (that service can hold several models; this process only
    # ever loads ONE, named by RERANKER_MODEL, so there is nothing to
    # disambiguate in a response). Accepted here anyway, rather than
    # rejected as an unknown field, purely so a HttpReranker-shaped
    # caller's payload (which always sends `model`) validates unchanged.
    # A value that does not match the loaded model is logged, not
    # rejected -- see app/api/rerank.py.
    model: str | None = None
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1)
    # None means "rank all of them". HttpReranker always sends
    # top_n=len(documents) explicitly and requires exactly that many
    # results back (see this module's docstring), but a direct Cohere-
    # style caller may omit it -- resolved against len(documents) in
    # app/api/rerank.py, not here, since that resolution needs both
    # fields at once.
    top_n: int | None = Field(default=None, ge=1)


class RerankResultItem(BaseModel):
    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    results: list[RerankResultItem]
