"""GET /v1/me -- the authenticated caller's own profile (`username` + UI
`locale`). PUT /v1/me/locale -- change the locale preference.
GET/POST/DELETE /v1/me/tokens -- self-service personal API tokens.
"""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import issue_api_token
from app.core.db import get_db
from app.core.ratelimit import enforce_rate_limit
from app.models.models import ApiToken, User
from app.schemas.me import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenListResponse,
    ApiTokenResponse,
    LocaleResponse,
    LocaleUpdateRequest,
    MeResponse,
)
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


# --- Personal API tokens ------------------------------------------------------

_TOKEN_MAX_PER_USER = 20


def _require_session(authorization: str | None = Header(default=None)) -> None:
    """Token management needs a signed-in session: a stolen bearer token
    must not mint its own replacements or revoke its owner's other tokens
    (same rule as Weave-Ingest's own token endpoints)."""
    if authorization and authorization.lower().startswith('bearer '):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='API tokens cannot manage tokens; use a signed-in session'
        )


def _token_response(token: ApiToken) -> ApiTokenResponse:
    return ApiTokenResponse(
        id=token.id, label=token.label, created_at=token.created_at, expires_at=token.expires_at,
        last_used_at=token.last_used_at,
    )


@router.get('/me/tokens', response_model=ApiTokenListResponse, dependencies=[Depends(_require_session)])
def list_my_tokens(db: Session = Depends(get_db), user: User = Depends(enforce_rate_limit)) -> ApiTokenListResponse:
    tokens = db.scalars(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())).all()
    return ApiTokenListResponse(items=[_token_response(token) for token in tokens])


@router.post(
    '/me/tokens', response_model=ApiTokenCreateResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_session)],
)
def create_my_token(
    payload: ApiTokenCreateRequest, db: Session = Depends(get_db), user: User = Depends(enforce_rate_limit)
) -> ApiTokenCreateResponse:
    """The raw token is in this response only; afterwards just its label and
    dates can be listed."""
    count = db.scalar(select(func.count()).select_from(ApiToken).where(ApiToken.user_id == user.id)) or 0
    if count >= _TOKEN_MAX_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f'Token limit reached ({_TOKEN_MAX_PER_USER} per user)'
        )
    token, raw_token = issue_api_token(db, user, label=payload.label.strip(), expires_days=payload.expires_in_days)
    db.commit()
    return ApiTokenCreateResponse(token=raw_token, **_token_response(token).model_dump())


@router.delete('/me/tokens/{token_id}', status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_require_session)])
def delete_my_token(token_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(enforce_rate_limit)) -> None:
    token = db.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Token not found')
    db.delete(token)
    db.commit()
