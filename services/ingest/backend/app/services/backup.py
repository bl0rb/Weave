"""Disaster-recovery export/import engine for all of Ingest's persisted data.

Produces and consumes a single `.weave-backup.tar.gz` archive: every table
in `app.models.models.Base.metadata` except a short, named exclusion list of
transient state (see `EXCLUDED_TABLES`), every `*_encrypted` column
re-encrypted under a passphrase-derived key instead of the server's own
`SECRET_KEY` (see `ENCRYPTED_COLUMNS`), every large binary column as its own
`blobs/<table>/<pk>.bin` archive entry (see `LargeBinary` handling below),
and the on-disk upload/result trees under `files/uploads/...` and
`files/results/...`.

Deliberately NOT exported: the knowledge/retrieval index itself (there is
nothing to export -- Knowledge is a separate service with its own storage);
`import_backup` instead re-queues every still-live `DocumentRelease` through
the existing publication outbox so Knowledge re-indexes everything on its
own once a worker is running, and best-effort dispatches
`notify_collection_registry_changed` for every affected collection (see
`_requeue_releases_for_index_rebuild`).

Two deliberate deviations from an earlier draft of this feature's design
that assumed a single `settings.storage_dir` setting:

1. `app/core/config.py` has no `storage_dir` -- it has two independently
   configurable roots, `uploads_dir` and `results_dir`. This engine treats
   them as two separate trees (`files/uploads/...`, `files/results/...`)
   rather than inventing a new settings field for a "smallest change" fix.
2. The archive's own storage directory (`backups_dir()`) is a *sibling* of
   both roots (`uploads_dir.parent / 'backups'`), not nested inside either
   -- so it never needs to be explicitly excluded while walking the trees,
   unlike a single-storage_dir layout would require.

Admin identity policy (see `_is_admin_identity_match` and
`import_backup`): every exported user (including former admins) is restored
with its original id, role and password hash. If any exported user's
username OR e-mail (case-insensitive, matching `ix_users_email_lower')
matches the importing admin's own account, that exported row's role is
forced to admin and its password hash is overwritten with the importing
admin's *current* hash, so the person keeps working with the credentials
they just used to log in -- its id is otherwise left untouched. Otherwise
the importing admin's pre-import account is inserted alongside the restored
data as an additional admin. Either way, `import_backup` always wipes the
`users` table before re-populating it (see below), which -- in a real
Postgres deployment -- cascades to `sessions` and `api_tokens` even though
neither table is itself in the import's table list. Chasing the narrower
"keep this one session alive across the wipe" behavior would mean converting
the wipe into a diff instead of a full DELETE-then-INSERT for a purely
cosmetic win, so this engine picks the smaller, safe option instead: every
import invalidates every existing session, and `report['requires_relogin']`
is always `True` -- the API/UI must tell the importing admin to log in
again afterwards.

Table ordering: `Base.metadata.sorted_tables` already gives a valid
dependency order (parents before children) for every foreign key in this
schema except `jobs.previous_job_id`, which self-references `jobs`. Rather
than disabling foreign-key enforcement for the whole import (which needs
superuser rights on Postgres and cannot be toggled mid-transaction on
SQLite), self-referential columns are nulled on first insert and fixed up
with a second UPDATE pass once every row of that table exists -- see
`_self_referential_columns`.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import DateTime, LargeBinary, Table, delete, func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import (
    Base,
    BackupRun,
    DocumentRelease,
    ImportRun,
    ImportRunStatus,
    KnowledgeWithdrawal,
    User,
    UserRole,
)
from app.services import security
from app.workers import publication_tasks

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
PBKDF2_ITERATIONS = 200_000
# Encrypted (never stored in plaintext, never logged) and compared byte-for-
# byte against the decrypted `manifest.passphrase_check` value -- the only
# purpose of this constant is that comparison, its content is not meaningful.
PASSPHRASE_CHECK_PLAINTEXT = b'weave-ingest-disaster-recovery-passphrase-check-v1'

_ROW_BATCH_SIZE = 500


class BackupError(Exception):
    """Base for every recoverable disaster-recovery engine error.

    `detail` is always a German, user-facing message -- the API layer is
    expected to surface it verbatim (design requirement: "all errors German
    and specific").
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class WrongPassphraseError(BackupError):
    pass


class IncompatibleArchiveError(BackupError):
    pass


class TargetNotFreshError(BackupError):
    def __init__(self, detail: str, reasons: list[str]) -> None:
        super().__init__(detail)
        self.reasons = reasons


# --- Table policy -------------------------------------------------------

# Transient state excluded from every export/import cycle, one reason each
# (project convention: a one-line "why" per exclusion-list entry). Notably
# NOT here: a "lockouts" or "rate limit" table -- neither exists in this
# schema (lockout fields are three plain columns on `users`; rate limiting
# is Redis-only) -- and `alembic_version`, which is structurally excluded
# already because it isn't part of `Base.metadata` at all.
EXCLUDED_TABLES: dict[str, str] = {
    'sessions': (
        'login session cookies are worthless outside the process that '
        'issued them, and every import invalidates existing sessions '
        'anyway by wiping `users` (see this module\'s docstring)'
    ),
    'login_handoff_codes': (
        'single-use, ~1 minute TTL cross-service login codes -- always '
        'expired by the time any archive could realistically be restored'
    ),
    'backup_runs': (
        'a backup must not contain a record of the run that produced it, '
        'or resurrect another archive/target\'s own run history'
    ),
}

# table -> {column_name: (decrypt_fn, encrypt_fn)} for every `*_encrypted`
# column, reusing the existing purpose-bound Fernet helpers in
# app/services/security.py. `embedding_api_key_encrypted` and
# `rerank_api_key_encrypted` intentionally share one HKDF key domain/helper
# pair -- confirmed at their call sites in app/api/retrieval_provider.py,
# not an oversight.
ENCRYPTED_COLUMNS: dict[str, dict[str, tuple[Callable[[str], str], Callable[[str], str]]]] = {
    'auth_providers': {
        'client_secret_encrypted': (security.decrypt_client_secret, security.encrypt_client_secret),
    },
    'import_sources': {
        'credential_encrypted': (security.decrypt_import_credential, security.encrypt_import_credential),
    },
    'vl_connections': {
        'api_key_encrypted': (security.decrypt_vl_api_key, security.encrypt_vl_api_key),
    },
    'chat_provider_config': {
        'api_key_encrypted': (security.decrypt_chat_provider_api_key, security.encrypt_chat_provider_api_key),
    },
    'retrieval_provider_config': {
        'embedding_api_key_encrypted': (
            security.decrypt_retrieval_provider_api_key, security.encrypt_retrieval_provider_api_key,
        ),
        'rerank_api_key_encrypted': (
            security.decrypt_retrieval_provider_api_key, security.encrypt_retrieval_provider_api_key,
        ),
    },
    'managed_bots': {
        'auth_token_encrypted': (security.decrypt_managed_bot_auth_token, security.encrypt_managed_bot_auth_token),
    },
    'webhook_connections': {
        'secret_encrypted': (security.decrypt_webhook_secret, security.encrypt_webhook_secret),
    },
}

# jobs.upload_path/result_path are always written absolute (see
# save_upload()/build_result_path() in app/services/storage.py) -- exported
# as relative-to-root so a restore onto a target with a different
# uploads_dir/results_dir still resolves. table -> {column: settings attr}.
PATH_COLUMNS: dict[str, dict[str, str]] = {
    'jobs': {'upload_path': 'uploads_dir', 'result_path': 'results_dir'},
}

# NOT normalized (documented limitation, not a silent gap): processing_info
# JSON may contain an absolute editor.latest_result_path string (see
# app/services/publications._markdown_from_job) -- it round-trips verbatim.
# A job restored onto a target with a different results_dir falls back to
# result_path/result_markdown for that one field, which is always correct;
# only the editor's "jump to latest edited version" convenience link can go
# stale, and only when uploads_dir/results_dir change between export and
# import.


def backups_dir() -> Path:
    """Where export archives (and incoming import uploads) belong.

    A sibling of `uploads_dir`/`results_dir` (see module docstring) rather
    than nested inside either -- so `_iter_storage_files` never has to
    special-case excluding it.
    """
    return settings.uploads_dir.parent / 'backups'


# --- Passphrase-derived encryption ---------------------------------------

def _derive_export_fernet_key(passphrase: str, salt: bytes) -> bytes:
    key_material = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS,
    ).derive(passphrase.encode('utf-8'))
    return base64.urlsafe_b64encode(key_material)


def _read_manifest(tar: tarfile.TarFile) -> dict:
    try:
        member = tar.extractfile('manifest.json')
    except KeyError:
        member = None
    if member is None:
        raise IncompatibleArchiveError(
            'Das Archiv enthält keine manifest.json und ist kein gültiges Sicherungsarchiv.'
        )
    return json.loads(member.read().decode('utf-8'))


def _validate_format(manifest: dict) -> None:
    if manifest.get('format_version') != FORMAT_VERSION:
        raise IncompatibleArchiveError(
            f'Nicht unterstützte Archivversion: {manifest.get("format_version")!r} '
            f'(erwartet: {FORMAT_VERSION}).'
        )


def _validate_passphrase(manifest: dict, passphrase: str) -> Fernet:
    kdf = manifest.get('kdf') or {}
    salt_b64 = kdf.get('salt')
    if not salt_b64 or manifest.get('encryption') != 'passphrase-fernet-v1':
        raise IncompatibleArchiveError('Das Archiv hat ein unbekanntes Verschlüsselungsformat.')
    fernet = Fernet(_derive_export_fernet_key(passphrase, base64.b64decode(salt_b64)))
    try:
        plaintext = fernet.decrypt(str(manifest.get('passphrase_check', '')).encode('utf-8'))
    except InvalidToken as exc:
        raise WrongPassphraseError('Die Passphrase ist falsch.') from exc
    if plaintext != PASSPHRASE_CHECK_PLAINTEXT:
        raise WrongPassphraseError('Die Passphrase ist falsch.')
    return fernet


def _current_alembic_revision(db: Session) -> str | None:
    """The target's own currently-applied Alembic revision, used as a proxy
    for "head" on both sides of the compatibility check (see module
    docstring's design-doc discrepancy note): avoids loading an Alembic
    `Config`/`ScriptDirectory` inside the running API process, and after a
    normal startup (migrations run once before the app serves traffic) the
    applied revision already equals head anyway. Returns None if the table
    doesn't exist yet (e.g. a test database created via
    `Base.metadata.create_all` rather than real Alembic migrations) -- the
    compatibility check is skipped rather than failing closed in that case.
    """
    try:
        return db.execute(text('SELECT version_num FROM alembic_version')).scalar()
    except Exception:
        return None


def _revision_ordinal(revision: str | None) -> int | None:
    """This repo's revisions are named `NNNN_description` in strictly
    increasing numeric order (see e.g. 0031_backup_runs's own docstring) --
    the leading number is a total order good enough for "is the archive
    older than or equal to the target", without needing an Alembic
    `ScriptDirectory` walk."""
    if not revision:
        return None
    prefix = revision.split('_', 1)[0]
    return int(prefix) if prefix.isdigit() else None


def _check_alembic_compatible(archive_revision: str | None, target_revision: str | None) -> None:
    archive_ordinal = _revision_ordinal(archive_revision)
    target_ordinal = _revision_ordinal(target_revision)
    if archive_ordinal is None or target_ordinal is None:
        logger.warning(
            'could not compare alembic revisions (archive=%r, target=%r); skipping the compatibility check',
            archive_revision, target_revision,
        )
        return
    if archive_ordinal > target_ordinal:
        raise IncompatibleArchiveError(
            f'Das Archiv stammt von einer neueren Datenbank-Version (Migration {archive_revision}) als '
            f'dieses Ziel (Migration {target_revision}). Bitte zuerst die Ziel-Installation aktualisieren.'
        )


# --- Target freshness ------------------------------------------------------

def target_state(db: Session) -> dict:
    """{'fresh': bool, 'reasons': [German strings]} -- whether `import_backup`
    would refuse this target without `force=True`.

    Freshness must match exactly what `import_backup`'s wipe step touches --
    every table in `_exported_tables()` -- not just a hand-picked subset of
    "interesting" models, or a target with real pre-existing data in a table
    outside that subset (e.g. a configured `AuthProvider`) would be reported
    'fresh' and get silently wiped without `force=True`. The single
    exception is the bootstrap admin's own row in `users`, which is expected
    to exist on every freshly-installed target and is not itself a sign of
    non-freshness.
    """
    # Friendlier, specific wording for the tables an admin is most likely to
    # recognize; every other importable table still gets a generic reason
    # below so freshness always matches the wipe exactly.
    friendly_reasons = {
        'jobs': 'Es sind bereits Dokumente (Jobs) vorhanden.',
        'collections': 'Es sind bereits Sammlungen vorhanden.',
        'import_sources': 'Es sind bereits Confluence-Quellen vorhanden.',
        'managed_bots': 'Es sind bereits verwaltete Bots vorhanden.',
        'technical_identities': 'Es sind bereits technische Identitäten vorhanden.',
    }
    reasons: list[str] = []
    for table in _exported_tables():
        count = db.scalar(select(func.count()).select_from(table)) or 0
        if table.name == 'users':
            if count > 1:
                reasons.append(
                    f'Es sind bereits {count} Benutzerkonten vorhanden (mehr als der Bootstrap-Administrator).'
                )
            continue
        if count > 0:
            reasons.append(friendly_reasons.get(table.name, f'Tabelle "{table.name}" enthält bereits {count} Zeile(n).'))
    return {'fresh': not reasons, 'reasons': reasons}


# --- Table helpers ----------------------------------------------------------

def _exported_tables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name not in EXCLUDED_TABLES]


def _self_referential_columns(table: Table) -> list[str]:
    names = []
    for column in table.columns:
        for fk in column.foreign_keys:
            if fk.column.table is table:
                names.append(column.name)
    return names


def _pk_values(table: Table, row: dict) -> dict:
    return {column.name: row[column.name] for column in table.primary_key.columns}


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    tar.addfile(info, io.BytesIO(data))


# --- Path normalization ------------------------------------------------------

def _normalize_path(raw: str, root: Path) -> str:
    """Absolute on-disk path -> path relative to `root`, for storage in the
    archive. Falls back to the raw value (kept absolute) if it can't be made
    relative -- `_reanchor_path` on the way back in handles that fallback
    explicitly rather than silently mis-resolving it."""
    try:
        root_resolved = root.resolve()
        candidate = Path(raw)
        resolved = candidate.resolve() if candidate.is_absolute() else (root_resolved / candidate).resolve()
        return str(resolved.relative_to(root_resolved))
    except (OSError, ValueError):
        return raw


def _reanchor_path(value: str, root: Path) -> str:
    """Inverse of `_normalize_path`, on the target's own root."""
    candidate = Path(value)
    if candidate.is_absolute():
        # Normalization couldn't make it relative at export time (e.g. it
        # pointed outside uploads_dir/results_dir) -- keep it as-is; it will
        # not resolve on this target either, but that was already true of
        # the source archive and is surfaced as a warning by the caller.
        return value
    return str((root / candidate).resolve())


# --- Row <-> JSONL encoding ---------------------------------------------------

def _encode_row(table: Table, mapping, tar: tarfile.TarFile, export_fernet: Fernet) -> dict:
    data: dict = {}
    for column in table.columns:
        value = mapping[column.name]
        if isinstance(column.type, LargeBinary):
            if value is None:
                data[column.name] = None
            else:
                pk = '-'.join(str(mapping[c.name]) for c in table.primary_key.columns)
                member_path = f'blobs/{table.name}/{pk}.bin'
                _add_bytes(tar, member_path, value)
                data[column.name] = {'$blob': member_path}
            continue
        if isinstance(value, datetime):
            data[column.name] = value.isoformat()
            continue
        data[column.name] = value

    for column_name, (decrypt_fn, _encrypt_fn) in ENCRYPTED_COLUMNS.get(table.name, {}).items():
        if data.get(column_name):
            try:
                plaintext = decrypt_fn(data[column_name])
            except ValueError as exc:
                raise BackupError(
                    f'Feld {table.name}.{column_name} konnte mit dem aktuellen Schlüssel nicht entschlüsselt '
                    'werden -- Export abgebrochen.'
                ) from exc
            data[column_name] = export_fernet.encrypt(plaintext.encode('utf-8')).decode('utf-8')

    for column_name, root_attr in PATH_COLUMNS.get(table.name, {}).items():
        if data.get(column_name):
            data[column_name] = _normalize_path(data[column_name], getattr(settings, root_attr))

    return data


def _decode_row(
    table: Table,
    data: dict,
    tar: tarfile.TarFile,
    archive_fernet: Fernet,
    warnings: list[str],
) -> dict:
    known_columns = {column.name for column in table.columns}
    for key in data:
        if key not in known_columns:
            message = f'Tabelle {table.name}: unbekannte Spalte "{key}" im Archiv wurde ignoriert.'
            if message not in warnings:
                warnings.append(message)

    row: dict = {}
    for column in table.columns:
        if column.name not in data:
            continue  # column default / server_default applies
        raw = data[column.name]
        if isinstance(column.type, LargeBinary):
            if raw is None:
                row[column.name] = None
            elif isinstance(raw, dict) and '$blob' in raw:
                try:
                    extracted = tar.extractfile(raw['$blob'])
                except KeyError as exc:
                    raise IncompatibleArchiveError(
                        'Das Archiv ist unvollständig oder beschädigt (fehlender Blob-Eintrag).'
                    ) from exc
                row[column.name] = extracted.read() if extracted is not None else None
            else:
                row[column.name] = None
            continue
        if isinstance(column.type, DateTime) and isinstance(raw, str):
            row[column.name] = datetime.fromisoformat(raw)
            continue
        row[column.name] = raw

    for column_name, (_decrypt_fn, encrypt_fn) in ENCRYPTED_COLUMNS.get(table.name, {}).items():
        if row.get(column_name):
            try:
                plaintext = archive_fernet.decrypt(row[column_name].encode('utf-8')).decode('utf-8')
            except InvalidToken as exc:
                raise WrongPassphraseError(
                    'Ein verschlüsseltes Feld konnte mit dieser Passphrase nicht entschlüsselt werden.'
                ) from exc
            row[column_name] = encrypt_fn(plaintext)

    for column_name, root_attr in PATH_COLUMNS.get(table.name, {}).items():
        if row.get(column_name):
            row[column_name] = _reanchor_path(row[column_name], getattr(settings, root_attr))

    return row


# --- On-disk file tree -------------------------------------------------------

def _iter_storage_files() -> list[tuple[Path, str]]:
    backups = backups_dir().resolve()
    pairs: list[tuple[Path, str]] = []
    for root, prefix in ((settings.uploads_dir, 'uploads'), (settings.results_dir, 'results')):
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for entry in sorted(root_resolved.rglob('*')):
            if not entry.is_file():
                continue
            if entry == backups or backups in entry.parents:
                continue
            pairs.append((entry, f'files/{prefix}/{entry.relative_to(root_resolved).as_posix()}'))
    return pairs


def _restore_files(tar: tarfile.TarFile) -> int:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    roots = {'uploads': settings.uploads_dir.resolve(), 'results': settings.results_dir.resolve()}
    restored = 0
    for member in tar.getmembers():
        if not member.isfile() or not member.name.startswith('files/'):
            continue
        parts = member.name.split('/', 2)
        if len(parts) != 3 or parts[1] not in roots:
            continue
        root = roots[parts[1]]
        target = (root / parts[2]).resolve()
        # Defense in depth against a path-traversal archive member: this
        # engine only ever writes members it built itself in export_backup,
        # but an admin-uploaded .tar.gz is untrusted input at the API
        # boundary and this function has no way to tell the two apart.
        if target != root and root not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        extracted = tar.extractfile(member)
        if extracted is None:
            continue
        with target.open('wb') as handle:
            handle.write(extracted.read())
        restored += 1
    return restored


# --- Export -------------------------------------------------------------

def export_backup(
    db: Session,
    *,
    passphrase: str,
    out_path: Path,
    progress_cb: Callable[[str, int], None] | None = None,
) -> dict:
    """Stream every included table + the on-disk upload/result trees into a
    single encrypted archive at `out_path`. Returns the manifest that was
    written into it.

    Table rows are fetched from the database in batches (`yield_per`, see
    `_ROW_BATCH_SIZE`) rather than loaded all at once; each table's JSONL is
    staged to a temp file and then streamed into the tar via `tarfile.add`
    (which reads it back in chunks), so no single table's serialized text
    needs to be held in memory as one block, and file-tree entries are added
    directly from disk. The one bounded exception is a large-blob column's
    bytes (`jobs.upload_content`, `job_artifacts.content`,
    `mail_messages.raw_content`): one row's blob is held in memory for the
    duration of writing its own archive entry, which is what SQLAlchemy
    already handed back as a single `bytes` value for that row.
    """
    progress_cb = progress_cb or (lambda table, rows_done: None)
    tables = _exported_tables()

    salt = os.urandom(16)
    export_fernet = Fernet(_derive_export_fernet_key(passphrase, salt))
    passphrase_check = export_fernet.encrypt(PASSPHRASE_CHECK_PLAINTEXT).decode('utf-8')

    manifest = {
        'format_version': FORMAT_VERSION,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source': {
            'alembic_revision': _current_alembic_revision(db),
            'instance_name': settings.app_name,
        },
        'tables': {table.name: (db.scalar(select(func.count()).select_from(table)) or 0) for table in tables},
        'encryption': 'passphrase-fernet-v1',
        'kdf': {
            'algorithm': 'pbkdf2-hmac-sha256',
            'iterations': PBKDF2_ITERATIONS,
            'salt': base64.b64encode(salt).decode('ascii'),
        },
        'passphrase_check': passphrase_check,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_name(out_path.name + '.part')
    with tempfile.TemporaryDirectory(prefix='weave-backup-export-') as stage_dir:
        stage = Path(stage_dir)
        with tarfile.open(tmp_out, 'w:gz') as tar:
            _add_bytes(tar, 'manifest.json', json.dumps(manifest, indent=2).encode('utf-8'))

            for table in tables:
                rows_done = 0
                jsonl_path = stage / f'{table.name}.jsonl'
                with jsonl_path.open('w', encoding='utf-8') as handle:
                    result = db.execute(select(table), execution_options={'yield_per': _ROW_BATCH_SIZE})
                    for mapping in result.mappings():
                        data = _encode_row(table, mapping, tar, export_fernet)
                        handle.write(json.dumps(data, ensure_ascii=False))
                        handle.write('\n')
                        rows_done += 1
                        if rows_done % _ROW_BATCH_SIZE == 0:
                            progress_cb(table.name, rows_done)
                tar.add(str(jsonl_path), arcname=f'tables/{table.name}.jsonl')
                jsonl_path.unlink(missing_ok=True)
                progress_cb(table.name, rows_done)

            files_written = 0
            for absolute, arcname in _iter_storage_files():
                tar.add(str(absolute), arcname=arcname)
                files_written += 1
            progress_cb('files', files_written)

    tmp_out.replace(out_path)
    return manifest


# --- Inspect (validate without importing) ------------------------------------

def inspect_backup(path: Path, passphrase: str) -> dict:
    """Validate an archive's format and passphrase WITHOUT importing
    anything -- the fast-fail check the API runs synchronously before it
    even starts a background import run."""
    with tarfile.open(path, 'r:gz') as tar:
        manifest = _read_manifest(tar)
        _validate_format(manifest)
        _validate_passphrase(manifest, passphrase)
    return manifest


# --- Admin identity policy ---------------------------------------------------

def _is_admin_identity_match(row: dict, importing_admin: dict) -> bool:
    """See module docstring's "Admin identity policy" section. True if `row`
    is an exported user whose username OR e-mail (case-insensitive) matches
    the importing admin's own account."""
    username = row.get('username')
    email = row.get('email')
    same_username = isinstance(username, str) and username.strip().lower() == importing_admin['username'].strip().lower()
    same_email = isinstance(email, str) and email.strip().lower() == importing_admin['email'].strip().lower()
    return same_username or same_email


# --- Transient/lease state reset --------------------------------------------

def _reset_running_import_runs(db: Session) -> None:
    """A restored ImportRun that was RUNNING has no live worker resuming it
    -- mark it FAILED with a note instead of leaving a run stuck "running"
    forever. FINISHED/FAILED/CANCELLED/PENDING rows are left untouched."""
    table = ImportRun.__table__
    db.execute(
        table.update()
        .where(table.c.status == ImportRunStatus.RUNNING.value)
        .values(
            status=ImportRunStatus.FAILED.value,
            error_message='Abgebrochen durch Wiederherstellung aus einer Sicherung.',
        )
    )


def _restamp_backup_run_creators(db: Session, orphaned_creators: dict[str, str], final_admin_id: str) -> None:
    """Re-point `backup_runs.created_by` for every HISTORICAL run (not the
    one currently executing, which is not written until after this returns
    -- see this module's API/CLI callers) whose original creator id did not
    survive the users-table wipe-and-reinsert this import just performed.

    `backup_runs` is excluded from every export/import cycle (see
    `EXCLUDED_TABLES`), so its rows are never themselves deleted or
    reinserted here -- but its `created_by` FK (`ondelete='SET NULL'`)
    still gets nulled by the database the moment step 1 deletes the `users`
    row it pointed at, for every prior run, not just the run this import is
    part of. Left alone, that would silently erase the audit trail of who
    performed every earlier export/import on this target across a routine
    re-import (a repeat DR drill, a rollback). Re-pointing every such
    orphaned row to the importing admin's own post-import id keeps that
    history attributable instead of quietly turning to NULL.
    """
    table = BackupRun.__table__
    surviving_ids = {row[0] for row in db.execute(select(User.__table__.c.id))}
    for run_id, original_creator_id in orphaned_creators.items():
        if original_creator_id in surviving_ids:
            continue  # untouched by the cascade -- still points at a real row
        db.execute(table.update().where(table.c.id == run_id).values(created_by=final_admin_id))


def requeue_releases_for_index_rebuild(db: Session) -> int:
    """Re-queue delivery for every DocumentRelease whose job has NOT been
    withdrawn from Knowledge, through the existing publication outbox (see
    app/services/publications.py, app/workers/publication_tasks.py) -- the
    stored event payload/markdown snapshot digest is left untouched, only
    the outbox state (`status`, `attempts`, `next_attempt_at`) is reset, so
    Knowledge re-indexes everything the next time a worker processes the
    outbox with no separate rebuild code path.

    Every restored release's lease belonged to a worker process that no
    longer exists, so lease fields are cleared unconditionally -- including
    for withdrawn releases, which are intentionally left out of the
    status/attempts reset below.

    This only sets DB rows to pending; it does NOT call
    `reconcile_due_releases()` or dispatch a Celery task itself. Actual
    redelivery still needs a running ingest worker -- see this function's
    caller for the report note the API/UI must surface when no worker is
    running (the harness's own local stack does not run one). It DOES,
    however, best-effort dispatch `notify_collection_registry_changed` for
    every collection with a requeued release -- same
    `publication_configured()`-guarded, try/except-wrapped Celery `.delay`
    pattern as every other call site (see app/api/routes.py,
    app/api/auth.py) -- so Knowledge's own registry cache is nudged
    immediately instead of only picking up the change on its next periodic
    poll (an already-documented fallback, not this function's job to rely
    on when a cheap nudge is just as easy to send).
    """
    releases = DocumentRelease.__table__
    db.execute(releases.update().values(lease_token=None, lease_until=None))
    withdrawn_job_ids = select(KnowledgeWithdrawal.job_id)
    eligible = releases.c.job_id.not_in(withdrawn_job_ids)

    collection_slugs: set[str] = set()
    for (payload,) in db.execute(select(releases.c.payload).where(eligible)):
        if isinstance(payload, dict):
            slug = (payload.get('frontmatter') or {}).get('collection')
            if isinstance(slug, str) and slug:
                collection_slugs.add(slug)

    result = db.execute(
        releases.update()
        .where(eligible)
        .values(status='pending', attempts=0, next_attempt_at=datetime.now(timezone.utc))
    )

    if collection_slugs and publication_tasks.publication_configured():
        for slug in sorted(collection_slugs):
            try:
                publication_tasks.notify_collection_registry_changed.delay(slug)
            except Exception:  # pragma: no cover - notification must never break the import
                logger.exception('Knowledge registry notification failed for collection %s during import', slug)

    return result.rowcount or 0


# Public alias reused by app/api/knowledge_maintenance.py's manual
# "Index aus Freigaben neu aufbauen" admin action -- the leading-underscore
# name stays as an alias for this module's own existing callers/tests
# rather than being renamed everywhere for a purely cosmetic churn.
_requeue_releases_for_index_rebuild = requeue_releases_for_index_rebuild


# --- Import -------------------------------------------------------------

def import_backup(
    db: Session,
    *,
    path: Path,
    passphrase: str,
    importing_admin_id: str,
    force: bool = False,
    progress_cb: Callable[[str, int], None] | None = None,
) -> dict:
    """Validate, then restore, `path` onto `db`. See this module's docstring
    for the admin-identity and session-invalidation rules, and
    `_requeue_releases_for_index_rebuild` for the index-rebuild step.

    Everything from "wipe" onward runs inside the caller's transaction --
    the caller (API background thread / CLI) is expected to call
    `db.commit()` only after this returns successfully, and to roll back on
    any raised `BackupError`, so a failed import never leaves the target
    half-wiped.
    """
    progress_cb = progress_cb or (lambda table, rows_done: None)

    with tarfile.open(path, 'r:gz') as tar:
        manifest = _read_manifest(tar)
        _validate_format(manifest)
        archive_fernet = _validate_passphrase(manifest, passphrase)

        target_revision = _current_alembic_revision(db)
        _check_alembic_compatible(manifest.get('source', {}).get('alembic_revision'), target_revision)

        state = target_state(db)
        if not state['fresh'] and not force:
            raise TargetNotFreshError(
                'Das Ziel enthält bereits Daten. Setzen Sie "Vorhandene Daten überschreiben", um fortzufahren.',
                state['reasons'],
            )

        importing_admin = db.get(User, importing_admin_id)
        if importing_admin is None:
            raise BackupError('Der durchführende Administrator wurde nicht gefunden.')
        importing_admin_snapshot = {
            'id': importing_admin.id,
            'username': importing_admin.username,
            'email': importing_admin.email,
            'password_hash': importing_admin.password_hash,
        }

        report: dict = {
            'tables': {},
            'files_restored': 0,
            'warnings': [],
            'skipped': [],
            'requeued_releases': 0,
            'requires_relogin': True,
            'indexing_note': (
                'Der Wissensindex wird über die bestehende Publikations-Outbox neu aufgebaut, sobald ein '
                'Ingest-Worker läuft -- ohne laufenden Worker bleibt die Neuindizierung ausstehend.'
            ),
        }

        tables = _exported_tables()
        table_names = {table.name for table in tables}
        for archived_name in manifest.get('tables', {}):
            if archived_name not in table_names and archived_name not in EXCLUDED_TABLES:
                report['skipped'].append(archived_name)
                report['warnings'].append(
                    f'Tabelle "{archived_name}" ist im Archiv enthalten, aber auf diesem Ziel unbekannt, und '
                    'wurde übersprungen.'
                )

        # Snapshot pre-wipe backup_runs creator ids (see
        # `_restamp_backup_run_creators`): the users wipe below cascades
        # `SET NULL` onto every one of these, not just the row for the run
        # currently executing, so the attribution has to be captured before
        # step 1 touches `users`.
        backup_runs_table = BackupRun.__table__
        orphaned_creators = {
            row.id: row.created_by
            for row in db.execute(select(backup_runs_table.c.id, backup_runs_table.c.created_by))
            if row.created_by is not None
        }

        # 1. Wipe every included table in reverse dependency order. A no-op
        # set of DELETEs when the target is already fresh, so the force and
        # fresh-target paths share this one code path.
        for table in reversed(tables):
            db.execute(delete(table))

        # 2. Import each table forward, in dependency order.
        admin_row_written = False
        final_admin_id = importing_admin_snapshot['id']
        for table in tables:
            member_name = f'tables/{table.name}.jsonl'
            try:
                fileobj = tar.extractfile(member_name)
            except KeyError:
                fileobj = None

            rows_done = 0
            self_fk_columns = _self_referential_columns(table)
            deferred_updates: list[tuple[dict, dict]] = []

            if fileobj is not None and table.name == 'users':
                # Decode every row first so at most one archived user can be
                # promoted to admin with the importing admin's overwritten
                # password hash: streaming row-by-row would independently
                # promote every row that matches by username OR e-mail, even
                # when two unrelated archived accounts each match on a
                # different field (see module docstring). Prefer the row
                # that is literally the importing admin's own pre-import
                # account (matching id); otherwise the first match in
                # archive order; any other matching row keeps its own
                # archived role/hash unmodified.
                decoded_rows = []
                for raw_line in fileobj:
                    line = raw_line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    decoded_rows.append(_decode_row(table, data, tar, archive_fernet, report['warnings']))

                matched_indices = [
                    i for i, row in enumerate(decoded_rows)
                    if _is_admin_identity_match(row, importing_admin_snapshot)
                ]
                chosen_index = None
                if matched_indices:
                    chosen_index = next(
                        (i for i in matched_indices if decoded_rows[i].get('id') == importing_admin_snapshot['id']),
                        matched_indices[0],
                    )
                    if len(matched_indices) > 1:
                        report['warnings'].append(
                            f'{len(matched_indices)} archivierte Benutzerkonten stimmen im Benutzernamen oder in '
                            'der E-Mail-Adresse mit dem durchführenden Administrator überein; nur eines wurde als '
                            'Administrator mit dem aktuellen Passwort übernommen, die übrigen behalten ihre '
                            'ursprüngliche Rolle und ihr ursprüngliches Passwort.'
                        )

                for i, row in enumerate(decoded_rows):
                    if i == chosen_index:
                        row = dict(row)
                        row['role'] = UserRole.ADMIN.value
                        row['password_hash'] = importing_admin_snapshot['password_hash']
                        # The archived row may be a formerly deactivated or
                        # lockout-throttled account -- reset these alongside
                        # role/password_hash so it can never block the
                        # importing admin's own login after the wipe.
                        row['is_active'] = True
                        row['locked_until'] = None
                        row['failed_login_count'] = 0
                        admin_row_written = True
                        final_admin_id = row['id']
                    for column_name in self_fk_columns:
                        if row.get(column_name) is not None:
                            deferred_updates.append((_pk_values(table, row), {column_name: row[column_name]}))
                            row[column_name] = None
                    db.execute(table.insert().values(**row))
                    rows_done += 1
                    if rows_done % _ROW_BATCH_SIZE == 0:
                        progress_cb(table.name, rows_done)
            elif fileobj is not None:
                for raw_line in fileobj:
                    line = raw_line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    row = _decode_row(table, data, tar, archive_fernet, report['warnings'])
                    for column_name in self_fk_columns:
                        if row.get(column_name) is not None:
                            deferred_updates.append((_pk_values(table, row), {column_name: row[column_name]}))
                            row[column_name] = None
                    db.execute(table.insert().values(**row))
                    rows_done += 1
                    if rows_done % _ROW_BATCH_SIZE == 0:
                        progress_cb(table.name, rows_done)

            if table.name == 'users' and not admin_row_written:
                # No exported user matched the importing admin's identity --
                # keep their own account as an additional admin (see module
                # docstring).
                db.execute(table.insert().values(
                    id=importing_admin_snapshot['id'],
                    username=importing_admin_snapshot['username'],
                    email=importing_admin_snapshot['email'],
                    password_hash=importing_admin_snapshot['password_hash'],
                    role=UserRole.ADMIN.value,
                ))
                rows_done += 1

            for pk, values in deferred_updates:
                stmt = table.update()
                for pk_column, pk_value in pk.items():
                    stmt = stmt.where(table.c[pk_column] == pk_value)
                db.execute(stmt.values(**values))

            progress_cb(table.name, rows_done)
            report['tables'][table.name] = rows_done
            expected = manifest.get('tables', {}).get(table.name)
            if expected is not None and expected != rows_done:
                report['warnings'].append(
                    f'Tabelle {table.name}: {rows_done} Zeile(n) importiert, {expected} im Archiv erwartet.'
                )

        # 3. Restore the on-disk upload/result trees.
        report['files_restored'] = _restore_files(tar)

    # 4. Reset transient/lease state that must not resume unattended,
    # re-attribute any backup_runs rows the users wipe just orphaned, and
    # re-queue index delivery -- all after the tar is closed, they only
    # touch the database.
    _reset_running_import_runs(db)
    if orphaned_creators:
        _restamp_backup_run_creators(db, orphaned_creators, final_admin_id)
    report['requeued_releases'] = _requeue_releases_for_index_rebuild(db)

    return report
