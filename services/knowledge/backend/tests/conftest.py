"""Shared TestClient/DB wiring for the backend test suite.

Mirrors Weave-Ingest's backend/tests/conftest.py: a dedicated sqlite file
(not :memory: -- FastAPI's TestClient runs endpoint code in a worker thread,
and an in-memory sqlite db is private to the connection that created it, so
a second thread would see an empty database) with get_db overridden once,
process-wide, before any test module runs. There is no auth layer to bypass
here (unlike Ingest's conftest.py), so this is simpler.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, get_db
from app.main import app
from app.models import models  # noqa: F401 -- registers all tables on Base.metadata

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
