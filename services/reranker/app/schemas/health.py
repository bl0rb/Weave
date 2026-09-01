from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    model: str
    threads: int
    warm: bool
    max_documents: int
