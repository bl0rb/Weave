"""Canonical portal snapshots and publication event payloads."""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime
from pathlib import Path

import yaml

from app.core.config import settings
from app.models.models import Collection, Job, Team, User
from app.services.webhooks import build_document_processed_payload


class PublicationValidationError(ValueError):
    """The current job result cannot be used as a portal snapshot."""


def _json_compatible(value, path: str = 'frontmatter'):
    """Normalize safe-loaded YAML into values accepted by JSON columns."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PublicationValidationError(f'Frontmatter contains a non-JSON number at {path}')
        return value
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item, f'{path}[{index}]') for index, item in enumerate(value)]
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PublicationValidationError(f'Frontmatter contains a non-string key at {path}')
            normalized[key] = _json_compatible(item, f'{path}.{key}')
        return normalized
    raise PublicationValidationError(f'Frontmatter contains a non-JSON value at {path}')


def publication_configured() -> bool:
    return bool(settings.portal_knowledge_base_url.strip() and settings.portal_knowledge_webhook_secret.strip())


def publication_url(release_id: str) -> str:
    return f'{settings.public_api_url.rstrip("/")}/api/v1/portal/releases/{release_id}/download'


def _markdown_from_job(job: Job) -> str | None:
    if job.result_markdown is not None:
        return job.result_markdown

    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    editor = info.get('editor') if isinstance(info.get('editor'), dict) else {}
    latest = editor.get('latest_result_path') if isinstance(editor.get('latest_result_path'), str) else None
    candidates = [Path(latest)] if latest else []
    if settings.results_dir.exists():
        candidates.extend(sorted(settings.results_dir.glob(f'edited/{job.id}.v*.md')))
    if job.result_path:
        candidates.append(Path(job.result_path))
    for path in candidates:
        try:
            resolved = path.resolve()
            if resolved.exists():
                return resolved.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            continue
    return None


def _parse_frontmatter(markdown: str) -> tuple[dict, str]:
    if not markdown.startswith('---\n'):
        raise PublicationValidationError('Document has no valid YAML frontmatter')
    end = markdown.find('\n---\n', 4)
    if end < 0:
        raise PublicationValidationError('Document has no valid YAML frontmatter')
    try:
        frontmatter = yaml.safe_load(markdown[4:end + 1])
    except yaml.YAMLError as exc:
        raise PublicationValidationError('Document has invalid YAML frontmatter') from exc
    if not isinstance(frontmatter, dict):
        raise PublicationValidationError('Document has invalid YAML frontmatter')
    return frontmatter, markdown[end + len('\n---\n'):]


def canonical_snapshot(db, job: Job, collection: Collection) -> tuple[str, str, dict]:
    """Return the exact markdown/hash/frontmatter pair shown by the portal.

    Collection and uploader identity are taken from database rows so edited
    YAML cannot change the cross-service identity. YAML serialization is
    deterministic and the body remains byte-for-byte unchanged.
    """
    markdown = _markdown_from_job(job)
    if not markdown or not markdown.strip():
        raise PublicationValidationError('Document has no markdown result')
    frontmatter, body = _parse_frontmatter(markdown)
    frontmatter = _json_compatible(frontmatter)
    frontmatter['collection'] = collection.slug
    frontmatter['collection_name'] = collection.name
    frontmatter['job_id'] = job.id
    frontmatter['document_version'] = job.document_version
    if isinstance(job.content_sha256, str) and len(job.content_sha256) == 64:
        frontmatter['content_sha256'] = job.content_sha256.lower()
    else:
        frontmatter.pop('content_sha256', None)
    if job.previous_job_id:
        frontmatter['previous_job_id'] = job.previous_job_id
    else:
        frontmatter.pop('previous_job_id', None)

    owner = db.get(User, job.owner_id) if job.owner_id else None
    if owner is not None:
        if owner.username:
            frontmatter['uploaded_by'] = owner.username
        else:
            frontmatter.pop('uploaded_by', None)
        team_name = None
        if owner.team_id:
            team = db.get(Team, owner.team_id)
            team_name = team.name if team is not None else None
        if team_name:
            frontmatter['team'] = team_name
        else:
            frontmatter.pop('team', None)
    else:
        frontmatter.pop('uploaded_by', None)
        frontmatter.pop('team', None)

    dumped = yaml.safe_dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    snapshot = f'---\n{dumped}---\n{body}'
    digest = hashlib.sha256(snapshot.encode('utf-8')).hexdigest()
    return snapshot, digest, frontmatter


def build_release_payload(job: Job, release_id: str, snapshot_hash: str, frontmatter: dict) -> dict:
    """Freeze the existing processed payload into the released event shape."""
    payload = build_document_processed_payload(job)
    content_sha256 = payload.get('content_sha256')
    if not isinstance(content_sha256, str) or len(content_sha256) != 64 or any(
        character not in '0123456789abcdef' for character in content_sha256
    ):
        raise PublicationValidationError('Job has no valid content SHA-256')
    if not isinstance(payload.get('engine'), str) or not payload['engine']:
        raise PublicationValidationError('Document has no valid processing engine in its frontmatter')
    payload['event'] = 'document.released'
    payload['release_id'] = release_id
    payload['markdown_sha256'] = snapshot_hash
    payload['markdown_url'] = publication_url(release_id)
    payload['frontmatter'] = frontmatter
    if isinstance(frontmatter.get('engine'), str):
        payload['engine'] = frontmatter['engine']
    if isinstance(frontmatter.get('processed_at'), str):
        payload['processed_at'] = frontmatter['processed_at']
    return payload


def release_endpoint() -> str:
    return f'{settings.portal_knowledge_base_url.rstrip("/")}/api/v1/events/ingest'
