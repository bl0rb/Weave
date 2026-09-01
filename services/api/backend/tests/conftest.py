"""Shared TestClient/DB wiring for the backend test suite.

`settings.database_url` is pointed at a dedicated sqlite file BEFORE
`app.core.db` is imported anywhere else in the process -- that module
creates its module-level `engine`/`SessionLocal` at import time, bound to
whatever `settings.database_url` holds right then. Doing this first (rather
than only overriding FastAPI's `get_db` dependency, as some sibling
services do) means app/cli.py's subcommands -- which open sessions via
`app.core.db.SessionLocal` directly, with no FastAPI dependency-injection
in the picture at all -- land in the exact same database the HTTP tests use
(see tests/test_cli.py), not a second, disconnected `weave_api.db`.

A dedicated file (not `:memory:`) because FastAPI's TestClient runs
endpoint code in a worker thread, and an in-memory sqlite database is
private to the connection that created it -- a second thread would see an
empty database.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

TEST_DB_URL = 'sqlite:///./test.db'
settings.database_url = TEST_DB_URL

from app.core import ratelimit  # noqa: E402
from app.core.db import Base, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import ApiToken, User  # noqa: E402

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Every test starts with a fresh rate-limit budget -- otherwise
    request volume from an earlier test (or an earlier assertion in the
    same test) would bleed into the next test's 429 expectations."""
    ratelimit.rate_limiter.reset()
    yield
    ratelimit.rate_limiter.reset()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate every table between tests so row-scoping assertions (e.g.
    "a fresh user sees zero conversations") never depend on test order."""
    yield
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def make_user_with_token(
    db,
    *,
    username: str = 'alice',
    team: str | None = None,
    disabled: bool = False,
    is_admin: bool = False,
    expires_at: datetime | None = None,
) -> tuple[User, str]:
    """Seed a User plus one ApiToken for it directly against the test
    database, returning `(user, raw_token)`. The raw token is never stored
    anywhere -- callers wrap it in an `Authorization: Bearer <token>`
    header via `auth_headers()` below, exactly like a real caller would."""
    user = User(id=uuid.uuid4(), username=username, team=team, disabled=disabled, is_admin=is_admin)
    db.add(user)
    db.flush()

    raw_token = secrets.token_urlsafe(32)
    token = ApiToken(
        user_id=user.id,
        token_sha256=hash_token(raw_token),
        label='test-token',
        expires_at=expires_at,
    )
    db.add(token)
    db.commit()
    db.refresh(user)
    return user, raw_token


def auth_headers(raw_token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {raw_token}'}


@pytest.fixture
def db_session():
    gen = get_db()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


@pytest.fixture
def expired_timestamp() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)
