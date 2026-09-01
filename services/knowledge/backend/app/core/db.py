from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(database_url: str) -> dict:
    """create_engine kwargs for `database_url`.

    pool_pre_ping revalidates pooled connections on checkout so a stale or
    dropped server-side connection (DB restart, idle timeout) surfaces as a
    transparent reconnect instead of an OperationalError mid-request -- cheap
    and dialect-agnostic, so it stays on unconditionally.

    pool_size/max_overflow/pool_recycle/pool_timeout are QueuePool-specific
    (see settings.db_pool_* in app/core/config.py) and only make sense for a
    real server backend: sqlite's default pool implementation
    (SingletonThreadPool for a file DB, NullPool for :memory:) does not
    accept those kwargs at all -- passing them unconditionally would break
    every sqlite-backed deployment (local dev, and the entire pytest suite).
    Mirrors Weave-Ingest's app/database/session.py:_engine_kwargs.
    """
    kwargs: dict = {'future': True, 'pool_pre_ping': True}
    if database_url.startswith('sqlite'):
        return kwargs
    kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        pool_timeout=settings.db_pool_timeout_seconds,
    )
    return kwargs


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
