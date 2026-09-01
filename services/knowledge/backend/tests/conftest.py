"""Shared TestClient/DB wiring for the backend test suite.

Mirrors Weave-Ingest's backend/tests/conftest.py: a dedicated sqlite file
(not :memory: -- FastAPI's TestClient runs endpoint code in a worker thread,
and an in-memory sqlite db is private to the connection that created it, so
a second thread would see an empty database) with get_db overridden once,
process-wide, before any test module runs.

The shared `client` carries the read API's service token by default (same
reasoning as tests/test_events_api.py signing its webhooks by default):
every test here is about what the endpoints DO, and having each of them
restate the credential would bury that. The credential's own behaviour --
missing, wrong, unconfigured -- is tested deliberately, in
tests/test_read_api_auth.py, with clients that do not carry it.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

TEST_TOKEN = 'test-service-token'
settings.knowledge_api_token = TEST_TOKEN
AUTH_HEADERS = {'Authorization': f'Bearer {TEST_TOKEN}'}

from app.core.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import models  # noqa: F401,E402 -- registers all tables on Base.metadata

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

client = TestClient(app, headers=AUTH_HEADERS)
