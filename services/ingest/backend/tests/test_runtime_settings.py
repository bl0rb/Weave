"""Paddle runtime settings live in PostgreSQL (runtime_settings), shared by
every API and worker process -- not in Redis."""

from app.database import session as session_module
from app.models.models import RuntimeSetting
from app.services import paddle_service
from conftest import TestingSessionLocal


def test_settings_persist_in_the_database_and_reach_other_processes(monkeypatch) -> None:
    monkeypatch.setattr(session_module, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(paddle_service, '_runtime_settings_cache', None)
    with TestingSessionLocal() as db:
        db.query(RuntimeSetting).delete()
        db.commit()

        paddle_service.update_paddle_settings(db, default_profile='ppocrv6_small', timeout_seconds=123)
        row = db.get(RuntimeSetting, 'paddle')
        assert row.value == {'default_profile': 'ppocrv6_small', 'timeout_seconds': '123'}

    # The worker path (no session passed) reads the same row ...
    assert paddle_service.get_paddle_settings() == {'default_profile': 'ppocrv6_small', 'timeout_seconds': 123}

    # ... and, like any other process, keeps it for a few seconds before
    # rereading -- simulate another replica changing it meanwhile.
    with TestingSessionLocal() as db:
        db.get(RuntimeSetting, 'paddle').value = {'default_profile': 'ppocrv6_tiny', 'timeout_seconds': '50'}
        db.commit()
    assert paddle_service.get_paddle_settings()['default_profile'] == 'ppocrv6_small'
    monkeypatch.setattr(paddle_service, '_RUNTIME_SETTINGS_CACHE_SECONDS', 0)
    assert paddle_service.get_paddle_settings() == {'default_profile': 'ppocrv6_tiny', 'timeout_seconds': 50}


def test_missing_settings_fall_back_to_the_configured_defaults(monkeypatch) -> None:
    monkeypatch.setattr(session_module, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(paddle_service, '_runtime_settings_cache', None)
    with TestingSessionLocal() as db:
        db.query(RuntimeSetting).delete()
        db.commit()
    assert paddle_service.get_paddle_settings() == paddle_service._default_runtime_settings()
