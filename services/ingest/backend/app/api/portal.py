from __future__ import annotations

import logging
from pathlib import Path
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.api.deps import aware_utc, get_current_user, get_knowledge_reader
from app.api.routes import (
    _active_process_job_ids,
    _apply_visible_filter,
    _can_manage_collection,
    _content_disposition,
    _is_import_page_job,
    _owner_visible,
    _require_visible_collection,
    restart_job,
)
from app.core.config import settings
from app.database.session import get_db
from app.models.models import Collection, DocumentRelease, ImportRun, ImportRunStatus, Job, JobStatus, KnowledgeWithdrawal, Team, User, UserRole
from app.schemas.jobs import JobRestartRequest
from app.schemas.portal import (
    PortalConfigResponse,
    PortalDocumentDetail,
    PortalDocumentItem,
    PortalDocumentListResponse,
    PortalReleaseRequest,
    PortalReleaseSummary,
    PortalReprocessRequest,
    PortalReprocessResponse,
)
from app.services.publications import (
    PublicationValidationError,
    build_release_payload,
    canonical_snapshot,
    publication_configured,
    release_endpoint,
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


def _quality(job: Job) -> tuple[str | None, str | None]:
    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    execution = info.get('execution') if isinstance(info.get('execution'), dict) else {}
    quality = execution.get('quality_gate') if isinstance(execution.get('quality_gate'), dict) else {}
    grade = quality.get('grade') if isinstance(quality.get('grade'), str) else None
    recommendation = quality.get('recommendation') if isinstance(quality.get('recommendation'), str) else None
    return grade, recommendation


def _job_is_controlled(db, job: Job, collection: Collection, user: User) -> bool:
    return user.role == UserRole.ADMIN or job.owner_id == user.id or _can_manage_collection(db, collection, user)


def _import_run_finished(db, job: Job) -> bool:
    if not job.import_run_id:
        return True
    run_status = db.scalar(select(ImportRun.status).where(ImportRun.id == job.import_run_id))
    return run_status == ImportRunStatus.FINISHED


def _summary(release: DocumentRelease | None) -> PortalReleaseSummary | None:
    if release is None:
        return None
    return PortalReleaseSummary(
        id=release.id,
        created_at=release.created_at,
        status=release.status,
        error_message=release.error_message,
    )


def _can_release_light(db, job: Job, collection: Collection, user: User) -> bool:
    """Cheap can_release check for a loaded detail row.

    List responses use the equivalent SQL expression below. Canonical
    frontmatter validation belongs to the detail/release paths, where the
    markdown is intentionally loaded.
    """
    if job.status != JobStatus.FINISHED or not job.result_markdown or job.password_hash:
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
        release=_summary(release),
    )


def _load_visible_job(db, job_id: str, user: User, *, for_update: bool = False) -> Job:
    # The release mutation uses this row lock so a concurrent save/restart
    # cannot pass its issued-release guard before the immutable snapshot is
    # committed. PostgreSQL enforces the lock; SQLite's FOR UPDATE is a
    # no-op, but its single-writer transaction still prevents concurrent
    # commits in the test/runtime dialect used here.
    job = db.get(Job, job_id, with_for_update=for_update)
    if job is None or job.benchmark_run_id is not None or not _owner_visible(db, job.owner_id, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
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
    if not job.result_markdown:
        return None
    try:
        return canonical_snapshot(db, job, collection)[0]
    except PublicationValidationError:
        return job.result_markdown


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
    return PortalConfigResponse(
        publication_configured=publication_configured(),
        team_name=team.name if team is not None else None,
    )


@router.get('/documents', response_model=PortalDocumentListResponse)
def list_portal_documents(
    collection_id: str | None = None,
    review_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalDocumentListResponse:
    collection_id_expr = Job.processing_info['settings']['collection_id'].as_string()
    quality_grade_expr = Job.processing_info['execution']['quality_gate']['grade'].as_string()
    quality_recommendation_expr = Job.processing_info['execution']['quality_gate']['recommendation'].as_string()
    control_expr = (
        True
        if user.role == UserRole.ADMIN
        else or_(Job.owner_id == user.id, Collection.owner_id == user.id)
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
            defer(Job.upload_content),
            defer(Job.result_markdown),
            defer(DocumentRelease.markdown_snapshot),
            defer(DocumentRelease.payload),
        )
        .order_by(Job.created_at.desc())
    )
    query = _apply_visible_filter(query, user)
    if collection_id is not None:
        query = query.where(Collection.id == collection_id)
    if review_only:
        query = query.where(
            Job.status == JobStatus.FINISHED,
            DocumentRelease.id.is_(None),
            or_(ImportRun.id.is_(None), ImportRun.status == ImportRunStatus.FINISHED),
        )

    count_query = (
        select(func.count())
        .select_from(Job)
        .join(Collection, Collection.id == collection_id_expr)
        .outerjoin(ImportRun, ImportRun.id == Job.import_run_id)
    )
    count_query = _apply_visible_filter(count_query, user)
    if collection_id is not None:
        count_query = count_query.where(Collection.id == collection_id)
    if review_only:
        count_query = count_query.outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id).where(
            Job.status == JobStatus.FINISHED,
            DocumentRelease.id.is_(None),
            or_(ImportRun.id.is_(None), ImportRun.status == ImportRunStatus.FINISHED),
        )
    total = int(db.scalar(count_query) or 0)
    rows = db.execute(query.offset(offset).limit(limit)).all()
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
    if release is not None:
        return PortalDocumentDetail(
            **_item(
                db, job, collection, user, release,
                can_release=_can_release_light(db, job, collection, user),
            ).model_dump(),
            markdown=release.markdown_snapshot,
            markdown_sha256=release.markdown_sha256,
            profile_id=_profile_id(job),
            can_reprocess=False,
        )
    try:
        markdown, digest, _ = canonical_snapshot(db, job, collection)
    except PublicationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PortalDocumentDetail(
        **_item(db, job, collection, user, release, can_release=_can_release_light(db, job, collection, user)).model_dump(),
        markdown=markdown,
        markdown_sha256=digest,
        profile_id=_profile_id(job),
        can_reprocess=_can_reprocess_light(db, job, collection, user, release),
    )


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
        .options(defer(Job.upload_content), defer(DocumentRelease.payload))
        .order_by(Job.created_at.asc(), Job.id.asc())
    )
    rows = db.execute(_apply_visible_filter(query, user)).all()

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
    if job.id in _active_process_job_ids():
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
        return _summary(existing)  # type: ignore[return-value]

    release_id = str(uuid.uuid4())
    try:
        event_payload = build_release_payload(job, release_id, digest, frontmatter)
        if (_quality(job)[0] or '').lower() == 'c':
            event_payload['quality_override'] = True
    except PublicationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    release = DocumentRelease(
        id=release_id,
        job_id=job.id,
        owner_id=user.id,
        markdown_snapshot=snapshot,
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
        return _summary(existing)  # type: ignore[return-value]
    try:
        publication_tasks.deliver_release.delay(release.id)
    except Exception:
        logger.exception('publication queue unavailable for release %s', release.id)
    return _summary(release)  # type: ignore[return-value]


def _release_control(db, release: DocumentRelease, user: User) -> tuple[Job, Collection]:
    job = _load_visible_job(db, release.job_id, user)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document collection not found')
    if user.role != UserRole.ADMIN and release.owner_id != user.id and collection.owner_id != user.id:
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


@router.post('/releases/{release_id}/retry', response_model=PortalReleaseSummary)
def retry_release(release_id: str, db=Depends(get_db), user: User = Depends(get_current_user)) -> PortalReleaseSummary:
    if not publication_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Portal publication is not configured')
    release = db.get(DocumentRelease, release_id)
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Release not found')
    _release_control(db, release, user)
    if release.status == 'sent':
        return _summary(release)  # type: ignore[return-value]
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
    return _summary(release)  # type: ignore[return-value]
