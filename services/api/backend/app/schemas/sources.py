"""GET /v1/sources/passage (app/api/sources.py) -- Weave-Retrieval's
`PassageResponse` (POST /api/v1/passage there), passed through: the cited
chunk plus up to `context` neighbouring chunks of the same document, for the
chat's "Textstelle öffnen" dialog."""

from pydantic import BaseModel, Field, field_validator


class PassageChunk(BaseModel):
    chunk_id: int
    chunk_index: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    text: str
    is_target: bool = False

    @field_validator('heading_path', mode='before')
    @classmethod
    def _null_heading_path(cls, value: object) -> object:
        return [] if value is None else value


class PassageResponse(BaseModel):
    document_id: str
    source_job_id: str | None = None
    title: str | None = None
    original_filename: str | None = None
    document_version: int | None = None
    source_kind: str | None = None
    source_url: str | None = None
    collection: str | None = None
    target_chunk_id: int
    chunks: list[PassageChunk]

    @field_validator('source_url')
    @classmethod
    def _http_only(cls, value: str | None) -> str | None:
        """Only an http(s) link may reach the browser (same rule as
        contracts/internal-chat.md's `Source.source_url`) -- anything else
        (`javascript:`, `file:`, ...) becomes `None` instead of a clickable
        link the chat would render."""
        if value is None:
            return None
        lowered = value.strip().lower()
        return value.strip() if lowered.startswith(('http://', 'https://')) else None
