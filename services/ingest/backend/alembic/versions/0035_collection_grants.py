"""collection grants: owners, members and readers per knowledge space (ADR 0008)

Revision ID: 0035_collection_grants
Revises: 0034_visibility_fail_closed
Create Date: 2026-09-26

Rights on a knowledge space move from three implicit sources -- the single
`owner_id`, the `read_teams`/`read_users` ACLs and the owner's primary team
(`users.team_id`) -- into explicit `collection_grants` rows with a role.
The backfill keeps every existing right:

* `owner_id` -> owner grant, and `created_by_id` (the creator is kept even
  after the ownership changes hands later).
* `read_users` -> reader grants (person sharing was read-only).
* `read_teams` -> member grants. The effective role of a team grant is the
  lower of the grant's role and the person's own team role, so team readers
  stay readers and team members keep uploading, exactly as before.
* the owner's primary team -> member grant and `responsible_team_id`
  (its members could read and, as team members, manage the space).

Afterwards `owner_id`, `read_teams` and `read_users` are dropped; the
registry contract's `read_teams`/`read_users` are computed from the grants.

sqlite-compatible on purpose (batch_alter_table, row-wise Python backfill,
no JSON SQL functions), same discipline as 0032.
"""

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = '0035_collection_grants'
down_revision = '0034_visibility_fail_closed'
branch_labels = None
depends_on = None


_collections = sa.table(
    'collections',
    sa.column('id', sa.String),
    sa.column('owner_id', sa.String),
    sa.column('read_teams', sa.JSON),
    sa.column('read_users', sa.JSON),
    sa.column('created_by_id', sa.String),
    sa.column('responsible_team_id', sa.String),
)
_grants = sa.table(
    'collection_grants',
    sa.column('id', sa.String),
    sa.column('collection_id', sa.String),
    sa.column('user_id', sa.String),
    sa.column('team_id', sa.String),
    sa.column('role', sa.String),
    sa.column('created_at', sa.DateTime(timezone=True)),
)
_users = sa.table('users', sa.column('id', sa.String), sa.column('team_id', sa.String))
_teams = sa.table('teams', sa.column('id', sa.String), sa.column('name', sa.String))


def upgrade() -> None:
    op.create_table(
        'collection_grants',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column(
            'collection_id', sa.String(length=36), sa.ForeignKey('collections.id', ondelete='CASCADE'), nullable=False
        ),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('team_id', sa.String(length=36), sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=True),
        sa.Column(
            'role',
            sa.Enum('owner', 'member', 'reader', name='collection_role', native_enum=False, validate_strings=True),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('collection_id', 'user_id', name='uq_collection_grants_user'),
        sa.UniqueConstraint('collection_id', 'team_id', name='uq_collection_grants_team'),
        sa.CheckConstraint('(user_id IS NULL) <> (team_id IS NULL)', name='ck_collection_grants_subject'),
        sa.CheckConstraint("team_id IS NULL OR role <> 'owner'", name='ck_collection_grants_team_role'),
    )
    op.create_index('ix_collection_grants_collection_id', 'collection_grants', ['collection_id'])
    op.create_index('ix_collection_grants_user_id', 'collection_grants', ['user_id'])
    op.create_index('ix_collection_grants_team_id', 'collection_grants', ['team_id'])

    with op.batch_alter_table('collections') as batch_op:
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('responsible_team_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_collections_created_by_id_users', 'users', ['created_by_id'], ['id'], ondelete='SET NULL'
        )
        batch_op.create_foreign_key(
            'fk_collections_responsible_team_id_teams', 'teams', ['responsible_team_id'], ['id'], ondelete='SET NULL'
        )
    op.create_index('ix_collections_created_by_id', 'collections', ['created_by_id'])
    op.create_index('ix_collections_responsible_team_id', 'collections', ['responsible_team_id'])

    bind = op.get_bind()
    primary_team = dict(bind.execute(sa.select(_users.c.id, _users.c.team_id)).all())
    team_id_by_name = {name: team_id for team_id, name in bind.execute(sa.select(_teams.c.id, _teams.c.name)).all()}
    now = datetime.now(timezone.utc)
    rows = bind.execute(
        sa.select(_collections.c.id, _collections.c.owner_id, _collections.c.read_teams, _collections.c.read_users)
    ).all()
    for collection_id, owner_id, read_teams, read_users in rows:
        owner_id = owner_id if owner_id in primary_team else None
        owner_team = primary_team.get(owner_id) if owner_id else None
        user_roles: dict[str, str] = {}
        if owner_id:
            user_roles[owner_id] = 'owner'
        for user_id in read_users or []:
            if user_id in primary_team:
                user_roles.setdefault(user_id, 'reader')
        team_ids = [team_id_by_name[name] for name in (read_teams or []) if name in team_id_by_name]
        if owner_team:
            team_ids.append(owner_team)
        grants = [
            {'id': str(uuid.uuid4()), 'collection_id': collection_id, 'user_id': user_id, 'team_id': None,
             'role': role, 'created_at': now}
            for user_id, role in user_roles.items()
        ] + [
            {'id': str(uuid.uuid4()), 'collection_id': collection_id, 'user_id': None, 'team_id': team_id,
             'role': 'member', 'created_at': now}
            for team_id in dict.fromkeys(team_ids)
        ]
        if grants:
            bind.execute(_grants.insert(), grants)
        bind.execute(
            _collections.update()
            .where(_collections.c.id == collection_id)
            .values(created_by_id=owner_id, responsible_team_id=owner_team)
        )

    op.drop_index('ix_collections_owner_id', table_name='collections')
    with op.batch_alter_table('collections') as batch_op:
        batch_op.drop_column('owner_id')
        batch_op.drop_column('read_teams')
        batch_op.drop_column('read_users')


def downgrade() -> None:
    with op.batch_alter_table('collections') as batch_op:
        batch_op.add_column(sa.Column('owner_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('read_teams', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        batch_op.add_column(sa.Column('read_users', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        batch_op.create_foreign_key('fk_collections_owner_id_users', 'users', ['owner_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_collections_owner_id', 'collections', ['owner_id'])

    # Best effort: the old model had one owner and no per-space member role.
    # The first owner grant (else the creator) becomes `owner_id`, every
    # team grant a `read_teams` entry, every other person a reader.
    bind = op.get_bind()
    team_name = dict(bind.execute(sa.select(_teams.c.id, _teams.c.name)).all())
    grants_by_collection: dict[str, list] = {}
    for row in bind.execute(
        sa.select(_grants.c.collection_id, _grants.c.user_id, _grants.c.team_id, _grants.c.role)
        .order_by(_grants.c.created_at)
    ).all():
        grants_by_collection.setdefault(row.collection_id, []).append(row)
    for collection_id, created_by_id in bind.execute(sa.select(_collections.c.id, _collections.c.created_by_id)).all():
        grants = grants_by_collection.get(collection_id, [])
        owners = [grant.user_id for grant in grants if grant.user_id and grant.role == 'owner']
        owner_id = owners[0] if owners else created_by_id
        bind.execute(
            _collections.update()
            .where(_collections.c.id == collection_id)
            .values(
                owner_id=owner_id,
                read_teams=[team_name[grant.team_id] for grant in grants if grant.team_id in team_name],
                read_users=[grant.user_id for grant in grants if grant.user_id and grant.user_id != owner_id],
            )
        )

    op.drop_index('ix_collections_responsible_team_id', table_name='collections')
    op.drop_index('ix_collections_created_by_id', table_name='collections')
    with op.batch_alter_table('collections') as batch_op:
        batch_op.drop_constraint('fk_collections_responsible_team_id_teams', type_='foreignkey')
        batch_op.drop_constraint('fk_collections_created_by_id_users', type_='foreignkey')
        batch_op.drop_column('responsible_team_id')
        batch_op.drop_column('created_by_id')
    op.drop_table('collection_grants')
