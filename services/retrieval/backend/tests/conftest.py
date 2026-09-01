"""Shared TestClient/DB wiring for the backend test suite.

Mirrors Weave-Knowledge's backend/tests/conftest.py: a dedicated sqlite file
(not :memory: -- FastAPI's TestClient runs endpoint code in a worker thread,
and an in-memory sqlite db is private to the connection that created it, so
a second thread would see an empty database) with get_db overridden once,
process-wide, before any test module runs.

Unlike Weave-Knowledge, every route here except /health sits behind
`require_service_token` (see app/core/auth.py) -- TEST_TOKEN is set on the
process-wide `settings` object BEFORE `app.main` is imported, so the app is
built with a real, non-empty RETRIEVAL_API_TOKEN and tests exercise the
actual dependency (missing/wrong/right token) instead of one bypassed via
dependency_overrides. Settings is a plain (non-frozen) pydantic model, and
every module that does `from app.core.config import settings` gets the same
singleton instance -- so mutating the attribute here, before anything else
imports it, is visible everywhere for the rest of the process.
"""

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

TEST_TOKEN = 'test-service-token'
settings.retrieval_api_token = TEST_TOKEN

from app.core.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import Chunk, Collection, Document  # noqa: E402

TEST_DB = 'sqlite:///./test.db'
engine = create_engine(TEST_DB, future=True)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

AUTH_HEADERS = {'Authorization': f'Bearer {TEST_TOKEN}'}


def make_document(**overrides) -> Document:
    """A minimally-valid Document row, as Weave-Knowledge's own indexer
    would have written it. Every NOT NULL column without a default gets a
    value; callers override only what a given test actually cares about.
    """
    defaults = dict(
        source_job_id=str(uuid.uuid4()),
        content_sha256='a' * 64,
        engine='paddleocr',
        frontmatter={'source': 'x.pdf'},
        tags=[],
        team='Kundenservice',
        department='Support',
        processed_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Document(**defaults)


def make_chunk(document: Document, *, chunk_index: int = 0, embedding: list[float] | None = None, **overrides) -> Chunk:
    """A minimally-valid Chunk row for `document`. `embedding`, when given,
    round-trips through VectorType's JSON fallback on sqlite (see
    tests/test_vector_type.py) -- a plain list of floats, the same "fake
    embedding" shape Weave-Knowledge's fake embedding provider produces.
    """
    defaults = dict(
        document_id=document.id,
        chunk_index=chunk_index,
        text=f'chunk {chunk_index} of {document.original_filename or document.id}',
        heading_path=[],
        char_count=20,
        meta={'team': document.team, 'department': document.department},
        embedding=embedding,
        embedding_model='fake-embed' if embedding is not None else None,
    )
    defaults.update(overrides)
    return Chunk(**defaults)


def make_collection(**overrides) -> Collection:
    """A minimally-valid Collection row -- the registry mirror this service
    only ever reads (see app/models/models.py's own Collection docstring).
    `read_teams=[]` (public) by default; pass e.g. `read_teams=['Engineering']`
    for a team-restricted collection.
    """
    defaults = dict(
        slug='support-docs',
        name='Support Docs',
        description=None,
        read_teams=[],
        synced_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Collection(**defaults)
