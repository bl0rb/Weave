"""Weave-Ingest administration CLI.

Run from `backend/` as:

    python -m app.cli backup export --out FILE
    python -m app.cli backup import --file FILE --admin-username NAME [--force]

Both `backup` subcommands read the export/import passphrase from the
WEAVE_BACKUP_PASSPHRASE environment variable rather than a --passphrase
flag or a settings field, so it never appears in a process listing, a
shell history file, or a log line -- see app/services/backup.py's module
docstring for the disaster-recovery engine these subcommands drive.

This is the same engine the admin UI's "Sicherung & Wiederherstellung" tab
drives through app/api/backup.py; the CLI exists as the documented
fallback for when the API itself is unreachable -- most importantly, right
after a fresh install, before anyone has logged in through the browser to
use the UI (see docs/betrieb.md's disaster-recovery runbook).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.models import User
from app.services import backup

_PASSPHRASE_ENV_VAR = 'WEAVE_BACKUP_PASSPHRASE'


def _require_passphrase() -> str | None:
    passphrase = os.environ.get(_PASSPHRASE_ENV_VAR)
    if not passphrase:
        print(f'error: {_PASSPHRASE_ENV_VAR} is not set', file=sys.stderr)
        return None
    return passphrase


def run_backup_export(*, out: str) -> int:
    """Export every included table + the on-disk upload/result trees to
    `out`. Fails with exit code 1 on any BackupError (currently only a
    missing/empty passphrase can occur here -- export never rejects on
    format/schema grounds the way import does)."""
    passphrase = _require_passphrase()
    if passphrase is None:
        return 1
    db = SessionLocal()
    try:
        out_path = Path(out).resolve()
        manifest = backup.export_backup(db, passphrase=passphrase, out_path=out_path)
        db.commit()
        row_total = sum(manifest.get('tables', {}).values())
        table_total = len(manifest.get('tables', {}))
        print(f'export written to {out_path} ({row_total} rows across {table_total} tables)')
        return 0
    except backup.BackupError as exc:
        db.rollback()
        print(f'error: {exc.detail}', file=sys.stderr)
        return 1
    finally:
        db.close()


def run_backup_import(*, file: str, admin_username: str, force: bool) -> int:
    """Restore `file` onto this database. `admin_username` must already
    exist on this target (e.g. the bootstrap admin created by the normal
    fresh-install flow, /auth/setup) -- it is the importing admin whose
    identity the engine's admin-identity policy applies (see
    app/services/backup.py's module docstring). Fails with exit code 1 on
    any BackupError, including a non-fresh target without --force."""
    passphrase = _require_passphrase()
    if passphrase is None:
        return 1
    db = SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.username == admin_username))
        if admin is None:
            print(
                f'error: user {admin_username!r} not found on this target -- create it first '
                '(e.g. via the normal first-run setup) before importing',
                file=sys.stderr,
            )
            return 1

        report = backup.import_backup(
            db,
            path=Path(file).resolve(),
            passphrase=passphrase,
            importing_admin_id=admin.id,
            force=force,
        )
        db.commit()

        row_total = sum(report['tables'].values())
        print(
            f'import finished: {row_total} rows across {len(report["tables"])} tables, '
            f'{report["files_restored"]} files restored, {report["requeued_releases"]} releases '
            're-queued for index rebuild'
        )
        for warning in report.get('warnings', []):
            print(f'warning: {warning}')
        if report.get('requires_relogin'):
            print('note: every existing session was invalidated by this import -- log in again')
        print(f'note: {report["indexing_note"]}')
        return 0
    except backup.TargetNotFreshError as exc:
        db.rollback()
        print(f'error: {exc.detail}', file=sys.stderr)
        for reason in exc.reasons:
            print(f'  - {reason}', file=sys.stderr)
        return 1
    except backup.BackupError as exc:
        db.rollback()
        print(f'error: {exc.detail}', file=sys.stderr)
        return 1
    finally:
        db.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='app.cli', description='Weave-Ingest administration CLI')
    subparsers = parser.add_subparsers(dest='command', required=True)

    backup_parser = subparsers.add_parser('backup', help='Disaster-recovery export/import (see app/services/backup.py)')
    backup_subparsers = backup_parser.add_subparsers(dest='backup_command', required=True)

    export_parser = backup_subparsers.add_parser('export', help='Export all Ingest data to a .weave-backup.tar.gz archive')
    export_parser.add_argument('--out', required=True, help='Path to write the archive to')

    import_parser = backup_subparsers.add_parser('import', help='Import an archive, restoring all Ingest data')
    import_parser.add_argument('--file', required=True, help='Path to a .weave-backup.tar.gz archive')
    import_parser.add_argument(
        '--admin-username',
        required=True,
        help='Username of the already-existing admin performing the import (see the admin-identity policy '
        'in app/services/backup.py)',
    )
    import_parser.add_argument('--force', action='store_true', help='Wipe and overwrite a non-fresh target')

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == 'backup' and args.backup_command == 'export':
        return run_backup_export(out=args.out)
    if args.command == 'backup' and args.backup_command == 'import':
        return run_backup_import(file=args.file, admin_username=args.admin_username, force=args.force)

    parser.error(f'unknown command {args.command!r}')  # argparse exits itself here
    return 2  # pragma: no cover -- unreachable, parser.error() calls sys.exit


if __name__ == '__main__':
    sys.exit(main())
