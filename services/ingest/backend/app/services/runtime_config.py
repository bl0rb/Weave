"""Read-only view of the running configuration for the admin UI
(app/api/runtime_config.py): every environment setting of app/core/config.py
with secrets masked, the settings admins changed in the UI (stored in the
database), connection checks, and an export as .env or JSON.

Nothing here changes configuration: environment values stay owned by the
deployment (Helm values, docker-compose, .env), the export is the copy an
operator takes over into it.
"""

from __future__ import annotations

import enum
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models.models import (
    AuthProvider,
    ChatProviderConfig,
    KnowledgeMemoryConfig,
    RetrievalProviderConfig,
    RuntimeSetting,
    VlConnection,
)
from app.services import object_store
from app.services.backup import _current_alembic_revision, backups_dir

SECRET_MASK = '••••••••'
_SECRET_SUFFIXES = ('password', 'secret', 'token', 'api_key', 'secret_key', 'secret_access_key')

# Ordered: the first group whose prefix matches a setting wins.
_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ('database', ('database_url', 'postgres_', 'db_')),
    ('object_storage', ('object_', 's3_')),
    ('backup', ('backup_',)),
    ('workers', ('redis_', 'celery_', 'worker_', 'job_', 'service_name')),
    ('security', (
        'secret_key', 'cors_', 'trusted_proxy_', 'rate_limit_', 'max_upload_', 'bootstrap_admin_', 'handoff_',
        'mcp_oauth_', 'tools_introspection_', 'public_api_url', 'session_cleanup_',
    )),
    ('ocr', ('paddle_', 'openai_api_', 'vl_', 'ocr_')),
    ('retrieval', ('embedding_', 'rerank_')),
    ('integrations', (
        'chat_', 'runtime_', 'knowledge_', 'portal_', 'publication_', 'system_status_', 'import_', 'confluence_',
        'webhook',
    )),
]

# Settings an admin changes in the UI; *_encrypted columns are reported only
# as has_<name>.
_MANUAL_SECTIONS: list[tuple[str, type, Callable[[Any], str]]] = [
    ('runtime_settings', RuntimeSetting, lambda row: row.key),
    ('chat_providers', ChatProviderConfig, lambda row: row.name or row.id),
    ('retrieval_provider', RetrievalProviderConfig, lambda row: row.id),
    ('knowledge_memory', KnowledgeMemoryConfig, lambda row: row.id),
    ('vl_connections', VlConnection, lambda row: row.name),
    ('auth_providers', AuthProvider, lambda row: row.slug),
]
_SKIPPED_COLUMNS = {'updated_by_id', 'created_at'}


def is_secret(name: str) -> bool:
    return name.endswith(_SECRET_SUFFIXES)


def _group(name: str) -> str:
    for group, prefixes in _GROUPS:
        if name.startswith(prefixes):
            return group
    return 'general'


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _masked(name: str, value: Any) -> Any:
    if name == 'database_url' and value:
        try:
            return make_url(value).render_as_string(hide_password=True)
        except Exception:  # noqa: BLE001 -- unparsable: show nothing of it
            return SECRET_MASK
    if is_secret(name):
        return SECRET_MASK if value else ''
    return _jsonable(value)


def _env_settings() -> list[dict]:
    items = []
    for name, field in Settings.model_fields.items():
        value = getattr(settings, name)
        default = field.get_default(call_default_factory=True)
        items.append({
            'name': name.upper(),
            'group': _group(name),
            'value': _masked(name, value),
            'default': _masked(name, default),
            'secret': is_secret(name),
            'is_set': value not in ('', None, [], {}),
            'customized': value != default,
        })
    order = [group for group, _ in _GROUPS] + ['general']
    return sorted(items, key=lambda item: order.index(item['group']))


def _row_values(row) -> dict:
    values: dict = {}
    for column in row.__table__.columns:
        if column.name in _SKIPPED_COLUMNS:
            continue
        value = getattr(row, column.key)
        if column.name.endswith('_encrypted'):
            values[f"has_{column.name.removesuffix('_encrypted')}"] = bool(value)
        else:
            values[column.name] = _jsonable(value)
    return values


def _manual_settings(db: Session) -> list[dict]:
    sections = []
    for section, model, label in _MANUAL_SECTIONS:
        rows = db.scalars(select(model)).all()
        sections.append({
            'section': section,
            'items': [{'id': label(row), 'values': _row_values(row)} for row in rows],
        })
    return sections


def config_overview(db: Session) -> dict:
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'env': _env_settings(),
        'manual': _manual_settings(db),
        'checks': list(CHECKS),
    }


def _env_value(value: Any) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(_jsonable(value))


def export_env() -> str:
    """Every setting that differs from its default, as .env lines. Secrets
    are never exported -- they stay commented placeholders to fill from the
    secret store."""
    lines = [
        '# Weave-Ingest -- exported running configuration',
        f"# {datetime.now(timezone.utc).isoformat()}; only values that differ from the defaults.",
        '# Secrets are not exported: fill the commented lines from your secret store.',
    ]
    for group, _ in [*_GROUPS, ('general', ())]:
        block = []
        for name, field in Settings.model_fields.items():
            if _group(name) != group:
                continue
            value = getattr(settings, name)
            if value == field.get_default(call_default_factory=True):
                continue
            if is_secret(name) or name == 'database_url':
                block.append(f'# {name.upper()}=')
            else:
                block.append(f'{name.upper()}={_env_value(value)}')
        if block:
            lines += ['', f'# --- {group}', *block]
    return '\n'.join(lines) + '\n'


def export_json(db: Session) -> dict:
    overview = config_overview(db)
    overview.pop('checks')
    return overview


# --- Connection checks -------------------------------------------------------

def _step(steps: list[dict], step_id: str, fn: Callable[[], str | None]) -> bool:
    started = time.monotonic()
    try:
        detail = fn() or ''
        ok = True
    except Exception as exc:  # noqa: BLE001 -- every failure is a check result
        detail = _error_text(exc)
        ok = False
    steps.append({'id': step_id, 'ok': ok, 'detail': detail, 'ms': round((time.monotonic() - started) * 1000)})
    return ok


def _error_text(exc: Exception) -> str:
    response = getattr(exc, 'response', None)
    if isinstance(response, dict) and 'Error' in response:
        error = response['Error']
        return f"{error.get('Code', '')}: {error.get('Message', '')}".strip(': ')
    return f'{type(exc).__name__}: {exc}'


def _result(name: str, steps: list[dict]) -> dict:
    return {'name': name, 'ok': all(step['ok'] for step in steps), 'steps': steps}


def check_database(db: Session, _: dict | None = None) -> dict:
    steps: list[dict] = []

    def query() -> str:
        db.execute(text('SELECT 1'))
        bind = db.get_bind()
        version = '.'.join(str(part) for part in (bind.dialect.server_version_info or ()))
        return f'{bind.dialect.name} {version}'.strip()

    if _step(steps, 'query', query):
        _step(steps, 'migrations', lambda: _current_alembic_revision(db) or '–')
    db.rollback()
    return _result('database', steps)


def check_redis(_: Session, __: dict | None = None) -> dict:
    import redis

    steps: list[dict] = []
    client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)

    def ping() -> str:
        client.ping()
        return make_url(settings.redis_url).render_as_string(hide_password=True)

    if _step(steps, 'ping', ping):
        _step(steps, 'version', lambda: str(client.info('server').get('redis_version', '')))
    client.close()
    return _result('redis', steps)


S3_FIELDS = (
    's3_bucket', 's3_endpoint_url', 's3_region', 's3_access_key_id', 's3_secret_access_key', 's3_key_prefix',
    's3_addressing_style', 's3_sse', 's3_kms_key_id',
)


def s3_test_config(overrides: dict | None) -> dict:
    """The running S3 settings with the admin's form values on top. An empty
    secret keeps the running one, so the form never has to show it."""
    config = {name: getattr(settings, name) for name in S3_FIELDS}
    for name, value in (overrides or {}).items():
        if name in S3_FIELDS and value is not None:
            if name == 's3_secret_access_key' and not value:
                continue
            config[name] = value.strip() if isinstance(value, str) else value
    return config


def check_s3(_: Session, overrides: dict | None = None) -> dict:
    config = s3_test_config(overrides)
    steps: list[dict] = []
    key = f"{config['s3_key_prefix']}healthcheck/{uuid.uuid4()}.txt"
    payload = f'weave-ingest s3 check {datetime.now(timezone.utc).isoformat()}'.encode()
    holder: dict = {}

    def validate() -> str:
        if not config['s3_bucket']:
            raise ValueError('S3_BUCKET ist nicht gesetzt.')
        if config['s3_kms_key_id'] and config['s3_sse'] != 'aws:kms':
            raise ValueError('S3_KMS_KEY_ID benötigt S3_SSE=aws:kms.')
        holder['client'] = object_store.build_s3_client(
            endpoint_url=config['s3_endpoint_url'],
            region=config['s3_region'],
            access_key_id=config['s3_access_key_id'],
            secret_access_key=config['s3_secret_access_key'],
            addressing_style=config['s3_addressing_style'],
            max_attempts=1,
            timeout_seconds=5,
        )
        return config['s3_endpoint_url'] or 'AWS'

    def head_bucket() -> str:
        holder['client'].head_bucket(Bucket=config['s3_bucket'])
        return config['s3_bucket']

    def put() -> str:
        extra: dict = {'ContentType': 'text/plain'}
        if config['s3_sse']:
            extra['ServerSideEncryption'] = config['s3_sse']
        if config['s3_kms_key_id']:
            extra['SSEKMSKeyId'] = config['s3_kms_key_id']
        response = holder['client'].put_object(Bucket=config['s3_bucket'], Key=key, Body=payload, **extra)
        sse = response.get('ServerSideEncryption') if isinstance(response, dict) else None
        return f'{key}' + (f' (SSE {sse})' if sse else '')

    def get() -> str:
        body = holder['client'].get_object(Bucket=config['s3_bucket'], Key=key)['Body']
        try:
            data = body.read()
        finally:
            body.close()
        if data != payload:
            raise ValueError('Gelesener Inhalt weicht vom geschriebenen ab.')
        return f'{len(data)} B'

    def delete() -> str:
        holder['client'].delete_object(Bucket=config['s3_bucket'], Key=key)
        return key

    if _step(steps, 'config', validate) and _step(steps, 'head_bucket', head_bucket) and _step(steps, 'put', put):
        _step(steps, 'get', get)
        _step(steps, 'delete', delete)
    return _result('s3', steps)


def check_backup_dir(_: Session, __: dict | None = None) -> dict:
    steps: list[dict] = []

    def write() -> str:
        directory = backups_dir()
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f'.write-check-{uuid.uuid4()}'
        probe.write_bytes(b'ok')
        probe.unlink()
        free = shutil.disk_usage(directory).free
        return f'{directory} ({free // (1024 * 1024)} MiB frei)'

    _step(steps, 'write', write)
    return _result('backup_dir', steps)


CHECKS: dict[str, Callable[[Session, dict | None], dict]] = {
    'database': check_database,
    'redis': check_redis,
    's3': check_s3,
    'backup_dir': check_backup_dir,
}
