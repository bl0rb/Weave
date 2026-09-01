"""Verifies the 0001_init migration's upgrade/downgrade round-trip on
SQLite. Only one migration exists so far, so `alembic upgrade head` from
empty just works with no pre-existing-schema scaffolding needed (same
reasoning as Weave-Knowledge's own tests/test_migrations.py).
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    # Built programmatically (not Config('alembic.ini')) so it doesn't
    # depend on the process cwd -- alembic.ini's `script_location = alembic`
    # is only correct relative to the backend/ directory, and tests may run
    # from the repo root.
    cfg = Config()
    cfg.set_main_option('script_location', str(BACKEND_DIR / 'alembic'))
    return cfg


def test_0001_init_migration_upgrade_downgrade_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / 'migration_scratch.db'
    db_url = f'sqlite:///{db_path}'
    monkeypatch.setattr(settings, 'database_url', db_url)

    cfg = _alembic_config()

    command.upgrade(cfg, 'head')

    engine = create_engine(db_url, future=True)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for expected in ('users', 'api_tokens', 'conversations', 'messages'):
        assert expected in tables, f'{expected} missing after upgrade'

    user_columns = {c['name'] for c in insp.get_columns('users')}
    assert {'id', 'username', 'team', 'disabled', 'is_admin', 'created_at'} <= user_columns

    token_columns = {c['name'] for c in insp.get_columns('api_tokens')}
    assert {'id', 'user_id', 'token_sha256', 'label', 'created_at', 'expires_at', 'last_used_at'} <= token_columns

    conversation_columns = {c['name'] for c in insp.get_columns('conversations')}
    assert {'id', 'user_id', 'bot_id', 'title', 'created_at', 'updated_at'} <= conversation_columns

    message_columns = {c['name'] for c in insp.get_columns('messages')}
    assert {'id', 'conversation_id', 'role', 'content', 'sources', 'trace', 'created_at'} <= message_columns

    token_fks = insp.get_foreign_keys('api_tokens')
    assert any(
        fk['referred_table'] == 'users' and fk['constrained_columns'] == ['user_id'] for fk in token_fks
    ), token_fks

    message_fks = insp.get_foreign_keys('messages')
    assert any(
        fk['referred_table'] == 'conversations' and fk['constrained_columns'] == ['conversation_id']
        for fk in message_fks
    ), message_fks

    # --- downgrade: everything should disappear ---
    command.downgrade(cfg, 'base')
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for removed in ('users', 'api_tokens', 'conversations', 'messages'):
        assert removed not in tables, f'{removed} still present after downgrade'

    # --- re-upgrade: should cleanly re-apply from empty ---
    command.upgrade(cfg, 'head')
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for expected in ('users', 'api_tokens', 'conversations', 'messages'):
        assert expected in tables


def test_0003_oidc_sessions_migration_upgrade_downgrade_round_trip(tmp_path, monkeypatch):
    """Verifies 0003_oidc_sessions (users.oidc_subject + the new `sessions`
    table, app/api/auth.py's OIDC login) on top of an already-upgraded
    0001/0002 schema -- same round-trip discipline as
    test_0001_init_migration_upgrade_downgrade_round_trip above, just
    starting from `0002_add_users_is_admin` instead of empty."""
    db_path = tmp_path / 'migration_scratch_oidc.db'
    db_url = f'sqlite:///{db_path}'
    monkeypatch.setattr(settings, 'database_url', db_url)

    cfg = _alembic_config()
    command.upgrade(cfg, '0002_add_users_is_admin')

    command.upgrade(cfg, 'head')

    engine = create_engine(db_url, future=True)
    insp = inspect(engine)
    assert 'sessions' in insp.get_table_names()

    user_columns = {c['name'] for c in insp.get_columns('users')}
    assert 'oidc_subject' in user_columns

    session_columns = {c['name'] for c in insp.get_columns('sessions')}
    assert {'id', 'user_id', 'token_hash', 'created_at', 'expires_at'} <= session_columns

    session_fks = insp.get_foreign_keys('sessions')
    assert any(
        fk['referred_table'] == 'users' and fk['constrained_columns'] == ['user_id'] for fk in session_fks
    ), session_fks

    unique_constraints = {c['column_names'][0] for c in insp.get_unique_constraints('sessions')}
    unique_indexes = {idx['column_names'][0] for idx in insp.get_indexes('sessions') if idx['unique']}
    assert 'token_hash' in unique_constraints or 'token_hash' in unique_indexes

    # --- downgrade back to 0002: the OIDC additions disappear, the
    # original schema survives untouched ---
    command.downgrade(cfg, '0002_add_users_is_admin')
    insp = inspect(engine)
    assert 'sessions' not in insp.get_table_names()
    user_columns = {c['name'] for c in insp.get_columns('users')}
    assert 'oidc_subject' not in user_columns
    assert 'is_admin' in user_columns  # 0002's own column survives the downgrade

    # --- re-upgrade: should cleanly re-apply from 0002 ---
    command.upgrade(cfg, 'head')
    insp = inspect(engine)
    assert 'sessions' in insp.get_table_names()


def test_0004_session_exchange_codes_migration_upgrade_downgrade_round_trip(tmp_path, monkeypatch):
    """Verifies 0004_session_exchange_codes (the cross-origin OIDC
    post-login handoff's one-time-code table, app/api/auth.py's
    `SessionExchangeCode`) on top of an already-upgraded 0003 schema --
    same round-trip discipline as the 0001/0003 tests above."""
    db_path = tmp_path / 'migration_scratch_session_exchange.db'
    db_url = f'sqlite:///{db_path}'
    monkeypatch.setattr(settings, 'database_url', db_url)

    cfg = _alembic_config()
    command.upgrade(cfg, '0003_oidc_sessions')

    command.upgrade(cfg, 'head')

    engine = create_engine(db_url, future=True)
    insp = inspect(engine)
    assert 'session_exchange_codes' in insp.get_table_names()

    columns = {c['name'] for c in insp.get_columns('session_exchange_codes')}
    assert {'id', 'user_id', 'code_hash', 'created_at', 'expires_at', 'used_at'} <= columns

    fks = insp.get_foreign_keys('session_exchange_codes')
    assert any(
        fk['referred_table'] == 'users' and fk['constrained_columns'] == ['user_id'] for fk in fks
    ), fks

    unique_constraints = {c['column_names'][0] for c in insp.get_unique_constraints('session_exchange_codes')}
    unique_indexes = {
        idx['column_names'][0] for idx in insp.get_indexes('session_exchange_codes') if idx['unique']
    }
    assert 'code_hash' in unique_constraints or 'code_hash' in unique_indexes

    # --- downgrade back to 0003: only this migration's own table
    # disappears, the rest of the schema survives untouched ---
    command.downgrade(cfg, '0003_oidc_sessions')
    insp = inspect(engine)
    assert 'session_exchange_codes' not in insp.get_table_names()
    assert 'sessions' in insp.get_table_names()  # 0003's own table survives the downgrade

    # --- re-upgrade: should cleanly re-apply from 0003 ---
    command.upgrade(cfg, 'head')
    insp = inspect(engine)
    assert 'session_exchange_codes' in insp.get_table_names()


def test_migration_history_has_a_single_head():
    from alembic.script import ScriptDirectory

    cfg = _alembic_config()
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f'alembic history has diverged into {len(heads)} heads: {heads}'


def test_migration_revision_ids_fit_alembic_version_column():
    """alembic_version.version_num is a VARCHAR(32) in Postgres; sqlite
    doesn't enforce that, so an over-long revision id would pass every test
    here and only blow up against a real Postgres deployment."""
    from alembic.script import ScriptDirectory

    cfg = _alembic_config()
    too_long = [rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions() if len(rev.revision) > 32]
    assert not too_long, f'revision ids exceed alembic_version VARCHAR(32): {too_long}'
