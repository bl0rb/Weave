"""AUFGABE 2/Punkt 3: explicit DB connection-pool sizing for the shared
`engine` in app/database/session.py.

`_engine_kwargs` is exercised directly (rather than reloading the module and
inspecting the resulting `engine`/`Pool` object) so this stays independent
of which DB driver happens to be installed and never opens a real
connection -- it's the exact function app/database/session.py calls once at
import time to build the real engine, so testing it IS testing that code
path.
"""

from app.core.config import settings
from app.database.session import _engine_kwargs, engine


def test_sqlite_url_gets_no_pool_kwargs() -> None:
    """sqlite's default pool implementation (SingletonThreadPool for a file
    DB, NullPool for :memory:) doesn't accept pool_size/max_overflow/
    pool_recycle/pool_timeout at all -- passing them would break every
    sqlite-backed deployment (local dev, this entire pytest suite)."""
    kwargs = _engine_kwargs('sqlite:///./whatever.db')
    assert kwargs == {'future': True, 'pool_pre_ping': True}


def test_sqlite_memory_url_gets_no_pool_kwargs() -> None:
    kwargs = _engine_kwargs('sqlite:///:memory:')
    assert kwargs == {'future': True, 'pool_pre_ping': True}


def test_postgres_url_gets_explicit_pool_kwargs_from_settings() -> None:
    kwargs = _engine_kwargs('postgresql+psycopg://user:pass@localhost:5432/db')
    assert kwargs['pool_pre_ping'] is True
    assert kwargs['future'] is True
    assert kwargs['pool_size'] == settings.db_pool_size
    assert kwargs['max_overflow'] == settings.db_max_overflow
    assert kwargs['pool_recycle'] == settings.db_pool_recycle_seconds
    assert kwargs['pool_timeout'] == settings.db_pool_timeout_seconds


def test_postgres_pool_kwargs_default_values() -> None:
    # The concrete defaults AUFGABE 2 asks for -- pinned here so a future
    # accidental change in app/core/config.py is caught by a failing test,
    # not just a changed .env.example comment.
    kwargs = _engine_kwargs('postgresql+psycopg://user:pass@localhost:5432/db')
    assert kwargs['pool_size'] == 10
    assert kwargs['max_overflow'] == 10
    assert kwargs['pool_recycle'] == 3600
    assert kwargs['pool_timeout'] == 10


def test_postgres_pool_kwargs_respect_overridden_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'db_pool_size', 25)
    monkeypatch.setattr(settings, 'db_max_overflow', 5)
    monkeypatch.setattr(settings, 'db_pool_recycle_seconds', 1200)
    monkeypatch.setattr(settings, 'db_pool_timeout_seconds', 30)

    kwargs = _engine_kwargs('postgresql+psycopg://user:pass@localhost:5432/db')

    assert kwargs['pool_size'] == 25
    assert kwargs['max_overflow'] == 5
    assert kwargs['pool_recycle'] == 1200
    assert kwargs['pool_timeout'] == 30


def test_module_level_engine_was_built_without_pool_kwargs_in_test_env() -> None:
    """Sanity check that the real module-level `engine` (built once at
    import time against settings.database_url, which resolves to sqlite in
    this test environment -- see app/core/config.py's _build_database_url)
    didn't blow up constructing itself, and really is sqlite-backed here."""
    assert engine.url.drivername.startswith('sqlite')
