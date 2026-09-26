"""Roles on knowledge spaces (ADR 0008).

Rights on a collection come only from its grants: a person or a team with
the role owner, member or reader. Admins may do everything, and
`visibility=public` makes every user a reader. For a team grant each person
gets the lower of the grant's role and their own team role; with several
grants the highest wins.
"""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.models import (
    Collection,
    CollectionGrant,
    CollectionRole,
    CollectionVisibility,
    Team,
    User,
    UserRole,
    user_teams,
)

_RANK = {CollectionRole.READER: 1, CollectionRole.MEMBER: 2, CollectionRole.OWNER: 3}


def role_at_least(role: CollectionRole | None, minimum: CollectionRole) -> bool:
    return role is not None and _RANK[role] >= _RANK[minimum]


def user_team_roles(db: Session, user: User) -> dict[str, CollectionRole]:
    """team id -> MEMBER or READER for every team ``user`` belongs to.

    ``users.team_id`` predates multi-membership; a primary team without its
    own ``user_teams`` row keeps the old implicit ``member`` role, an
    explicit row (especially ``reader``) always wins.
    """
    team_ids = set(user.team_ids)
    if not team_ids:
        return {}
    rows = db.execute(
        select(user_teams.c.team_id, user_teams.c.role).where(
            user_teams.c.user_id == user.id, user_teams.c.team_id.in_(team_ids)
        )
    ).all()
    roles = {team_id: CollectionRole.READER if role == 'reader' else CollectionRole.MEMBER for team_id, role in rows}
    if user.team_id in team_ids:
        roles.setdefault(user.team_id, CollectionRole.MEMBER)
    return roles


def collection_role(
    db: Session, collection: Collection, user: User, *, team_roles: dict[str, CollectionRole] | None = None
) -> CollectionRole | None:
    """The caller's effective role on ``collection``; None means no access.
    Pass ``team_roles`` when evaluating many collections for one user."""
    if user.role == UserRole.ADMIN:
        return CollectionRole.OWNER
    if team_roles is None:
        team_roles = user_team_roles(db, user)
    best: CollectionRole | None = None
    for grant in collection.grants:
        if grant.user_id is not None and grant.user_id == user.id:
            role = grant.role
        elif grant.team_id is not None and grant.team_id in team_roles:
            role = min(grant.role, team_roles[grant.team_id], key=_RANK.__getitem__)
        else:
            continue
        if best is None or _RANK[role] > _RANK[best]:
            best = role
    if best is None and collection.visibility == CollectionVisibility.PUBLIC:
        best = CollectionRole.READER
    return best


def member_collection_filter(db: Session, user: User):
    """SQL predicate for every collection where a non-admin ``user`` is at
    least a member (may upload and operate documents)."""
    member_team_ids = [
        team_id for team_id, role in user_team_roles(db, user).items() if role == CollectionRole.MEMBER
    ]
    condition = and_(
        CollectionGrant.user_id == user.id,
        CollectionGrant.role.in_([CollectionRole.OWNER, CollectionRole.MEMBER]),
    )
    if member_team_ids:
        condition = or_(
            condition,
            and_(CollectionGrant.team_id.in_(member_team_ids), CollectionGrant.role == CollectionRole.MEMBER),
        )
    return Collection.id.in_(select(CollectionGrant.collection_id).where(condition))


def owns_any_collection(db: Session, user: User) -> bool:
    return db.scalar(
        select(CollectionGrant.id)
        .where(CollectionGrant.user_id == user.id, CollectionGrant.role == CollectionRole.OWNER)
        .limit(1)
    ) is not None


def registry_acl(collection: Collection) -> tuple[list[str], list[str]]:
    """``(read_teams, read_users)`` for the Collections registry contract:
    every role includes reading, so every granted team name and person id."""
    read_teams = sorted(grant.team.name for grant in collection.grants if grant.team is not None)
    read_users = sorted(grant.user_id for grant in collection.grants if grant.user_id is not None)
    return read_teams, read_users


def team_grant_collection_slugs(db: Session, team_id: str) -> list[str]:
    """Slugs whose registry entry names this team (for rename/delete)."""
    return list(db.scalars(
        select(Collection.slug)
        .join(CollectionGrant, CollectionGrant.collection_id == Collection.id)
        .where(CollectionGrant.team_id == team_id)
        .distinct()
    ).all())


def backfill_legacy_grants(db: Session, legacy: dict[str, dict]) -> None:
    """Restore counterpart of migration 0035 for archives from before it.

    ``legacy`` maps a collection id to its archived ``owner_id``,
    ``read_teams`` and ``read_users``; the same rules as 0035 apply.
    Unknown users and teams are skipped.
    """
    user_ids = {user_id for (user_id,) in db.execute(select(User.id)).all()}
    primary_team = dict(db.execute(select(User.id, User.team_id)).all())
    team_id_by_name = {name: team_id for team_id, name in db.execute(select(Team.id, Team.name)).all()}
    for collection_id, values in legacy.items():
        collection = db.get(Collection, collection_id)
        if collection is None:
            continue
        owner_id = values.get('owner_id') if values.get('owner_id') in user_ids else None
        owner_team = primary_team.get(owner_id) if owner_id else None
        user_roles: dict[str, CollectionRole] = {}
        if owner_id:
            user_roles[owner_id] = CollectionRole.OWNER
        for user_id in values.get('read_users') or []:
            if user_id in user_ids:
                user_roles.setdefault(user_id, CollectionRole.READER)
        team_ids = [team_id_by_name[name] for name in values.get('read_teams') or [] if name in team_id_by_name]
        if owner_team:
            team_ids.append(owner_team)
        for user_id, role in user_roles.items():
            db.add(CollectionGrant(collection_id=collection_id, user_id=user_id, role=role))
        for team_id in dict.fromkeys(team_ids):
            db.add(CollectionGrant(collection_id=collection_id, team_id=team_id, role=CollectionRole.MEMBER))
        if collection.created_by_id is None:
            collection.created_by_id = owner_id
        if collection.responsible_team_id is None:
            collection.responsible_team_id = owner_team
    db.flush()
