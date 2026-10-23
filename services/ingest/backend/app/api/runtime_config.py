"""Admin view of the running configuration (app/services/runtime_config.py):
overview, connection checks and export. Read-only -- the configuration
itself stays owned by the deployment."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.database.session import get_db
from app.services import runtime_config

router = APIRouter(
    prefix='/api/v1/admin/config',
    tags=['config-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)


class S3CheckOverrides(BaseModel):
    """Form values for the S3 check; omitted fields keep the running value,
    an empty secret too."""

    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_key_prefix: str | None = None
    s3_addressing_style: Literal['auto', 'path', 'virtual'] | None = None
    s3_sse: Literal['', 'AES256', 'aws:kms'] | None = None
    s3_kms_key_id: str | None = None


@router.get('')
def get_config(db: Session = Depends(get_db)) -> dict:
    return runtime_config.config_overview(db)


@router.post('/checks/{name}')
def run_check(name: str, overrides: S3CheckOverrides | None = None, db: Session = Depends(get_db)) -> dict:
    check = runtime_config.CHECKS.get(name)
    if check is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unbekannter Test.')
    return check(db, overrides.model_dump(exclude_none=True) if overrides else None)


@router.get('/export')
def export_config(format: Literal['env', 'json'] = Query('env'), db: Session = Depends(get_db)):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    headers = {'Cache-Control': 'no-store'}
    if format == 'json':
        headers['Content-Disposition'] = f'attachment; filename="weave-ingest-config-{stamp}.json"'
        return JSONResponse(runtime_config.export_json(db), headers=headers)
    headers['Content-Disposition'] = f'attachment; filename="weave-ingest-{stamp}.env"'
    return PlainTextResponse(runtime_config.export_env(), headers=headers)
