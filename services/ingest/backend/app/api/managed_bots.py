"""Admin CRUD, owner maintenance (ADR 0008) and Runtime-only projection
for n8n-backed bots."""

import hmac
import httpx
import logging
from datetime import datetime, timezone
from pydantic import ValidationError

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, origin_guard, require_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.models import (
    BotGrant, BotRole, BotTombstone, ChatProviderConfig, Collection, ManagedBot, Team, User, UserRole,
)
from app.schemas.managed_bots import (
    BotGrantInput,
    BotGrantResponse,
    ManagedBotAdminResponse,
    ManagedBotCreate,
    ManagedBotInternalListResponse,
    ManagedBotInternalResponse,
    ManagedBotListResponse,
    ManagedBotOwnerListResponse,
    ManagedBotOwnerResponse,
    ManagedBotOwnerUpdate,
    ManagedBotUpdate,
)
from app.services.collection_access import collection_role
from app.services.security import (
    decrypt_managed_bot_auth_token,
    encrypt_managed_bot_auth_token,
    enforce_rate_limit,
)

logger = logging.getLogger(__name__)


router_admin = APIRouter(
    prefix='/api/v1/auth/admin/bots',
    tags=['managed-bots-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)
router_internal = APIRouter(prefix='/api/v1/internal/bots', tags=['managed-bots-internal'])
router_owner = APIRouter(prefix='/api/v1/bots', tags=['managed-bots-owner'], dependencies=[Depends(origin_guard)])


def _grant_responses(row: ManagedBot) -> list[BotGrantResponse]:
    """Owners first, then users; persons before teams."""
    items = []
    for grant in row.grants:
        if grant.user is not None:
            items.append(BotGrantResponse(
                user_id=grant.user_id, role=grant.role, name=grant.user.username,
                team=grant.user.team.name if grant.user.team else None, is_active=grant.user.is_active,
            ))
        elif grant.team is not None:
            items.append(BotGrantResponse(team_id=grant.team_id, role=grant.role, name=grant.team.name))
    return sorted(items, key=lambda item: (item.role != BotRole.OWNER, item.team_id is not None, item.name.lower()))


def _admin_response(row: ManagedBot) -> ManagedBotAdminResponse:
    return ManagedBotAdminResponse(
        id=row.id,
        kind=row.kind,
        name=row.name,
        description=row.description,
        enabled=row.enabled,
        webhook_url=row.webhook_url,
        system_prompt=row.system_prompt,
        temperature=row.temperature,
        retrieval_enabled=row.retrieval_enabled,
        retrieval_filters=dict(row.retrieval_filters or {}),
        top_k=row.top_k,
        final_k=row.final_k,
        rerank=row.rerank,
        include_uncollected=row.include_uncollected,
        streaming=row.streaming,
        has_auth_token=bool(row.auth_token_encrypted),
        timeout_seconds=row.timeout_seconds,
        public=row.public,
        grants=_grant_responses(row),
        collections=list(row.collections or []),
        require_sources=row.require_sources,
        no_context_reply=row.no_context_reply,
        agent=dict(row.agent_config) if row.agent_config else None,
        llm_endpoint=row.llm_endpoint,
        llm_endpoints=list(row.llm_endpoints or []),
        created_at=row.created_at,
        updated_at=row.updated_at,
        source='managed',
        editable=True,
    )


def _runtime_bots(db: Session | None = None) -> list[ManagedBotAdminResponse]:
    if not settings.runtime_api_token:
        return []
    base_url = settings.runtime_bots_base_url.rstrip('/')
    headers = {'Authorization': f'Bearer {settings.runtime_api_token}'}
    try:
        # Runtime exposes the complete validated roster in one request. The
        # old summary-plus-detail sequence made admin refreshes grow linearly
        # with the number of bots and could time out as bots were added.
        response = httpx.get(f'{base_url}/internal/bot-configs', headers=headers, timeout=5)
        if getattr(response, 'status_code', 200) == 404:
            # Keep admin compatibility while an older Runtime rolls forward.
            response = httpx.get(f'{base_url}/internal/bots', headers=headers, timeout=5)
            response.raise_for_status()
            items = response.json()
            configs = []
            for item in items:
                try:
                    detail = httpx.get(
                        f'{base_url}/internal/bots/{item["id"]}', headers=headers, timeout=5,
                    )
                    detail.raise_for_status()
                    config = detail.json()
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    config = item
                configs.append(config if isinstance(config, dict) else item)
            items = configs
        else:
            response.raise_for_status()
            items = response.json()
        if not isinstance(items, list):
            return []
    except (httpx.HTTPError, ValueError, TypeError, ValidationError):
        # Managed n8n bots remain administrable when Runtime is temporarily
        # unavailable; the next refresh exposes local YAML bots again. Logged,
        # because callers such as the space-deletion guard then check only
        # Ingest's own bots.
        logger.warning('Runtime bot roster unavailable; only managed bots are known', exc_info=True)
        return []
    # YAML bots name teams. Known ones become ordinary user grants; unknown
    # names are still shown (without an id) so the list doesn't claim the
    # bot is closed, and are dropped once an admin saves an override.
    teams_by_name = {team.name: team for team in db.scalars(select(Team)).all()} if db is not None else {}
    result = []
    for item in items:
        try:
            if not isinstance(item, dict):
                continue
            config = item
            bot_id = str(config['id'])
            name = str(config['name'])
        except (KeyError, TypeError, ValueError):
            continue
        retrieval = config.get('retrieval') or {}
        model = config.get('model') or {}
        n8n = config.get('n8n') or {}
        guard = config.get('guard') or {}
        permissions = config.get('permissions') or {}
        provider = model.get('provider') if isinstance(model, dict) else None
        kind = config.get('kind') or ('n8n' if provider == 'n8n' else 'llm')
        result.append(ManagedBotAdminResponse(
            id=bot_id, kind=kind, name=name, description=config.get('description'), enabled=True,
            webhook_url=n8n.get('webhook_url'), system_prompt=config.get('system_prompt'), temperature=model.get('temperature'),
            retrieval_enabled=bool(retrieval.get('enabled')), retrieval_filters=retrieval.get('filters') or {},
            top_k=retrieval.get('top_k', 20), final_k=retrieval.get('final_k', 5), rerank=bool(retrieval.get('rerank', True)),
            include_uncollected=bool(retrieval.get('include_uncollected', True)), streaming=bool(n8n.get('streaming', False)),
            has_auth_token=bool(n8n.get('auth_token')), timeout_seconds=n8n.get('timeout_seconds', 120),
            public=not (permissions.get('teams') or permissions.get('users')) if permissions.get('public') is None
            else bool(permissions.get('public')),
            grants=[
                BotGrantResponse(team_id=teams_by_name[name].id if name in teams_by_name else None, role=BotRole.USER, name=name)
                for name in permissions.get('teams') or []
            ],
            collections=list(retrieval.get('collections') or []),
            require_sources=bool(guard.get('require_sources', True)), no_context_reply=guard.get('no_context_reply', ''),
            agent=config.get('agent'),
            llm_endpoint=model.get('endpoint'), llm_endpoints=list(model.get('endpoints') or []),
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), source='runtime', editable=True,
        ))
    return result


def _require_runtime_token(request: Request) -> None:
    expected = settings.chat_config_service_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='service misconfigured')
    authorization = request.headers.get('authorization', '')
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid service token')


def _validated_grants(db: Session, grants: list[BotGrantInput]) -> list[BotGrantInput]:
    """422 on unknown users/teams and on one subject named twice with
    different roles; exact duplicates collapse."""
    by_subject: dict[tuple[str | None, str | None], BotGrantInput] = {}
    for grant in grants:
        key = (grant.user_id, grant.team_id)
        previous = by_subject.get(key)
        if previous is not None and previous.role != grant.role:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Widersprüchliche Rollen für {grant.user_id or grant.team_id}',
            )
        by_subject[key] = grant
    user_ids = [user_id for user_id, _ in by_subject if user_id]
    team_ids = [team_id for _, team_id in by_subject if team_id]
    known = set(db.scalars(select(User.id).where(User.id.in_(user_ids))).all()) if user_ids else set()
    known |= set(db.scalars(select(Team.id).where(Team.id.in_(team_ids))).all()) if team_ids else set()
    unknown = sorted(set(user_ids + team_ids) - known)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Unbekannte Personen oder Teams: {", ".join(unknown)}',
        )
    return list(by_subject.values())


def _validate_collections(db: Session, slugs: list[str]) -> None:
    known_collections = set(db.scalars(select(Collection.slug).where(Collection.slug.in_(slugs))).all())
    unknown_collections = sorted(set(slugs) - known_collections)
    if unknown_collections:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Unbekannte Wissensbereiche: {", ".join(unknown_collections)}',
        )


def _validate_llm_endpoints(db: Session, payload: ManagedBotCreate | ManagedBotUpdate) -> None:
    agent_on = payload.agent is not None and payload.agent.enabled
    subagent_endpoints = {sub.endpoint for sub in payload.agent.subagents if sub.endpoint} if agent_on else set()
    wanted = ({payload.llm_endpoint, *payload.llm_endpoints} | subagent_endpoints) - {None, '*'}
    if not wanted:
        return
    rows = {
        row.id: row
        for row in db.scalars(select(ChatProviderConfig).where(ChatProviderConfig.id.in_(wanted))).all()
    }
    unknown = sorted(wanted - set(rows) - {'default'})
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Unbekannte LLM-Endpoints: {", ".join(unknown)}',
        )
    # Runtime refuses a subagent endpoint without tool calls per turn; say so at save time.
    without_tools = sorted(
        endpoint for endpoint in subagent_endpoints if not getattr(rows.get(endpoint), 'supports_tools', False)
    )
    if without_tools:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'LLM-Endpoints ohne Tool-Aufrufe können nicht für Subagenten genutzt werden: {", ".join(without_tools)}',
        )


def _scope_slugs(payload: ManagedBotCreate | ManagedBotUpdate) -> list[str]:
    """The bot's own spaces plus every subagent's: all must exist, or a
    later space created under a dangling slug would be searched at once."""
    subagents = payload.agent.subagents if payload.agent else []
    return list(dict.fromkeys([*payload.collections, *(slug for sub in subagents for slug in sub.collections)]))


def _replace_grants(row: ManagedBot, grants: list[BotGrantInput]) -> None:
    """Replace the access list in place; a subject that stays keeps its row
    and only changes its role (see routes._replace_grants for why)."""
    wanted = {(grant.user_id, grant.team_id): grant.role for grant in grants}
    for existing in list(row.grants):
        key = (existing.user_id, existing.team_id)
        if key in wanted:
            existing.role = wanted.pop(key)
        else:
            row.grants.remove(existing)
    for (user_id, team_id), role in wanted.items():
        row.grants.append(BotGrant(user_id=user_id, team_id=team_id, role=role))


def _apply(row: ManagedBot, payload: ManagedBotCreate | ManagedBotUpdate, admin: User, grants: list[BotGrantInput]) -> None:
    next_url = payload.webhook_url
    url_changed = bool(row.webhook_url and row.webhook_url != next_url)
    row.name = payload.name
    row.kind = payload.kind
    row.description = payload.description
    row.enabled = payload.enabled
    row.webhook_url = next_url
    row.system_prompt = payload.system_prompt
    row.temperature = payload.temperature
    row.retrieval_enabled = payload.retrieval_enabled
    row.retrieval_filters = dict(payload.retrieval_filters)
    row.top_k = payload.top_k
    row.final_k = payload.final_k
    row.rerank = payload.rerank
    row.include_uncollected = payload.include_uncollected
    row.streaming = payload.streaming
    row.timeout_seconds = payload.timeout_seconds
    row.public = payload.public
    _replace_grants(row, grants)
    row.collections = list(payload.collections)
    row.require_sources = payload.require_sources
    row.no_context_reply = payload.no_context_reply
    row.agent_config = payload.agent.model_dump() if payload.agent else None
    row.llm_endpoint = payload.llm_endpoint
    row.llm_endpoints = list(payload.llm_endpoints)
    row.updated_by_id = admin.id
    if payload.auth_token:
        row.auth_token_encrypted = encrypt_managed_bot_auth_token(payload.auth_token.strip())
    elif payload.clear_auth_token or url_changed:
        row.auth_token_encrypted = None


@router_admin.get('', response_model=ManagedBotListResponse)
def list_managed_bots(request: Request, db: Session = Depends(get_db)) -> ManagedBotListResponse:
    enforce_rate_limit(request)
    rows = db.scalars(select(ManagedBot).order_by(ManagedBot.name, ManagedBot.id)).all()
    managed = [_admin_response(row) for row in rows]
    managed_ids = {item.id for item in managed}
    tombstoned_ids = set(db.scalars(select(BotTombstone.id)).all())
    runtime = [item for item in _runtime_bots(db) if item.id not in managed_ids and item.id not in tombstoned_ids]
    return ManagedBotListResponse(items=sorted(managed + runtime, key=lambda item: (item.name, item.id)))


@router_admin.post('', response_model=ManagedBotAdminResponse, status_code=status.HTTP_201_CREATED)
def create_managed_bot(
    payload: ManagedBotCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ManagedBotAdminResponse:
    enforce_rate_limit(request)
    if db.get(ManagedBot, payload.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Diese Bot-ID wird bereits verwendet.')
    grants = _validated_grants(db, payload.grants)
    _validate_collections(db, _scope_slugs(payload))
    _validate_llm_endpoints(db, payload)
    # Re-creating a previously deleted Runtime/YAML bot restores it.
    tombstone = db.get(BotTombstone, payload.id)
    if tombstone is not None:
        db.delete(tombstone)
    row = ManagedBot(id=payload.id, name=payload.name, webhook_url=payload.webhook_url)
    _apply(row, payload, admin, grants)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _admin_response(row)


@router_admin.put('/{bot_id}', response_model=ManagedBotAdminResponse)
def update_managed_bot(
    bot_id: str,
    payload: ManagedBotUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ManagedBotAdminResponse:
    enforce_rate_limit(request)
    row = db.get(ManagedBot, bot_id)
    if row is None:
        if bot_id not in {item.id for item in _runtime_bots()}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Bot nicht gefunden.')
        # Updating a read-only Runtime/YAML bot creates a managed override and
        # must undo a prior delete suppression for that same ID.
        tombstone = db.get(BotTombstone, bot_id)
        if tombstone is not None:
            db.delete(tombstone)
        row = ManagedBot(id=bot_id, name=payload.name, webhook_url=payload.webhook_url)
        db.add(row)
    else:
        tombstone = db.get(BotTombstone, bot_id)
        if tombstone is not None:
            db.delete(tombstone)
    grants = _validated_grants(db, payload.grants)
    _validate_collections(db, _scope_slugs(payload))
    _validate_llm_endpoints(db, payload)
    _apply(row, payload, admin, grants)
    db.commit()
    db.refresh(row)
    return _admin_response(row)


@router_admin.delete('/{bot_id}')
def delete_managed_bot(
    bot_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    enforce_rate_limit(request)
    row = db.get(ManagedBot, bot_id)
    if row is None:
        # Runtime's bundled YAML bots are visible in this admin surface but
        # cannot be removed from disk.  Record their deletion so Runtime can
        # suppress them across roster refreshes.
        if bot_id not in {item.id for item in _runtime_bots()}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Bot nicht gefunden.')
    else:
        db.delete(row)
    tombstone = db.get(BotTombstone, bot_id)
    if tombstone is None:
        db.add(BotTombstone(id=bot_id, deleted_by_id=admin.id))
    else:
        tombstone.deleted_by_id = admin.id
    db.commit()
    return {'status': 'deleted'}


@router_internal.get('', response_model=ManagedBotInternalListResponse, dependencies=[Depends(_require_runtime_token)])
def internal_managed_bots(response: Response, db: Session = Depends(get_db)) -> ManagedBotInternalListResponse:
    response.headers['Cache-Control'] = 'no-store'
    rows = db.scalars(select(ManagedBot).where(ManagedBot.enabled.is_(True)).order_by(ManagedBot.id)).all()
    disabled_ids = set(db.scalars(select(BotTombstone.id)).all())
    disabled_ids.update(db.scalars(select(ManagedBot.id).where(ManagedBot.enabled.is_(False))).all())
    items: list[ManagedBotInternalResponse] = []
    for row in rows:
        try:
            auth_token = decrypt_managed_bot_auth_token(row.auth_token_encrypted) if row.auth_token_encrypted else ''
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f'credential unavailable for bot {row.id}',
            ) from exc
        items.append(ManagedBotInternalResponse(
            id=row.id,
            kind=row.kind,
            name=row.name,
            description=row.description,
            webhook_url=row.webhook_url,
            system_prompt=row.system_prompt,
            temperature=row.temperature,
            retrieval_enabled=row.retrieval_enabled,
            retrieval_filters=dict(row.retrieval_filters or {}),
            top_k=row.top_k,
            final_k=row.final_k,
            rerank=row.rerank,
            include_uncollected=row.include_uncollected,
            streaming=row.streaming,
            auth_token=auth_token,
            timeout_seconds=row.timeout_seconds,
            teams=sorted(grant.team.name for grant in row.grants if grant.team is not None),
            users=sorted(grant.user_id for grant in row.grants if grant.user_id is not None),
            public=row.public,
            collections=list(row.collections or []),
            require_sources=row.require_sources,
            no_context_reply=row.no_context_reply,
            agent=dict(row.agent_config) if row.agent_config else None,
            llm_endpoint=row.llm_endpoint,
            llm_endpoints=list(row.llm_endpoints or []),
        ))
    return ManagedBotInternalListResponse(items=items, disabled_ids=sorted(disabled_ids))


# --- Owner maintenance (ADR 0008) --------------------------------------------

def _owner_response(row: ManagedBot) -> ManagedBotOwnerResponse:
    return ManagedBotOwnerResponse(
        id=row.id,
        kind=row.kind,
        name=row.name,
        description=row.description,
        enabled=row.enabled,
        system_prompt=row.system_prompt,
        retrieval_enabled=row.retrieval_enabled,
        collections=list(row.collections or []),
        require_sources=row.require_sources,
        no_context_reply=row.no_context_reply,
        public=row.public,
        grants=_grant_responses(row),
        updated_at=row.updated_at,
    )


def _owned_bot(db: Session, bot_id: str, user: User) -> ManagedBot:
    """404 unless ``user`` owns the bot (or is an admin) -- never reveals
    that a bot the caller doesn't own exists."""
    row = db.get(ManagedBot, bot_id)
    if row is None or (
        user.role != UserRole.ADMIN
        and not any(grant.user_id == user.id and grant.role == BotRole.OWNER for grant in row.grants)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Bot nicht gefunden.')
    return row


@router_owner.get('', response_model=ManagedBotOwnerListResponse)
def list_owned_bots(
    request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ManagedBotOwnerListResponse:
    """The managed bots the caller owns. Admins use the admin surface for
    every bot; here they too only see their own."""
    enforce_rate_limit(request)
    rows = db.scalars(
        select(ManagedBot)
        .join(BotGrant, BotGrant.bot_id == ManagedBot.id)
        .where(BotGrant.user_id == user.id, BotGrant.role == BotRole.OWNER)
        .order_by(ManagedBot.name, ManagedBot.id)
    ).all()
    return ManagedBotOwnerListResponse(items=[_owner_response(row) for row in rows])


@router_owner.patch('/{bot_id}', response_model=ManagedBotOwnerResponse)
def update_owned_bot(
    bot_id: str,
    payload: ManagedBotOwnerUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ManagedBotOwnerResponse:
    """Owners maintain the content and the users of their bot; the
    connection and the owners themselves stay with the administrators."""
    enforce_rate_limit(request)
    row = _owned_bot(db, bot_id, user)
    if payload.description is not None:
        row.description = payload.description.strip() or None
    if payload.system_prompt is not None:
        prompt = payload.system_prompt.strip()
        if row.kind == 'llm' and not prompt:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail='LLM-Bots brauchen einen Systemprompt.')
        row.system_prompt = prompt or None
    if payload.collections is not None:
        _validate_collections(db, payload.collections)
        # An empty list means "every space the asker can read": emptying a
        # restricted bot widens it past what the owner may assign, so only
        # an administrator may lift the restriction.
        if not payload.collections and row.collections and user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='Nur Admins können die Einschränkung auf Wissensbereiche aufheben.',
            )
        # An owner may only attach spaces they can read themselves; spaces
        # an administrator attached before may stay.
        added = [slug for slug in payload.collections if slug not in (row.collections or [])]
        collections = {c.slug: c for c in db.scalars(select(Collection).where(Collection.slug.in_(added))).all()} if added else {}
        unreadable = sorted(slug for slug in added if collection_role(db, collections[slug], user) is None)
        if unreadable:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f'Keine Leserechte für: {", ".join(unreadable)}',
            )
        row.collections = list(payload.collections)
    if payload.require_sources is not None:
        row.require_sources = payload.require_sources
    if payload.no_context_reply is not None:
        reply = payload.no_context_reply.strip()
        if not reply:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail='no_context_reply cannot be empty')
        row.no_context_reply = reply
    if payload.public is not None:
        row.public = payload.public
    if payload.grants is not None:
        owners = [
            BotGrantInput(user_id=grant.user_id, role=BotRole.OWNER)
            for grant in row.grants if grant.role == BotRole.OWNER
        ]
        owner_ids = {grant.user_id for grant in owners}
        users = [grant for grant in _validated_grants(db, payload.grants) if grant.user_id not in owner_ids]
        _replace_grants(row, owners + users)
    row.updated_by_id = user.id
    db.commit()
    db.refresh(row)
    return _owner_response(row)
