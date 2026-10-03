from __future__ import annotations

import copy
import hashlib
import logging
from pathlib import Path
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import aware_utc, get_current_user, get_knowledge_reader
from app.api.import_routes import _can_edit_run
from app.api.routes import (
    _ARTIFACT_INLINE_CONTENT_TYPES,
    _active_process_job_ids,
    _apply_visible_filter,
    _can_manage_collection,
    _collection_control_job_filter,
    _content_disposition,
    _is_import_page_job,
    _job_folder_path,
    _require_visible,
    _require_visible_collection,
    _withdraw_and_delete_job,
    dispatch_withdrawals,
    restart_job,
)
from app.core.config import settings
from app.database.session import get_db
from app.models.models import (
    Collection,
    DocumentRelease,
    ImportPageState,
    ImportRun,
    ImportRunStatus,
    Job,
    JobArtifact,
    JobStatus,
    KnowledgeWithdrawal,
    MailMessage,
    Team,
    User,
    UserRole,
)
from app.schemas.jobs import CollectionTeamRef, JobRestartRequest
from app.schemas.portal import (
    PortalBulkActionRequest,
    PortalBulkActionResponse,
    PortalBulkErrorItem,
    PortalConfigResponse,
    PortalCollectionReleaseRequest,
    PortalCollectionReleaseResponse,
    PortalDocumentDetail,
    PortalDocumentItem,
    PortalDocumentListResponse,
    PortalDocumentSource,
    PortalEditRequest,
    PortalEditResponse,
    PortalImportScopeItem,
    PortalImportScopesResponse,
    PortalQualityDetail,
    PortalReindexCollectionResponse,
    PortalReleaseRequest,
    PortalReleaseSummary,
    PortalReprocessRequest,
    PortalReprocessResponse,
)
from app.services import object_store
from app.services.backup import requeue_collection_releases_for_reindex
from app.services.markdown_edit import record_markdown_version
from app.services.publications import (
    PublicationValidationError,
    _markdown_from_job,
    _parse_frontmatter,
    build_release_payload,
    canonical_snapshot,
    publication_configured,
    release_endpoint,
    restore_relative_image_urls,
    rewrite_release_image_urls,
)
from app.workers import publication_tasks

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/v1/portal')
knowledge_router = APIRouter(prefix='/api/v1/portal')
_EXPORT_CHUNK_BYTES = 1024 * 1024
_EXPORT_SPOOL_BYTES = 8 * 1024 * 1024


def _settings_for_job(job: Job) -> dict:
    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    value = info.get('settings') if isinstance(info.get('settings'), dict) else {}
    return value


def _collection_for_job(db, job: Job) -> Collection | None:
    collection_id = _settings_for_job(job).get('collection_id')
    return db.get(Collection, collection_id) if isinstance(collection_id, str) else None


def _review_decision(job: Job) -> str | None:
    """Reviewer's skip/unskip decision, mirroring _quality()'s JSON reader."""
    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    review = info.get('portal_review') if isinstance(info.get('portal_review'), dict) else {}
    decision = review.get('decision') if isinstance(review.get('decision'), str) else None
    return decision


def _quality(job: Job) -> tuple[str | None, str | None]:
    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    execution = info.get('execution') if isinstance(info.get('execution'), dict) else {}
    quality = execution.get('quality_gate') if isinstance(execution.get('quality_gate'), dict) else {}
    grade = quality.get('grade') if isinstance(quality.get('grade'), str) else None
    recommendation = quality.get('recommendation') if isinstance(quality.get('recommendation'), str) else None
    return grade, recommendation


def _quality_detail(job: Job) -> tuple[PortalQualityDetail | None, str | None]:
    """Full quality-gate payload for the detail view, plus a reason key when absent.

    Reason keys (mapped to German sentences by the frontend): 'not_finished'
    (job still pending/running), 'failed' (job failed), 'legacy' (finished
    before the quality gate existed, or a non-import job whose gate result
    was otherwise never recorded), 'import_without_gate' (Confluence import
    jobs that skip the OCR quality gate -- see import_tasks.py), 'unknown'
    (fallback).
    """
    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    execution = info.get('execution') if isinstance(info.get('execution'), dict) else {}
    quality = execution.get('quality_gate') if isinstance(execution.get('quality_gate'), dict) else None
    if quality:
        return (
            PortalQualityDetail(
                grade=quality.get('grade'),
                score=quality.get('score'),
                recommendation=quality.get('recommendation'),
                thresholds=quality.get('thresholds') or {'A': 0.9, 'B': 0.75},
                signals=quality.get('signals') or {},
                issues=quality.get('issues') or [],
            ),
            None,
        )
    if job.status == JobStatus.PENDING or job.status == JobStatus.RUNNING:
        return None, 'not_finished'
    if job.status == JobStatus.FAILED:
        return None, 'failed'
    if job.status == JobStatus.FINISHED:
        return None, ('import_without_gate' if job.import_run_id else 'legacy')
    return None, 'unknown'


def _apply_quality_grade_filter(query, quality_grade_expr, value: str | None):
    """Apply the portal-wide 'quality_grade' filter (A/B/C or 'none') to a query.

    Shared by portal.py's document list and portal_management.py's activity
    list so the two duplicated quality-grade SQL expression sets stay in sync.
    """
    if value is None:
        return query
    normalized = value.strip().lower()
    if normalized == 'none':
        return query.where(quality_grade_expr.is_(None))
    if normalized in ('a', 'b', 'c'):
        return query.where(func.lower(quality_grade_expr) == normalized)
    return query


def _apply_import_scope_filter(query, value: str | None):
    """Apply the portal document list's 'import_scope' filter: 'none' for
    uploads/mail (no import run), or '<scope_type>:<scope_value>' (split on
    the first ':') for one Confluence import scope -- see the
    /collections/{id}/import-scopes endpoint below, which lists the values
    this accepts for a given collection."""
    if value is None:
        return query
    if value == 'none':
        return query.where(Job.import_run_id.is_(None))
    scope_type, sep, scope_value = value.partition(':')
    if not sep or not scope_type or not scope_value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="import_scope must be 'none' or '<scope_type>:<scope_value>'",
        )
    return query.where(ImportRun.scope_type == scope_type, ImportRun.scope_value == scope_value)


def _job_is_controlled(db, job: Job, collection: Collection, user: User) -> bool:
    return user.role == UserRole.ADMIN or job.owner_id == user.id or _can_manage_collection(db, collection, user)


def _import_run_finished(db, job: Job) -> bool:
    if not job.import_run_id:
        return True
    run_status = db.scalar(select(ImportRun.status).where(ImportRun.id == job.import_run_id))
    return run_status == ImportRunStatus.FINISHED


def _summary(release: DocumentRelease | None, released_by: str | None = None) -> PortalReleaseSummary | None:
    if release is None:
        return None
    return PortalReleaseSummary(
        id=release.id,
        created_at=release.created_at,
        status=release.status,
        error_message=release.error_message,
        released_by=released_by,
    )


def _confluence_label(title: str | None) -> str:
    # ImportPageState.title is set to job.original_filename (a slugified
    # '<title>.md' filename), not the real Confluence page title -- strip
    # the extension so the label at least doesn't look like a filename.
    if not title:
        return 'Confluence-Seite'
    return title[:-3] if title.lower().endswith('.md') else title


def _iso(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _provenance(job: Job, uploaded_by: str | None) -> dict:
    """Uploader and last portal edit. An edited version is a new Job, so the
    original upload time travels along in processing_info."""
    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    edit = info.get('portal_edit') if isinstance(info.get('portal_edit'), dict) else {}
    return {
        'uploaded_by': uploaded_by,
        'uploaded_at': _iso(info.get('source_uploaded_at')) or job.created_at,
        'edited_by': edit.get('by') if isinstance(edit.get('by'), str) else None,
        'edited_at': _iso(edit.get('at')),
    }


def _source(
    job: Job, page_state: ImportPageState | None, mail: tuple[str, str] | None, uploaded_by: str | None = None
) -> PortalDocumentSource:
    """Resolve where a document came from, for the portal's Herkunft display.

    Confluence pages and mail attachments are identified by their Job
    columns (import_run_id / mail_message_id). ImportPageState.job_id is
    overwritten to the newest import on every refresh, so a superseded Job
    (kept visible via the previous_job_id version chain, same as uploads)
    can have import_run_id set but no matching ImportPageState row -- in
    that case we still know it's a Confluence import and fall back to the
    source_url stored unconditionally on the Job itself at import time,
    rather than mislabeling it as an upload. Same idea for mail: a
    mail_message_id with no resolvable MailMessage row (deleted) is
    reported as 'unknown', never silently as 'upload'.
    """
    if job.import_run_id:
        if page_state is not None:
            return PortalDocumentSource(
                kind='confluence',
                label=_confluence_label(page_state.title),
                path=None,
                url=page_state.url or None,
                **_provenance(job, uploaded_by),
            )
        import_settings = _settings_for_job(job).get('import')
        fallback_url = import_settings.get('source_url') if isinstance(import_settings, dict) else None
        return PortalDocumentSource(
            kind='confluence', label='Confluence-Seite', path=None, url=fallback_url, **_provenance(job, uploaded_by)
        )
    if job.mail_message_id:
        if mail is not None:
            subject, from_address = mail
            if subject:
                label = f'{subject} ({from_address})' if from_address else subject
            elif from_address:
                label = f'E-Mail von {from_address}'
            else:
                label = 'E-Mail-Anhang'
            return PortalDocumentSource(kind='mail', label=label, path=None, url=None, **_provenance(job, uploaded_by))
        return PortalDocumentSource(kind='unknown', label='Unbekannte Herkunft', path=None, url=None)
    return PortalDocumentSource(
        kind='upload', label='Hochgeladen', path=_job_folder_path(job), url=None, **_provenance(job, uploaded_by)
    )


def _can_release_light(db, job: Job, collection: Collection, user: User) -> bool:
    """Cheap can_release check for a loaded detail row.

    List responses use the equivalent SQL expression below. Canonical
    frontmatter validation belongs to the detail/release paths, where the
    markdown is intentionally loaded.
    """
    if job.status != JobStatus.FINISHED or job.password_hash:
        return False
    if not _markdown_from_job(job):
        return False
    if db.get(KnowledgeWithdrawal, job.id) is not None:
        return False
    if not _import_run_finished(db, job):
        return False
    if not _job_is_controlled(db, job, collection, user):
        return False
    grade, recommendation = _quality(job)
    if recommendation and recommendation.lower() == 'block' and (grade or '').lower() != 'c':
        return False
    return True


def _profile_id(job: Job) -> str | None:
    profile_id = _settings_for_job(job).get('profile_id')
    return profile_id if isinstance(profile_id, str) else None


def _can_reprocess_light(
    db, job: Job, collection: Collection, user: User, release: DocumentRelease | None
) -> bool:
    if release is not None or job.password_hash:
        return False
    if job.status not in (JobStatus.FINISHED, JobStatus.FAILED):
        return False
    if _is_import_page_job(job) or not _import_run_finished(db, job):
        return False
    return _job_is_controlled(db, job, collection, user)


def _newer_version_exists(db, job: Job) -> bool:
    return db.scalar(select(Job.id).where(Job.previous_job_id == job.id).limit(1)) is not None


def _can_edit(db, job: Job, collection: Collection, user: User) -> bool:
    """The portal editor releases what it saves, so the same people and
    preconditions as a release apply -- and only on the newest version: an
    edit of an older one would fork the version chain."""
    if not publication_configured() or job.status != JobStatus.FINISHED or job.password_hash:
        return False
    if not _markdown_from_job(job) or db.get(KnowledgeWithdrawal, job.id) is not None:
        return False
    if not _import_run_finished(db, job) or _newer_version_exists(db, job):
        return False
    return _job_is_controlled(db, job, collection, user)


def _new_edit_version(db, job: Job) -> Job:
    """A released version is immutable, so an edit of it becomes the next
    version of the same document -- chained like a re-upload, carrying
    everything that identifies its source (collection, import, mail, upload
    bytes, artifacts). Knowledge then supersedes the old version."""
    info = copy.deepcopy(job.processing_info) if isinstance(job.processing_info, dict) else {}
    info.pop('portal_review', None)
    info.pop('editor', None)  # the Markdown version history is per job
    info['source_uploaded_at'] = info.get('source_uploaded_at') or job.created_at.isoformat()
    edited = Job(
        id=str(uuid.uuid4()),
        original_filename=job.original_filename,
        # Shared with the predecessor on purpose: the stored original is
        # immutable, so the new version references the same object.
        upload_path=job.upload_path,
        upload_object_id=job.upload_object_id,
        upload_mime_type=job.upload_mime_type,
        upload_size_bytes=job.upload_size_bytes,
        status=JobStatus.FINISHED,
        processing_info=info,
        owner_id=job.owner_id,
        import_run_id=job.import_run_id,
        mail_message_id=job.mail_message_id,
        content_sha256=job.content_sha256,
        document_version=job.document_version + 1,
        previous_job_id=job.id,
        tags=list(job.tags),
    )
    db.add(edited)
    for artifact in db.scalars(select(JobArtifact).where(JobArtifact.job_id == job.id)):
        db.add(JobArtifact(
            job_id=edited.id, kind=artifact.kind, filename=artifact.filename, content_type=artifact.content_type,
            object_id=artifact.object_id, size_bytes=artifact.size_bytes, source_url=artifact.source_url,
            sha256=artifact.sha256,
        ))
    db.flush()  # the page state below references the new row
    # The next Confluence sync must chain onto the edit (or skip an unchanged
    # page and keep it), never fork a second version off the original. Only
    # this space's sync state follows the edit (ADR 0008).
    db.execute(
        update(ImportPageState)
        .where(
            ImportPageState.job_id == job.id,
            ImportPageState.collection_id == _settings_for_job(job).get('collection_id'),
        )
        .values(job_id=edited.id)
    )
    return edited


def _item(
    db,
    job: Job,
    collection: Collection,
    user: User,
    release: DocumentRelease | None,
    *,
    can_release: bool | None = None,
    quality_grade: str | None = None,
    quality_recommendation: str | None = None,
    released_by: str | None = None,
    source: PortalDocumentSource | None = None,
    page_state: ImportPageState | None = None,
    mail: tuple[str, str] | None = None,
    uploaded_by: str | None = None,
) -> PortalDocumentItem:
    grade, recommendation = _quality(job)
    return PortalDocumentItem(
        id=job.id,
        original_filename=job.original_filename,
        status=job.status.value if hasattr(job.status, 'value') else str(job.status),
        collection_id=collection.id,
        collection_name=collection.name,
        created_at=job.created_at,
        quality_grade=quality_grade if quality_grade is not None else grade,
        quality_recommendation=(quality_recommendation if quality_recommendation is not None else recommendation),
        can_release=_can_release_light(db, job, collection, user) if can_release is None else can_release,
        release=_summary(release, released_by),
        source=source if source is not None else _source(job, page_state, mail, uploaded_by),
        review_decision=_review_decision(job),
    )


def _load_visible_job(db, job_id: str, user: User, *, for_update: bool = False) -> Job:
    # The release mutation uses this row lock so a concurrent save/restart
    # cannot pass its issued-release guard before the immutable snapshot is
    # committed. PostgreSQL enforces the lock; SQLite's FOR UPDATE is a
    # no-op, but its single-writer transaction still prevents concurrent
    # commits in the test/runtime dialect used here.
    job = db.get(Job, job_id, with_for_update=for_update)
    if job is None or job.benchmark_run_id is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
    _require_visible(db, job, user)
    return job


def _protected(job: Job) -> None:
    if job.password_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Password-protected jobs require a separate unlock before portal access or release',
        )


def _safe_markdown_filename(original_filename: str, fallback: str) -> str:
    """Return one flat, portable archive/download name.

    Only the basename is considered. Replacing punctuation rather than
    preserving path separators prevents zip-slip entries even when an old
    database row contains a client supplied relative path.
    """
    basename = original_filename.replace('\\', '/').rsplit('/', 1)[-1]
    stem = Path(basename).stem.strip()
    stem = re.sub(r'[^\w .()\-]+', '_', stem, flags=re.UNICODE).strip(' .')
    return f'{stem or fallback}.md'


def _download_headers(filename: str) -> dict[str, str]:
    return {
        'Content-Disposition': _content_disposition('attachment', filename),
        'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff',
    }


def _export_markdown(db, job: Job, collection: Collection, release: DocumentRelease | None) -> str | None:
    """Resolve the portal-visible Markdown without changing publication.

    An issued release is immutable and therefore wins over later edits.
    Unreleased rows use the same canonicalization as the review page. Very
    old malformed frontmatter remains exportable as its processed raw text;
    the release endpoint will still reject it until corrected.
    """
    if release is not None:
        return release.markdown_snapshot
    markdown = _markdown_from_job(job)
    if not markdown:
        return None
    try:
        return canonical_snapshot(db, job, collection)[0]
    except PublicationValidationError:
        return markdown


def _stream_spooled_file(file):
    try:
        while chunk := file.read(_EXPORT_CHUNK_BYTES):
            yield chunk
    finally:
        file.close()


def _require_publishable(job: Job, collection: Collection, db, user: User, supplied_hash: str, accept_quality_warning: bool = False) -> tuple[str, str, dict]:
    if db.get(KnowledgeWithdrawal, job.id) is not None:
        raise HTTPException(status_code=409, detail='Document was withdrawn from Knowledge; import a new version')
    if not _job_is_controlled(db, job, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot release this document')
    if job.status != JobStatus.FINISHED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job is not finished')
    if not _import_run_finished(db, job):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Import run is not finished')
    _protected(job)
    grade, recommendation = _quality(job)
    is_grade_c = (grade or '').lower() == 'c'
    if is_grade_c and not accept_quality_warning:
        raise HTTPException(status_code=409, detail='Explicit confirmation of quality grade C is required')
    if recommendation and recommendation.lower() == 'block' and not is_grade_c:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Quality gate blocks release')
    try:
        snapshot, digest, frontmatter = canonical_snapshot(db, job, collection)
    except PublicationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if digest != supplied_hash.lower():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Markdown changed; refresh the document preview')
    return snapshot, digest, frontmatter


@router.get('/config', response_model=PortalConfigResponse)
def portal_config(db=Depends(get_db), user: User = Depends(get_current_user)) -> PortalConfigResponse:
    team = db.get(Team, user.team_id) if user.team_id else None
    teams = list(db.scalars(select(Team).where(Team.id.in_(user.team_ids)).order_by(Team.name))) if user.team_ids else []
    return PortalConfigResponse(
        publication_configured=publication_configured(),
        team_name=team.name if team is not None else None,
        team_names=[member_team.name for member_team in teams],
        teams=[CollectionTeamRef(id=member_team.id, name=member_team.name) for member_team in teams],
    )


@router.get('/documents', response_model=PortalDocumentListResponse)
def list_portal_documents(
    collection_id: str | None = None,
    review_only: bool = Query(False),
    review_state: str | None = Query(None),
    quality_grade: str | None = Query(None),
    import_scope: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalDocumentListResponse:
    # review_state: 'review' (default when review_only=true) excludes skipped
    # documents from the pending-review view; 'skipped' shows only skipped
    # documents; 'all' (default otherwise) shows everything.
    effective_review_state = review_state or ('review' if review_only else 'all')
    collection_id_expr = Job.processing_info['settings']['collection_id'].as_string()
    quality_grade_expr = Job.processing_info['execution']['quality_gate']['grade'].as_string()
    quality_recommendation_expr = Job.processing_info['execution']['quality_gate']['recommendation'].as_string()
    review_decision_expr = Job.processing_info['portal_review']['decision'].as_string()
    control_expr = True if user.role == UserRole.ADMIN else or_(
        Job.owner_id == user.id,
        _collection_control_job_filter(db, user),
    )
    can_release_expr = and_(
        ~select(KnowledgeWithdrawal.job_id).where(KnowledgeWithdrawal.job_id == Job.id).exists(),
        Job.status == JobStatus.FINISHED,
        func.length(func.coalesce(Job.result_markdown, '')) > 0,
        Job.password_hash.is_(None),
        control_expr,
        or_(Job.import_run_id.is_(None), ImportRun.status == ImportRunStatus.FINISHED),
        or_(quality_recommendation_expr.is_(None), func.lower(quality_recommendation_expr) != 'block', func.lower(quality_grade_expr) == 'c'),
    )

    query = (
        select(
            Job,
            Collection,
            DocumentRelease,
            quality_grade_expr.label('portal_quality_grade'),
            quality_recommendation_expr.label('portal_quality_recommendation'),
            case((can_release_expr, True), else_=False).label('portal_can_release'),
        )
        .join(Collection, Collection.id == collection_id_expr)
        .outerjoin(ImportRun, ImportRun.id == Job.import_run_id)
        .outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id)
        .options(
            defer(Job.result_markdown),
            defer(DocumentRelease.markdown_snapshot),
            defer(DocumentRelease.payload),
        )
        .order_by(Job.created_at.desc())
    )
    query = _apply_visible_filter(query, user, db=db)
    if collection_id is not None:
        query = query.where(Collection.id == collection_id)
    if effective_review_state == 'review':
        query = query.where(
            Job.status == JobStatus.FINISHED,
            DocumentRelease.id.is_(None),
            or_(ImportRun.id.is_(None), ImportRun.status == ImportRunStatus.FINISHED),
        )
    if effective_review_state == 'skipped':
        query = query.where(func.lower(review_decision_expr) == 'skipped', DocumentRelease.id.is_(None))
    elif effective_review_state == 'review':
        query = query.where(or_(review_decision_expr.is_(None), func.lower(review_decision_expr) != 'skipped'))
    query = _apply_quality_grade_filter(query, quality_grade_expr, quality_grade)
    query = _apply_import_scope_filter(query, import_scope)

    count_query = (
        select(func.count())
        .select_from(Job)
        .join(Collection, Collection.id == collection_id_expr)
        .outerjoin(ImportRun, ImportRun.id == Job.import_run_id)
    )
    count_query = _apply_visible_filter(count_query, user, db=db)
    if collection_id is not None:
        count_query = count_query.where(Collection.id == collection_id)
    if effective_review_state == 'review':
        count_query = count_query.outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id).where(
            Job.status == JobStatus.FINISHED,
            DocumentRelease.id.is_(None),
            or_(ImportRun.id.is_(None), ImportRun.status == ImportRunStatus.FINISHED),
        )
    if effective_review_state == 'skipped':
        count_query = count_query.outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id).where(
            func.lower(review_decision_expr) == 'skipped', DocumentRelease.id.is_(None)
        )
    elif effective_review_state == 'review':
        count_query = count_query.where(or_(review_decision_expr.is_(None), func.lower(review_decision_expr) != 'skipped'))
    count_query = _apply_quality_grade_filter(count_query, quality_grade_expr, quality_grade)
    count_query = _apply_import_scope_filter(count_query, import_scope)
    total = int(db.scalar(count_query) or 0)
    rows = db.execute(query.offset(offset).limit(limit)).all()

    # Batch-resolve release owners, Confluence page states and mail
    # metadata for this page of rows in three IN(...) queries -- mirrors
    # `_load_job_owners` in routes.py -- rather than one lookup per row.
    jobs = [row[0] for row in rows]
    releases = [row[2] for row in rows if row[2] is not None]
    owner_ids = {release.owner_id for release in releases if release.owner_id}
    owner_ids |= {job.owner_id for job in jobs if job.owner_id}
    owner_usernames = {
        owner_id: username
        for owner_id, username in db.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all()
    } if owner_ids else {}

    import_job_ids = [job.id for job in jobs if job.import_run_id]
    page_states_by_job = {
        page.job_id: page
        for page in db.scalars(select(ImportPageState).where(ImportPageState.job_id.in_(import_job_ids)))
    } if import_job_ids else {}

    mail_message_ids = {job.mail_message_id for job in jobs if job.mail_message_id}
    mail_by_id = {
        mail_id: (subject, from_address)
        for mail_id, subject, from_address in db.execute(
            select(MailMessage.id, MailMessage.subject, MailMessage.from_address).where(MailMessage.id.in_(mail_message_ids))
        ).all()
    } if mail_message_ids else {}

    return PortalDocumentListResponse(
        items=[
            _item(
                db,
                row[0],
                row[1],
                user,
                row[2],
                can_release=bool(row[5]),
                quality_grade=row[3],
                quality_recommendation=row[4],
                released_by=owner_usernames.get(row[2].owner_id) if row[2] is not None else None,
                page_state=page_states_by_job.get(row[0].id),
                mail=mail_by_id.get(row[0].mail_message_id),
                uploaded_by=owner_usernames.get(row[0].owner_id) if row[0].owner_id else None,
            )
            for row in rows
        ],
        total=total,
    )


@router.get('/documents/{job_id}', response_model=PortalDocumentDetail)
def get_portal_document(job_id: str, db=Depends(get_db), user: User = Depends(get_current_user)) -> PortalDocumentDetail:
    job = _load_visible_job(db, job_id, user)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document collection not found')
    _protected(job)
    release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
    released_by = db.get(User, release.owner_id).username if release is not None and release.owner_id else None
    page_state = (
        db.scalar(select(ImportPageState).where(ImportPageState.job_id == job.id)) if job.import_run_id else None
    )
    mail = None
    if job.mail_message_id:
        mail_row = db.execute(
            select(MailMessage.subject, MailMessage.from_address).where(MailMessage.id == job.mail_message_id)
        ).first()
        mail = tuple(mail_row) if mail_row is not None else None
    quality, quality_missing_reason = _quality_detail(job)
    uploader = db.get(User, job.owner_id) if job.owner_id else None
    uploaded_by = uploader.username if uploader is not None else None
    can_edit = _can_edit(db, job, collection, user)
    if release is not None:
        return PortalDocumentDetail(
            **_item(
                db, job, collection, user, release,
                can_release=_can_release_light(db, job, collection, user),
                released_by=released_by,
                page_state=page_state,
                mail=mail,
                uploaded_by=uploaded_by,
            ).model_dump(),
            markdown=release.markdown_snapshot,
            markdown_sha256=release.markdown_sha256,
            profile_id=_profile_id(job),
            can_reprocess=False,
            quality=quality,
            quality_missing_reason=quality_missing_reason,
            can_edit=can_edit,
        )
    try:
        markdown, digest, _ = canonical_snapshot(db, job, collection)
    except PublicationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PortalDocumentDetail(
        **_item(
            db, job, collection, user, release,
            can_release=_can_release_light(db, job, collection, user),
            released_by=released_by,
            page_state=page_state,
            mail=mail,
            uploaded_by=uploaded_by,
        ).model_dump(),
        markdown=markdown,
        markdown_sha256=digest,
        profile_id=_profile_id(job),
        can_reprocess=_can_reprocess_light(db, job, collection, user, release),
        quality=quality,
        quality_missing_reason=quality_missing_reason,
        can_edit=can_edit,
    )


def _set_review_decision(job: Job, decision: str | None, user: User) -> None:
    info = dict(job.processing_info) if isinstance(job.processing_info, dict) else {}
    if decision is None:
        info.pop('portal_review', None)
    else:
        info['portal_review'] = {'decision': decision, 'at': datetime.now(timezone.utc).isoformat(), 'by': user.id}
    job.processing_info = info


def _skip_or_unskip(job_id: str, decision: str | None, db, user: User) -> PortalDocumentItem:
    job = _load_visible_job(db, job_id, user, for_update=True)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job has no known collection')
    _protected(job)
    if not _can_release_light(db, job, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot review this document')
    if db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job has an issued portal release and cannot be skipped')
    _set_review_decision(job, decision, user)
    db.commit()
    return _item(db, job, collection, user, None)


@router.post('/documents/{job_id}/skip', response_model=PortalDocumentItem)
def skip_portal_document(job_id: str, db=Depends(get_db), user: User = Depends(get_current_user)) -> PortalDocumentItem:
    return _skip_or_unskip(job_id, 'skipped', db, user)


@router.post('/documents/{job_id}/unskip', response_model=PortalDocumentItem)
def unskip_portal_document(job_id: str, db=Depends(get_db), user: User = Depends(get_current_user)) -> PortalDocumentItem:
    return _skip_or_unskip(job_id, None, db, user)


@router.get('/documents/{job_id}/markdown')
def download_portal_document_markdown(
    job_id: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Download the exact Markdown represented by the portal review."""
    job = _load_visible_job(db, job_id, user)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document collection not found')
    _protected(job)
    if job.status != JobStatus.FINISHED or not _import_run_finished(db, job):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Document is not ready for download')
    release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
    markdown = _export_markdown(db, job, collection, release)
    if markdown is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No Markdown is available')
    filename = _safe_markdown_filename(job.original_filename, job.id)
    return Response(
        content=markdown,
        media_type='text/markdown; charset=utf-8',
        headers=_download_headers(filename),
    )


@router.get('/collections/{collection_id}/markdown.zip')
def download_collection_markdown(
    collection_id: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream every downloadable, caller-visible Markdown in a collection."""
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Collection not found')
    _require_visible_collection(db, collection, user)

    collection_id_expr = Job.processing_info['settings']['collection_id'].as_string()
    query = (
        select(Job, DocumentRelease)
        .outerjoin(ImportRun, ImportRun.id == Job.import_run_id)
        .outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id)
        .where(
            collection_id_expr == collection.id,
            Job.status == JobStatus.FINISHED,
            Job.password_hash.is_(None),
            or_(Job.import_run_id.is_(None), ImportRun.status == ImportRunStatus.FINISHED),
        )
        .options(defer(DocumentRelease.payload))
        .order_by(Job.created_at.asc(), Job.id.asc())
    )
    rows = db.execute(_apply_visible_filter(query, user, db=db)).all()

    archive = tempfile.SpooledTemporaryFile(max_size=_EXPORT_SPOOL_BYTES, mode='w+b')
    used_names: set[str] = set()
    exported_files = 0
    with zipfile.ZipFile(archive, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
        for job, release in rows:
            markdown = _export_markdown(db, job, collection, release)
            if markdown is None:
                continue
            archive_name = _safe_markdown_filename(job.original_filename, job.id)
            original_stem = Path(archive_name).stem
            collision = 1
            while archive_name.casefold() in used_names:
                suffix = job.id[:8] if collision == 1 else f'{job.id[:8]}-{collision}'
                archive_name = f'{original_stem}-{suffix}.md'
                collision += 1
            used_names.add(archive_name.casefold())
            zip_file.writestr(archive_name, markdown)
            exported_files += 1

    if exported_files == 0:
        archive.close()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No downloadable Markdown files found in this knowledge area',
        )

    archive.seek(0)
    archive_filename = _safe_markdown_filename(f'{collection.name}.md', collection.slug)[:-3] + '-markdown.zip'
    return StreamingResponse(
        _stream_spooled_file(archive),
        media_type='application/zip',
        headers=_download_headers(archive_filename),
    )


@router.get('/collections/{collection_id}/import-scopes', response_model=PortalImportScopesResponse)
def list_portal_import_scopes(
    collection_id: str, db=Depends(get_db), user: User = Depends(get_current_user)
) -> PortalImportScopesResponse:
    """Distinct Confluence import scopes among the caller-visible documents
    of a collection, for the knowledge space's 'Confluence-Bereich' filter
    (/api/v1/portal/documents?import_scope=...). A re-run/refresh shares its
    scope_type/scope_value with the original import (see ImportRun), so
    every job for a scope is counted together under one entry -- also when
    several people imported it. Editing offers the caller's own newest run
    of the scope (any run for an admin), never someone else's newer run."""
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Collection not found')
    _require_visible_collection(db, collection, user)

    collection_id_expr = Job.processing_info['settings']['collection_id'].as_string()
    # Columns only: one row per document, and ImportRun.state (the crawl map) can be large.
    query = (
        select(
            ImportRun.id, ImportRun.owner_id, ImportRun.kind, ImportRun.status,
            ImportRun.scope_type, ImportRun.scope_value, ImportRun.root_page_title,
        )
        .select_from(Job)
        .join(ImportRun, ImportRun.id == Job.import_run_id)
        .where(collection_id_expr == collection.id)
        .order_by(ImportRun.created_at.asc())
    )
    rows = db.execute(_apply_visible_filter(query, user, db=db)).all()

    scopes: dict[tuple[str, str], dict] = {}
    for run in rows:
        entry = scopes.setdefault(
            (run.scope_type, run.scope_value), {'count': 0, 'label': '', 'run': run, 'own_run': None}
        )
        entry['count'] += 1
        entry['run'] = run  # ordered by created_at, so the last one is the newest run of this scope
        if run.owner_id == user.id or user.role == UserRole.ADMIN:
            entry['own_run'] = run
        if run.root_page_title:
            entry['label'] = run.root_page_title

    other_count = int(
        db.scalar(
            _apply_visible_filter(
                select(func.count()).select_from(Job).where(collection_id_expr == collection.id, Job.import_run_id.is_(None)),
                user,
                db=db,
            )
        )
        or 0
    )
    items = []
    for (scope_type, scope_value), entry in scopes.items():
        own_run = entry['own_run']
        edit_run_id = own_run.id if own_run is not None and _can_edit_run(own_run, user) else None
        items.append(PortalImportScopeItem(
            value=f'{scope_type}:{scope_value}',
            scope_type=scope_type,
            scope_value=scope_value,
            label=entry['label'] or scope_value,
            count=entry['count'],
            latest_run_id=entry['run'].id,
            edit_run_id=edit_run_id,
            can_edit=edit_run_id is not None,
        ))
    items.sort(key=lambda item: item.label.lower())
    return PortalImportScopesResponse(items=items, other_count=other_count)


@router.post(
    '/documents/{job_id}/reprocess',
    response_model=PortalReprocessResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_portal_document(
    job_id: str,
    request: Request,
    payload: PortalReprocessRequest,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalReprocessResponse:
    job = _load_visible_job(db, job_id, user, for_update=True)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job has no known collection')
    if not _job_is_controlled(db, job, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot reprocess this document')
    if db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Job has an issued portal release; a new document version is required',
        )
    _protected(job)
    if job.status not in (JobStatus.FINISHED, JobStatus.FAILED):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job is not eligible for reprocessing')
    if _is_import_page_job(job):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Imported pages cannot be restarted')
    if not _import_run_finished(db, job):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Import run is not finished')
    if job.id in _active_process_job_ids(db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job is currently running')

    try:
        _snapshot, digest, _frontmatter = canonical_snapshot(db, job, collection)
    except PublicationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if digest != payload.markdown_sha256.lower():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Markdown changed; refresh the document preview')

    result = restart_job(
        job_id,
        request,
        JobRestartRequest(profile_id=payload.profile_id),
        db,
        user,
    )
    return PortalReprocessResponse(**result)


@router.post('/documents/{job_id}/release', response_model=PortalReleaseSummary, status_code=status.HTTP_202_ACCEPTED)
def release_portal_document(
    job_id: str,
    payload: PortalReleaseRequest,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalReleaseSummary:
    if not publication_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Portal publication is not configured')
    job = _load_visible_job(db, job_id, user, for_update=True)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job has no known collection')
    snapshot, digest, frontmatter = _require_publishable(job, collection, db, user, payload.markdown_sha256, payload.accept_quality_warning)
    existing = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
    if existing is not None:
        frozen = existing.payload if isinstance(existing.payload, dict) else {}
        if frozen.get('document_version') != job.document_version or frozen.get('content_sha256') != job.content_sha256 or existing.markdown_sha256 != digest:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job changed; a new document version is required')
        existing_by = db.get(User, existing.owner_id).username if existing.owner_id else None
        return _summary(existing, existing_by)  # type: ignore[return-value]

    release_id = str(uuid.uuid4())
    rewritten_snapshot = rewrite_release_image_urls(snapshot, release_id)
    # The event payload's markdown_sha256 must match the bytes actually served by
    # download_release/download_release_artifact (the POST-rewrite snapshot), since
    # Knowledge's fetch_released_markdown() verifies the downloaded body against it.
    released_digest = hashlib.sha256(rewritten_snapshot.encode('utf-8')).hexdigest()
    try:
        event_payload = build_release_payload(job, release_id, released_digest, frontmatter)
        if (_quality(job)[0] or '').lower() == 'c':
            event_payload['quality_override'] = True
    except PublicationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    release = DocumentRelease(
        id=release_id,
        job_id=job.id,
        owner_id=user.id,
        # Snapshot stored here has 'artifacts/<file>' image links rewritten to the
        # absolute release-artifact endpoint. markdown_sha256 stays the pre-rewrite
        # digest (frontend optimistic-concurrency contract); the event payload above
        # carries the post-rewrite digest that matches these actual served bytes.
        markdown_snapshot=rewritten_snapshot,
        markdown_sha256=digest,
        payload=event_payload,
        status='pending',
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(release)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
        if existing is None:
            raise
        frozen = existing.payload if isinstance(existing.payload, dict) else {}
        if frozen.get('document_version') != job.document_version or existing.markdown_sha256 != digest:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job changed; a new document version is required')
        existing_by = db.get(User, existing.owner_id).username if existing.owner_id else None
        return _summary(existing, existing_by)  # type: ignore[return-value]
    try:
        publication_tasks.deliver_release.delay(release.id)
    except Exception:
        logger.exception('publication queue unavailable for release %s', release.id)
    return _summary(release, user.username)  # type: ignore[return-value]


@router.post(
    '/collections/{collection_id}/release-all',
    response_model=PortalCollectionReleaseResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def release_collection_documents(
    collection_id: str,
    payload: PortalCollectionReleaseRequest,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalCollectionReleaseResponse:
    if not publication_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Portal publication is not configured')
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Collection not found')
    if user.role != UserRole.ADMIN and not _can_manage_collection(db, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot release this collection')

    collection_id_expr = Job.processing_info['settings']['collection_id'].as_string()
    jobs = db.scalars(
        select(Job)
        .outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id)
        .where(collection_id_expr == collection.id, DocumentRelease.id.is_(None))
        .order_by(Job.created_at.asc(), Job.id.asc())
    ).all()
    pending: list[tuple[Job, str, str, dict]] = []
    skipped = 0
    for job in jobs:
        if not _can_release_light(db, job, collection, user):
            skipped += 1
            continue
        try:
            snapshot, digest, frontmatter = canonical_snapshot(db, job, collection)
        except PublicationValidationError:
            skipped += 1
            continue
        grade, _recommendation = _quality(job)
        if (grade or '').lower() == 'c' and not payload.accept_quality_warnings:
            skipped += 1
            continue
        pending.append((job, snapshot, digest, frontmatter))

    releases: list[DocumentRelease] = []
    for job, snapshot, digest, frontmatter in pending:
        release_id = str(uuid.uuid4())
        rewritten_snapshot = rewrite_release_image_urls(snapshot, release_id)
        released_digest = hashlib.sha256(rewritten_snapshot.encode('utf-8')).hexdigest()
        event_payload = build_release_payload(job, release_id, released_digest, frontmatter)
        if (_quality(job)[0] or '').lower() == 'c':
            event_payload['quality_override'] = True
        release = DocumentRelease(
            id=release_id,
            job_id=job.id,
            owner_id=user.id,
            # markdown_sha256 stays the pre-rewrite digest (optimistic-concurrency
            # contract); event_payload carries the post-rewrite digest matching the
            # bytes actually served for this snapshot.
            markdown_snapshot=rewritten_snapshot,
            markdown_sha256=digest,
            payload=event_payload,
            status='pending',
            next_attempt_at=datetime.now(timezone.utc),
        )
        db.add(release)
        releases.append(release)
    db.commit()

    for release in releases:
        try:
            publication_tasks.deliver_release.delay(release.id)
        except Exception:
            logger.exception('publication queue unavailable for release %s', release.id)
    return PortalCollectionReleaseResponse(released=len(releases), skipped=skipped)


@router.post('/documents/{job_id}/edit', response_model=PortalEditResponse, status_code=status.HTTP_202_ACCEPTED)
def edit_portal_document(
    job_id: str,
    payload: PortalEditRequest,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalEditResponse:
    """Change a document's Markdown and release the change at once.

    The change stays the published version until a changed source -- a new
    upload or a Confluence sync of a changed page -- brings the next one. An
    unreleased version is edited in place; a released one becomes a new
    version (see _new_edit_version). Save and release share one transaction:
    if the release is refused (quality gate, grade C without confirmation),
    nothing is stored.
    """
    if not publication_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Portal publication is not configured')
    job = _load_visible_job(db, job_id, user, for_update=True)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Job has no known collection')
    _protected(job)
    if not _job_is_controlled(db, job, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot edit this document')
    if _newer_version_exists(db, job):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='A newer version of this document exists')
    if not _can_edit(db, job, collection, user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Document cannot be edited in its current state')
    release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
    current = release.markdown_sha256 if release is not None else None
    if current is None:
        try:
            current = canonical_snapshot(db, job, collection)[1]
        except PublicationValidationError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if current != payload.markdown_sha256.lower():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Markdown changed; refresh the document preview')

    markdown = payload.markdown.replace('\r\n', '\n')
    if release is not None:
        markdown = restore_relative_image_urls(markdown, release.id)
    try:
        _parse_frontmatter(markdown.strip())
    except PublicationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    target = _new_edit_version(db, job) if release is not None else job
    record_markdown_version(db, target, markdown, now)
    info = dict(target.processing_info) if isinstance(target.processing_info, dict) else {}
    info['portal_edit'] = {'by': user.username, 'by_id': user.id, 'at': now.isoformat()}
    target.processing_info = info
    db.flush()
    try:
        digest = canonical_snapshot(db, target, collection)[1]
    except PublicationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    summary = release_portal_document(
        target.id,
        PortalReleaseRequest(markdown_sha256=digest, accept_quality_warning=payload.accept_quality_warning),
        db,
        user,
    )
    return PortalEditResponse(document_id=target.id, document_version=target.document_version, release=summary)


@router.post('/documents/bulk', response_model=PortalBulkActionResponse)
def bulk_portal_documents(
    payload: PortalBulkActionRequest,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalBulkActionResponse:
    if payload.action == 'release' and not publication_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Portal publication is not configured')
    done = 0
    errors: list[PortalBulkErrorItem] = []
    releases: list[DocumentRelease] = []
    withdrawals: list[str] = []
    for job_id in payload.job_ids:
        try:
            job = db.get(Job, job_id, with_for_update=True)
            if job is None or job.benchmark_run_id is not None:
                errors.append(PortalBulkErrorItem(job_id=job_id, reason='Dokument nicht gefunden'))
                continue
            _require_visible(db, job, user)
            collection = _collection_for_job(db, job)
            if collection is None:
                errors.append(PortalBulkErrorItem(job_id=job_id, reason='Kein Wissensbereich zugeordnet'))
                continue
            if payload.action == 'release':
                if not _can_release_light(db, job, collection, user):
                    errors.append(PortalBulkErrorItem(job_id=job_id, reason='Keine Freigabeberechtigung'))
                    continue
                try:
                    snapshot, digest, frontmatter = canonical_snapshot(db, job, collection)
                except PublicationValidationError as exc:
                    errors.append(PortalBulkErrorItem(job_id=job_id, reason=str(exc)))
                    continue
                grade, _recommendation = _quality(job)
                if (grade or '').lower() == 'c' and not payload.accept_quality_warnings:
                    errors.append(PortalBulkErrorItem(job_id=job_id, reason='Qualitätsstufe C erfordert Bestätigung'))
                    continue
                if db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)) is not None:
                    errors.append(PortalBulkErrorItem(job_id=job_id, reason='Bereits freigegeben'))
                    continue
                release_id = str(uuid.uuid4())
                rewritten_snapshot = rewrite_release_image_urls(snapshot, release_id)
                released_digest = hashlib.sha256(rewritten_snapshot.encode('utf-8')).hexdigest()
                event_payload = build_release_payload(job, release_id, released_digest, frontmatter)
                if (grade or '').lower() == 'c':
                    event_payload['quality_override'] = True
                release = DocumentRelease(
                    id=release_id,
                    job_id=job.id,
                    owner_id=user.id,
                    markdown_snapshot=rewritten_snapshot,
                    markdown_sha256=digest,
                    payload=event_payload,
                    status='pending',
                    next_attempt_at=datetime.now(timezone.utc),
                )
                db.add(release)
                releases.append(release)
                done += 1
            elif payload.action in ('skip', 'unskip'):
                _protected(job)
                if not _can_release_light(db, job, collection, user):
                    errors.append(PortalBulkErrorItem(job_id=job_id, reason='Keine Berechtigung'))
                    continue
                if db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)) is not None:
                    errors.append(PortalBulkErrorItem(job_id=job_id, reason='Bereits freigegeben'))
                    continue
                _set_review_decision(job, 'skipped' if payload.action == 'skip' else None, user)
                done += 1
            elif payload.action == 'delete':
                # Permission already checked by _require_visible above (same
                # check the single DELETE /api/v1/jobs/{id} endpoint applies).
                # Password-protected jobs additionally require the password,
                # which this bulk endpoint has no way to collect per job.
                if job.password_hash:
                    errors.append(PortalBulkErrorItem(job_id=job_id, reason='Passwortgeschützte Dokumente können nur einzeln gelöscht werden'))
                    continue
                if not payload.withdraw_released and db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)) is not None:
                    errors.append(PortalBulkErrorItem(job_id=job_id, reason='Freigegebene Dokumente können nicht gelöscht werden'))
                    continue
                if _withdraw_and_delete_job(db, job):
                    withdrawals.append(job_id)
                done += 1
        except HTTPException as exc:
            errors.append(PortalBulkErrorItem(job_id=job_id, reason=str(exc.detail)))
        except Exception:  # noqa: BLE001 - never abort the whole batch on one job
            logger.exception('bulk portal action failed for job %s', job_id)
            errors.append(PortalBulkErrorItem(job_id=job_id, reason='Unerwarteter Fehler'))
    db.commit()
    for release in releases:
        try:
            publication_tasks.deliver_release.delay(release.id)
        except Exception:
            logger.exception('publication queue unavailable for release %s', release.id)
    dispatch_withdrawals(withdrawals)
    return PortalBulkActionResponse(done=done, errors=errors)


def _release_control(db, release: DocumentRelease, user: User) -> tuple[Job, Collection]:
    job = _load_visible_job(db, release.job_id, user)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document collection not found')
    # Members may release (ADR 0008), so they may also retry a delivery --
    # the same people as a release or a reindex, not only the releaser.
    if not _job_is_controlled(db, job, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot retry this release')
    return job, collection


@knowledge_router.get('/releases/{release_id}/download', response_class=PlainTextResponse)
def download_release(release_id: str, db=Depends(get_db), user: User | None = Depends(get_knowledge_reader)) -> PlainTextResponse:
    release = db.get(DocumentRelease, release_id)
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Release not found')
    if db.get(KnowledgeWithdrawal, release.job_id) is not None:
        raise HTTPException(status_code=410, detail='Document was withdrawn from Knowledge')
    job = db.get(Job, release.job_id) if user is None else _load_visible_job(db, release.job_id, user)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
    _protected(job)
    return PlainTextResponse(release.markdown_snapshot, media_type='text/markdown; charset=utf-8')


@knowledge_router.get('/releases/{release_id}/artifacts/{filename}')
def download_release_artifact(
    release_id: str,
    filename: str,
    db=Depends(get_db),
    user: User | None = Depends(get_knowledge_reader),
) -> Response:
    """Serve an image/attachment referenced by a released markdown snapshot.

    Same auth and withdrawal checks as download_release; the artifact is
    looked up by (release.job_id, filename) so a foreign filename 404s
    instead of ever returning another job's bytes (mirrors the IDOR-safe
    pattern in routes.get_job_artifact_content).
    """
    release = db.get(DocumentRelease, release_id)
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Release not found')
    if db.get(KnowledgeWithdrawal, release.job_id) is not None:
        raise HTTPException(status_code=410, detail='Document was withdrawn from Knowledge')
    job = db.get(Job, release.job_id) if user is None else _load_visible_job(db, release.job_id, user)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
    _protected(job)
    artifact = db.scalar(
        select(JobArtifact).where(JobArtifact.job_id == release.job_id, JobArtifact.filename == filename)
    )
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Artifact not found')
    disposition = 'inline' if artifact.content_type in _ARTIFACT_INLINE_CONTENT_TYPES else 'attachment'
    return Response(
        content=object_store.read_bytes(db, artifact.object_id),
        media_type=artifact.content_type,
        headers={
            'Content-Disposition': _content_disposition(disposition, artifact.filename),
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, max-age=3600',
        },
    )


@router.post('/releases/{release_id}/retry', response_model=PortalReleaseSummary)
def retry_release(release_id: str, db=Depends(get_db), user: User = Depends(get_current_user)) -> PortalReleaseSummary:
    if not publication_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Portal publication is not configured')
    release = db.get(DocumentRelease, release_id)
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Release not found')
    _release_control(db, release, user)
    released_by = db.get(User, release.owner_id).username if release.owner_id else None
    if release.status == 'sent':
        return _summary(release, released_by)  # type: ignore[return-value]
    if release.status not in ('pending', 'failed'):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Release cannot be retried')
    release.status = 'pending'
    release.error_message = None
    release.next_attempt_at = datetime.now(timezone.utc)
    release.attempts = 0
    if release.lease_until is None or aware_utc(release.lease_until) <= datetime.now(timezone.utc):
        release.lease_token = None
        release.lease_until = None
    db.commit()
    try:
        publication_tasks.deliver_release.delay(release.id)
    except Exception:
        logger.exception('publication queue unavailable for retry %s', release.id)
    return _summary(release, released_by)  # type: ignore[return-value]


@router.post('/documents/{job_id}/reindex', response_model=PortalReleaseSummary)
def reindex_portal_document(job_id: str, db=Depends(get_db), user: User = Depends(get_current_user)) -> PortalReleaseSummary:
    """'Neu indizieren' for a single already-released document (incident 2026-09-22
    follow-up): unlike retry_release above, this is a regular action offered
    for any released document, not only a delivery failure, so it accepts
    any existing DocumentRelease status (pending/sent/failed) rather than
    refusing 'sent' releases.
    """
    if not publication_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Portal publication is not configured')
    job = _load_visible_job(db, job_id, user)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document collection not found')
    # Withdrawal check comes before the permission check: _can_release_light
    # itself returns False for a withdrawn document, which would otherwise mask
    # the more specific 409 'withdrawn' response behind a generic 403.
    if db.get(KnowledgeWithdrawal, job.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Document was withdrawn from Knowledge')
    # Contract: permission = can_release, matching the frontend's own can_release
    # gate on the 'Neu indizieren' button (reviews.tsx).
    if not _can_release_light(db, job, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot reindex this document')
    release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
    if release is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Document has not been released yet')
    released_by = db.get(User, release.owner_id).username if release.owner_id else None
    release.status = 'pending'
    release.error_message = None
    release.next_attempt_at = datetime.now(timezone.utc)
    release.attempts = 0
    if release.lease_until is None or aware_utc(release.lease_until) <= datetime.now(timezone.utc):
        release.lease_token = None
        release.lease_until = None
    # Forces Knowledge to replace already-indexed chunks instead of skipping this
    # delivery as a duplicate by release_id/markdown_sha256 (contracts/events/document.released.md).
    payload = release.payload if isinstance(release.payload, dict) else {}
    payload['reindex'] = True
    release.payload = payload
    flag_modified(release, 'payload')
    db.commit()
    try:
        publication_tasks.deliver_release.delay(release.id)
    except Exception:
        logger.exception('publication queue unavailable for reindex %s', release.id)
    return _summary(release, released_by)  # type: ignore[return-value]


@router.post('/collections/{collection_id}/reindex', response_model=PortalReindexCollectionResponse)
def reindex_collection_documents(collection_id: str, db=Depends(get_db), user: User = Depends(get_current_user)) -> PortalReindexCollectionResponse:
    """'Wissensbereich neu indizieren' for every non-withdrawn release of one
    collection (incident 2026-09-22 follow-up). Reuses the same outbox/backoff/
    lease machinery as reindex_portal_document above, scoped by collection via
    requeue_collection_releases_for_reindex.
    """
    if not publication_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Portal publication is not configured')
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Collection not found')
    if user.role != UserRole.ADMIN and not _can_manage_collection(db, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot reindex this collection')
    release_ids = requeue_collection_releases_for_reindex(db, collection.id)
    db.commit()
    for release_id in release_ids:
        try:
            publication_tasks.deliver_release.delay(release_id)
        except Exception:
            logger.exception('publication queue unavailable for reindex %s', release_id)
    return PortalReindexCollectionResponse(requeued=len(release_ids))
