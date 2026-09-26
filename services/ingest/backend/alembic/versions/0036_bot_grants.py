"""bot grants: owners and users per managed bot (ADR 0008)

Revision ID: 0036_bot_grants
Revises: 0035_collection_grants
Create Date: 2026-09-26

`managed_bots.teams` (team names; empty meant "every team") becomes
explicit `bot_grants` rows with the role `user` plus a `public` flag:

* an empty list -> `public = true`, no grants (still usable by everyone);
* a non-empty list -> one user grant per known team, `public = false`.

Owners do not exist yet; an administrator assigns them afterwards. The
`teams` column is dropped; Runtime receives team names computed from the
grants.

sqlite-compatible on purpose (batch_alter_table, row-wise Python backfill),
same discipline as 0035.
"""

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = '0036_bot_grants'
down_revision = '0035_collection_grants'
branch_labels = None
depends_on = None


_bots = sa.table(
    'managed_bots', sa.column('id', sa.String), sa.column('teams', sa.JSON), sa.column('public', sa.Boolean)
)
_grants = sa.table(
    'bot_grants',
    sa.column('id', sa.String),
    sa.column('bot_id', sa.String),
    sa.column('user_id', sa.String),
    sa.column('team_id', sa.String),
    sa.column('role', sa.String),
    sa.column('created_at', sa.DateTime(timezone=True)),
)
_teams = sa.table('teams', sa.column('id', sa.String), sa.column('name', sa.String))


def upgrade() -> None:
    op.create_table(
        'bot_grants',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('bot_id', sa.String(length=255), sa.ForeignKey('managed_bots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('team_id', sa.String(length=36), sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=True),
        sa.Column(
            'role', sa.Enum('owner', 'user', name='bot_role', native_enum=False, validate_strings=True), nullable=False
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('bot_id', 'user_id', name='uq_bot_grants_user'),
        sa.UniqueConstraint('bot_id', 'team_id', name='uq_bot_grants_team'),
        sa.CheckConstraint('(user_id IS NULL) <> (team_id IS NULL)', name='ck_bot_grants_subject'),
        sa.CheckConstraint("team_id IS NULL OR role <> 'owner'", name='ck_bot_grants_team_role'),
    )
    op.create_index('ix_bot_grants_bot_id', 'bot_grants', ['bot_id'])
    op.create_index('ix_bot_grants_user_id', 'bot_grants', ['user_id'])
    op.create_index('ix_bot_grants_team_id', 'bot_grants', ['team_id'])
    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.add_column(sa.Column('public', sa.Boolean(), nullable=False, server_default='0'))

    bind = op.get_bind()
    team_id_by_name = {name: team_id for team_id, name in bind.execute(sa.select(_teams.c.id, _teams.c.name)).all()}
    now = datetime.now(timezone.utc)
    for bot_id, teams in bind.execute(sa.select(_bots.c.id, _bots.c.teams)).all():
        if not teams:
            bind.execute(_bots.update().where(_bots.c.id == bot_id).values(public=True))
            continue
        grants = [
            {'id': str(uuid.uuid4()), 'bot_id': bot_id, 'user_id': None, 'team_id': team_id, 'role': 'user',
             'created_at': now}
            for team_id in dict.fromkeys(team_id_by_name[name] for name in teams if name in team_id_by_name)
        ]
        if grants:
            bind.execute(_grants.insert(), grants)

    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.drop_column('teams')


def downgrade() -> None:
    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.add_column(sa.Column('teams', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))

    # Best effort: team grants become the team list again. A bot that was
    # restricted to persons only has no team to name and falls back to the
    # old "empty = every team", so the downgrade widens it -- the old model
    # simply cannot express it.
    bind = op.get_bind()
    team_name = dict(bind.execute(sa.select(_teams.c.id, _teams.c.name)).all())
    names_by_bot: dict[str, list[str]] = {}
    for bot_id, team_id in bind.execute(
        sa.select(_grants.c.bot_id, _grants.c.team_id).where(_grants.c.team_id.is_not(None))
    ).all():
        if team_id in team_name:
            names_by_bot.setdefault(bot_id, []).append(team_name[team_id])
    for bot_id, public in bind.execute(sa.select(_bots.c.id, _bots.c.public)).all():
        bind.execute(
            _bots.update().where(_bots.c.id == bot_id).values(teams=[] if public else names_by_bot.get(bot_id, []))
        )

    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.drop_column('public')
    op.drop_table('bot_grants')
