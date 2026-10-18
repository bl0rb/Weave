"""Read-only VL-connections list for authenticated users (e.g. the
connections page, so non-admins can see which vision models a processing
profile can run against). Admin VL-connection CRUD lives in app/api/auth.py.

Registered in app/main.py under the same get_current_user + origin_guard
dependencies as the main job router.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database.session import get_db
from app.models.models import User, VlConnection
from app.schemas.vl_connections import VlConnectionPublic, VlConnectionPublicListResponse
from app.services.security import enforce_rate_limit

router = APIRouter(prefix='/api/v1', tags=['vl-connections'])


@router.get('/vl-connections', response_model=VlConnectionPublicListResponse)
def list_vl_connections(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> VlConnectionPublicListResponse:
    enforce_rate_limit(request)
    rows = db.scalars(
        select(VlConnection).where(VlConnection.enabled.is_(True)).order_by(VlConnection.name)
    ).all()
    return VlConnectionPublicListResponse(items=[VlConnectionPublic.model_validate(row) for row in rows])
