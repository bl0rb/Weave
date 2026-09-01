"""app/cli.py: create-user / create-token, called directly (not via
subprocess) so pytest's `capsys` fixture can capture stdout/stderr. Both
subcommands open their own `app.core.db.SessionLocal` session -- since
tests/conftest.py already pointed `settings.database_url` at the shared
test database before `app.core.db` was ever imported, that lands in the
exact same sqlite file the HTTP tests use.
"""

import uuid

from sqlalchemy import select

from app.cli import run_create_token, run_create_user
from app.models.models import ApiToken, User
from tests.conftest import hash_token


def _last_line_token(stdout: str) -> str:
    return stdout.strip().splitlines()[-1].rsplit(': ', 1)[-1]


def test_create_user_prints_token_once_and_only_its_hash_is_stored(db_session, capsys):
    username = f'cli-user-{uuid.uuid4().hex[:8]}'
    exit_code = run_create_user(username=username, team='Support')
    assert exit_code == 0

    out = capsys.readouterr().out
    assert 'token (shown once' in out
    raw_token = _last_line_token(out)

    user = db_session.scalar(select(User).where(User.username == username))
    assert user is not None
    assert user.team == 'Support'
    assert user.disabled is False

    token_row = db_session.scalar(select(ApiToken).where(ApiToken.user_id == user.id))
    assert token_row is not None
    assert token_row.expires_at is None
    # The raw token must never be persisted -- only its sha256 digest is.
    assert token_row.token_sha256 != raw_token
    assert token_row.token_sha256 == hash_token(raw_token)


def test_create_user_rejects_a_duplicate_username(capsys):
    username = f'cli-dup-{uuid.uuid4().hex[:8]}'
    assert run_create_user(username=username, team=None) == 0
    capsys.readouterr()

    exit_code = run_create_user(username=username, team=None)
    assert exit_code == 1
    assert 'already exists' in capsys.readouterr().err


def test_create_token_issues_a_second_hashed_token_for_an_existing_user(db_session, capsys):
    username = f'cli-second-{uuid.uuid4().hex[:8]}'
    assert run_create_user(username=username, team=None) == 0
    capsys.readouterr()  # drain create-user's own stdout

    exit_code = run_create_token(username=username, expires_days=30)
    assert exit_code == 0
    raw_token = _last_line_token(capsys.readouterr().out)

    user = db_session.scalar(select(User).where(User.username == username))
    tokens = db_session.scalars(select(ApiToken).where(ApiToken.user_id == user.id)).all()
    assert len(tokens) == 2

    new_token = next(t for t in tokens if t.expires_at is not None)
    assert new_token.token_sha256 == hash_token(raw_token)
    assert new_token.token_sha256 != raw_token


def test_create_token_for_an_unknown_user_fails_without_creating_one(capsys):
    exit_code = run_create_token(username='does-not-exist', expires_days=None)
    assert exit_code == 1
    assert 'not found' in capsys.readouterr().err
