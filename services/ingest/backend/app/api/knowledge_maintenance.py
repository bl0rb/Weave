"""'Index aus Freigaben neu aufbauen' -- manual admin action that re-queues
delivery for every non-withdrawn DocumentRelease through the existing
publication outbox, exactly like the disaster-recovery import engine's
automatic post-import rebuild does (see app/services/backup.py's
`requeue_releases_for_index_rebuild`, reused here under its public name
rather than duplicated). Also exposes the small status projection the admin
UI polls while a rebuild (or the automatic one after an import) is in
flight.

Refuses with 409 while a backup import is RUNNING: an import's own wipe/
restore of `document_releases` would otherwise race a concurrent requeue of
the very rows it is about to overwrite.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.database.session import get_db
from app.models.models import BackupRun, BackupRunKind, BackupRunStatus, DocumentRelease, KnowledgeWithdrawal, User
from app.schemas.knowledge_maintenance import KnowledgeRebuildResponse, KnowledgeRebuildStatusResponse
from app.services.backup import requeue_releases_for_index_rebuild
from app.services.publications import publication_configured
from app.services.security import enforce_rate_limit

router = APIRouter(prefix='/api/v1/admin/knowledge', dependencies=[Depends(require_admin), Depends(origin_guard)])


def _active_import_run(db: Session) -> BackupRun | None:
    return db.scalar(
        select(BackupRun).where(BackupRun.kind == BackupRunKind.IMPORT, BackupRun.status == BackupRunStatus.RUNNING)
    )


@router.post('/rebuild', response_model=KnowledgeRebuildResponse)
def rebuild_knowledge_index(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> KnowledgeRebuildResponse:
    enforce_rate_limit(request)
    if not publication_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Die Veröffentlichung an den Wissensdienst ist nicht konfiguriert.',
        )
    if _active_import_run(db) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Es läuft gerade eine Wiederherstellung. Bitte warten Sie, bis diese abgeschlossen ist.',
        )
    requeued = requeue_releases_for_index_rebuild(db)
    db.commit()
    return KnowledgeRebuildResponse(requeued=requeued, worker_required=True)


@router.get('/rebuild-status', response_model=KnowledgeRebuildStatusResponse)
def rebuild_knowledge_index_status(request: Request, db: Session = Depends(get_db)) -> KnowledgeRebuildStatusResponse:
    enforce_rate_limit(request)
    releases = DocumentRelease.__table__
    total_releases = db.scalar(select(func.count()).select_from(releases)) or 0
    withdrawn_job_ids = select(KnowledgeWithdrawal.job_id)
    withdrawn = db.scalar(select(func.count()).select_from(releases).where(releases.c.job_id.in_(withdrawn_job_ids))) or 0
    pending = db.scalar(select(func.count()).select_from(releases).where(releases.c.status == 'pending')) or 0
    sent = db.scalar(select(func.count()).select_from(releases).where(releases.c.status == 'sent')) or 0
    failed = db.scalar(select(func.count()).select_from(releases).where(releases.c.status == 'failed')) or 0
    last_sent_at = db.scalar(select(func.max(releases.c.updated_at)).where(releases.c.status == 'sent'))
    return KnowledgeRebuildStatusResponse(
        total_releases=total_releases, withdrawn=withdrawn, pending=pending, sent=sent, failed=failed,
        last_sent_at=last_sent_at,
    )
