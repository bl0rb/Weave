"""Weave-API administration CLI.

Run from `backend/` as:

    python -m app.cli create-user --username alice [--team Support]
    python -m app.cli create-token --username alice [--expires-days 30]

Both subcommands print the raw bearer token to stdout EXACTLY ONCE -- only
its sha256 digest (ApiToken.token_sha256, see app/models/models.py and
app/core/auth.py) is ever persisted, so a token lost after this line is
unrecoverable and a fresh one must be minted with `create-token` instead.
There is deliberately no "list tokens" or "revoke token" subcommand yet --
out of scope for this skeleton stage (see README's Status).
"""

import argparse
import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import issue_api_token
from app.core.db import SessionLocal
from app.models.models import User


def _issue_token(db: Session, user: User, *, label: str, expires_days: int | None) -> str:
    _, raw_token = issue_api_token(db, user, label=label, expires_days=expires_days)
    db.commit()
    return raw_token


def run_create_user(*, username: str, team: str | None) -> int:
    """Create a new User plus an initial (never-expiring) API token for it.
    Fails with exit code 1 if `username` already exists -- this subcommand
    only ever creates, never updates, an existing account."""
    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.username == username))
        if existing is not None:
            print(f'error: user {username!r} already exists', file=sys.stderr)
            return 1

        user = User(username=username, team=team)
        db.add(user)
        db.commit()

        raw_token = _issue_token(db, user, label=f'cli:{username}:initial', expires_days=None)
        print(f'created user {username!r} (id={user.id})')
        print(f'token (shown once, store it now): {raw_token}')
        return 0
    finally:
        db.close()


def run_create_token(*, username: str, expires_days: int | None) -> int:
    """Issue an additional API token for an already-existing user. Fails
    with exit code 1 if `username` does not exist -- this subcommand never
    creates a user as a side effect."""
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            print(f'error: user {username!r} not found', file=sys.stderr)
            return 1

        raw_token = _issue_token(db, user, label=f'cli:{username}', expires_days=expires_days)
        print(f'token (shown once, store it now): {raw_token}')
        return 0
    finally:
        db.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='app.cli', description='Weave-API administration CLI')
    subparsers = parser.add_subparsers(dest='command', required=True)

    create_user = subparsers.add_parser('create-user', help='Create a new user and an initial API token')
    create_user.add_argument('--username', required=True)
    create_user.add_argument('--team', default=None, help='Optional team label (free text, see User.team)')

    create_token = subparsers.add_parser('create-token', help='Issue an additional API token for an existing user')
    create_token.add_argument('--username', required=True)
    create_token.add_argument('--expires-days', type=int, default=None, help='Token lifetime in days (default: never expires)')

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == 'create-user':
        return run_create_user(username=args.username, team=args.team)
    if args.command == 'create-token':
        return run_create_token(username=args.username, expires_days=args.expires_days)

    parser.error(f'unknown command {args.command!r}')  # argparse exits itself here
    return 2  # pragma: no cover -- unreachable, parser.error() calls sys.exit


if __name__ == '__main__':
    sys.exit(main())
