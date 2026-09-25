"""GET /v1/me -- the authenticated caller's own profile (`username` + UI
`locale`). PUT /v1/me/locale -- change the locale preference.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import enforce_rate_limit
from app.models.models import User
from app.schemas.me import LocaleResponse, LocaleUpdateRequest, MeResponse
from app.services.ingest_identity import IngestIdentityError, ingest_subject, update_identity_locale

router = APIRouter(prefix='/v1', tags=['me'])


@router.get('/me', response_model=MeResponse)
def get_me(user: User = Depends(enforce_rate_limit)) -> MeResponse:
    return MeResponse(username=user.username, locale=user.locale)


@router.put('/me/locale', response_model=LocaleResponse)
def update_my_locale(
    payload: LocaleUpdateRequest, db: Session = Depends(get_db), user: User = Depends(enforce_rate_limit)
) -> LocaleResponse:
    """For a Weave-Ingest-backed identity (`oidc_subject` prefixed
    `weave-ingest:`) the write goes to that service FIRST, via
    `update_identity_locale` -- unreachable/misconfigured there is a 503
    that leaves the local value untouched, never a locally-only write that
    would drift from the identity authority. A user with no ingest subject
    has nothing to forward to and is stored locally only.
    """
    subject = ingest_subject(user.oidc_subject)
    if subject is not None:
        try:
            update_identity_locale(subject, payload.locale)
        except IngestIdentityError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Identity authority unavailable'
            ) from None
    user.locale = payload.locale
    db.commit()
    return LocaleResponse(locale=user.locale)
