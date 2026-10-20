"""0053_managed_bot_config_extras: the nullable JSON column comes and goes
with the revision and leaves managed_bots otherwise untouched (same
scaffolding as test_migrations.py)."""

from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings
from test_migrations import _alembic_config, _build_legacy_metadata


def _columns(engine) -> set[str]:
    return {column['name'] for column in inspect(engine).get_columns('managed_bots')}


def test_0053_config_extras_round_trip(tmp_path, monkeypatch):
    db_url = f'sqlite:///{tmp_path / "config_extras.db"}'
    monkeypatch.setattr(settings, 'database_url', db_url)
    engine = create_engine(db_url, future=True)
    _build_legacy_metadata().create_all(bind=engine)
    cfg = _alembic_config()
    command.stamp(cfg, '0003_job_markdown_versions')
    command.upgrade(cfg, '0052_markdown_objects')
    assert 'config_extras' not in _columns(engine)
    command.upgrade(cfg, 'head')
    assert 'config_extras' in _columns(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO managed_bots (id, name, webhook_url, collections, config_extras) "
            "VALUES ('extras-bot', 'Extras', 'https://n8n.example.test/webhook', '[]', '{\"clarify_max_options\": 5}')"
        ))
        row = conn.execute(text('SELECT config_extras FROM managed_bots')).one()
        assert '5' in str(row.config_extras)
    command.downgrade(cfg, '0052_markdown_objects')
    assert 'config_extras' not in _columns(engine)
    assert 'managed_bots' in inspect(engine).get_table_names()
    command.upgrade(cfg, 'head')
    assert 'config_extras' in _columns(engine)
    engine.dispose()
