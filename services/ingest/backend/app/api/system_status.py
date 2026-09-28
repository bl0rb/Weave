from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.database.session import get_db
from app.schemas.system_status import AdminSystemStatusResponse, SystemStatusResponse
from app.services.system_status import system_status

router = APIRouter(prefix='/api/v1/system-status', tags=['system-status'], dependencies=[Depends(get_current_user)])
router_admin = APIRouter(
    prefix='/api/v1/auth/admin/system-status', tags=['system-status-admin'], dependencies=[Depends(require_admin)]
)


@router.get('', response_model=SystemStatusResponse)
def get_system_status(db: Session = Depends(get_db)) -> dict:
    return system_status(db)


@router_admin.get('', response_model=AdminSystemStatusResponse)
def get_admin_system_status(refresh: bool = False, db: Session = Depends(get_db)) -> dict:
    """Every component with latency and detail; ``refresh`` skips the short cache."""
    return system_status(db, refresh=refresh)
