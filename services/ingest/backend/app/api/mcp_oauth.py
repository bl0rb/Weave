"""Resolve IdP-issued MCP access tokens to existing Weave users."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from joserfc import jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.technical_identities import _require_tools_introspection_token
from app.core.config import settings
from app.database.session import get_db
from app.models.models import AuthProvider, User
from app.services.oidc import OIDCError, fetch_jwks, get_discovery_document

router = APIRouter(
    prefix='/api/v1/internal/mcp-oauth',
    tags=['mcp-oauth-internal'],
    dependencies=[Depends(_require_tools_introspection_token)],
)


class IntrospectionRequest(BaseModel):
    token: str = Field(min_length=1, max_length=16384)
    issuer: str


@router.post('/introspect')
def introspect_access_token(payload: IntrospectionRequest, db: Session = Depends(get_db)) -> dict:
    if not settings.mcp_oauth_provider_slug or not settings.mcp_oauth_audience or not settings.mcp_oauth_required_scopes:
        raise HTTPException(status_code=503, detail='service misconfigured')
    provider = db.scalar(select(AuthProvider).where(AuthProvider.slug == settings.mcp_oauth_provider_slug))
    if provider is None or not provider.enabled or payload.issuer != provider.issuer_url:
        return {'active': False}
    if settings.mcp_oauth_audience == provider.client_id:
        raise HTTPException(status_code=503, detail='MCP audience must differ from the portal client')

    # Discover only the operator-selected provider, never an unverified
    # token's issuer. Keep the existing SSRF-safe OIDC fetch helpers.
    try:
        discovery = get_discovery_document(provider.issuer_url)
        if discovery.get('issuer') != provider.issuer_url:
            raise OIDCError('issuer mismatch')
        keys = fetch_jwks(discovery['jwks_uri'])
    except (OIDCError, KeyError, ValueError):
        raise HTTPException(status_code=503, detail='identity provider unavailable') from None

    try:
        claims = jwt.decode(payload.token, keys, algorithms=['RS256', 'ES256']).claims
        jwt.JWTClaimsRegistry(
            iss={'essential': True, 'values': [provider.issuer_url]},
            aud={'essential': True, 'values': [settings.mcp_oauth_audience]},
            exp={'essential': True},
            sub={'essential': True},
        ).validate(claims)
        subject = claims.get(settings.mcp_oauth_subject_claim)
        scopes = claims.get('scope') or claims.get('scp', '')
        if not isinstance(subject, str) or not subject or not isinstance(scopes, str):
            return {'active': False}
        if not set(settings.mcp_oauth_required_scopes).issubset(scopes.split()):
            return {'active': False, 'error': 'insufficient_scope'}
    except Exception:  # Invalid signature, algorithm, token structure or claims.
        return {'active': False}

    # Bind by provider + verified subject only, never by email/username or
    # token-supplied groups. Users first sign in through the Weave portal.
    if settings.mcp_oauth_subject_claim == 'oid':
        try:
            subject = str(UUID(subject))
        except ValueError:
            return {'active': False}
        identity_column = User.oidc_object_id
    else:
        identity_column = User.oidc_subject
    user = db.scalar(select(User).where(
        User.oidc_provider_id == provider.id, identity_column == subject, User.is_active.is_(True),
    ))
    if user is None:
        return {'active': False}
    teams = list(dict.fromkeys(([user.team.name] if user.team else []) + [team.name for team in user.memberships]))
    return {
        'active': True, 'user_id': user.id, 'username': user.username,
        'subject': user.id, 'teams': teams,
    }
