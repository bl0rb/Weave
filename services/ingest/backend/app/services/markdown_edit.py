"""A manual Markdown edit stored on a job.

Shared by the technical editor (PUT /jobs/{id}/save) and the portal's
"bearbeiten und freigeben" (POST /portal/documents/{id}/edit), so both keep
the same version history and quality gate.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import Job, JobMarkdownVersion
from app.services.field_validation import validate_document
from app.services.quality_gate import evaluate_document_quality


def edited_quality_gate(markdown: str) -> dict:
    """The quality gate the edited Markdown would get, without storing it."""
    content = markdown.strip()
    return evaluate_document_quality(content, field_validation=validate_document(content))


def record_markdown_version(db: Session, job: Job, markdown: str, now: datetime) -> int:
    """Store ``markdown`` as the job's next version and current result; returns the version.

    Does not commit. The caller has already validated the content and holds
    the job's row lock.
    """
    # Copy rather than alias job.processing_info: mutating the attribute's own
    # backing dict in place before reassigning it defeats SQLAlchemy's dirty
    # check (old and new end up `==`), so the UPDATE for this column would be
    # silently skipped and the edit lost on the next read.
    info = dict(job.processing_info) if isinstance(job.processing_info, dict) else {}
    editor = info.get('editor') if isinstance(info.get('editor'), dict) else {}

    # DB-first: with no shared volume between backend and worker, version
    # history is truth-sourced from job_markdown_versions rows rather than
    # from the (possibly stale, e.g. cleared by a job restart) editor
    # metadata mirrored below. This also sidesteps the (job_id, version)
    # unique constraint being violated if processing_info ever drifts from
    # the version rows already on record.
    highest_version = db.scalar(
        select(func.max(JobMarkdownVersion.version)).where(JobMarkdownVersion.job_id == job.id)
    ) or 0
    version = highest_version + 1
    db.add(JobMarkdownVersion(job_id=job.id, version=version, content=markdown, created_at=now))

    # Legacy on-disk '.v{n}.md' files are gone; 'path' keys stay in the JSON
    # shape for backward compatibility but are now always null.
    versions = list(editor.get('versions')) if isinstance(editor.get('versions'), list) else []
    versions.append({'version': version, 'path': None, 'updated_at': now.isoformat()})
    info['editor'] = {
        'version': version,
        'latest_result_path': None,
        'updated_at': now.isoformat(),
        'versions': versions,
    }

    # A manual edit invalidates the OCR-time quality gate (grade/score/signals
    # all describe the *original* extraction, not the reviewer's rewrite) --
    # recompute it against the saved markdown so review-UI badges/filters and
    # the 'Warum Stufe X?' breakdown reflect what was actually released.
    execution = info.get('execution') if isinstance(info.get('execution'), dict) else None
    if isinstance(execution, dict) and execution.get('quality_gate'):
        info['execution'] = {**execution, 'quality_gate': edited_quality_gate(markdown)}

    job.processing_info = {**info}
    job.result_markdown = markdown
    return version
